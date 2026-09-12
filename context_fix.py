#!/usr/bin/env python3
"""context_fix.py - replace speaker_event_context rows for an event with evidence-based roles/keywords from a spec file.
  venv/bin/python3 context_fix.py --event-id 28 --spec context_spec_event28.json [--apply]
Spec: {"<speaker_id>": {"roles": [...], "keywords": [...]}, ...}. Every speaker in the spec must already have a row for the
event (run generate_speaker_context.py first). Dry run prints BEFORE/NEW; --apply writes generated_by='manual' and prints the
rollback SQL. Run context_check.py afterwards; never re-run the generator on an event after fixing it."""
import argparse, os, sys, json, psycopg2
from psycopg2.extras import Json
ap = argparse.ArgumentParser(); ap.add_argument('--event-id', type=int, required=True); ap.add_argument('--spec', required=True); ap.add_argument('--apply', action='store_true')
a = ap.parse_args()
spec = {int(k): v for k, v in json.load(open(a.spec)).items()}
conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'], connect_timeout=10)
cur = conn.cursor()
cur.execute("SELECT udt_name FROM information_schema.columns WHERE table_name='speaker_event_context' AND column_name='exclusive_keywords'")
udt = cur.fetchone()[0]; adapt = (lambda v: v) if udt.startswith('_') else Json
rollback = []
for sid, new in spec.items():
    cur.execute("SELECT id, exclusive_keywords, roles, generated_by FROM speaker_event_context WHERE event_id = %s AND speaker_id = %s", (a.event_id, sid))
    rows = cur.fetchall(); assert len(rows) == 1, f'speaker {sid}: {len(rows)} row(s) for event {a.event_id}'
    rid, old_kw, old_roles, old_gen = rows[0]
    print(f"speaker {sid} row {rid} BEFORE ({old_gen}): roles={old_roles} keywords={old_kw}\n  NEW: roles={new['roles']} keywords={new['keywords']}")
    rollback.append(f"UPDATE speaker_event_context SET exclusive_keywords = '{json.dumps(old_kw)}'::jsonb, roles = '{json.dumps(old_roles)}'::jsonb, generated_by = '{old_gen}' WHERE id = {rid};")
    if a.apply:
        cur.execute("UPDATE speaker_event_context SET exclusive_keywords = %s, roles = %s, generated_by = 'manual', updated_at = NOW() WHERE id = %s", (adapt(new['keywords']), adapt(new['roles']), rid))
        assert cur.rowcount == 1
if not a.apply:
    print('dry run - nothing written (add --apply)'); sys.exit(0)
conn.commit()
cur.execute("SELECT id, speaker_id, roles, exclusive_keywords, generated_by FROM speaker_event_context WHERE event_id = %s ORDER BY speaker_id", (a.event_id,))
for r in cur.fetchall(): print('AFTER:', r)
print('ROLLBACK:', ' '.join(rollback))
