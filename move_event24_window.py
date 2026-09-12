#!/usr/bin/env python3
"""Move rehearsal event 24 into the debate.html live window (or restore it).
Usage:
  venv/bin/python3 move_event24_window.py --show
  venv/bin/python3 move_event24_window.py --apply          # event_date=today MST, start_time=now MST -> page renders is-live for 3 h
  venv/bin/python3 move_event24_window.py --restore 2026-09-02 18:00:00
Refuses unless event 24 has is_public=F, is_listed=F, capture_enabled=F and the rehearsal slug."""
import os, sys
from datetime import datetime, timedelta, timezone
import psycopg2
EVENT_ID = 24
SLUG = 's12-rehearsal3-cd7-20260902'
MST = timezone(timedelta(hours=-7))
def connect():
    return psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ.get('DB_HOST', 'localhost'), port=os.environ.get('DB_PORT', '5432'))
def fetch(cur):
    cur.execute("SELECT id, slug, event_date, start_time, timezone, is_public, is_listed, capture_enabled FROM events WHERE id = %s", (EVENT_ID,))
    return cur.fetchone()
def main():
    args = sys.argv[1:]
    if not args or args[0] not in ('--show', '--apply', '--restore'):
        print(__doc__); sys.exit(2)
    conn = connect(); cur = conn.cursor()
    row = fetch(cur)
    if row is None:
        print(f"event {EVENT_ID} not found"); sys.exit(1)
    eid, slug, event_date, start_time, tz, is_public, is_listed, cap = row
    print(f"BEFORE: id={eid} slug={slug} event_date={event_date} start_time={start_time} tz={tz} is_public={is_public} is_listed={is_listed} capture_enabled={cap}")
    if args[0] == '--show':
        sys.exit(0)
    if slug != SLUG or is_public or is_listed or cap:
        print("REFUSED: not the rehearsal event or a flag is TRUE"); sys.exit(1)
    if args[0] == '--apply':
        now_mst = datetime.now(MST)
        new_date = now_mst.date()
        new_time = now_mst.replace(second=0, microsecond=0).time()
        print(f"ROLLBACK: venv/bin/python3 move_event24_window.py --restore {event_date} {start_time}")
    else:
        if len(args) != 3:
            print("usage: --restore YYYY-MM-DD HH:MM:SS"); sys.exit(2)
        new_date = datetime.strptime(args[1], '%Y-%m-%d').date()
        new_time = datetime.strptime(args[2], '%H:%M:%S').time()
    cur.execute("UPDATE events SET event_date = %s, start_time = %s, timezone = 'MST' WHERE id = %s", (new_date, new_time, EVENT_ID))
    assert cur.rowcount == 1, cur.rowcount
    conn.commit()
    eid, slug, event_date, start_time, tz, is_public, is_listed, cap = fetch(cur)
    print(f"AFTER:  id={eid} slug={slug} event_date={event_date} start_time={start_time} tz={tz}")
    if args[0] == '--apply':
        print("page should render is-live now until start_time + 3 h (MST)")
    cur.close(); conn.close()
if __name__ == '__main__':
    main()
