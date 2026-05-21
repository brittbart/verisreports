#!/usr/bin/env python3
"""
debate_stream_deepgram.py — Verum Signal v1.7
Live debate transcription via Deepgram nova-3 with per-word speaker diarization.
Replaces Rev AI streaming with cleaner speaker attribution.

USAGE:
  python3 debate_stream_deepgram.py \
    --mode live \
    --url "https://www.youtube.com/@IowaPBS/live" \
    --event-slug iowa-senate-dem-2026-r2 \
    --speaker-order "185,186" \
    [--dry-run]
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime

import websockets.sync.client as ws_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Env / DB
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

def get_db_conn():
    from api import get_db
    return get_db()

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['live'], default='live')
    p.add_argument('--url', required=True)
    p.add_argument('--event-slug', required=True)
    p.add_argument('--speaker-order', default='')
    p.add_argument('--dry-run', action='store_true')
    return p.parse_args()

# ---------------------------------------------------------------------------
# Speaker resolution
# ---------------------------------------------------------------------------

def parse_speaker_order(order_str):
    if not order_str:
        return []
    return [int(x.strip()) for x in order_str.split(',') if x.strip()]

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

def get_event_speakers(event_id):
    """Return ordered list of (speaker_id, name) for this event."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.id, s.name
            FROM event_speakers es
            JOIN speakers s ON s.id = es.speaker_id
            WHERE es.event_id = %s
            ORDER BY es.speaker_order
        """, (event_id,))
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Name detection (same logic as Rev AI version)
# ---------------------------------------------------------------------------

def build_name_map(event_speakers):
    """
    Build a map from name fragment (lower) -> DB speaker_id, plus a set of
    fragments that require whole-word matching (to prevent substring collisions).
    Returns: (name_map, whole_word_set)
    Heuristic: any variant <=4 chars uses whole_word_only=True (e.g. 'marx'
    must not match 'Marxist', 'Marxism', etc.)
    """
    name_map = {}
    whole_word_set = set()
    for sid, sname in event_speakers:
        for part in sname.lower().split():
            if len(part) > 3:
                name_map[part] = sid
                if len(part) <= 4:
                    whole_word_set.add(part)
    # Rev AI misspelling variants (retained — harmless if not in event_speakers)
    # May 26 Colorado Gov GOP R2: Bottoms, Kirkmeyer, Marx
    # Dict format: {'variants': [...], 'whole_word_only': bool}
    # List format (legacy): treated as {'variants': [...], 'whole_word_only': False}
    misspellings = {
        'turek': {'variants': ['turk', 'terk', 'turek'], 'whole_word_only': False},
        'wahls': {'variants': ['walz', 'walls', 'wals', 'wahls'], 'whole_word_only': False},
        'bottoms': {'variants': ['bottoms', 'bottom'], 'whole_word_only': False},
        'kirkmeyer': {'variants': ['kirkmeyer', 'kirkmeier', 'kirkmyer', 'kirk meyer'], 'whole_word_only': False},
        'marx': {'variants': ['marx'], 'whole_word_only': True},  # <=4 chars — whole-word only
    }
    for correct, entry in misspellings.items():
        if isinstance(entry, list):
            entry = {'variants': entry, 'whole_word_only': False}
        if correct in name_map:
            for v in entry['variants']:
                name_map[v] = name_map[correct]
                if entry['whole_word_only']:
                    whole_word_set.add(v)
    return name_map, whole_word_set

def detect_name_cue(text, name_map, whole_word_set=None):
    """
    Return DB speaker_id if a single candidate name is mentioned.
    Return None if both names mentioned (moderator bio section) or no match.
    Fragments in whole_word_set use word-boundary regex instead of substring match.
    """
    import re
    if whole_word_set is None:
        whole_word_set = set()
    tl = text.lower()
    detected = {}
    for fragment, sid in name_map.items():
        if fragment in whole_word_set:
            if re.search(r'\b' + re.escape(fragment) + r'\b', tl, re.IGNORECASE):
                detected[sid] = True
        else:
            if fragment in tl:
                detected[sid] = True
    if len(detected) == 1:
        return list(detected.keys())[0]
    return None  # 0 or 2+ matches

# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def insert_utterance(event_id, speaker_id, text, dry_run=False):
    if dry_run:
        spk_label = f"speaker_id={speaker_id}" if speaker_id else "speaker_id=None"
        print(f"  [DRY RUN] [{spk_label}] {text[:80]}")
        return None
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO speaker_utterances (event_id, speaker_id, text, created_at)
            VALUES (%s, %s, %s, NOW())
            RETURNING id
        """, (event_id, speaker_id, text))
        uid = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return uid
    except Exception as e:
        conn.rollback()
        print(f"  [DB ERROR] {e}")
        return None
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Claim extraction trigger
# ---------------------------------------------------------------------------

def maybe_run_extraction(event_id, utterance_count, dry_run):
    if utterance_count % 5 != 0:
        return
    cmd = [sys.executable, 'extract_debate_claims.py',
             '--event-id', str(event_id),
             '--limit', '20']
    if dry_run:
        cmd.append('--dry-run')
    subprocess.Popen(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Audio pipeline
# ---------------------------------------------------------------------------

def resolve_stream_url(url):
    """Resolve a YouTube or other URL to a direct stream URL via yt-dlp."""
    print(f"  Resolving stream URL...")
    r = subprocess.run(
        ['yt-dlp', '-g', '-f', 'bestaudio/best', '--quiet', url],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"yt-dlp failed: {r.stderr.strip()}")
    stream_url = r.stdout.strip().split('\n')[0]
    print(f"  Stream URL resolved ✓")
    return stream_url

def start_ffmpeg(stream_url):
    """Start ffmpeg, return Popen object with PCM audio on stdout."""
    return subprocess.Popen([
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-reconnect', '1', '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', stream_url,
        '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
        '-f', 's16le', 'pipe:1'
    ], stdout=subprocess.PIPE)

# ---------------------------------------------------------------------------
# Main streaming loop
# ---------------------------------------------------------------------------

def run_live(args, event_id, speaker_order, event_speakers, dry_run):
    api_key = os.environ.get('DEEPGRAM_API_KEY', '')
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY not set")

    dg_url = (
        "wss://api.deepgram.com/v1/listen"
        "?model=nova-3"
        "&encoding=linear16"
        "&sample_rate=16000"
        "&channels=1"
        "&diarize=true"
        "&punctuate=true"
        "&interim_results=false"
        "&endpointing=500"
    )

    name_map, whole_word_set = build_name_map(event_speakers)
    print(f"  Name detection active: {sorted(name_map.keys())}")

    # Speaker tracking
    # Maps Deepgram speaker index (0,1,2...) -> DB speaker_id
    dg_to_db = {}          # confirmed mappings
    pending_speaker = [None]  # pending name cue waiting for next new speaker

    # Order-based fallback: first new speaker = speaker_order[0], etc.
    order_assigned = []

    utterance_count = [0]

    # Load persisted speaker mappings from DB (survive stream restarts)
    try:
        _map_conn = get_db_conn()
        _map_cur = _map_conn.cursor()
        _map_cur.execute("SELECT dg_speaker_map FROM events WHERE id = %s", (event_id,))
        _map_row = _map_cur.fetchone()
        if _map_row and _map_row[0]:
            for dg_idx_str, sid in _map_row[0].items():
                dg_to_db[int(dg_idx_str)] = sid
            print(f"  [PERSIST] Loaded {len(dg_to_db)} speaker mapping(s) from DB: {dg_to_db}")
        else:
            print("  [PERSIST] No prior speaker mappings found — starting fresh")
        _map_cur.close()
        _map_conn.close()
    except Exception as _e:
        print(f"  [PERSIST] WARNING: Could not load speaker mappings from DB: {_e} — proceeding with empty map")

    def persist_mapping(dg_idx, speaker_id):
        """Write confirmed dg_idx -> speaker_id mapping to DB. Non-fatal on failure."""
        try:
            _pc = get_db_conn()
            _pu = _pc.cursor()
            _pu.execute(
                "UPDATE events SET dg_speaker_map = jsonb_set(COALESCE(dg_speaker_map, '{}'::jsonb), %s, %s::jsonb) WHERE id = %s",
                ([str(dg_idx)], str(speaker_id), event_id)
            )
            _pc.commit()
            _pu.close()
            _pc.close()
        except Exception as _pe:
            print(f"  [PERSIST] WARNING: Could not persist mapping dg={dg_idx}->sid={speaker_id}: {_pe} — stream continues")

    def resolve_speaker(dg_idx, transcript):
        """
        Resolve Deepgram speaker index to DB speaker_id.
        Priority: confirmed > pending name cue > order-based fallback
        """
        # Check name cue in this transcript
        detected = detect_name_cue(transcript, name_map, whole_word_set)
        if detected is not None:
            # Name cue detected — always set pending, never confirm current
            pending_speaker[0] = detected
            print(f"  [NAME CUE] speaker={detected}: {transcript[:60]}")

        # Already confirmed this Deepgram index
        if dg_idx in dg_to_db:
            return dg_to_db[dg_idx]

        # New Deepgram speaker index — assign pending if available
        if pending_speaker[0] is not None:
            sid = pending_speaker[0]
            dg_to_db[dg_idx] = sid
            pending_speaker[0] = None
            if dg_idx not in order_assigned:
                order_assigned.append(dg_idx)
            print(f"  [CONFIRMED] Deepgram spk {dg_idx} = DB speaker {sid}")
            persist_mapping(dg_idx, sid)
            return sid

        # Order-based fallback
        if dg_idx not in order_assigned:
            order_assigned.append(dg_idx)
            idx = order_assigned.index(dg_idx)
            if idx < len(speaker_order):
                sid = speaker_order[idx]
                dg_to_db[dg_idx] = sid
                print(f"  [ORDER] Deepgram spk {dg_idx} = DB speaker {sid} (slot {idx})")
                persist_mapping(dg_idx, sid)
                return sid

        return dg_to_db.get(dg_idx)

    # Resolve stream URL and prime ffmpeg
    stream_url = resolve_stream_url(args.url)
    ffmpeg = start_ffmpeg(stream_url)

    print("  Buffering audio before connecting...")
    buffer = ffmpeg.stdout.read(32000)  # ~2s at 16kHz mono
    if len(buffer) < 8000:
        raise RuntimeError("ffmpeg produced insufficient audio — check stream URL")
    print(f"  ✓ Buffered {len(buffer)} bytes, connecting to Deepgram...")

    done = threading.Event()
    KEEPALIVE = json.dumps({"type": "KeepAlive"})

    try:
        with ws_client.connect(
            dg_url,
            additional_headers={"Authorization": f"Token {api_key}"},
            open_timeout=10,
        ) as sock:
            print("  ✓ Connected to Deepgram")

            # Send buffered audio immediately
            sock.send(buffer)

            def receive():
                while not done.is_set():
                    try:
                        raw = sock.recv(timeout=15)
                        data = json.loads(raw)
                        msg_type = data.get('type')

                        if msg_type == 'Metadata':
                            print(f"  ✓ Deepgram session active")
                            continue

                        if msg_type != 'Results':
                            continue

                        alts = data.get('channel', {}).get('alternatives', [{}])
                        transcript = alts[0].get('transcript', '').strip()
                        words = alts[0].get('words', [])

                        if not transcript:
                            continue

                        # Get dominant speaker from word-level labels
                        speaker_counts = Counter(
                            w.get('speaker') for w in words if 'speaker' in w
                        )
                        dg_idx = speaker_counts.most_common(1)[0][0] if speaker_counts else None

                        now = datetime.now().strftime('%H:%M:%S')
                        db_speaker_id = resolve_speaker(dg_idx, transcript) if dg_idx is not None else None

                        if db_speaker_id:
                            label = f"speaker_id={db_speaker_id}"
                        else:
                            label = f"Deepgram spk {dg_idx}"

                        print(f"  [{now}] [{label}] {transcript[:80]}")

                        uid = insert_utterance(event_id, db_speaker_id, transcript, dry_run)
                        if uid or dry_run:
                            utterance_count[0] += 1
                            maybe_run_extraction(event_id, utterance_count[0], dry_run)

                    except TimeoutError:
                        continue
                    except Exception as e:
                        if not done.is_set():
                            print(f"  [RECV ERROR] {e}")
                            done.set()

            recv_thread = threading.Thread(target=receive, daemon=True)
            recv_thread.start()

            # Stream audio
            last_ka = time.time()
            try:
                while not done.is_set():
                    chunk = ffmpeg.stdout.read(4096)
                    if not chunk:
                        print("  Audio stream ended")
                        break
                    sock.send(chunk)
                    if time.time() - last_ka > 5:
                        sock.send(KEEPALIVE)
                        last_ka = time.time()
            except KeyboardInterrupt:
                print("\n  Stopped by user.")
            finally:
                done.set()
                ffmpeg.terminate()

    except Exception as e:
        print(f"  [CONNECTION ERROR] {e}")
        ffmpeg.terminate()
        raise

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    load_env()
    args = parse_args()

    print(f"Event slug: {args.event_slug}")
    print(f"Mode: {args.mode}")
    print(f"Dry run: {args.dry_run}")

    speaker_order = parse_speaker_order(args.speaker_order)
    print(f"Speaker order: {speaker_order}")

    event_id = get_event_id(args.event_slug)
    if not event_id:
        print(f"ERROR: Event '{args.event_slug}' not found")
        sys.exit(1)
    print(f"Event ID: {event_id}")

    event_speakers = get_event_speakers(event_id)
    if not event_speakers:
        print("WARNING: No speakers found for this event — name detection disabled")

    print(f"\nStarting Deepgram live stream from: {args.url}")
    print("Press Ctrl+C to stop.\n")

    run_live(args, event_id, speaker_order, event_speakers, args.dry_run)

if __name__ == '__main__':
    main()
