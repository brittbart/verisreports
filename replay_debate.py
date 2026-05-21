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
"""

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
    else:
        log(f"  No new content — replay matched existing coverage.")
    log("=" * 60)


if __name__ == '__main__':
    main()
