#!/usr/bin/env python3
"""
pre_debate_check.py — Verum Signal pre-debate dry-run checklist.
Run 24 hours before each scheduled debate. Exits non-zero if any critical check fails.

Usage:
    python3 pre_debate_check.py
    python3 pre_debate_check.py --event-slug colorado-governor-republican-primary-debate-round-3
"""
import os, sys, argparse, subprocess
from datetime import datetime, timezone, timedelta

if os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv(override=False)

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"

results = []

def check(label, passed, detail='', critical=True):
    tag = PASS if passed else (FAIL if critical else WARN)
    print(f"  {tag} {label}" + (f" — {detail}" if detail else ""))
    results.append((label, passed, critical))

def section(title):
    print(f"\n{'─'*50}\n  {title}\n{'─'*50}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-slug', default=None)
    args = parser.parse_args()

    print(f"\nVerum Signal — Pre-debate checklist")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── 1. DB connectivity ──────────────────────────────────────────────────
    section("1. Database")
    try:
        from verdict_engine import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events")
        count = cur.fetchone()[0]
        check("DB connection", True, f"{count} events in DB")
    except Exception as e:
        check("DB connection", False, str(e))
        conn = None

    # ── 2. Upcoming debate event ────────────────────────────────────────────
    section("2. Debate event")
    event = None
    if conn:
        try:
            if args.event_slug:
                cur.execute("""
                    SELECT id, slug, event_name, event_date, start_time, timezone, stream_url, is_public
                    FROM events WHERE slug = %s
                """, (args.event_slug,))
            else:
                cur.execute("""
                    SELECT id, slug, event_name, event_date, start_time, timezone, stream_url, is_public
                    FROM events
                    WHERE event_date >= CURRENT_DATE
                    ORDER BY event_date ASC LIMIT 1
                """)
            event = cur.fetchone()
            if event:
                eid, slug, name, edate, stime, tz, stream_url, is_public = event
                check("Next event found", True, f"{name} ({edate})")
                check("Event is public", bool(is_public), f"is_public={is_public}")
                check("Stream URL set", bool(stream_url), stream_url or "MISSING", critical=False)
            else:
                check("Next event found", False, "No upcoming events in DB")
        except Exception as e:
            check("Event query", False, str(e))

    # ── 3. Event speakers ───────────────────────────────────────────────────
    section("3. Speakers")
    if conn and event:
        try:
            cur.execute("""
                SELECT s.name, es.is_active FROM event_speakers es
                JOIN speakers s ON s.id = es.speaker_id
                WHERE es.event_id = %s
            """, (event[0],))
            speakers = cur.fetchall()
            active = [s for s in speakers if s[1]]
            check("Speakers seeded", len(speakers) > 0, f"{len(speakers)} total, {len(active)} active")
            check("At least 2 active speakers", len(active) >= 2, f"{len(active)} active")
        except Exception as e:
            check("Speaker query", False, str(e))

    # ── 4. _derive_status check ─────────────────────────────────────────────
    section("4. Status derivation")
    if conn and event:
        try:
            from debate_routes import _derive_status
            from datetime import date, time as dtime
            eid, slug, name, edate, stime, tz, stream_url, is_public = event
            status = _derive_status(edate, None, stime, tz)
            check("_derive_status returns 'upcoming'", status == 'upcoming', f"got '{status}'")
        except Exception as e:
            check("_derive_status", False, str(e))

    # ── 5. yt_dlp Python library ────────────────────────────────────────────
    section("5. Stream resolution")
    try:
        import yt_dlp
        check("yt_dlp library importable", True, f"version {yt_dlp.version.__version__}")
    except Exception as e:
        check("yt_dlp library importable", False, str(e))

    if conn and event and event[6]:  # stream_url
        surl = event[6]
        # Only attempt resolution on specific video URLs — channel/playlist URLs hang
        is_video_url = 'watch?v=' in surl or 'youtu.be/' in surl
        if is_video_url:
            try:
                from stream_utils import resolve_stream_url, PreLiveError
                try:
                    url = resolve_stream_url(surl)
                    check("YouTube URL resolves", True, url[:60] + '...')
                except PreLiveError as e:
                    check("YouTube URL resolves", True, f"Pre-live (expected): {e}", critical=False)
                except Exception as e:
                    check("YouTube URL resolves", False, str(e))
            except Exception as e:
                check("stream_utils import", False, str(e))
        else:
            check("YouTube URL resolves", True, f"Channel URL set (specific video needed closer to debate): {surl}", critical=False)
    else:
        check("YouTube URL resolves", False, "No stream_url set — skip or add one", critical=False)

    # ── 6. Scheduler / ingestion health ────────────────────────────────────
    section("6. Ingestion pipeline")
    if conn:
        try:
            cur.execute("""
                SELECT started_at FROM job_runs
                WHERE stage = 'verdicts' AND status = 'ok'
                ORDER BY started_at DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                age = (datetime.utcnow() - row[0].replace(tzinfo=None)).total_seconds()
                check("Last verdict job < 2 hours ago", age < 7200, f"{int(age/60)} min ago")
            else:
                check("Last verdict job found", False, "No successful verdict jobs found")
        except Exception as e:
            check("Job runs query", False, str(e))

        try:
            cur.execute("SELECT MAX(published_at) FROM articles WHERE published_at IS NOT NULL")
            row = cur.fetchone()
            if row and row[0]:
                age = (datetime.utcnow() - row[0].replace(tzinfo=None)).total_seconds() / 3600
                check("Most recent article < 2 hours old", age < 2, f"{age:.1f}h ago")
            else:
                check("Recent articles found", False, "No articles with published_at")
        except Exception as e:
            check("Article freshness", False, str(e))

    # ── 7. Anthropic API ────────────────────────────────────────────────────
    section("7. Anthropic API")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        # Minimal test call
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply OK"}]
        )
        check("Anthropic API reachable", True, f"response: {resp.content[0].text.strip()}")
    except Exception as e:
        check("Anthropic API reachable", False, str(e))

    # ── 8. veris-stream health ──────────────────────────────────────────────
    section("8. veris-stream")
    try:
        import urllib.request, json as _json
        ops_user = os.getenv('OPS_USERNAME', '')
        ops_pass = os.getenv('OPS_PASSWORD', '')
        if not ops_user or not ops_pass:
            check("Stream health endpoint", False, "OPS_USERNAME/OPS_PASSWORD not set in env", critical=False)
            raise Exception('skip')
        import base64
        token = base64.b64encode(f"{ops_user}:{ops_pass}".encode()).decode()
        req = urllib.request.Request(
            'https://verumsignal.com/api/ops/stream-health',
            headers={'Authorization': f'Basic {token}'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        status = data.get('status', 'unknown')
        stale = data.get('stale', True)
        age = data.get('age_seconds', 9999)
        check("Stream service heartbeat fresh", not stale, f"status={status}, {int(age/60)}m ago")
    except Exception as e:
        check("Stream health endpoint", False, str(e), critical=False)

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    critical_fails = [r for r in results if not r[1] and r[2]]
    warnings = [r for r in results if not r[1] and not r[2]]
    passed = [r for r in results if r[1]]
    print(f"  {len(passed)} passed  |  {len(warnings)} warnings  |  {len(critical_fails)} critical failures")
    if critical_fails:
        print(f"\n  CRITICAL FAILURES — resolve before debate:")
        for label, _, _ in critical_fails:
            print(f"    ✗ {label}")
        print()
        sys.exit(1)
    else:
        print(f"\n  All critical checks passed. Ready for debate.\n")
        sys.exit(0)

if __name__ == '__main__':
    main()
