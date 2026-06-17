#!/usr/bin/env python3
"""
prepare_debate.py — Verum Signal pre-debate validation and launch script.

Usage:
    python3 prepare_debate.py --event-id 9
    python3 prepare_debate.py --event-id 9 --dry-run

Performs:
1. Validates event exists with correct configuration
2. Checks API health (Anthropic + Rev AI)
3. Cleans up stale processes
4. Flips is_public=TRUE at T-45 (or immediately if already past T-45)
5. Confirms stream detects and launches within 5 minutes

Day-of usage: Run at T-90 minutes (90 min before scheduled start_time).
The script will wait until T-45 to flip is_public=TRUE.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone as tz

# ── DB connection ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from verdict_engine import get_connection

GENERIC_MODERATOR_ID = 3  # Reserved ID — see extract_debate_claims.py

TZ_OFFSETS = {
    'ET': -4, 'EST': -5, 'EDT': -4,
    'CT': -5, 'CST': -6, 'CDT': -5,
    'MT': -6, 'MST': -7, 'MDT': -6,
    'PT': -7, 'PST': -8, 'PDT': -7,
}


def log(msg, level='INFO'):
    ts = datetime.now(tz.utc).strftime('%H:%M:%S')
    prefix = {'INFO': '  ', 'PASS': '✓ ', 'WARN': '⚠ ', 'FAIL': '✗ ', 'HEAD': ''}
    print(f"[{ts}] {prefix.get(level, '')}{msg}", flush=True)


def check(label, passed, detail='', fatal=False):
    if passed:
        log(f"PASS  {label}", 'PASS')
    else:
        log(f"FAIL  {label}{(' — ' + detail) if detail else ''}", 'FAIL')
        if fatal:
            log("Aborting — fatal check failed.", 'FAIL')
            sys.exit(1)
    return passed


# ── Step 1: Validate event ───────────────────────────────────────────────────
def validate_event(event_id, dry_run=False):
    log("=" * 60, 'HEAD')
    log(f"Step 1 — Validating event {event_id}", 'HEAD')
    log("=" * 60, 'HEAD')

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, event_name, event_date, start_time, timezone,
               is_public, stream_url, search_query, methodology_version
        FROM events WHERE id = %s
    """, (event_id,))
    row = cur.fetchone()

    check("Event exists in DB", row is not None,
          f"No event found with id={event_id}", fatal=True)

    (eid, name, event_date, start_time, event_tz,
     is_public, stream_url, search_query, method_ver) = row

    log(f"  Event: {name}")
    log(f"  Date:  {event_date}  Start: {start_time} {event_tz}")
    log(f"  is_public: {is_public}  methodology: {method_ver}")

    check("start_time is set", start_time is not None,
          "start_time is NULL — set it before running this script", fatal=True)

    # Compute UTC start time
    offset = TZ_OFFSETS.get(event_tz or 'CT', -5)
    event_tz_obj = tz(timedelta(hours=offset))
    event_start_local = datetime.combine(event_date, start_time).replace(tzinfo=event_tz_obj)
    now_utc = datetime.now(tz.utc)
    minutes_until = (event_start_local - now_utc).total_seconds() / 60

    check("Event is within 24 hours", abs(minutes_until) <= 1440,
          f"Event is {minutes_until:.0f} minutes away")
    log(f"  Event starts in {minutes_until:.0f} minutes ({event_start_local.strftime('%Y-%m-%d %H:%M %Z')})")

    if is_public:
        log("  ⚠ is_public is already TRUE — confirm this is intentional", 'WARN')
    else:
        log("  is_public=FALSE (correct pre-launch state)")

    check("stream_url or search_query is set",
          bool(stream_url or search_query),
          "Neither stream_url nor search_query is set — stream discovery will fail")

    if stream_url:
        log(f"  stream_url: {stream_url[:80]}")
    else:
        log(f"  ⚠ No stream_url — will fall back to yt-dlp search: {search_query}", 'WARN')

    # Check speakers
    cur.execute("""
        SELECT s.id, s.name, s.speaker_type, es.speaker_order
        FROM event_speakers es
        JOIN speakers s ON s.id = es.speaker_id
        WHERE es.event_id = %s
          AND es.is_active = TRUE
        ORDER BY es.speaker_order
    """, (event_id,))
    speakers = cur.fetchall()

    log(f"  Speakers ({len(speakers)}):")
    for sid, sname, stype, sorder in speakers:
        log(f"    order={sorder} id={sid} {sname} ({stype})")

    has_moderator = any(s[2] == 'moderator' for s in speakers)
    has_candidates = sum(1 for s in speakers if s[2] in ('politician', 'official')) >= 2

    check("Generic moderator at speaker_order=0", has_moderator,
          "No moderator in event_speakers — add GENERIC_MODERATOR_ID=3")
    check("At least 2 candidates configured", has_candidates,
          f"Only {sum(1 for s in speakers if s[2] in ('politician', 'official'))} candidate(s) found")

    cur.close()
    conn.close()
    return event_start_local, minutes_until, is_public


# ── Step 2: API health checks ────────────────────────────────────────────────
def check_apis():
    log("=" * 60, 'HEAD')
    log("Step 2 — API health checks", 'HEAD')
    log("=" * 60, 'HEAD')

    # Anthropic
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'), timeout=10.0)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}]
        )
        check("Anthropic API responding", True)
    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            check("Anthropic API responding", False,
                  "529 Overloaded — API under load, may affect surge verification")
        else:
            check("Anthropic API responding", False, str(e))
    except Exception as e:
        check("Anthropic API responding", False, str(e))

    # Check credit balance hint (can't query directly but check env)
    api_key = os.getenv('ANTHROPIC_API_KEY', '')
    check("ANTHROPIC_API_KEY is set", bool(api_key),
          "ANTHROPIC_API_KEY not in environment")

    # Rev AI
    rev_token = os.getenv('REV_AI_TOKEN', '')
    check("REV_AI_TOKEN is set", bool(rev_token),
          "REV_AI_TOKEN not in environment — stream will fail")

    if rev_token:
        try:
            import urllib.request
            req = urllib.request.Request(
                'https://api.rev.ai/speechtotext/v1/jobs?limit=1',
                headers={'Authorization': f'Bearer {rev_token}'}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                check("Rev AI API responding", resp.status == 200)
        except Exception as e:
            check("Rev AI API responding", False, str(e))


# ── Step 3: Pre-resolve stream URL ────────────────────────────────────────
def preresolve_stream(event_id, dry_run=False):
    log("=" * 60, 'HEAD')
    log("Step 3 — Pre-resolve stream URL", 'HEAD')
    log("=" * 60, 'HEAD')

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT stream_url FROM events WHERE id = %s", (event_id,))
    row = cur.fetchone()
    stream_url = row[0] if row else None
    cur.close()
    conn.close()

    if not stream_url:
        log("  No stream_url set — skipping pre-resolve", 'WARN')
        return

    is_video = 'watch?v=' in stream_url or 'youtu.be/' in stream_url
    if not is_video:
        log(f"  Channel/page URL — cannot pre-resolve: {stream_url}", 'WARN')
        return

    try:
        from stream_utils import resolve_stream_url, PreLiveError
        try:
            resolved = resolve_stream_url(stream_url)
            log(f"  Resolved: {resolved[:80]}...")
            if not dry_run:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE events SET stream_url_resolved = %s WHERE id = %s",
                    (resolved, event_id))
                conn.commit()
                cur.close()
                conn.close()
                log("  Cached resolved URL in DB ✓", 'PASS')
            else:
                log("  [DRY RUN] Would cache resolved URL", 'PASS')
        except PreLiveError:
            log("  Pre-live (expected at T-90) — will resolve at stream start", 'PASS')
        except Exception as e:
            log(f"  Resolution failed: {e} — will retry at stream start", 'WARN')
    except ImportError as e:
        log(f"  stream_utils not available: {e}", 'WARN')


# ── Step 4: Generate speaker context if missing ──────────────────────────
def ensure_speaker_context(event_id, dry_run=False):
    log("=" * 60, 'HEAD')
    log("Step 4 — Speaker context (attribution safety)", 'HEAD')
    log("=" * 60, 'HEAD')

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM speaker_event_context WHERE event_id = %s", (event_id,))
    count = cur.fetchone()[0]
    cur.close()
    conn.close()

    if count > 0:
        log(f"  Speaker context exists ({count} speaker(s)) ✓", 'PASS')
        return

    log("  No speaker context found — generating via LLM...")
    if dry_run:
        log("  [DRY RUN] Would run: python3 generate_speaker_context.py --event-id " + str(event_id))
        return

    try:
        result = subprocess.run(
            [sys.executable, 'generate_speaker_context.py', '--event-id', str(event_id)],
            capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            log("  Speaker context generated ✓", 'PASS')
            print(result.stdout)
        else:
            log(f"  Generation failed: {result.stderr[:200]}", 'WARN')
    except Exception as e:
        log(f"  Could not generate: {e}", 'WARN')


# ── Step 5: Process cleanup ──────────────────────────────────────────────────
def cleanup_processes(dry_run=False):
    log("=" * 60, 'HEAD')
    log("Step 5 — Process cleanup", 'HEAD')
    log("=" * 60, 'HEAD')

    targets = ['debate_stream.py', 'extract_debate_claims.py']
    for target in targets:
        result = subprocess.run(
            ['pgrep', '-f', target],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split('\n') if result.stdout.strip() else []
        pids = [p for p in pids if p]

        if pids:
            if dry_run:
                log(f"  [DRY RUN] Would kill {target} pids: {pids}", 'WARN')
            else:
                for pid in pids:
                    try:
                        subprocess.run(['kill', pid], check=True)
                        log(f"  Killed {target} pid={pid}", 'WARN')
                    except Exception as e:
                        log(f"  Could not kill {target} pid={pid}: {e}", 'WARN')
        else:
            log(f"  No stale {target} processes")


# ── Step 4: Flip is_public at T-45 ──────────────────────────────────────────
def flip_is_public(event_id, minutes_until, current_is_public, dry_run=False):
    log("=" * 60, 'HEAD')
    log("Step 6 — is_public flip at T-45", 'HEAD')
    log("=" * 60, 'HEAD')

    T_MINUS_45 = 45  # minutes before start_time

    if current_is_public:
        log("  is_public already TRUE — skipping flip")
        return

    if minutes_until > T_MINUS_45:
        wait_seconds = (minutes_until - T_MINUS_45) * 60
        log(f"  Waiting {wait_seconds:.0f}s until T-45 ({T_MINUS_45} min before start)...")
        if not dry_run:
            time.sleep(wait_seconds)

    if dry_run:
        log("  [DRY RUN] Would flip is_public=TRUE now")
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE events SET is_public = TRUE WHERE id = %s", (event_id,))
    conn.commit()
    cur.close()
    conn.close()
    log("  Flipped is_public=TRUE ✓", 'PASS')


# ── Step 5: Confirm stream starts ────────────────────────────────────────────
def verify_stream_starts(event_id, dry_run=False):
    log("=" * 60, 'HEAD')
    log("Step 7 — Confirming stream starts", 'HEAD')
    log("=" * 60, 'HEAD')

    if dry_run:
        log("  [DRY RUN] Skipping stream verification")
        return

    log("  Waiting up to 5 minutes for first utterance...")
    for attempt in range(30):  # 30 x 10s = 5 minutes
        time.sleep(10)
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*), MAX(created_at)
            FROM speaker_utterances
            WHERE event_id = %s
              AND created_at > NOW() - INTERVAL '10 minutes'
        """, (event_id,))
        count, latest = cur.fetchone()
        cur.close()
        conn.close()

        if count > 0:
            log(f"  ✓ Stream active — {count} utterance(s) in last 10 min (latest: {latest})", 'PASS')
            return

        log(f"  Waiting... ({(attempt+1)*10}s elapsed, 0 utterances so far)")

    log("  ✗ No utterances after 5 minutes — stream may not have started", 'FAIL')
    log("  Manual check: SELECT COUNT(*) FROM speaker_utterances WHERE event_id = " + str(event_id))


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Verum Signal pre-debate preparation script')
    parser.add_argument('--event-id', type=int, required=True, help='DB event ID')
    parser.add_argument('--dry-run', action='store_true', help='Validate only, no changes')
    args = parser.parse_args()

    log("=" * 60, 'HEAD')
    log(f"Verum Signal — Pre-debate preparation", 'HEAD')
    log(f"Event ID: {args.event_id}  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}", 'HEAD')
    log(f"Reminder: is_public flips at T-45 (45 min before start_time)", 'HEAD')
    log("=" * 60, 'HEAD')

    event_start, minutes_until, is_public = validate_event(args.event_id, args.dry_run)
    check_apis()
    preresolve_stream(args.event_id, args.dry_run)
    ensure_speaker_context(args.event_id, args.dry_run)
    cleanup_processes(args.dry_run)
    flip_is_public(args.event_id, minutes_until, is_public, args.dry_run)
    verify_stream_starts(args.event_id, args.dry_run)

    log("=" * 60, 'HEAD')
    log("Pre-debate preparation complete.", 'HEAD')
    log("Monitor: watch -n 30 'psql $DATABASE_URL -c \"SELECT COUNT(*) FROM speaker_utterances WHERE event_id=" + str(args.event_id) + "\"'", 'HEAD')
    log("=" * 60, 'HEAD')


if __name__ == '__main__':
    main()
