#!/usr/bin/env python3
"""
railway_stream.py — Verum Signal v1.7 auto stream launcher

Runs as a Railway cron every 5 minutes.
- Checks for any upcoming event starting within 15 minutes
- Searches YouTube for the livestream using the event's search_query
- Launches debate_stream.py in live mode
- Exits when the event date passes (Railway cron handles re-running)

Also handles post-debate async ingestion:
- After event_date passes, submits the recorded video to Rev AI async
- Populates any missing utterances from the full recording

USAGE (manual test):
  python3 railway_stream.py --check      # just check what would fire
  python3 railway_stream.py              # normal run
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Env loading
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
# DB helpers
# ---------------------------------------------------------------------------
def get_db_conn():
    from api import get_db
    return get_db()

def get_upcoming_events(window_minutes=15):
    """
    Return events that start within window_minutes from now.
    Looks at event_date + start_time combined.
    """
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, slug, event_name, event_date, start_time, timezone,
                   search_query, stream_url
            FROM events
            WHERE is_public = TRUE
              AND event_date = CURRENT_DATE
              AND start_time IS NOT NULL
        """)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()

def get_speaker_order(event_id):
    """Return ordered speaker IDs for this event."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT speaker_id FROM event_speakers
            WHERE event_id = %s
            ORDER BY speaker_order
        """, (event_id,))
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]
    finally:
        conn.close()

def mark_stream_started(event_id):
    """Record that we started streaming this event."""
    conn = get_db_conn()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            UPDATE events SET notes = COALESCE(notes, '') || ' [auto-stream-started:' ||
            NOW()::text || ']' WHERE id = %s
        """, (event_id,))
        cur.close()
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# YouTube search
# ---------------------------------------------------------------------------
def find_youtube_stream(search_query, max_results=5):
    """
    Search YouTube for a livestream matching search_query.
    Returns the URL of the best match, preferring live streams.
    """
    print(f"  Searching YouTube: {search_query!r}")

    result = subprocess.run([
        'yt-dlp',
        '--flat-playlist',
        '--dump-json',
        '--no-warnings',
        f'ytsearch{max_results}:{search_query}'
    ], capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        print(f"  yt-dlp search failed: {result.stderr[:200]}")
        return None

    candidates = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        try:
            d = json.loads(line)
            title = d.get('title', '').lower()
            url = d.get('url') or d.get('webpage_url', '')
            is_live = d.get('is_live', False)
            duration = d.get('duration')  # None for live streams

            # Score: prefer live, prefer no duration (live), prefer recent
            score = 0
            if is_live:
                score += 100
            if duration is None:
                score += 50
            if any(kw in title for kw in ['live', 'debate', 'senate', 'primary']):
                score += 10

            if url:
                candidates.append((score, url, title, is_live))
                print(f"    [{score:3d}] {'LIVE' if is_live else '    '} {title[:60]}")
        except Exception:
            continue

    if not candidates:
        print("  No candidates found")
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_url, best_title, best_live = candidates[0]
    print(f"  Selected: {best_title[:60]}")

    # Convert to full YouTube URL if needed
    if not best_url.startswith('http'):
        best_url = f"https://www.youtube.com/watch?v={best_url}"

    return best_url

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def get_event_start_dt(event_date, start_time, tz_label):
    """
    Build a naive UTC-comparable datetime from event_date + start_time.
    We compare against local server time since Railway runs UTC.
    """
    # Timezone offsets (approximate, handles DST roughly)
    TZ_OFFSETS = {
        'ET': -4, 'EST': -5, 'EDT': -4,
        'CT': -5, 'CST': -6, 'CDT': -5,
        'MT': -6, 'MST': -7, 'MDT': -6,
        'PT': -7, 'PST': -8, 'PDT': -7,
    }
    offset_hours = TZ_OFFSETS.get(tz_label or 'CT', -5)

    # Build naive datetime in local time
    dt_local = datetime(
        event_date.year, event_date.month, event_date.day,
        start_time.hour, start_time.minute, 0
    )
    # Convert to UTC
    dt_utc = dt_local - timedelta(hours=offset_hours)
    return dt_utc

def minutes_until(dt_utc):
    """Minutes until dt_utc from now (UTC)."""
    now_utc = datetime.utcnow()
    delta = dt_utc - now_utc
    return delta.total_seconds() / 60

# ---------------------------------------------------------------------------
# Main stream launcher
# ---------------------------------------------------------------------------
def launch_stream(event_id, slug, event_name, stream_url, speaker_order):
    """Launch debate_stream.py in live mode."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debate_stream_deepgram.py')

    speaker_order_str = ','.join(str(s) for s in speaker_order)

    cmd = [
        sys.executable, script,
        '--mode', 'live',
        '--url', stream_url,
        '--event-slug', slug,
    ]
    if speaker_order_str:
        cmd += ['--speaker-order', speaker_order_str]

    print(f"\nLaunching stream for: {event_name}")
    print(f"  URL: {stream_url}")
    print(f"  Speakers: {speaker_order_str}")
    print(f"  Command: {' '.join(cmd)}")

    mark_stream_started(event_id)

    # Run stream — this blocks until stream ends or errors
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print("\nStream stopped by user.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true',
                        help='Check what would fire without launching')
    parser.add_argument('--force-slug', default='',
                        help='Force launch for a specific event slug')
    args = parser.parse_args()

    load_env()

    # Check yt-dlp available
    if subprocess.run(['which', 'yt-dlp'], capture_output=True).returncode != 0:
        print("ERROR: yt-dlp not found. Run: pip install yt-dlp")
        sys.exit(1)

    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] Checking for upcoming events...")

    rows = get_upcoming_events(window_minutes=20)
    if not rows:
        print("No events today. Exiting.")
        return

    for row in rows:
        (event_id, slug, event_name, event_date, start_time,
         timezone_label, search_query, stream_url) = row

        # Calculate time until start
        start_dt_utc = get_event_start_dt(event_date, start_time, timezone_label)
        mins = minutes_until(start_dt_utc)

        print(f"\nEvent: {event_name}")
        print(f"  Slug: {slug}")
        print(f"  Starts in: {mins:.0f} minutes")
        print(f"  Search query: {search_query!r}")
        print(f"  Stored stream URL: {stream_url!r}")

        # Force launch check
        if args.force_slug and args.force_slug != slug:
            continue

        # Only launch if within 15 minutes of start (or forced)
        if not args.force_slug and (mins > 15 or mins < -180):
            print(f"  → Not in launch window (15 min before to 3 hours after start)")
            continue

        if args.check:
            print(f"  → [CHECK MODE] Would launch stream")
            continue

        # Find YouTube URL
        final_url = stream_url  # use stored URL if available

        if not final_url and search_query:
            final_url = find_youtube_stream(search_query)

        if not final_url:
            print(f"  ERROR: No stream URL found for {slug}")
            print(f"  Set search_query on the event or add stream_url manually")
            continue

        # Get speaker order
        speaker_order = get_speaker_order(event_id)
        print(f"  Speaker order: {speaker_order}")

        # Launch
        launch_stream(event_id, slug, event_name, final_url, speaker_order)

if __name__ == '__main__':
    main()
