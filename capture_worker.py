#!/usr/bin/env python3
"""capture_worker.py - DB-driven capture scheduler. Every --interval seconds: select events with capture_enabled=TRUE and a
stream_url; for each whose live window is open (event_time.window: start-30min .. start+3h; hard stop start+4h) and has no
running child, spawn `watchdog_debate_capture.py --mode live --url <stream_url> --event-slug <slug> --speaker-order <roster>`
(plus --dry-run when the worker runs with --dry-run). Children start staggered --stagger seconds apart so voice-ID ticks do
not align. A child that exits is recorded and NOT respawned (the watchdog owns crash-retry). Status goes to capture_status.
  venv/bin/python3 capture_worker.py                 # loop forever (the Railway start command)
  venv/bin/python3 capture_worker.py --once          # one tick, then exit (testing)
  venv/bin/python3 capture_worker.py --once --dry-run
Logs: logs/event_<id>_capture_<stamp>.log per child. SIGTERM to the worker terminates every child's process group."""
import argparse, os, signal, socket, subprocess, sys, time, datetime
import psycopg2
import event_time
HERE = os.path.dirname(os.path.abspath(__file__))
HARD_STOP = datetime.timedelta(hours=4)
HOST = os.environ.get('RAILWAY_SERVICE_NAME') or socket.gethostname()
def db():
    return psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'], connect_timeout=10)
def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] worker: {msg}", flush=True)
def due_events(cur, now):
    cur.execute("SELECT id, slug, stream_url, event_date, start_time, timezone FROM events WHERE capture_enabled = TRUE AND stream_url IS NOT NULL AND stream_url <> ''")
    out = []
    for eid, slug, url, d, t, tz in cur.fetchall():
        w = event_time.window(d, t, tz)
        if w is None: continue
        w0, w1 = w; start = event_time.start_dt(d, t, tz)
        if w0 <= now <= start + HARD_STOP:
            out.append((eid, slug, url, w0, w1, start))
    return out
def roster(cur, eid):
    cur.execute("SELECT speaker_id FROM event_speakers WHERE event_id = %s ORDER BY speaker_order", (eid,))
    return ','.join(str(r[0]) for r in cur.fetchall())
def upsert(cur, eid, **f):
    f['updated_at'] = datetime.datetime.now(datetime.timezone.utc)
    cols = ['event_id'] + list(f); vals = [eid] + list(f.values())
    sets = ', '.join(f'{c} = EXCLUDED.{c}' for c in f)
    cur.execute(f"INSERT INTO capture_status ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(vals))}) ON CONFLICT (event_id) DO UPDATE SET {sets}", vals)
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--interval', type=int, default=60); ap.add_argument('--stagger', type=int, default=20)
    ap.add_argument('--max-concurrent', type=int, default=2); ap.add_argument('--once', action='store_true'); ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    os.makedirs(os.path.join(HERE, 'logs'), exist_ok=True)
    children = {}   # eid -> (Popen, logfile, started_at, start_dt)
    finished = set()
    stopping = False
    def on_term(sig, frame):
        nonlocal stopping; stopping = True; log(f'signal {sig}: terminating {len(children)} child group(s)')
        for eid, (p, _, _, _) in children.items():
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass
    signal.signal(signal.SIGTERM, on_term); signal.signal(signal.SIGINT, on_term)
    log(f"start host={HOST} interval={a.interval}s max_concurrent={a.max_concurrent} dry_run={a.dry_run} once={a.once}")
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            conn = db(); cur = conn.cursor()
            for eid, (p, lf, started, start_dt) in list(children.items()):
                rc = p.poll()
                if rc is not None:
                    lf.close(); del children[eid]; finished.add(eid)
                    upsert(cur, eid, state='exited', exit_code=rc, last_seen=now); log(f'event {eid} child exited rc={rc}')
                elif now > start_dt + HARD_STOP:
                    os.killpg(p.pid, signal.SIGTERM); upsert(cur, eid, state='killed', last_seen=now); log(f'event {eid} hard stop at start+4h')
                else:
                    upsert(cur, eid, state='running', last_seen=now)
            if not stopping:
                for eid, slug, url, w0, w1, start in due_events(cur, now):
                    if eid in children or eid in finished: continue
                    if len(children) >= a.max_concurrent:
                        upsert(cur, eid, state='waiting', last_seen=now, host=HOST); log(f'event {eid} due but max_concurrent reached'); continue
                    order = roster(cur, eid)
                    if not order:
                        upsert(cur, eid, state='waiting', last_seen=now, host=HOST); log(f'event {eid} has no roster - not started'); continue
                    stamp = now.strftime('%Y%m%d_%H%M%S')
                    log_path = os.path.join(HERE, 'logs', f'event_{eid}_capture_{stamp}.log')
                    cmd = [sys.executable, '-u', os.path.join(HERE, 'watchdog_debate_capture.py'), '--mode', 'live', '--url', url, '--event-slug', slug, '--speaker-order', order] + (['--dry-run'] if a.dry_run else [])
                    lf = open(log_path, 'a')
                    p = subprocess.Popen(cmd, cwd=HERE, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
                    children[eid] = (p, lf, now, start)
                    upsert(cur, eid, state='running', pid=p.pid, host=HOST, started_at=now, last_seen=now, exit_code=None, log_path=log_path, dry_run=a.dry_run)
                    log(f"event {eid} {slug}: started pid {p.pid} roster {order} window {w0.isoformat()}..{w1.isoformat()} log {os.path.basename(log_path)}")
                    time.sleep(a.stagger)
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            log(f'tick error: {e!r}')
        if a.once or (stopping and not children):
            break
        time.sleep(a.interval)
    log(f'exit; children still running: {sorted(children)}')
if __name__ == '__main__':
    main()
