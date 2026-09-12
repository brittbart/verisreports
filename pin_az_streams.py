#!/usr/bin/env python3
"""Pin the Clean Elections English stream ids to events.stream_url for the AZ general debates.
Dry-run by default; --apply writes. Refuses any row whose slug differs or whose stream_url is already set."""
import os, sys, psycopg2
from dotenv import load_dotenv
load_dotenv()
PINS = {
    25: ('arizona-treasurer-2026-general',          'W_FKrJRXsfs'),
    26: ('arizona-superintendent-2026-general',     'OXJxr3Bepls'),
    34: ('arizona-attorney-general-2026-general',   'YmMfCPXxDAM'),
    35: ('arizona-secretary-of-state-2026-general', '9Lg5XQMNVaI'),
    36: ('arizona-cd4-2026-general',                'nzOL5SE03do'),
    37: ('arizona-lt-governor-2026-general',        'bGaWtKhUSV8'),
    38: ('arizona-governor-2026-general',           'l_754NIYZX4'),
    39: ('arizona-cd1-2026-general',                'Au6-jiQ9eDw'),
}
apply = '--apply' in sys.argv
conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
                        password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'])
cur = conn.cursor()
cur.execute("SELECT id, slug, stream_url FROM events WHERE id = ANY(%s) ORDER BY id", (list(PINS),))
rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
assert set(rows) == set(PINS), f"missing rows: {set(PINS) - set(rows)}"
for eid, (slug, vid) in PINS.items():
    db_slug, db_url = rows[eid]
    assert db_slug == slug, f"event {eid}: slug {db_slug!r} != {slug!r} -- refusing"
    assert db_url is None, f"event {eid}: stream_url already {db_url!r} -- refusing"
    print(f"{'APPLY' if apply else 'DRY  '} {eid:>2} {slug:<42} -> https://www.youtube.com/watch?v={vid}")
if apply:
    for eid, (slug, vid) in PINS.items():
        cur.execute("UPDATE events SET stream_url=%s WHERE id=%s AND slug=%s AND stream_url IS NULL",
                    (f"https://www.youtube.com/watch?v={vid}", eid, slug))
        assert cur.rowcount == 1, f"event {eid}: rowcount {cur.rowcount}"
    conn.commit()
    cur.execute("SELECT id, stream_url FROM events WHERE id = ANY(%s) ORDER BY id", (list(PINS),))
    for r in cur.fetchall(): print("AFTER", r)
print("ROLLBACK: UPDATE events SET stream_url=NULL WHERE id IN (" + ",".join(map(str, PINS)) + ");")
conn.close()
