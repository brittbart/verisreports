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

# Advisory-lock namespace for debate capture. Arbitrary but fixed: paired with
# event_id it forms the two-int key for pg_try_advisory_lock, so it must never
# change or old and new holders would not see each other's locks.
STREAM_LOCK_NAMESPACE = 918273


def acquire_stream_lock(event_id):
    """Take a session-scoped advisory lock for this event.

    Returns the holding CONNECTION on success, or None if another process
    already holds it. The connection must stay open for the whole capture —
    closing it releases the lock, which is exactly why the caller keeps it.

    Session-scoped is the right choice: if this container dies the connection
    drops and Postgres frees the lock, so a replacement can take over. A
    table-based lock would need timeout logic and would strand the event if a
    container died holding it.
    """
    try:
        from verdict_engine import get_connection
        conn = get_connection()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s, %s)",
                    (STREAM_LOCK_NAMESPACE, int(event_id)))
        got = cur.fetchone()[0]
        cur.close()
        if got:
            log(f"Capture lock ACQUIRED for event {event_id}")
            return conn
        conn.close()
        log(f"Capture lock DENIED for event {event_id} — another process is "
            f"already capturing it. Not starting a competing stream.")
        return None
    except Exception as e:
        # Fail OPEN: a lock we cannot reach must not stop a live debate being
        # captured. Say so loudly — this is the window in which the original
        # concurrent-writer defect can still occur.
        log(f"Capture lock UNAVAILABLE ({e}) — proceeding WITHOUT it. "
            f"Concurrent capture is possible; check for duplicate "
            f"utterance_order values afterwards.")
        return 'NOLOCK'


def release_stream_lock(handle, event_id):
    """Release the advisory lock and close its connection."""
    if handle in (None, 'NOLOCK'):
        return
    try:
        cur = handle.cursor()
        cur.execute("SELECT pg_advisory_unlock(%s, %s)",
                    (STREAM_LOCK_NAMESPACE, int(event_id)))
        cur.close()
        handle.close()
        log(f"Capture lock released for event {event_id}")
    except Exception as e:
        log(f"Capture lock release failed ({e}) — the session ending will "
            f"free it regardless")


def write_exit_event(event_id, exit_code, stderr_text, restarts):
    """Persist WHY the capture subprocess died.

    debate_stream.py's exit code and stderr were logged to stdout only, so
    Railway's ephemeral logs held the only explanation and thousands of
    restarts across five events discarded theirs. Without this the question
    "why does capture keep failing" cannot be answered after the fact — which
    is exactly the position the 2026-08 audit found itself in.

    stderr is truncated to 500 chars: enough for a traceback's final frames,
    small enough that a crash-loop cannot bloat the table.
    """
    try:
        from verdict_engine import get_connection
        snippet = (stderr_text or '').strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + '...[truncated]'
        if not snippet:
            snippet = '(no stderr)'
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO job_runs (stage, started_at, finished_at, duration_ms,
                                      status, items_processed, hostname, error_message)
                VALUES ('stream_exit', NOW(), NOW(), 0, %s, %s, %s, %s)
            """, (f'exit_{exit_code}', event_id or 0, os.uname().nodename,
                  f'restart #{restarts} | code={exit_code} | {snippet}'))
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"Exit-event write failed: {e}")


def touch_heartbeat():
    """Refresh THIS container's current heartbeat row instead of inserting.

    An INSERT per poll is what filled the volume on 2026-07-10 (83,909 rows,
    96% of job_runs) and got the poll-loop write deleted in cb5bca3. An UPDATE
    in place costs one row and never grows, so it can run at the full poll
    interval and give a liveness signal fresh to 15 seconds.

    Semantics of the refreshed row: started_at is when this state began,
    finished_at is when the poller was last known alive. A reader compares
    finished_at to NOW() to tell a sleeping poller from a dead one.

    Returns True if a row was refreshed. False means there was nothing to
    refresh — the caller inserts one so the next poll has a target.
    """
    try:
        from verdict_engine import get_connection
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE job_runs
                   SET finished_at = NOW(),
                       duration_ms = GREATEST(0, EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000)::bigint
                 WHERE id = (
                     SELECT id FROM job_runs
                      WHERE stage = 'stream_heartbeat'
                        AND hostname = %s
                        AND status = 'idle'
                      ORDER BY started_at DESC
                      LIMIT 1
                 )
            """, (os.uname().nodename,))
            refreshed = cur.rowcount > 0
        conn.commit()
        conn.close()
        return refreshed
    except Exception as e:
        log(f"Liveness refresh failed: {e}")
        return True  # do not spam inserts when the DB is unreachable


def is_event_still_live(event_id):
    """Is THIS event still inside its live window?

    Deliberately scoped to one event. The previous check asked "which event is
    live?" and compared — so a second concurrent event ended the first one's
    capture. Returns True on error: a transient DB failure must not kill a
    live capture, and the stream's own max_duration still bounds it.
    """
    try:
        from verdict_engine import get_live_event_id
        # get_live_event_id returns one id; ask it repeatedly only to learn
        # whether OUR id is among the live set. Cheap and avoids changing the
        # shared helper's signature, which railway_verdicts.py also calls.
        from verdict_engine import get_connection
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        _OFFS = {'ET': -4, 'EST': -5, 'EDT': -4, 'CT': -5, 'CST': -6, 'CDT': -5,
                 'MT': -6, 'MST': -7, 'MDT': -6, 'PT': -7, 'PST': -8, 'PDT': -7}
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT event_date, start_time, timezone FROM events
            WHERE id = %s AND is_public = TRUE AND start_time IS NOT NULL
        """, (event_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return False
        event_date, start_time, event_tz = row
        if not event_date or not start_time:
            return False
        off = _OFFS.get(event_tz or 'CT', -5)
        start = _dt.combine(event_date, start_time).replace(
            tzinfo=_tz(_td(hours=off)))
        now = _dt.now(_tz.utc)
        return (start - _td(minutes=45)) <= now <= (start + _td(hours=3))
    except Exception as e:
        log(f"Liveness check failed for event {event_id} ({e}) — "
            f"assuming still live rather than killing the capture")
        return True


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
                   string_agg(s.name || ':' || es.speaker_id::text, ',' ORDER BY es.speaker_order) as speaker_map,
                   e.rev_ai_vocabulary_id
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
        slug, stream_url, search_query, speaker_order, speaker_map, rev_ai_vocabulary_id = row
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
        return event_id, slug, stream_url, speaker_order, speaker_map, rev_ai_vocabulary_id
    except Exception as e:
        log(f"Error checking live event: {e}")
        return None

def run_stream(event_id, slug, stream_url, speaker_order, speaker_map, rev_ai_vocabulary_id=None):
    """Run debate_stream.py for the live event. Returns when stream ends."""
    if not stream_url:
        log(f"No stream URL for event {event_id} ({slug}) — skipping")
        return
    # Circuit breaker: max 5 restarts in 10 minutes
    restart_times = []
    total_restarts = 0          # cumulative for this run_stream call
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
    if rev_ai_vocabulary_id:
        cmd += ['--vocabulary-id', rev_ai_vocabulary_id]
        log(f"Custom vocabulary: {rev_ai_vocabulary_id}")
    original_youtube_url = stream_url  # preserve for re-resolution on HLS expiry
    log(f"Starting stream for event {event_id} ({slug}): {stream_url}")
    log(f"Command: {' '.join(cmd)}")
    from stream_utils import resolve_stream_url, PreLiveError
    try:
        while True:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(__file__),
                stderr=subprocess.PIPE,
                env=os.environ.copy()  # explicitly pass Railway env vars to subprocess
            )
            restart_times.append(time.time())
            total_restarts += 1
            # Prune old restart times outside the circuit breaker window
            restart_times = [t for t in restart_times if time.time() - t < CIRCUIT_BREAKER_WINDOW]
            if len(restart_times) > CIRCUIT_BREAKER_MAX:
                # NOTE: this returns from run_stream, it does not stop the
                # service. main() polls again in POLL_INTERVAL seconds, finds
                # the event still live, and calls run_stream with a FRESH
                # counter — so the breaker trips repeatedly rather than once.
                # On 2026-06-25 that produced ~60 breaks over three hours.
                # total_restarts is the figure worth reading; the count in the
                # message is always CIRCUIT_BREAKER_MAX+1 by construction.
                log(f"CIRCUIT BREAKER: {len(restart_times)} restarts in "
                    f"{CIRCUIT_BREAKER_WINDOW}s ({total_restarts} this run) — "
                    f"returning to poll loop, which will start over")
                write_heartbeat(
                    'circuit_broken', event_id=event_id,
                    error_msg=f'{len(restart_times)} in {CIRCUIT_BREAKER_WINDOW}s; '
                              f'{total_restarts} restarts this run')
                return
            while True:
                ret = proc.poll()
                if ret is not None:
                    # Capture and log stderr before doing anything else
                    stderr_output = ''
                    try:
                        stderr_output = proc.stderr.read().decode('utf-8', errors='replace').strip()
                        if stderr_output:
                            log(f"SUBPROCESS STDERR:\n{stderr_output}")
                        else:
                            log("SUBPROCESS STDERR: (empty)")
                    except Exception as e:
                        log(f"Could not read stderr: {e}")
                        stderr_output = f'(stderr unreadable: {e})'
                    # Persist it. Railway's logs are ephemeral, so without this
                    # the reason for each death is lost the moment it scrolls.
                    write_exit_event(event_id, ret, stderr_output, total_restarts)
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
                # Check if THIS event is still live.
                #
                # This used to call get_live_event() and compare the returned
                # id against event_id. With two events live at once — which
                # happened on 2026-06-08, events 13 and 15, the only
                # live-window overlap in the platform's history — that call
                # could return the OTHER event, so a healthy capture was told
                # "your event ended" and terminated. Each termination is a
                # restart, and six restarts in ten minutes trips the breaker.
                # Event 13 was never captured; event 15 became the most
                # heavily double-written transcript on the platform.
                if not is_event_still_live(event_id):
                    log(f"Event {event_id} no longer live — stopping stream")
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
    # Seed a row for this container so the first poll has something to refresh
    # and a freshly started poller is immediately visible as alive.
    write_heartbeat('idle')
    while True:
        event = get_live_event()
        if event:
            event_id, slug, stream_url, speaker_order, speaker_map, rev_ai_vocabulary_id = event
            log(f"Live event detected: {event_id} ({slug})")
            lock = acquire_stream_lock(event_id)
            if lock is None:
                # Another container is capturing this event. Keep polling
                # rather than starting a second Rev AI job on the same stream —
                # that is what produced 227 duplicated utterance_order slots on
                # event 16 and made its transcript unresolvable.
                write_heartbeat('lock_denied', event_id=event_id)
                time.sleep(POLL_INTERVAL)
                continue
            try:
                write_heartbeat('streaming', event_id=event_id)
                run_stream(event_id, slug, stream_url, speaker_order, speaker_map, rev_ai_vocabulary_id)
            finally:
                release_stream_lock(lock, event_id)
            write_heartbeat('idle')
            log("Stream ended — resuming poll")
        else:
            # Refresh liveness every poll. Without this a sleeping poller and a
            # dead one are indistinguishable — the ambiguity that made the
            # 2026-07-11 heartbeat silence unreadable for two audit sessions.
            if not touch_heartbeat():
                write_heartbeat('idle')
            log("No live event — sleeping")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
