#!/usr/bin/env python3
"""
replay_debate.py — Verum Signal post-event replay backfill.

Runs debate_stream.py --mode async against a full archived debate URL to
capture any utterances missed during live coverage (e.g. if stream started
late, crashed, or missed the first N minutes).

The utterance dedup constraint (ON CONFLICT DO NOTHING on speaker_utterances)
ensures already-processed utterances are silently skipped. Only new content
is added.

Usage:
    python3 replay_debate.py --event-id 9 --url "https://youtube.com/watch?v=..."
    python3 replay_debate.py --event-id 9 --url "..." --dry-run

Run this in the hour after the debate ends. Allow 10-20 minutes for Rev AI
async transcription to complete.

SOURCE QUALITY GUIDE (best to worst):
  1. Clean Elections YouTube (AZ debates) — single source, no ads, preferred
  2. C-SPAN recordings — clean, no commercials, reliable
  3. Network YouTube streams (KTAR, WOOD TV8) — usually clean
  4. Local TV broadcast recordings (PIX11, WBTV) — may include ads,
     post-debate interviews, and sponsor segments. Use --cutoff-time
     to trim at debate end, and expect attribution noise from ORDER MAP.
  AVOID: DVR recordings, cable captures with commercials embedded.


import argparse
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from verdict_engine import get_connection


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def get_event_info(event_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.slug,
               string_agg(es.speaker_id::text, ',' ORDER BY es.speaker_order) AS speaker_order,
               string_agg(s.name || ':' || es.speaker_id::text, ',' ORDER BY es.speaker_order) AS speaker_map
        FROM events e
        LEFT JOIN event_speakers es ON es.event_id = e.id
        LEFT JOIN speakers s ON s.id = es.speaker_id
        WHERE e.id = %s
        GROUP BY e.id
    """, (event_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row  # (slug, speaker_order_str, speaker_map_str)


def get_pre_replay_counts(event_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM speaker_utterances WHERE event_id = %s", (event_id,))
    utterances = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM claims WHERE event_id = %s AND claim_origin = 'debate_claim'", (event_id,))
    claims = cur.fetchone()[0]
    cur.close()
    conn.close()
    return utterances, claims


def main():
    parser = argparse.ArgumentParser(description='Post-event debate replay backfill')
    parser.add_argument('--event-id', type=int, required=True, help='DB event ID')
    parser.add_argument('--url', required=True, help='YouTube or archived stream URL')
    parser.add_argument('--dry-run', action='store_true', help='Submit to Rev AI but do not write to DB')
    parser.add_argument('--cutoff-time', default='', help='Trim audio at this timestamp before submitting (HH:MM:SS or seconds). Use to exclude post-debate interviews/ads from TV broadcast replays.')
    args = parser.parse_args()

    log("=" * 60)
    log(f"Verum Signal — Post-event replay backfill")
    log(f"Event ID: {args.event_id}  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    log(f"URL: {args.url[:80]}")
    log("=" * 60)

    # Get event info
    row = get_event_info(args.event_id)
    if not row:
        log(f"ERROR: Event {args.event_id} not found in DB")
        sys.exit(1)

    slug, speaker_order, speaker_map = row
    log(f"Event slug: {slug}")
    log(f"Speakers: {speaker_map}")

    # Baseline counts before replay
    pre_utterances, pre_claims = get_pre_replay_counts(args.event_id)
    log(f"Pre-replay: {pre_utterances} utterances, {pre_claims} debate claims in DB")
    log("")

    # Build debate_stream.py command
    python = sys.executable
    script = os.path.join(os.path.dirname(__file__), 'debate_stream.py')
    cmd = [
        python, '-u', script,
        '--mode', 'async',
        '--url', args.url,
        '--event-slug', slug,
    ]
    if speaker_map:
        cmd += ['--speakers', speaker_map.upper()]
    if speaker_order:
        cmd += ['--speaker-order', speaker_order]
    if args.dry_run:
        cmd += ['--dry-run']
    if args.cutoff_time:
        cmd += ['--cutoff-time', args.cutoff_time]
        log(f"Audio cutoff: {args.cutoff_time} (post-debate content excluded)")

    log(f"Running: {' '.join(cmd[:6])} ...")
    log("Rev AI async transcription typically takes 5-15 minutes.")
    log("Utterances already in DB will be silently skipped (dedup constraint).")
    log("")

    result = subprocess.run(cmd)

    if result.returncode != 0:
        log(f"ERROR: debate_stream.py exited with code {result.returncode}")
        sys.exit(1)

    if args.dry_run:
        log("Dry run complete — no DB writes made.")
        return

    # Post-replay counts
    post_utterances, post_claims = get_pre_replay_counts(args.event_id)
    new_utterances = post_utterances - pre_utterances
    new_claims = post_claims - pre_claims

    log("")
    log("=" * 60)
    log("Replay complete.")
    log(f"  Utterances added: {new_utterances} (skipped {post_utterances - pre_utterances - new_utterances} duplicates)")
    log(f"  Debate claims added: {new_claims}")
    log(f"  Total utterances now: {post_utterances}")
    log(f"  Total debate claims now: {post_claims}")
    if new_utterances > 0:
        log(f"  New content queued for extraction and verification.")
        # Fix 1: set attribution_confidence=1.0 on replay utterances
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(__file__))
            from verdict_engine import get_connection as _gc
            _rconn = _gc()
            _rcur = _rconn.cursor()
            _rcur.execute(
                "UPDATE speaker_utterances SET attribution_confidence = 1.0 WHERE event_id = %s AND attribution_confidence IS NULL",
                (args.event_id,)
            )
            _updated = _rcur.rowcount
            _rconn.commit()
            _rcur.close()
            _rconn.close()
            log(f"  Replay confidence: set attribution_confidence=1.0 on {_updated} utterances")
        except Exception as _ce:
            log(f"  WARNING: Could not set replay confidence scores: {_ce}")
        # Fix 2: auto cold reattribution after replay
        log("")
        log("Running cold LLM reattribution pass...")
        _reattr_script = os.path.join(os.path.dirname(__file__), "reattribute_llm.py")
        _reattr_result = subprocess.run(
            [sys.executable, "-u", _reattr_script,
             "--event-id", str(args.event_id), "--cold", "--apply"],
            capture_output=False
        )
        if _reattr_result.returncode != 0:
            log("  WARNING: reattribute_llm.py exited with errors")
        else:
            log("  Cold reattribution complete.")
    else:
        log(f"  No new content — replay matched existing coverage.")
    log("=" * 60)


if __name__ == '__main__':
    main()
