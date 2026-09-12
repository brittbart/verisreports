#!/usr/bin/env python3
"""context_check.py - measure speaker_event_context exclusivity against the candidates' own enrolment transcripts.
For each speaker on the event, every exclusive_keyword is tested against (a) the OTHER candidates' cluster text - any hit
is a FAIL (the guard would act on the wrong speaker) - and (b) the speaker's own cluster text - no hit is a WARN (the
keyword is unsupported by evidence). Clusters come from the offset map (json + cluster per clip => that clip's speaker).
Read-only. Exit 1 on any FAIL.
  venv/bin/python3 context_check.py --event-id 27 --manifest s11_enroll_manifest_iowa.json --map s11_offset_map_iowa.json"""
import argparse, json, os, sys, psycopg2
HERE = os.path.dirname(os.path.abspath(__file__))
def cluster_text(jpath, cluster):
    d = json.load(open(os.path.join(HERE, jpath)))
    return ' '.join(e['value'] for m in d['monologues'] if m['speaker'] == cluster for e in m['elements'] if e.get('type') == 'text').lower()
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--event-id', type=int, required=True); ap.add_argument('--manifest', required=True); ap.add_argument('--map', required=True)
    a = ap.parse_args()
    man = json.load(open(os.path.join(HERE, a.manifest))); mp = json.load(open(os.path.join(HERE, a.map)))
    clip_spk = {c['clip']: (c['speaker_id'], c['name']) for c in man['clips']}
    text = {}
    for e in mp:
        sid, name = clip_spk[e['clip']]
        text[sid] = text.get(sid, '') + ' ' + cluster_text(e['json'], e['cluster'])
    conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'], connect_timeout=10)
    cur = conn.cursor(); cur.execute("SELECT speaker_id, roles, exclusive_keywords, generated_by FROM speaker_event_context WHERE event_id = %s ORDER BY speaker_id", (a.event_id,))
    rows = cur.fetchall(); fails = 0
    for sid, roles, kws, gen in rows:
        name = next((n for s, n in clip_spk.values() if s == sid), str(sid))
        print(f'\n== {name} ({sid}) generated_by={gen} roles={roles}')
        own = text.get(sid, ''); others = {s: t for s, t in text.items() if s != sid}
        for kw in kws:
            k = kw.lower(); hit_others = [s for s, t in others.items() if k in t]; hit_own = k in own
            if hit_others: fails += 1; print(f'  FAIL {kw!r}: said by other speaker(s) {hit_others}')
            elif not hit_own: print(f'  WARN {kw!r}: not found in {name}\'s own transcripts (unsupported)')
            else: print(f'  ok   {kw!r}')
    print(f'\n{"FAIL" if fails else "PASS"}: {fails} non-exclusive keyword(s) across {len(rows)} speaker(s)')
    sys.exit(1 if fails else 0)
if __name__ == '__main__':
    main()
