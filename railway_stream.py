"""
railway_stream.py — Verum Signal debate stream service.
Runs continuously on Railway. Polls every 60s for a live debate event.
When a live event is detected, starts debate_stream.py in live mode.
Auto-restarts on disconnect or crash.
"""
import os
import sys
import time
import subprocess
from datetime import datetime

if os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv(override=False)

POLL_INTERVAL = 60  # seconds between checks when idle
STREAM_SCRIPT = os.path.join(os.path.dirname(__file__), "debate_stream.py")
PYTHON = sys.executable

def log(msg):
    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_live_event():
    """Return (event_id, slug, stream_url, speaker_order) or None."""
    try:
        from verdict_engine import get_live_event_id, get_connection
        event_id = get_live_event_id()
        if not event_id:
            return None
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT e.slug, e.stream_url,
                   string_agg(es.speaker_id::text, ',' ORDER BY es.speaker_order) as speaker_order,
                   string_agg(s.name || ':' || es.speaker_id::text, ',' ORDER BY es.speaker_order) as speaker_map
            FROM events e
            LEFT JOIN event_speakers es ON es.event_id = e.id
            LEFT JOIN speakers s ON s.id = es.speaker_id
            WHERE e.id = %s
            GROUP BY e.id
        """, (event_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        slug, stream_url, speaker_order, speaker_map = row
        return event_id, slug, stream_url, speaker_order, speaker_map
    except Exception as e:
        log(f"Error checking live event: {e}")
        return None

def run_stream(event_id, slug, stream_url, speaker_order, speaker_map):
    """Run debate_stream.py for the live event. Returns when stream ends."""
    if not stream_url:
        log(f"No stream URL for event {event_id} ({slug}) — skipping")
        return
    cmd = [
        PYTHON, '-u', STREAM_SCRIPT,
        '--mode', 'live',
        '--url', stream_url,
        '--event-slug', slug,
    ]
    if speaker_map:
        cmd += ['--speakers', speaker_map.upper()]
    if speaker_order:
        cmd += ['--speaker-order', speaker_order]
    log(f"Starting stream for event {event_id} ({slug}): {stream_url}")
    log(f"Command: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, cwd=os.path.dirname(__file__))
        while True:
            ret = proc.poll()
            if ret is not None:
                log(f"Stream process exited with code {ret}")
                break
            # Check if event is still live
            event = get_live_event()
            if not event or event[0] != event_id:
                log("Event no longer live — stopping stream")
                proc.terminate()
                proc.wait(timeout=10)
                break
            time.sleep(30)
    except Exception as e:
        log(f"Stream error: {e}")
        try:
            proc.terminate()
        except:
            pass

def main():
    log("Verum Signal stream service started")
    log(f"Polling every {POLL_INTERVAL}s for live debate events")
    while True:
        event = get_live_event()
        if event:
            event_id, slug, stream_url, speaker_order, speaker_map = event
            log(f"Live event detected: {event_id} ({slug})")
            run_stream(event_id, slug, stream_url, speaker_order, speaker_map)
            log("Stream ended — resuming poll")
        else:
            log("No live event — sleeping")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
