#!/usr/bin/env python3
"""create_event.py - create a debate event, its new speaker rows and its roster in ONE transaction.
Replaces the per-event create_event_sept23/sept15 scripts. Dry run by default; --apply writes.
  venv/bin/python3 create_event.py --slug arizona-attorney-general-2026-general \
      --name "Arizona Attorney General General Election Debate" --date 2026-09-23 --time 18:00 --tz America/Phoenix \
      --speaker "Kris Mayes" --speaker "Warren Petersen" --notes "Sep 23 2026 Clean Elections debate ..." [--apply]
  --speaker-id 253            reuse an existing speakers row (same candidate, another debate) - repeatable, order kept
  --speaker "Name"            create a politician row (normalized_name/slug derived) - repeatable, order kept
  --moderator-id 3            roster position 0 (default 3, the shared Moderator row); --no-moderator to omit
Roster order = moderator, then speakers in the order given on the command line = the --speaker-order argument.
Refuses: existing slug; a --speaker whose normalized_name or slug already exists (use --speaker-id); a timezone
Postgres will not accept (ET/CT/MT/PT - store IANA names); a --speaker-id that does not exist.
Flags written: event_type debate, is_public FALSE, is_listed TRUE, capture_enabled FALSE, methodology v1.7,
attribution_confidence_threshold 0.60, stream_url NULL (pin later). Prints the rollback SQL on --apply."""
import argparse, os, re, sys, datetime
import psycopg2
import event_time
def norm(name):
    n = re.sub(r'[^a-z0-9 ]+', '', name.lower()).strip()
    return re.sub(r'\s+', ' ', n), re.sub(r'\s+', '-', n)
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--slug', required=True); ap.add_argument('--name', required=True)
    ap.add_argument('--date', required=True, help='YYYY-MM-DD'); ap.add_argument('--time', required=True, help='HH:MM local')
    ap.add_argument('--tz', required=True, help='IANA name, e.g. America/Phoenix, America/New_York')
    ap.add_argument('--speaker', action='append', default=[], metavar='NAME'); ap.add_argument('--speaker-id', action='append', type=int, default=[], metavar='ID')
    ap.add_argument('--moderator-id', type=int, default=3); ap.add_argument('--no-moderator', action='store_true')
    ap.add_argument('--notes', default=''); ap.add_argument('--methodology', default='v1.7'); ap.add_argument('--threshold', type=float, default=0.60)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    FIXED = {'EST', 'EDT', 'CST', 'CDT', 'MST', 'MDT', 'PST', 'PDT'}
    if a.tz not in FIXED:
        sys.exit(f"REFUSED: timezone {a.tz!r} - store a fixed abbreviation from {sorted(FIXED)}: Postgres rejects ET/CT/MT/PT and mobile_sse "
                 f"(the stream service) falls back to CT for IANA names. Use the daylight form (EDT/CDT/MDT/PDT) for events before 2026-11-01, "
                 f"standard after; MST for Arizona always.")
    if not re.fullmatch(r'[a-z0-9-]+', a.slug): sys.exit(f'REFUSED: slug {a.slug!r} must be lowercase letters, digits, hyphens')
    ev_date = datetime.date.fromisoformat(a.date); ev_time = datetime.time.fromisoformat(a.time)
    w0, w1 = event_time.window(ev_date, ev_time, a.tz)
    # speakers in command-line order: argparse gives two lists; rebuild the interleaving from sys.argv
    order = []
    argv = sys.argv[1:]
    for i, tok in enumerate(argv):
        if tok == '--speaker': order.append(('new', argv[i + 1]))
        elif tok == '--speaker-id': order.append(('id', int(argv[i + 1])))
    if not order: sys.exit('REFUSED: at least one --speaker or --speaker-id')
    conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'], connect_timeout=10)
    cur = conn.cursor()
    cur.execute("SELECT id FROM events WHERE slug = %s", (a.slug,))
    if cur.fetchone(): sys.exit(f'REFUSED: slug {a.slug!r} already exists')
    roster = []
    if not a.no_moderator:
        cur.execute("SELECT id, name, speaker_type FROM speakers WHERE id = %s", (a.moderator_id,))
        m = cur.fetchone()
        if not m: sys.exit(f'REFUSED: moderator id {a.moderator_id} not found')
        roster.append(('existing', m[0], m[1], m[2]))
    for kind, val in order:
        if kind == 'id':
            cur.execute("SELECT id, name, speaker_type FROM speakers WHERE id = %s", (val,))
            r = cur.fetchone()
            if not r: sys.exit(f'REFUSED: --speaker-id {val} not found')
            roster.append(('existing', r[0], r[1], r[2]))
        else:
            nn, sl = norm(val)
            cur.execute("SELECT id, name FROM speakers WHERE normalized_name = %s OR slug = %s", (nn, sl))
            clash = cur.fetchone()
            if clash: sys.exit(f'REFUSED: --speaker {val!r} collides with existing speaker {clash} - use --speaker-id {clash[0]}')
            roster.append(('new', None, val, 'politician'))
    cur.execute("SELECT last_value FROM speakers_id_seq"); seq = cur.fetchone()[0]
    print(f"{'APPLY' if a.apply else 'DRY RUN'}: event {a.slug} | {a.name}\n  {a.date} {a.time} {a.tz}  -> live window {w0.isoformat()} .. {w1.isoformat()} (UTC)\n  speakers_id_seq now {seq}")
    for i, (kind, sid, name, st) in enumerate(roster):
        print(f"  roster {i}: {kind:8s} id={sid if sid else 'next'} {name!r} ({st})")
    if not a.apply:
        print('dry run - nothing written (add --apply)'); conn.close(); return
    new_ids = []
    for i, (kind, sid, name, st) in enumerate(roster):
        if kind == 'new':
            nn, sl = norm(name)
            cur.execute("INSERT INTO speakers (name, normalized_name, slug, speaker_type) VALUES (%s, %s, %s, 'politician') RETURNING id", (name, nn, sl))
            sid = cur.fetchone()[0]; new_ids.append(sid); roster[i] = ('new', sid, name, st)
    cur.execute("""INSERT INTO events (slug, event_type, event_name, event_date, start_time, timezone, is_public, is_listed, capture_enabled,
                   methodology_version, attribution_confidence_threshold, notes) VALUES (%s,'debate',%s,%s,%s,%s,FALSE,TRUE,FALSE,%s,%s,%s) RETURNING id""",
                (a.slug, a.name, ev_date, ev_time, a.tz, a.methodology, a.threshold, a.notes))
    eid = cur.fetchone()[0]
    for i, (kind, sid, name, st) in enumerate(roster):
        cur.execute("INSERT INTO event_speakers (event_id, speaker_id, speaker_order) VALUES (%s,%s,%s)", (eid, sid, i))
    conn.commit()
    cur.execute("SELECT id, slug, event_date, start_time, timezone, is_public, is_listed, capture_enabled, stream_url FROM events WHERE id=%s", (eid,)); print('event:', cur.fetchone())
    cur.execute("SELECT es.speaker_order, s.id, s.name, s.speaker_type FROM event_speakers es JOIN speakers s ON s.id=es.speaker_id WHERE es.event_id=%s ORDER BY 1", (eid,))
    for r in cur.fetchall(): print('roster:', r)
    ids = ','.join(str(sid) for _, sid, _, _ in roster)
    print(f"--speaker-order {ids}")
    rb = f"DELETE FROM event_speakers WHERE event_id={eid}; DELETE FROM events WHERE id={eid};"
    if new_ids: rb += f" DELETE FROM speakers WHERE id IN ({','.join(map(str, new_ids))}); SELECT setval('speakers_id_seq',(SELECT max(id) FROM speakers));"
    print(f"COMMITTED event id {eid}. Rollback: {rb}")
    conn.close()
if __name__ == '__main__':
    main()
