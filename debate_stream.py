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
# DB connection — standalone with hardcoded fallback (no Flask import)
# Railway Runtime V2 strips env vars from subprocesses spawned by always-on
# services. This mirrors the api.py:47 pattern with individual kwargs.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2

def get_db_conn():
    if os.environ.get('DATABASE_URL'):
        return psycopg2.connect(dsn=os.environ['DATABASE_URL'])
    return psycopg2.connect(
        dbname=os.environ.get('DB_NAME', 'railway'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP'),
        host=os.environ.get('DB_HOST', 'shinkansen.proxy.rlwy.net'),
        port=os.environ.get('DB_PORT', '35370'),
        connect_timeout=10,
        application_name='veris-debate-stream',
    )

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

def parse_speaker_order(order_str, conn=None):
    """
    Parse speaker order string into list of DB speaker IDs.
    Accepts either integer IDs ("185,186") or name strings ("Kirkmeyer,Bottoms").
    Name strings are resolved via DB lookup (case-insensitive, partial match).
    """
    if not order_str:
        return []
    parts = [x.strip() for x in order_str.split(',')]
    # If all parts are integers, treat as IDs directly
    if all(p.isdigit() for p in parts):
        return [int(p) for p in parts]
    # Otherwise resolve names via DB
    if conn is None:
        try:
            from verdict_engine import get_connection as _gc
            conn = _gc()
            _close = True
        except Exception as e:
            print(f"ERROR: cannot resolve speaker names without DB connection: {e}")
            raise
    else:
        _close = False
    ids = []
    with conn.cursor() as cur:
        for name in parts:
            cur.execute(
                "SELECT id, name FROM speakers WHERE name ILIKE %s ORDER BY id LIMIT 1",
                (f'%{name}%',)
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Speaker not found: '{name}'. Check spelling or use integer ID.")
            print(f"  Resolved speaker '{name}' -> id={row[0]} ({row[1]})")
            ids.append(row[0])
    if _close:
        conn.close()
    return ids

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

# Confidence threshold below which speaker attribution is flagged as uncertain
ATTRIBUTION_CONFIDENCE_THRESHOLD = 0.60

def write_utterance(event_id, speaker_id, text, utterance_order, dry_run=False,
                    timestamp_seconds=None, attribution_confidence=None,
                    force_uncertain=False):
    """Write a single utterance to speaker_utterances and queue for extraction.

    If attribution_confidence is below ATTRIBUTION_CONFIDENCE_THRESHOLD,
    the utterance is written with speaker_id=None and attribution_uncertain=True
    so it can be re-attributed in a post-debate pass without polluting claims
    with wrong speaker data.

    If force_uncertain=True, the utterance keeps its speaker_id (so claims still
    generate during live coverage) but is flagged attribution_uncertain=True for
    post-debate review via reattribute_uncertain.py.
    """
    text = text.strip()
    if not text or len(text) < 10:
        return None

    # Apply confidence gate
    attribution_uncertain = False
    if attribution_confidence is not None and attribution_confidence < ATTRIBUTION_CONFIDENCE_THRESHOLD:
        print(f"  [UNCERTAIN] confidence={attribution_confidence:.2f} < {ATTRIBUTION_CONFIDENCE_THRESHOLD} — writing unattributed")
        speaker_id = None
        attribution_uncertain = True
    elif force_uncertain:
        # Caller flagged this as uncertain (e.g. last_known fallback)
        # Keep speaker_id so claims still generate, but flag for post-debate review
        attribution_uncertain = True
        print(f"  [UNCERTAIN] force_uncertain — speaker_id={speaker_id} kept but flagged for review")

    if dry_run:
        flag = ' [UNCERTAIN]' if attribution_uncertain else ''
        print(f"  [DRY RUN]{flag} utterance: speaker_id={speaker_id} conf={attribution_confidence} | {text[:80]}")
        return -1

    conn = get_db_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor()

        effective_speaker_id = speaker_id if speaker_id else None

        cur.execute("""
            INSERT INTO speaker_utterances
                (speaker_id, event_id, utterance_text, utterance_order,
                 timestamp_seconds, attribution_confidence, attribution_uncertain, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT DO NOTHING
            RETURNING id
        """, (effective_speaker_id, event_id, text, utterance_order,
              timestamp_seconds, attribution_confidence, attribution_uncertain))

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

    # Support local file paths as well as URLs
    import os as _os
    if _os.path.exists(args.url):
        print(f"\nSubmitting local file to Rev AI async: {args.url}")
        job = client.submit_job_local_file(
                args.url,
                skip_diarization=False,
                skip_punctuation=False,
                speaker_channels_count=None,
                metadata=f"verum-signal-{args.event_slug}",
            )
    else:
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
            if e.type_ == 'text'
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

    # Load persisted speaker mappings from DB (survive stream restarts)
    # Note: column named dg_speaker_map for historical reasons (Deepgram era); now stores Rev AI mappings
    try:
        _mc = get_db_conn(); _mu = _mc.cursor()
        _mu.execute("SELECT dg_speaker_map FROM events WHERE id = %s", (event_id,))
        _mr = _mu.fetchone()
        if _mr and _mr[0]:
            for k, v in _mr[0].items():
                confirmed_speaker_ids[int(k)] = v
                seen_speaker_ids[int(k)] = v
            print(f"  [PERSIST] Loaded {len(confirmed_speaker_ids)} mapping(s) from DB: {confirmed_speaker_ids}")
        else:
            print("  [PERSIST] No prior mappings — starting fresh")
        _mu.close(); _mc.close()
    except Exception as _pe:
        print(f"  [PERSIST] WARNING: Could not load mappings: {_pe} — proceeding with empty map")

    def persist_mapping(rev_idx, speaker_id):
        """Write Rev AI idx -> speaker_id mapping to DB. Non-fatal on failure."""
        try:
            _pc = get_db_conn(); _pu = _pc.cursor()
            _pu.execute(
                "UPDATE events SET dg_speaker_map = jsonb_set(COALESCE(dg_speaker_map, '{}'::jsonb), %s, %s::jsonb) WHERE id = %s",
                ([str(rev_idx)], str(speaker_id), event_id)
            )
            _pc.commit(); _pu.close(); _pc.close()
        except Exception as _we:
            print(f"  [PERSIST] WARNING: Could not persist mapping {rev_idx}->{speaker_id}: {_we}")
    # Calibration phase — first 3 minutes, aggressive name detection

    # Voice re-identification — fires once at 90s after stream start
    # Extracts embeddings from WAV file for each detected Rev AI speaker
    # and matches against enrolled embeddings to populate confirmed_speaker_ids.
    # Runs in background thread — does not block streaming.
    voice_id_done = [False]
    # Track which audio time segments belong to each Rev AI speaker index
    # Used by voice re-identification to sample correct audio without relying on DB speaker_id
    rev_speaker_timestamps = {}  # rev_idx -> [timestamp_seconds, ...]

    def run_voice_identification():
        import time as _time
        _time.sleep(60)  # wait 60s for audio to accumulate
        if voice_id_done[0]:
            return
        voice_id_done[0] = True
        try:
            import numpy as np
            import os as _os
            from voice_verify import load_enrolled_embeddings, extract_embedding, cosine_distance
            enrolled = load_enrolled_embeddings()
            if not enrolled:
                print("  [VOICE ID] No enrolled speakers found — skipping")
                return
            if not _os.path.exists(audio_path):
                print("  [VOICE ID] WAV file not found — skipping")
                return
            import soundfile as sf
            info = sf.info(audio_path)
            total_secs = info.duration
            if total_secs < 30:
                print(f"  [VOICE ID] Only {total_secs:.0f}s of audio — need 30s minimum")
                return
            # Find Rev AI speakers not yet confirmed
            unconfirmed = [rid for rid in seen_speaker_ids
                           if rid not in confirmed_speaker_ids]
            if not unconfirmed:
                print("  [VOICE ID] All speakers already confirmed — skipping")
                return
            print(f"  [VOICE ID] Running for Rev AI speakers: {unconfirmed} ({total_secs:.0f}s audio available)")
            VOICE_ID_THRESHOLD = 0.55
            id_results = {}
            for rev_idx in unconfirmed:
                # Use timestamps collected live in rev_speaker_timestamps
                # This is independent of DB speaker_id — avoids the misattribution problem
                ts_list = rev_speaker_timestamps.get(rev_idx, [])
                if not ts_list:
                    print(f"  [VOICE ID] No timestamps tracked for Rev AI {rev_idx} — skipping")
                    continue
                # Take up to 5 timestamps within available audio
                valid_ts = [ts for ts in ts_list if ts is not None and ts + 8 < total_secs]
                valid_ts = valid_ts[:5]
                if not valid_ts:
                    print(f"  [VOICE ID] No valid timestamps for Rev AI {rev_idx}")
                    continue
                embs = []
                for ts in valid_ts:
                    emb = extract_embedding(audio_path, start_sec=ts, end_sec=ts+8)
                    if emb is not None:
                        embs.append(emb)
                if not embs:
                    print(f"  [VOICE ID] Could not extract embedding for Rev AI {rev_idx}")
                    continue
                avg_emb = np.mean(embs, axis=0)
                avg_emb = avg_emb / np.linalg.norm(avg_emb)
                distances = {sid: cosine_distance(avg_emb, data["embedding"])
                             for sid, data in enrolled.items()}
                best_sid = min(distances, key=distances.get)
                best_dist = distances[best_sid]
                dist_str = ", ".join(f"spk{k}={v:.3f}" for k, v in distances.items())
                print(f"  [VOICE ID] Rev AI {rev_idx}: {dist_str}")
                if best_dist < VOICE_ID_THRESHOLD:
                    id_results[rev_idx] = best_sid
                    print(f"  [VOICE ID] CONFIRMED: Rev AI {rev_idx} = speaker_{best_sid} (dist={best_dist:.3f})")
                else:
                    print(f"  [VOICE ID] No confident match for Rev AI {rev_idx} (best={best_dist:.3f})")
            # Apply results — only map each DB speaker once (prevent duplicates)
            already_mapped = set(confirmed_speaker_ids.values())
            for rev_idx, db_sid in id_results.items():
                if db_sid not in already_mapped:
                    confirmed_speaker_ids[rev_idx] = db_sid
                    seen_speaker_ids[rev_idx] = db_sid
                    persist_mapping(rev_idx, db_sid)
                    already_mapped.add(db_sid)
                    print(f"  [VOICE ID] Mapped Rev AI {rev_idx} -> speaker_{db_sid} (persisted)")
                else:
                    print(f"  [VOICE ID] SKIP Rev AI {rev_idx} -> speaker_{db_sid} already mapped")
            if id_results:
                print(f"  [VOICE ID] Complete — {len(id_results)} speaker(s) identified")
            else:
                print("  [VOICE ID] Complete — no confident identifications")
        except Exception as _ve:
            import traceback
            print(f"  [VOICE ID] ERROR: {_ve}")
            traceback.print_exc()

    threading.Thread(target=run_voice_identification, daemon=True).start()

    calibration_start = [time.time()]
    CALIBRATION_SECS = 180  # 3 minutes
    calibration_done = [False]
    def is_calibrating():
        if calibration_done[0]:
            return False
        elapsed = time.time() - calibration_start[0]
        if elapsed > CALIBRATION_SECS:
            # Time's up — check if we got both speakers
            missing = [sid for sid in (speaker_order or [])
                       if sid not in confirmed_speaker_ids.values()]
            if missing:
                print(f"  [CALIBRATION] 3min elapsed. Missing speakers: {missing}")
                # Fall back to order-based for unconfirmed speakers
                unconfirmed_rev_ids = [rid for rid in seen_speaker_ids
                                        if rid not in confirmed_speaker_ids]
                for i, rid in enumerate(sorted(unconfirmed_rev_ids)):
                    for sid in missing:
                        if sid not in confirmed_speaker_ids.values():
                            confirmed_speaker_ids[rid] = sid
                            seen_speaker_ids[rid] = sid
                            print(f"  [CALIBRATION] Fallback: Rev AI {rid} = DB speaker {sid}")
                            break
            else:
                print(f"  [CALIBRATION] Complete — all speakers confirmed")
            calibration_done[0] = True
            return False
        return True

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
            # Dict format: {'variants': [...], 'whole_word_only': bool}
            # whole_word_only=True uses word-boundary regex to prevent substring collisions
            # Heuristic: variants <=4 chars OR collision-prone words get whole_word_only=True
            misspellings = {
                'turek':    {'variants': ['turk', 'terk', 'turek'], 'whole_word_only': False},
                'wahls':    {'variants': ['walz', 'walls', 'wals', 'wahls'], 'whole_word_only': False},
                'kirkmeyer':{'variants': ['kirkmeier', 'kirkmyer', 'kirkmeyer', 'kirk meyer'], 'whole_word_only': False},
                'bottoms':  {'variants': ['bottom', 'bottoms'], 'whole_word_only': False},
                'bennet':   {'variants': ['bennett', 'benet', 'bennet'], 'whole_word_only': False},
                'weiser':   {'variants': ['wiser', 'wyser', 'weiser'], 'whole_word_only': False},
                'marx':     {'variants': ['marx'], 'whole_word_only': True},   # 4 chars, whole-word prevents 'Marxist'/'Marxism'
                'marks':    {'variants': ['marks'], 'whole_word_only': True},  # 5 chars but collides with 'remarkable'/'marksman'
                'moderator':{'variants': ['moderator', 'the moderator'], 'whole_word_only': False},
            }
            whole_word_set = set()
            for correct, entry in misspellings.items():
                if isinstance(entry, list):
                    entry = {'variants': entry, 'whole_word_only': False}
                if correct in name_map:
                    for v in entry['variants']:
                        name_map[v] = name_map[correct]
                        if entry['whole_word_only']:
                            whole_word_set.add(v)
            cur.close(); conn.close()
            print(f"  Name detection active: {list(name_map.keys())}")
        except Exception as e:
            print(f"  [WARNING] Could not build name map: {e}")

    def detect_name_cue(text):
        import re
        tl = text.lower()
        best_match, best_len = None, 0
        for frag, sid in name_map.items():
            # Auto-fragmented names: require >=5 chars to prevent short-fragment collisions
            # Explicit misspellings dict entries: allow >=4 chars (deliberately curated)
            is_dict_entry = frag in whole_word_set or any(
                frag in entry['variants']
                for entry in misspellings.values()
                if isinstance(entry, dict)
            )
            min_len = 4 if is_dict_entry else 5
            if len(frag) < min_len or len(frag) <= best_len:
                continue
            if frag in whole_word_set:
                if re.search(r'\b' + re.escape(frag) + r'\b', tl, re.IGNORECASE):
                    best_match, best_len = sid, len(frag)
            else:
                if frag in tl:
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
            # When moderator says "Josh Turk, what would you..." the NEXT new
            # speaker ID belongs to that candidate.
            # Special case: if BOTH names appear in same utterance (bio section),
            # the last name mentioned is the one introduced next — skip it.
            # ADDRESS PATTERN: moderator-style address at utterance start
            # "Mr. Weiser, your response" → next speaker is Weiser, not current speaker
            # These patterns indicate the utterance is FROM the moderator TO a candidate
            import re as _re
            _address_patterns = [
                r'^(mr\.?|mrs\.?|ms\.?|senator|attorney general|general)\s+\w+',
                r'^thank you,?\s+\w+',
                r'^same question to you,?\s+(mr\.?|mrs\.?|senator)?\s*\w+',
                r'^(short answer|your response|your question),?\s+(mr\.?|senator)?\s*\w+',
            ]
            # Only treat as moderator address if current speaker IS the moderator
            # or is unmapped. If current speaker is a confirmed candidate,
            # "Mr. Weiser" is a cross-reference, not a handoff.
            _current_is_candidate = (
                rev_speaker_idx in confirmed_speaker_ids
                and confirmed_speaker_ids[rev_speaker_idx] != 3  # not moderator
            )
            _is_address = (
                not _current_is_candidate
                and any(_re.match(p, text.lower()) for p in _address_patterns)
            )

            detected = detect_name_cue(text)
            if detected is not None:
                tl_check = text.lower()
                # Dynamically check if multiple candidate names are present
                # Uses name_map built from actual event speakers (not hardcoded)
                non_mod_speakers = {sid for sid in set(name_map.values()) if sid != 3}
                speakers_mentioned = set()
                for frag, sid in name_map.items():
                    if sid in non_mod_speakers and frag in tl_check:
                        speakers_mentioned.add(sid)
                both_present = len(speakers_mentioned) >= 2
                if both_present and not is_calibrating():
                    print(f"  [NAME CUE] skipped (both names in utterance): {text[:60]}")
                elif _is_address:
                    # Moderator is addressing a candidate — set pending for NEXT speaker
                    # but do NOT lock current utterance to that candidate
                    pending_speaker_id[0] = detected
                    print(f"  [NAME CUE] address→pending={detected}: {text[:60]}")
                else:
                    # During calibration, use first name mentioned
                    if both_present and is_calibrating():
                        print(f"  [CALIBRATION] Both names present — using first match: {detected}")
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
                persist_mapping(rev_speaker_idx, speaker_id)
            elif rev_speaker_idx not in seen_speaker_ids:
                # Try order-based mapping first if speaker_order provided
                if speaker_order and rev_speaker_idx < len(speaker_order):
                    mapped = speaker_order[rev_speaker_idx]
                    seen_speaker_ids[rev_speaker_idx] = mapped
                    confirmed_speaker_ids[rev_speaker_idx] = mapped
                    print(f"  [ORDER MAP] Rev AI {rev_speaker_idx} = DB speaker {mapped}")
                    persist_mapping(rev_speaker_idx, mapped)
                elif confirmed_speaker_ids:
                    last_known = list(confirmed_speaker_ids.values())[-1]
                    seen_speaker_ids[rev_speaker_idx] = last_known
                    print(f"  [LAST_KNOWN] Rev AI {rev_speaker_idx} → speaker_id={last_known} (uncertain — flagged for review)")
                else:
                    seen_speaker_ids[rev_speaker_idx] = None  # unconfirmed — wait for name cue
                speaker_id = seen_speaker_ids[rev_speaker_idx]
            else:
                speaker_id = seen_speaker_ids.get(rev_speaker_idx)

            # Grab timestamp from first text element (seconds from stream start)
            ts_seconds = next(
                (int(e['ts']) for e in elements if e.get('type') == 'text' and e.get('ts') is not None),
                None
            )

            # Track timestamps per Rev AI speaker index for voice re-identification
            # Independent of DB speaker_id — avoids misattribution contamination
            if ts_seconds is not None and rev_speaker_idx is not None:
                if rev_speaker_idx not in rev_speaker_timestamps:
                    rev_speaker_timestamps[rev_speaker_idx] = []
                if len(rev_speaker_timestamps[rev_speaker_idx]) < 10:
                    rev_speaker_timestamps[rev_speaker_idx].append(ts_seconds)

            # Compute mean word-level confidence for this segment
            # Rev AI returns confidence per text element (0.0–1.0)
            conf_scores = [
                float(e['confidence']) for e in elements
                if e.get('type') == 'text' and e.get('confidence') is not None
            ]
            mean_confidence = (sum(conf_scores) / len(conf_scores)) if conf_scores else None

            # MODERATOR GATE: force speaker_id=3 for utterances matching moderator patterns
            # Short utterances with transition phrases are almost always the moderator
            _mod_patterns = [
                r'^thank you',
                r'^(mr\.?|mrs\.?|senator|attorney general)\s+\w+[,\.]',
                r'^(let\'s|let us)\s+(turn|move|go)',
                r'^(short answer|your response|your question)',
                r'^same question',
                r'^(we\'ll|we will)\s+(continue|move|take)',
                r'^(and\s+)?(finally|lastly|one more)',
            ]
            if (speaker_id != 3 and
                len(text.split()) <= 20 and
                any(_re.match(p, text.lower()) for p in _mod_patterns)):
                speaker_id = 3
                print(f"  [MOD GATE] Forced to moderator: {text[:60]}")

            # Determine if speaker resolution used an uncertain path
            _used_fallback = (
                rev_speaker_idx in seen_speaker_ids
                and rev_speaker_idx not in confirmed_speaker_ids
            )

            uid = write_utterance(
                event_id, speaker_id, text,
                utterance_order[0], args.dry_run,
                timestamp_seconds=ts_seconds,
                attribution_confidence=mean_confidence,
                force_uncertain=_used_fallback,
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

    # Get audio stream URL via yt_dlp Python library (handles JS challenge on Railway)
    print("Resolving stream URL...")
    from stream_utils import resolve_stream_url, PreLiveError
    try:
        stream_url = resolve_stream_url(args.url)
        print(f"  Stream URL resolved ✓")
    except PreLiveError as e:
        print(f"ERROR: {e}")
        sys.exit(2)  # exit code 2 = pre-live (not a real failure)
    except Exception as e:
        print(f"ERROR: yt_dlp resolution failed: {e}")
        sys.exit(1)

    # Configure Rev AI streaming
    config = MediaConfig(
        content_type='audio/x-raw',
        layout='interleaved',
        rate=16000,
        audio_format='S16LE',
        channels=1,
    )

    client = RevAiStreamingClient(token, config)

    # Save debate audio to disk for post-debate voice verification
    audio_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debate_audio')
    os.makedirs(audio_dir, exist_ok=True)
    audio_filename = f'event_{event_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.wav'
    audio_path = os.path.join(audio_dir, audio_filename)
    print(f"  Audio will be saved to: {audio_path}")

    # Start ffmpeg to pipe audio to stdin AND save to file
    # Output 1: pipe for Rev AI (raw PCM s16le)
    # Output 2: wav file on disk for post-debate verification
    ffmpeg_cmd = [
        'ffmpeg', '-i', stream_url,
        '-ar', '16000', '-ac', '1',
        '-f', 's16le', '-acodec', 'pcm_s16le',
        'pipe:1',
        '-ar', '16000', '-ac', '1',
        audio_path,
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

        vocab_id = args.vocabulary_id if args.vocabulary_id else None
        if vocab_id:
            print(f"  Using custom vocabulary: {vocab_id}")
        response_gen = client.start(audio_generator(), custom_vocabulary_id=vocab_id)
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
    parser.add_argument('--vocabulary-id', default='',
                        help='Rev AI custom vocabulary ID to use for this stream')
    args = parser.parse_args()

    load_env()
    token = os.environ.get('REV_AI_TOKEN')
    if not token:
        print("ERROR: REV_AI_TOKEN not set in .env")
        sys.exit(1)

    anthropic_key = os.environ.get('ANTHROPIC_API_KEY')
    if not anthropic_key:
        print("WARNING: ANTHROPIC_API_KEY not set -- claims will not be extracted or verified")
    else:
        print(f"ANTHROPIC_API_KEY: ...{anthropic_key[-4:]}")
    print(f"REV_AI_TOKEN: ...{token[-4:]}")
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


    # Auto-load speaker_order from DB if not provided via CLI
    # Ensures bare WSL fallback launches get the correct speaker roster
    if not speaker_order:
        try:
            _so_conn = get_db_conn()
            _so_cur = _so_conn.cursor()
            _so_cur.execute(
                "SELECT speaker_id FROM event_speakers "
                "WHERE event_id = %s AND is_active = TRUE "
                "ORDER BY speaker_order",
                (event_id,)
            )
            speaker_order = [r[0] for r in _so_cur.fetchall()]
            _so_cur.close()
            _so_conn.close()
            if speaker_order:
                print(f"Speaker order (auto-loaded from DB): {speaker_order}")
            else:
                print("WARNING: No active speakers found in event_speakers")
        except Exception as _soe:
            print(f"WARNING: Could not auto-load speaker_order: {_soe}")

    # Load per-event attribution confidence threshold from DB
    # Overrides module-level ATTRIBUTION_CONFIDENCE_THRESHOLD constant
    # 2-speaker debates use 0.75, 4+ speaker debates use 0.60 (default)
    try:
        import debate_stream as _ds
        _tc = get_db_conn()
        _tcu = _tc.cursor()
        _tcu.execute(
            "SELECT attribution_confidence_threshold FROM events WHERE id = %s",
            (event_id,)
        )
        _trow = _tcu.fetchone()
        if _trow and _trow[0] is not None:
            _ds.ATTRIBUTION_CONFIDENCE_THRESHOLD = float(_trow[0])
            print(f"Attribution confidence threshold: {_ds.ATTRIBUTION_CONFIDENCE_THRESHOLD} (from DB)")
        _tcu.close(); _tc.close()
    except Exception as _te:
        print(f"WARNING: Could not load threshold from DB: {_te}")

    if args.mode == 'async':
        run_async(args, token, speaker_map, speaker_order, event_id)
    else:
        run_live(args, token, speaker_map, speaker_order, event_id)

if __name__ == '__main__':
    main()
