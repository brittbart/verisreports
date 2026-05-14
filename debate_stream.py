#!/usr/bin/env python3
"""
debate_stream.py — Verum Signal v1.7
Ingests debate audio into the pipeline via Rev AI.

TWO MODES:
  --mode async   Submit a YouTube/media URL to Rev AI async transcription.
                 Best for: testing with recorded debates, post-debate ingestion.
                 Rev AI downloads and transcribes the file, then we poll for results.

  --mode live    Stream live audio (from YouTube livestream via yt-dlp + ffmpeg)
                 to Rev AI's WebSocket streaming API.
                 Best for: live debates in real time.

USAGE:
  # Test with Round 1 recording (async):
  python3 debate_stream.py \\
    --mode async \\
    --url "https://www.youtube.com/watch?v=kwxDAqHun0E" \\
    --event-slug iowa-senate-dem-2026-r1 \\
    --speakers "JOSH TUREK:185,ZACH WAHLS:186" \\
    --dry-run

  # Live tomorrow (streaming):
  python3 debate_stream.py \\
    --mode live \\
    --url "https://www.youtube.com/watch?v=LIVE_URL_HERE" \\
    --event-slug iowa-senate-dem-2026-r2 \\
    --speakers "JOSH TUREK:185,ZACH WAHLS:186"

SPEAKER MAPPING:
  Rev AI returns "speaker_id": 0, 1, 2...
  We map these to known speaker IDs in our DB.
  Order matches order of first appearance in audio.
  For two-candidate debates: speaker 0 = first to speak, speaker 1 = second.
  Use --speakers to map: "JOSH TUREK:185,ZACH WAHLS:186"
  If order is uncertain use --speaker-order to specify: "185,186"
"""

import argparse
import json
import os
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# DB connection (uses api.py pattern — hardcoded fallback)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def get_db_conn():
    from api import get_db
    return get_db()

# ---------------------------------------------------------------------------
# Load env
# ---------------------------------------------------------------------------
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())

# ---------------------------------------------------------------------------
# Speaker mapping helpers
# ---------------------------------------------------------------------------
def parse_speaker_map(speakers_str):
    """
    Parse "JOSH TUREK:185,ZACH WAHLS:186" into {name_upper: speaker_id}
    """
    result = {}
    if not speakers_str:
        return result
    for part in speakers_str.split(','):
        part = part.strip()
        if ':' in part:
            name, sid = part.rsplit(':', 1)
            result[name.strip().upper()] = int(sid.strip())
    return result

def parse_speaker_order(order_str):
    """
    Parse "185,186" into [185, 186] — maps Rev AI speaker index to DB speaker_id
    """
    if not order_str:
        return []
    return [int(x.strip()) for x in order_str.split(',')]

def resolve_speaker_id(rev_speaker_idx, speaker_order, speaker_name=None, speaker_map=None):
    """
    Given Rev AI's speaker index (0, 1, 2...) and our mappings,
    return the DB speaker_id or None.
    """
    # Try name-based mapping first
    if speaker_name and speaker_map:
        name_upper = speaker_name.strip().upper()
        for key, sid in speaker_map.items():
            if key in name_upper or name_upper in key:
                return sid

    # Fall back to order-based mapping
    if speaker_order and rev_speaker_idx is not None:
        if rev_speaker_idx < len(speaker_order):
            return speaker_order[rev_speaker_idx]

    return None

# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------
def get_event_id(slug):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM events WHERE slug = %s", (slug,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    finally:
        conn.close()

def write_utterance(event_id, speaker_id, text, utterance_order, dry_run=False):
    """Write a single utterance to speaker_utterances and queue for extraction."""
    text = text.strip()
    if not text or len(text) < 10:
        return None

    if dry_run:
        print(f"  [DRY RUN] utterance: speaker_id={speaker_id} | {text[:80]}")
        return -1

    conn = get_db_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor()

        # Use speaker_id=1 (unknown) if not resolved
        effective_speaker_id = speaker_id if speaker_id else 1

        cur.execute("""
            INSERT INTO speaker_utterances
                (speaker_id, event_id, utterance_text, utterance_order, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT DO NOTHING
            RETURNING id
        """, (effective_speaker_id, event_id, text, utterance_order))

        row = cur.fetchone()
        uid = row[0] if row else None
        cur.close()
        return uid
    finally:
        conn.close()

def trigger_extraction(event_id, dry_run=False):
    """
    Run extract_debate_claims quality pipeline on new utterances.
    Replaces raw utterance insertion with proper claim extraction.
    """
    try:
        from extract_debate_claims import run_extraction
        run_extraction(event_id, limit=20, dry_run=dry_run)
    except Exception as e:
        print(f"  [extraction] Error: {e}")

# ---------------------------------------------------------------------------
# ASYNC MODE — submit URL to Rev AI, poll for completion
# ---------------------------------------------------------------------------
def run_async(args, token, speaker_map, speaker_order, event_id):
    from rev_ai import apiclient, JobStatus

    client = apiclient.RevAiAPIClient(token)

    print(f"\nSubmitting to Rev AI async: {args.url}")
    job = client.submit_job_url(
        args.url,
        skip_diarization=False,
        skip_punctuation=False,
        speaker_channels_count=None,
        metadata=f"verum-signal-{args.event_slug}",
    )
    print(f"  Job ID: {job.id}")
    print(f"  Status: {job.status}")

    if args.dry_run:
        print("\n[DRY RUN] Job submitted. Would poll and write to DB.")
        print(f"  Job ID to poll manually: {job.id}")
        return

    # Poll for completion
    print("\nPolling for completion (this takes a few minutes)...")
    while True:
        job = client.get_job_details(job.id)
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Status: {job.status}")
        if job.status == JobStatus.TRANSCRIBED:
            break
        elif job.status == JobStatus.FAILED:
            print(f"ERROR: Job failed — {job.failure_detail}")
            sys.exit(1)
        time.sleep(15)

    print("\n✓ Transcription complete. Fetching transcript...")
    transcript = client.get_transcript_object(job.id)

    utterance_order = 0
    written = 0

    for monologue in transcript.monologues:
        rev_speaker_idx = monologue.speaker
        text = ' '.join(
            e.value for e in monologue.elements
            if e.type == 'text'
        ).strip()

        if not text:
            continue

        speaker_id = resolve_speaker_id(
            rev_speaker_idx, speaker_order,
            speaker_name=None, speaker_map=speaker_map
        )

        uid = write_utterance(event_id, speaker_id, text, utterance_order, args.dry_run)
        if uid:
            written += 1
            label = f"Speaker {rev_speaker_idx}" if speaker_id is None else f"speaker_id={speaker_id}"
            print(f"  [{label}] {text[:70]}...")

        utterance_order += 1

    print(f"\n✓ Written {written} utterances to DB")
    trigger_extraction(event_id, args.dry_run)

# ---------------------------------------------------------------------------
# LIVE MODE — stream audio via ffmpeg → Rev AI WebSocket
# ---------------------------------------------------------------------------
def run_live(args, token, speaker_map, speaker_order, event_id):
    try:
        from rev_ai.streamingclient import RevAiStreamingClient
        from rev_ai.models import MediaConfig
    except ImportError:
        print("ERROR: rev-ai streaming requires websocket-client. Run:")
        print("  pip install rev-ai --break-system-packages")
        sys.exit(1)

    # Check yt-dlp
    if not subprocess.run(['which', 'yt-dlp'], capture_output=True).returncode == 0:
        print("ERROR: yt-dlp not found. Install with:")
        print("  pip install yt-dlp --break-system-packages")
        sys.exit(1)

    # Check ffmpeg
    if not subprocess.run(['which', 'ffmpeg'], capture_output=True).returncode == 0:
        print("ERROR: ffmpeg not found. Install with:")
        print("  sudo apt-get install -y ffmpeg")
        sys.exit(1)

    print(f"\nStarting live stream from: {args.url}")
    print("Press Ctrl+C to stop.\n")

    utterance_order = [0]
    buffer = {}  # speaker_idx -> text buffer
    written_count = [0]
    seen_speaker_ids = {}       # Rev AI ID -> DB speaker_id (order-based fallback)
    confirmed_speaker_ids = {}  # Rev AI ID -> DB speaker_id (name-confirmed, authoritative)
    pending_speaker_id = [None] # next speaker assigned this DB speaker_id

    # Build name detection map from speaker_order
    name_map = {}
    if speaker_order:
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT id, name FROM speakers WHERE id = ANY(%s)", (speaker_order,))
            for sid, sname in cur.fetchall():
                name_map[sname.lower()] = sid
                for part in sname.lower().split():
                    if len(part) > 3:
                        name_map[part] = sid
            # Add common Rev AI misspellings
            misspellings = {
                'turek': ['turk', 'terk', 'turek'],
                'wahls': ['walz', 'walls', 'wals', 'wahls'],
            }
            for correct, variants in misspellings.items():
                if correct in name_map:
                    for v in variants:
                        name_map[v] = name_map[correct]
            cur.close(); conn.close()
            print(f"  Name detection active: {list(name_map.keys())}")
        except Exception as e:
            print(f"  [WARNING] Could not build name map: {e}")

    def detect_name_cue(text):
        tl = text.lower()
        best_match, best_len = None, 0
        for frag, sid in name_map.items():
            # Require minimum 4 chars AND prefer longer matches
            # This prevents single common names from firing falsely
            if frag in tl and len(frag) > best_len and len(frag) >= 5:
                best_match, best_len = sid, len(frag)
        return best_match

    def on_partial(response):
        pass  # ignore partials

    def on_final(response):
        try:
            data = json.loads(response) if isinstance(response, str) else response
            # Handle speaker_switch event from enable_speaker_switch=true
            if data.get('type') == 'speaker_switch':
                return

            elements = data.get('elements', [])
            # Rev AI may return speaker_id as string — normalize to int
            raw_spk = data.get('speaker_id', 0)
            try:
                rev_speaker_idx = int(raw_spk)
            except (TypeError, ValueError):
                rev_speaker_idx = 0
            # Rev AI uses 1000 as sentinel for "pre-switch unknown speaker"
            # Treat as speaker index 0 (first in order)
            if rev_speaker_idx == 1000:
                rev_speaker_idx = 0

            text = ' '.join(
                e['value'] for e in elements
                if e.get('type') == 'text'
            ).strip()

            if not text or len(text) < 15:
                return

            # NAME DETECTION: anchor speaker from moderator cues
            # Name cues always set pending — never confirm the current speaker ID
            # This handles moderator bios where "Zach Wahls lives in..." fires on
            # the moderator's voice, but we want to tag the NEXT new voice as Wahls
            detected = detect_name_cue(text)
            if detected is not None:
                pending_speaker_id[0] = detected
                print(f"  [NAME CUE] speaker={detected}: {text[:60]}")

            # SPEAKER RESOLUTION (priority order):
            # 1. Name-confirmed (most reliable) — only if already locked
            # 2. New Rev AI ID + pending name cue → lock it
            # 3. Order-based fallback
            if rev_speaker_idx in confirmed_speaker_ids:
                speaker_id = confirmed_speaker_ids[rev_speaker_idx]
            elif rev_speaker_idx not in seen_speaker_ids and pending_speaker_id[0] is not None:
                # New speaker ID appeared — assign pending name cue to it
                speaker_id = pending_speaker_id[0]
                confirmed_speaker_ids[rev_speaker_idx] = speaker_id
                seen_speaker_ids[rev_speaker_idx] = speaker_id
                pending_speaker_id[0] = None
                print(f"  [CONFIRMED] Rev AI {rev_speaker_idx} = DB speaker {speaker_id}")
            elif rev_speaker_idx not in seen_speaker_ids:
                # Only assign order-based mapping if we have at least one confirmed name cue
                # This prevents the intro announcer from consuming speaker slot 0
                if confirmed_speaker_ids:
                    last_known = list(confirmed_speaker_ids.values())[-1]
                    seen_speaker_ids[rev_speaker_idx] = last_known
                else:
                    seen_speaker_ids[rev_speaker_idx] = None  # unconfirmed — wait for name cue
                speaker_id = seen_speaker_ids[rev_speaker_idx]
            else:
                speaker_id = seen_speaker_ids.get(rev_speaker_idx)

            uid = write_utterance(
                event_id, speaker_id, text,
                utterance_order[0], args.dry_run
            )

            if uid:
                written_count[0] += 1
                label = f"Speaker {rev_speaker_idx}" if speaker_id is None else f"speaker_id={speaker_id}"
                ts = datetime.now().strftime('%H:%M:%S')
                print(f"  [{ts}] [{label}] {text[:80]}")

                # Trigger extraction every 5 utterances
                if written_count[0] % 5 == 0:
                    threading.Thread(
                        target=trigger_extraction,
                        args=(event_id, args.dry_run),
                        daemon=True
                    ).start()

            utterance_order[0] += 1

        except Exception as e:
            print(f"  [WARNING] Error processing response: {e}")

    def on_error(error):
        print(f"  [ERROR] {error}")

    def on_close(code, reason):
        print(f"\n  Stream closed: {code} {reason}")
        # Final extraction trigger
        trigger_extraction(event_id, args.dry_run)

    # Get audio stream URL via yt-dlp
    print("Resolving stream URL...")
    result = subprocess.run(
        ['yt-dlp', '-g', '--format', 'bestaudio', args.url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: yt-dlp failed: {result.stderr}")
        sys.exit(1)

    stream_url = result.stdout.strip().split('\n')[0]
    print(f"  Stream URL resolved ✓")

    # Configure Rev AI streaming
    config = MediaConfig(
        content_type='audio/x-raw',
        layout='interleaved',
        rate=16000,
        audio_format='S16LE',
        channels=1,
    )

    client = RevAiStreamingClient(token, config)

    # Start ffmpeg to pipe audio to stdin
    ffmpeg_cmd = [
        'ffmpeg', '-i', stream_url,
        '-ar', '16000', '-ac', '1',
        '-f', 's16le', '-acodec', 'pcm_s16le',
        'pipe:1'
    ]

    print("Starting ffmpeg audio capture...")
    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    def audio_generator():
        chunk_size = 8000  # 0.25s at 16kHz 16-bit mono
        while True:
            chunk = ffmpeg_proc.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk

    print("Connecting to Rev AI streaming...")
    try:
        # Patch URL to enable speaker switch detection (not exposed by SDK)
        _orig_connect = client.client.connect
        def _patched_connect(url, **kwargs):
            if 'enable_speaker_switch' not in url:
                url += '&enable_speaker_switch=true'
            print(f"  Rev AI streaming with speaker switch detection enabled")
            return _orig_connect(url, **kwargs)
        client.client.connect = _patched_connect

        response_gen = client.start(audio_generator())
        for response in response_gen:
            if hasattr(response, 'type'):
                if response.type == 'partial':
                    on_partial(response)
                elif response.type == 'final':
                    on_final(response.__dict__ if hasattr(response, '__dict__') else response)
            elif isinstance(response, str):
                try:
                    data = json.loads(response)
                    if data.get('type') == 'final':
                        on_final(data)
                except Exception:
                    pass
    except KeyboardInterrupt:
        print("\n\nStopped by user.")
        ffmpeg_proc.terminate()
        trigger_extraction(event_id, args.dry_run)
    except Exception as e:
        print(f"\nStream error: {e}")
        ffmpeg_proc.terminate()
        raise

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Verum Signal debate stream ingester')
    parser.add_argument('--mode', choices=['async', 'live'], default='async',
                        help='async: recorded video; live: real-time stream')
    parser.add_argument('--url', required=True,
                        help='YouTube URL or direct media URL')
    parser.add_argument('--event-slug', required=True,
                        help='Event slug (must exist in events table)')
    parser.add_argument('--speakers', default='',
                        help='Speaker name:id mapping e.g. "JOSH TUREK:185,ZACH WAHLS:186"')
    parser.add_argument('--speaker-order', default='',
                        help='Speaker IDs in order of appearance e.g. "185,186"')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse and print without writing to DB')
    args = parser.parse_args()

    load_env()
    token = os.environ.get('REV_AI_TOKEN')
    if not token:
        print("ERROR: REV_AI_TOKEN not set in .env")
        sys.exit(1)

    speaker_map = parse_speaker_map(args.speakers)
    speaker_order = parse_speaker_order(args.speaker_order)

    print(f"Event slug: {args.event_slug}")
    print(f"Mode: {args.mode}")
    print(f"Speaker map: {speaker_map}")
    print(f"Speaker order: {speaker_order}")
    print(f"Dry run: {args.dry_run}")

    event_id = get_event_id(args.event_slug)
    if not event_id:
        print(f"ERROR: Event '{args.event_slug}' not found in DB")
        sys.exit(1)
    print(f"Event ID: {event_id}")

    if args.mode == 'async':
        run_async(args, token, speaker_map, speaker_order, event_id)
    else:
        run_live(args, token, speaker_map, speaker_order, event_id)

if __name__ == '__main__':
    main()
