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

def write_heartbeat(status, event_id=None, error_msg=None):
    """Write stream status heartbeat to job_runs table."""
    try:
        from verdict_engine import get_connection
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO job_runs (stage, started_at, finished_at, duration_ms, status, items_processed, hostname, error_message)
                VALUES ('stream_heartbeat', NOW(), NOW(), 0, %s, %s, %s, %s)
            """, (status, event_id or 0, os.uname().nodename, error_msg or ''))
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"Heartbeat write failed: {e}")

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
            LEFT JOIN event_speakers es ON es.event_id = e.id AND es.is_active = TRUE
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
        # If no stream_url, try to resolve via yt_dlp Python library
        if not stream_url and search_query:
            log(f"No stream_url — searching YouTube: {search_query}")
            try:
                from stream_utils import resolve_stream_url, PreLiveError
                stream_url = resolve_stream_url(f'ytsearch1:{search_query} live')
                log(f"Resolved stream URL via search: {stream_url[:80]}")
            except PreLiveError as e:
                log(f"Stream not yet live (search): {e}")
            except Exception as e:
                log(f"yt_dlp search failed: {e}")
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
    original_youtube_url = stream_url  # preserve for re-resolution on HLS expiry
    log(f"Starting stream for event {event_id} ({slug}): {stream_url}")
    log(f"Command: {' '.join(cmd)}")
    from stream_utils import resolve_stream_url, PreLiveError
    try:
        while True:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(__file__),
                stderr=subprocess.PIPE
            )
            restart_times.append(time.time())
            # Prune old restart times outside the circuit breaker window
            restart_times = [t for t in restart_times if time.time() - t < CIRCUIT_BREAKER_WINDOW]
            if len(restart_times) > CIRCUIT_BREAKER_MAX:
                log(f"CIRCUIT BREAKER: {len(restart_times)} restarts in {CIRCUIT_BREAKER_WINDOW}s — stopping stream service")
                write_heartbeat('circuit_broken', error_msg=f'{len(restart_times)} restarts in {CIRCUIT_BREAKER_WINDOW}s')
                return
            while True:
                ret = proc.poll()
                if ret is not None:
                    # Capture and log stderr before doing anything else
                    try:
                        stderr_output = proc.stderr.read().decode('utf-8', errors='replace').strip()
                        if stderr_output:
                            log(f"SUBPROCESS STDERR:\n{stderr_output}")
                        else:
                            log("SUBPROCESS STDERR: (empty)")
                    except Exception as e:
                        log(f"Could not read stderr: {e}")
                    if ret == 2:
                        # Pre-live exit — do not count as circuit breaker failure
                        restart_times.pop()
                        log(f"Stream exited pre-live (code 2) — sleeping 60s, not counting as failure")
                        time.sleep(60)
                    else:
                        log(f"Stream process exited with code {ret} — re-resolving URL before restart")
                        # Re-resolve to get fresh HLS URL (avoids expiry failures)
                        try:
                            fresh_url = resolve_stream_url(original_youtube_url)
                            cmd[cmd.index('--url') + 1] = fresh_url
                            log(f"Fresh stream URL resolved ✓")
                        except PreLiveError:
                            log("Stream ended (pre-live on re-resolve) — stopping")
                            return
                        except Exception as e:
                            log(f"URL re-resolution failed: {e} — retrying with original")
                    break
                # Check if event is still live
                event = get_live_event()
                if not event or event[0] != event_id:
                    log("Event no longer live — stopping stream")
                    proc.terminate()
                    proc.wait(timeout=10)
                    return
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
            write_heartbeat('streaming', event_id=event_id)
            run_stream(event_id, slug, stream_url, speaker_order, speaker_map)
            write_heartbeat('idle')
            log("Stream ended — resuming poll")
        else:
            write_heartbeat('idle')
            log("No live event — sleeping")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
