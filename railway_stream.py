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

POLL_INTERVAL = 15  # seconds between checks when idle (reduced from 60s for faster restart recovery)
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
            SELECT e.slug, e.stream_url, e.search_query,
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
        slug, stream_url, search_query, speaker_order, speaker_map = row
        # If no stream_url, try to resolve via yt-dlp search query
        if not stream_url and search_query:
            log(f"No stream_url — searching YouTube: {search_query}")
            if True:
                try:
                    import subprocess as _sp
                    result = _sp.run(
                        ['yt-dlp', '--get-url', '--format', 'bestaudio/best',
                         f'ytsearch1:{search_query} live'],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        stream_url = result.stdout.strip().split('\n')[0]
                        log(f"Resolved stream URL via search: {stream_url[:80]}")
                except Exception as e:
                    log(f"yt-dlp search failed: {e}")
        return event_id, slug, stream_url, speaker_order, speaker_map
    except Exception as e:
        log(f"Error checking live event: {e}")
        return None

def run_stream(event_id, slug, stream_url, speaker_order, speaker_map):
    """Run debate_stream.py for the live event. Returns when stream ends."""
    if not stream_url:
        log(f"No stream URL for event {event_id} ({slug}) — skipping")
        return
    # Circuit breaker: max 5 restarts in 10 minutes
    restart_times = []
    CIRCUIT_BREAKER_WINDOW = 600  # 10 minutes
    CIRCUIT_BREAKER_MAX = 5
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
        restart_times.append(time.time())
        # Prune old restart times outside the circuit breaker window
        restart_times = [t for t in restart_times if time.time() - t < CIRCUIT_BREAKER_WINDOW]
        if len(restart_times) > CIRCUIT_BREAKER_MAX:
            log(f"CIRCUIT BREAKER: {len(restart_times)} restarts in {CIRCUIT_BREAKER_WINDOW}s — stopping stream service")
            return
        while True:
            ret = proc.poll()
            if ret is not None:
                log(f"Stream process exited with code {ret} — will attempt restart if event still live")
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
