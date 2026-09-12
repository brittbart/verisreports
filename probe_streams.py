#!/usr/bin/env python3
"""probe_streams.py - find and (with --pin) record the YouTube stream id for events whose stream_url is NULL.
For each such event (or --event-id N), run the host-specific ytsearch queries below, keep results whose live_status is
is_upcoming/is_live and whose scheduled date (release_timestamp, in the event's zone) equals event_date, and print them.
--pin writes stream_url only when exactly ONE candidate matches; anything else is printed for a human decision.
Read-only without --pin. Run daily from the first hour; stop once every October event has a URL.
  venv/bin/python3 probe_streams.py            # all NULL-stream debate events
  venv/bin/python3 probe_streams.py --event-id 27 --pin"""
import argparse, json, os, subprocess, sys, datetime
import psycopg2, event_time
QUERIES = {
    'iowa-senate-2026-general':      ['Iowa PBS Iowa Press Debates U.S. Senate Hinson Turek', 'Iowa PBS Senate debate October 7 2026'],
    'maine-senate-2026-general-1':   ['WGME Collins Jackson Senate debate', 'CBS 13 Maine Senate debate October 6 2026'],
    'michigan-senate-2026-general-1':['WOOD TV8 Debate Night in Michigan U.S. Senate El-Sayed Rogers', 'WOOD TV8 Senate debate October 8 2026'],
    'maine-senate-2026-general-2':   ['WMTW Collins Jackson Senate debate', 'WMTW Maine Senate debate October 8 2026'],
    'maine-senate-2026-general-3':   ['WABI Collins Jackson Senate debate', 'WAGM Maine Senate debate October 13 2026'],
    'maine-senate-2026-general-4':   ['NEWS CENTER Maine Collins Jackson Senate debate', 'Maine Public Senate debate October 15 2026'],
    'michigan-senate-2026-general-2':['WXYZ El-Sayed Rogers Senate debate', 'WXYZ Michigan Senate debate October 21 2026'],
}
def ytsearch(q, n=8):
    cmd = ['venv/bin/yt-dlp', '--skip-download', '--ignore-no-formats-error', '--print', '%(id)s\t%(live_status)s\t%(release_timestamp)s\t%(channel)s\t%(title).80s', f'ytsearch{n}:{q}']
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        p = line.split('\t')
        if len(p) == 5: out.append({'id': p[0], 'status': p[1], 'release': p[2], 'channel': p[3], 'title': p[4]})
    return out
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--event-id', type=int); ap.add_argument('--pin', action='store_true'); a = ap.parse_args()
    conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'], connect_timeout=10)
    cur = conn.cursor()
    cur.execute("SELECT id, slug, event_date, start_time, timezone FROM events WHERE event_type='debate' AND stream_url IS NULL AND event_date >= CURRENT_DATE" + (" AND id = %s" if a.event_id else "") + " ORDER BY event_date", (a.event_id,) if a.event_id else ())
    for eid, slug, d, t, tz in cur.fetchall():
        qs = QUERIES.get(slug)
        print(f"\n=== {eid} {slug} {d} {t} {tz}")
        if not qs: print('  no queries configured for this slug'); continue
        seen, cands = set(), []
        for q in qs:
            for r in ytsearch(q):
                if r['id'] in seen: continue
                seen.add(r['id'])
                if r['status'] not in ('is_upcoming', 'is_live'): continue
                sched = None
                if r['release'] not in ('NA', '', 'None'):
                    sched = datetime.datetime.fromtimestamp(int(float(r['release'])), tz=datetime.timezone.utc).astimezone(event_time.zone(tz))
                match = bool(sched and sched.date() == d)
                print(f"  {'MATCH ' if match else '      '}{r['id']} {r['status']:12s} {sched.isoformat() if sched else 'no schedule':25s} {r['channel'][:28]:28s} {r['title'][:60]}")
                if match: cands.append(r)
        if not seen: print('  (no upcoming/live results)')
        if a.pin:
            if len(cands) == 1:
                url = f"https://www.youtube.com/watch?v={cands[0]['id']}"
                cur.execute("UPDATE events SET stream_url = %s WHERE id = %s AND stream_url IS NULL", (url, eid)); conn.commit()
                print(f"  PINNED {url}  (rollback: UPDATE events SET stream_url = NULL WHERE id = {eid};)")
            else:
                print(f"  not pinned: {len(cands)} matching candidate(s)")
    conn.close()
if __name__ == '__main__':
    main()
