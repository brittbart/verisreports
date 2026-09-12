"""Fixture for event_time.py - run: venv/bin/python3 test_event_time.py"""
from datetime import date, time, datetime, timedelta, timezone as tz
import event_time as et
U = tz.utc
def d(y, m, dd, hh=0, mm=0): return datetime(y, m, dd, hh, mm, tzinfo=U)
# 1. MST is fixed -7 (Arizona), summer and winter
assert et.start_iso(date(2026, 9, 23), time(16, 0), 'MST') == '2026-09-23T16:00:00-07:00'
assert et.start_iso(date(2026, 12, 23), time(16, 0), 'MST') == '2026-12-23T16:00:00-07:00'
assert et.start_iso(date(2026, 12, 23), time(16, 0), 'America/Phoenix') == '2026-12-23T16:00:00-07:00'
# 2. IANA zones follow DST; the old dict did not (America/Denver fell to CT = -5)
assert et.start_iso(date(2026, 8, 21), time(21, 14), 'America/Denver') == '2026-08-21T21:14:00-06:00'
assert et.start_iso(date(2026, 12, 21), time(21, 14), 'America/Denver') == '2026-12-21T21:14:00-07:00'
assert et.start_iso(date(2026, 10, 6), time(19, 0), 'America/New_York') == '2026-10-06T19:00:00-04:00'
assert et.start_iso(date(2026, 11, 6), time(19, 0), 'America/New_York') == '2026-11-06T19:00:00-05:00'
# 3. generic abbreviations are DST-aware; fixed abbreviations are fixed (Postgres semantics)
assert et.start_iso(date(2026, 10, 6), time(19, 0), 'ET') == '2026-10-06T19:00:00-04:00'
assert et.start_iso(date(2026, 10, 6), time(19, 0), 'EST') == '2026-10-06T19:00:00-05:00'
assert et.start_iso(date(2026, 10, 7), time(19, 0), 'CT') == '2026-10-07T19:00:00-05:00'
# 4. window: Sept 23 Treasurer 16:00 MST -> 22:30Z .. 02:00Z next day
w0, w1 = et.window(date(2026, 9, 23), time(16, 0), 'MST')
assert (w0, w1) == (d(2026, 9, 23, 22, 30), d(2026, 9, 24, 2, 0)), (w0, w1)
# 5. derive_status across the window, MST
E, T = date(2026, 9, 23), time(16, 0)
assert et.derive_status(E, T, 'MST', _now=d(2026, 9, 23, 22, 29)) == 'upcoming'
assert et.derive_status(E, T, 'MST', _now=d(2026, 9, 23, 22, 30)) == 'live'
assert et.derive_status(E, T, 'MST', _now=d(2026, 9, 24, 1, 59)) == 'live'
assert et.derive_status(E, T, 'MST', _now=d(2026, 9, 24, 2, 1)) == 'complete'
assert et.derive_status(E, T, 'MST', _now=d(2026, 9, 22, 12, 0)) == 'upcoming'
assert et.derive_status(E, T, 'MST', _now=d(2026, 9, 25, 12, 0)) == 'complete'
# 6. the local-date boundary: 23:00 MST on the 23rd is 06:00Z on the 24th and still 'today' locally
assert et.derive_status(date(2026, 9, 23), time(23, 0), 'MST', _now=d(2026, 9, 24, 5, 0)) == 'upcoming'
assert et.derive_status(date(2026, 9, 23), time(23, 0), 'MST', _now=d(2026, 9, 24, 6, 5)) == 'live'
# 7. ET event (Maine Oct 6 19:00): live from 22:30Z, not from 23:30Z as a fixed -5 would say
assert et.derive_status(date(2026, 10, 6), time(19, 0), 'America/New_York', _now=d(2026, 10, 6, 22, 31)) == 'live'
assert et.derive_status(date(2026, 10, 6), time(19, 0), 'America/New_York', _now=d(2026, 10, 6, 22, 29)) == 'upcoming'
# 8. no start_time -> live all day locally; no date -> complete
assert et.derive_status(date(2026, 9, 23), None, 'MST', _now=d(2026, 9, 23, 12, 0)) == 'live'
assert et.derive_status(None, T, 'MST') == 'complete'
# 9. unknown zone falls back to UTC, does not raise
assert et.start_iso(date(2026, 9, 23), time(16, 0), 'Mars/Olympus') == '2026-09-23T16:00:00+00:00'
assert et.zone(None).utcoffset(None) == timedelta(0)
# 10. is_postgres_zone: what create_event may store
assert et.is_postgres_zone('America/Phoenix') and et.is_postgres_zone('MST') and et.is_postgres_zone('America/New_York')
assert not et.is_postgres_zone('ET') and not et.is_postgres_zone('CT') and not et.is_postgres_zone('Mars/Olympus')
# 11. the debate_routes wrapper, once patched, must agree with the module for every DB value in use
try:
    import debate_routes
    for tzv in ('MST', 'America/Denver', 'America/New_York', 'America/Chicago'):
        for now in (d(2026, 9, 23, 22, 29), d(2026, 9, 23, 22, 31), d(2026, 9, 24, 2, 1)):
            assert debate_routes._derive_status(E, None, T, tzv, _now=now) == et.derive_status(E, T, tzv, _now=now), (tzv, now)
    print('debate_routes._derive_status agrees with event_time for 4 zones x 3 instants')
except ImportError as e:
    print('debate_routes not importable here (%s) - module tests only' % e)
print('event_time: all assertions passed')
