#!/usr/bin/env python3
"""Flip event 24 (the rehearsal event ONLY) into/out of a worker-testable state: stream_url = the CD7 VOD, capture_enabled TRUE.
  venv/bin/python3 set_event24_capture.py --on | --off | --show     Refuses on any other event or if is_public/is_listed are TRUE."""
import os, sys, psycopg2
EVENT_ID, SLUG = 24, 's12-rehearsal3-cd7-20260902'
VOD = 'https://www.youtube.com/watch?v=VmNCTGl9yxw'
mode = (sys.argv[1:] or ['--show'])[0]
conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'], connect_timeout=10)
cur = conn.cursor()
cur.execute("SELECT id, slug, is_public, is_listed, capture_enabled, stream_url, event_date, start_time FROM events WHERE id = %s", (EVENT_ID,))
row = cur.fetchone(); print('BEFORE:', row)
assert row and row[1] == SLUG and not row[2] and not row[3], 'REFUSED: not the rehearsal event or public/listed'
if mode == '--on':
    cur.execute("UPDATE events SET stream_url = %s, capture_enabled = TRUE WHERE id = %s", (VOD, EVENT_ID))
elif mode == '--off':
    cur.execute("UPDATE events SET stream_url = NULL, capture_enabled = FALSE WHERE id = %s", (EVENT_ID,))
else:
    sys.exit(0)
assert cur.rowcount == 1; conn.commit()
cur.execute("SELECT id, slug, is_public, is_listed, capture_enabled, stream_url FROM events WHERE id = %s", (EVENT_ID,)); print('AFTER: ', cur.fetchone())
