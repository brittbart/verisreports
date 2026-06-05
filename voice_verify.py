#!/usr/bin/env python3
"""
voice_verify.py — Verum Signal speaker voice verification.

Uses SpeechBrain's ECAPA-TDNN model to verify speaker attributions
by comparing voice embeddings from the debate audio against pre-enrolled
reference embeddings for each candidate.

TWO MODES:

  enroll — Create speaker embedding from reference audio
    python3 voice_verify.py enroll \
      --audio reference_bennet.wav \
      --speaker-id 190 \
      --speaker-name "Michael Bennet"

  verify — Check debate attributions against enrolled embeddings
    python3 voice_verify.py verify \
      --event-id 12 \
      --audio debate_audio/event_12_20260613_180000.wav

ENROLLMENT AUDIO:
  Download reference audio from public sources (Senate hearings, press
  conferences) using yt-dlp, then convert to 16kHz mono WAV:
    yt-dlp -x --audio-format wav "https://youtube.com/watch?v=XXXXX" -o reference.wav
    ffmpeg -i reference.wav -ar 16000 -ac 1 reference_16k.wav

  Use 30-120 seconds of clear solo speech (no crosstalk, no music).
  Multiple enrollment files can be averaged for better accuracy.

REQUIREMENTS:
  pip install speechbrain torchaudio --break-system-packages
"""

import argparse
import json
import os
import sys
import numpy as np
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EMBEDDINGS_DIR = os.path.join(SCRIPT_DIR, 'speaker_embeddings')
AUDIO_DIR = os.path.join(SCRIPT_DIR, 'debate_audio')

# ---------------------------------------------------------------------------
# Model loading (lazy — only loads when needed)
# ---------------------------------------------------------------------------
_model = None
_model_dir = os.path.join(SCRIPT_DIR, '.speechbrain_cache')

def get_model():
    """Load ECAPA-TDNN model. Downloads on first run (~100MB)."""
    global _model
    if _model is not None:
        return _model
    print("  Loading ECAPA-TDNN speaker embedding model...")
    from speechbrain.inference.speaker import EncoderClassifier
    _model = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=_model_dir,
    )
    print("  ✓ Model loaded")
    return _model


def extract_embedding(audio_path, start_sec=None, end_sec=None):
    """Extract speaker embedding from audio file or segment.

    Returns numpy array of shape (192,).
    """
    import torch
    import soundfile as sf

    data, sample_rate = sf.read(audio_path, dtype='float32')

    # Convert to mono if needed (soundfile returns [samples] or [samples, channels])
    if data.ndim > 1:
        data = data.mean(axis=1)

    # Resample to 16kHz if needed
    if sample_rate != 16000:
        import torchaudio
        waveform = torch.tensor(data).unsqueeze(0)
        resampler = torchaudio.transforms.Resample(sample_rate, 16000)
        waveform = resampler(waveform)
        sample_rate = 16000
    else:
        waveform = torch.tensor(data).unsqueeze(0)

    # Crop to segment if specified
    if start_sec is not None and end_sec is not None:
        start_sample = int(start_sec * sample_rate)
        end_sample = int(end_sec * sample_rate)
        # Clamp to valid range
        start_sample = max(0, start_sample)
        end_sample = min(waveform.shape[1], end_sample)
        if end_sample <= start_sample:
            return None
        waveform = waveform[:, start_sample:end_sample]

    # Minimum duration check (model needs at least ~0.5s)
    min_samples = int(0.5 * sample_rate)
    if waveform.shape[1] < min_samples:
        return None

    model = get_model()
    with torch.no_grad():
        embedding = model.encode_batch(waveform)
    return embedding.squeeze().numpy()


def cosine_distance(a, b):
    """Cosine distance between two vectors. 0 = identical, 2 = opposite."""
    from scipy.spatial.distance import cosine
    return cosine(a, b)


# ---------------------------------------------------------------------------
# ENROLL MODE
# ---------------------------------------------------------------------------
def cmd_enroll(args):
    """Create speaker embedding from reference audio."""
    os.makedirs(EMBEDDINGS_DIR, exist_ok=True)

    audio_files = args.audio  # list of files
    speaker_id = args.speaker_id
    speaker_name = args.speaker_name

    print(f"\n  Enrolling speaker: {speaker_name} (speaker_id={speaker_id})")
    print(f"  Audio files: {len(audio_files)}")

    embeddings = []
    for audio_path in audio_files:
        if not os.path.exists(audio_path):
            print(f"  ✗ File not found: {audio_path}")
            continue

        print(f"  Processing: {audio_path}")
        emb = extract_embedding(audio_path)
        if emb is not None:
            embeddings.append(emb)
            print(f"    ✓ Embedding extracted (shape={emb.shape})")
        else:
            print(f"    ✗ Could not extract embedding (too short?)")

    if not embeddings:
        print("  ✗ No embeddings extracted — check audio files")
        return

    # Average embeddings for more robust representation
    avg_embedding = np.mean(embeddings, axis=0)
    # L2 normalize
    avg_embedding = avg_embedding / np.linalg.norm(avg_embedding)

    # Save
    out_path = os.path.join(EMBEDDINGS_DIR, f'speaker_{speaker_id}.json')
    data = {
        'speaker_id': speaker_id,
        'speaker_name': speaker_name,
        'embedding': avg_embedding.tolist(),
        'num_sources': len(embeddings),
        'source_files': [os.path.basename(f) for f in audio_files],
        'enrolled_at': datetime.now().isoformat(),
    }
    with open(out_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n  ✓ Enrollment saved to: {out_path}")
    print(f"  ✓ Averaged {len(embeddings)} embedding(s)")


# ---------------------------------------------------------------------------
# VERIFY MODE
# ---------------------------------------------------------------------------
def load_enrolled_embeddings():
    """Load all enrolled speaker embeddings."""
    enrolled = {}
    if not os.path.exists(EMBEDDINGS_DIR):
        return enrolled
    for fname in os.listdir(EMBEDDINGS_DIR):
        if fname.startswith('speaker_') and fname.endswith('.json'):
            with open(os.path.join(EMBEDDINGS_DIR, fname)) as f:
                data = json.load(f)
            enrolled[data['speaker_id']] = {
                'name': data['speaker_name'],
                'embedding': np.array(data['embedding']),
            }
    return enrolled


def cmd_verify(args):
    """Verify debate speaker attributions against enrolled embeddings."""
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SCRIPT_DIR, '.env'), override=False)

    event_id = args.event_id
    audio_path = args.audio

    if not audio_path:
        # Auto-detect from debate_audio directory
        if os.path.exists(AUDIO_DIR):
            candidates = sorted([
                f for f in os.listdir(AUDIO_DIR)
                if f.startswith(f'event_{event_id}_') and f.endswith('.wav')
            ])
            if candidates:
                audio_path = os.path.join(AUDIO_DIR, candidates[-1])  # most recent
                print(f"  Auto-detected audio: {audio_path}")

    if not audio_path or not os.path.exists(audio_path):
        print(f"  ✗ No audio file found for event {event_id}")
        print(f"    Provide --audio path or check {AUDIO_DIR}/")
        return

    # Load enrolled embeddings
    enrolled = load_enrolled_embeddings()
    if not enrolled:
        print("  ✗ No enrolled speakers found")
        print(f"    Run: python3 voice_verify.py enroll --audio <reference.wav> --speaker-id <id> --speaker-name <name>")
        return
    print(f"  Enrolled speakers: {', '.join(f'{v['name']} ({k})' for k, v in enrolled.items())}")

    # Fetch utterances with timestamps
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'), connect_timeout=10,
    )
    cur = conn.cursor()
    cur.execute("""
        SELECT su.id, su.utterance_order, su.speaker_id, s.name,
               su.timestamp_seconds, su.utterance_text,
               su.attribution_uncertain
        FROM speaker_utterances su
        LEFT JOIN speakers s ON s.id = su.speaker_id
        WHERE su.event_id = %s
          AND su.speaker_id IS NOT NULL
          AND su.timestamp_seconds IS NOT NULL
        ORDER BY su.utterance_order ASC
    """, (event_id,))
    utterances = cur.fetchall()
    cur.close()
    conn.close()

    if not utterances:
        print(f"  ✗ No utterances with timestamps found for event {event_id}")
        return

    print(f"  Utterances to verify: {len(utterances)}")
    print(f"  Audio file: {audio_path}\n")

    # Process each utterance
    # Group consecutive utterances from same speaker into segments
    # (individual utterances may be too short for reliable embeddings)
    SEGMENT_MIN_DURATION = 3.0  # seconds — need enough audio for reliable embedding
    MISMATCH_THRESHOLD = 0.45   # cosine distance above which we flag

    segments = []
    current = None

    for uid, uorder, speaker_id, speaker_name, ts, text, uncertain in utterances:
        if speaker_id == 3:  # skip moderator
            continue
        if speaker_id not in enrolled:
            continue

        if current and current['speaker_id'] == speaker_id:
            # Extend current segment
            current['end_ts'] = ts + 5.0  # estimate 5s per utterance
            current['utterances'].append({
                'uid': uid, 'uorder': uorder, 'text': text,
                'uncertain': uncertain,
            })
        else:
            if current:
                segments.append(current)
            current = {
                'speaker_id': speaker_id,
                'speaker_name': speaker_name,
                'start_ts': max(0, ts - 0.5),  # slight padding
                'end_ts': ts + 5.0,
                'utterances': [{
                    'uid': uid, 'uorder': uorder, 'text': text,
                    'uncertain': uncertain,
                }],
            }

    if current:
        segments.append(current)

    print(f"  Grouped into {len(segments)} speaker segments\n")

    # Verify each segment
    mismatches = []
    verified = 0
    skipped = 0

    for i, seg in enumerate(segments):
        duration = seg['end_ts'] - seg['start_ts']
        if duration < SEGMENT_MIN_DURATION:
            skipped += 1
            continue

        emb = extract_embedding(audio_path, seg['start_ts'], seg['end_ts'])
        if emb is None:
            skipped += 1
            continue

        # Compare against all enrolled speakers
        distances = {}
        for sid, enrolled_data in enrolled.items():
            dist = cosine_distance(emb, enrolled_data['embedding'])
            distances[sid] = dist

        attributed_sid = seg['speaker_id']
        attributed_dist = distances.get(attributed_sid, 999)

        # Find closest enrolled speaker
        closest_sid = min(distances, key=distances.get)
        closest_dist = distances[closest_sid]

        verified += 1

        if closest_sid != attributed_sid and attributed_dist > MISMATCH_THRESHOLD:
            mismatches.append({
                'segment_index': i,
                'utterance_orders': [u['uorder'] for u in seg['utterances']],
                'first_text': seg['utterances'][0]['text'][:70],
                'attributed_to': f"{seg['speaker_name']} ({attributed_sid})",
                'attributed_distance': round(attributed_dist, 3),
                'closest_speaker': f"{enrolled[closest_sid]['name']} ({closest_sid})",
                'closest_distance': round(closest_dist, 3),
            })

        # Progress
        if (i + 1) % 25 == 0:
            print(f"  [{i+1}/{len(segments)}] verified, {len(mismatches)} mismatches so far...")

    # Report
    print(f"\n{'─' * 50}")
    print(f"  RESULTS")
    print(f"{'─' * 50}")
    print(f"  Segments verified:  {verified}")
    print(f"  Segments skipped:   {skipped} (too short)")
    print(f"  Mismatches found:   {len(mismatches)}")
    print(f"  Mismatch threshold: {MISMATCH_THRESHOLD} cosine distance\n")

    if not mismatches:
        print("  ✓ All verified attributions are acoustically consistent")
        return

    print(f"  ⚠ {len(mismatches)} potential misattribution(s):\n")
    for mm in mismatches:
        print(f"  utterance_order(s): {mm['utterance_orders']}")
        print(f"    text: {mm['first_text']}")
        print(f"    attributed: {mm['attributed_to']} (distance={mm['attributed_distance']})")
        print(f"    closest:    {mm['closest_speaker']} (distance={mm['closest_distance']})")
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Verum Signal speaker voice verification')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Enroll
    enroll_parser = subparsers.add_parser('enroll', help='Enroll speaker from reference audio')
    enroll_parser.add_argument('--audio', nargs='+', required=True,
                               help='Reference audio file(s) — 16kHz WAV recommended')
    enroll_parser.add_argument('--speaker-id', type=int, required=True)
    enroll_parser.add_argument('--speaker-name', required=True)

    # Verify
    verify_parser = subparsers.add_parser('verify', help='Verify debate attributions')
    verify_parser.add_argument('--event-id', type=int, required=True)
    verify_parser.add_argument('--audio', default=None,
                               help='Debate audio WAV (auto-detected from debate_audio/ if omitted)')

    args = parser.parse_args()

    print("=" * 68)
    print(f"Verum Signal — Speaker voice verification")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 68)

    if args.command == 'enroll':
        cmd_enroll(args)
    elif args.command == 'verify':
        cmd_verify(args)


if __name__ == '__main__':
    main()
