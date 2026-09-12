#!/usr/bin/env python3
"""Create the capture_status table the capture worker writes and the ops page will read. Dry run by default; --apply creates.
One row per event: state waiting|running|exited|killed, pid, host, started_at, last_seen, exit_code, log_path, dry_run."""
import os, sys, psycopg2
DDL = """CREATE TABLE IF NOT EXISTS capture_status (
    event_id    INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    state       TEXT NOT NULL,
    pid         INTEGER,
    host        TEXT,
    started_at  TIMESTAMPTZ,
    last_seen   TIMESTAMPTZ,
    exit_code   INTEGER,
    log_path    TEXT,
    dry_run     BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)"""
conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'], connect_timeout=10)
cur = conn.cursor()
cur.execute("SELECT to_regclass('public.capture_status')"); exists = cur.fetchone()[0]
print('capture_status exists:', bool(exists))
if '--apply' not in sys.argv[1:]:
    print(DDL); print('dry run - nothing created (add --apply)'); sys.exit(0)
cur.execute(DDL); conn.commit()
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='capture_status' ORDER BY ordinal_position")
for r in cur.fetchall(): print(' ', r)
print("CREATED. Rollback: DROP TABLE capture_status;")
