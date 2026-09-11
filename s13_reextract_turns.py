#!/usr/bin/env python3
"""s13_reextract_turns.py -- MEASUREMENT ONLY. Re-run an event's utterances through
COMPLETE speaker turns and the production extraction pipeline. Never inserts,
never marks processed_at. Compare the count against what live capture produced.

  venv/bin/python3 s13_reextract_turns.py --event-id 21            # turn stats + pre-filter only, no API
  venv/bin/python3 s13_reextract_turns.py --event-id 21 --call-api # + real extraction calls (costs API)
  --cap-chars N   also report/extract with turns flushed at N chars (production flush candidate)
"""
import argparse, os, sys, collections
import psycopg2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_debate_claims as X
X._log_filtered = lambda *a, **k: None   # production logs filtered turns to the DB; this run must write nothing

ap = argparse.ArgumentParser()
ap.add_argument('--event-id', type=int, required=True)
ap.add_argument('--call-api', action='store_true')
ap.add_argument('--cap-chars', type=int, default=0)
args = ap.parse_args()

conn = psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
                        password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'])
conn.set_session(readonly=True)
cur = conn.cursor()
# Same 12-column shape fetch_politician_utterances returns, but ALL rows (LEFT JOIN, no processed_at gate)
cur.execute("""
    SELECT su.id, su.utterance_text, su.utterance_order,
           su.speaker_id, COALESCE(s.name, '<unconfirmed>'), COALESCE(s.speaker_type, 'unknown'), s.party,
           e.event_name, e.event_date, e.slug,
           su.timestamp_seconds, su.attribution_uncertain
    FROM speaker_utterances su
    LEFT JOIN speakers s ON s.id = su.speaker_id
    JOIN events e ON e.id = su.event_id
    WHERE su.event_id = %s
    ORDER BY su.utterance_order ASC
""", (args.event_id,))
rows = cur.fetchall()
cur.execute("SELECT COUNT(*) FROM claims WHERE event_id = %s AND claim_origin = 'debate_claim'", (args.event_id,))
baseline = cur.fetchone()[0]
print(f"event {args.event_id}: {len(rows)} utterances, live capture produced {baseline} claims")

def cap_turns(turns, cap):
    if not cap: return turns
    out = []
    for t in turns:
        buf_t, buf_u, buf_r = [], [], []
        for r in t['member_rows']:
            if buf_t and sum(len(x) for x in buf_t) + len(r[1]) > cap:
                out.append(dict(t, text=' '.join(buf_t), first_uid=buf_u[0], all_uids=buf_u, member_rows=buf_r, row=buf_r[0]))
                buf_t, buf_u, buf_r = [], [], []
            buf_t.append(r[1]); buf_u.append(r[0]); buf_r.append(r)
        if buf_t:
            out.append(dict(t, text=' '.join(buf_t), first_uid=buf_u[0], all_uids=buf_u, member_rows=buf_r, row=buf_r[0]))
    return out

all_turns = X.group_utterances_into_turns(rows)
cand = [t for t in all_turns if t['row'][5] in ('politician', 'official') and t['speaker_id'] != X.GENERIC_MODERATOR_ID]
cand = cap_turns(cand, args.cap_chars)
lens = sorted(len(t['text']) for t in cand)
frags = sorted(len(t['all_uids']) for t in cand)
q = lambda a, p: a[min(len(a)-1, int(p*len(a)))] if a else 0
print(f"turns: {len(all_turns)} total, {len(cand)} candidate turns" + (f" (capped at {args.cap_chars} chars)" if args.cap_chars else ""))
print(f"  chars/turn  p25 {q(lens,.25)}  median {q(lens,.5)}  p75 {q(lens,.75)}  max {q(lens,.999)}")
print(f"  frags/turn  p25 {q(frags,.25)}  median {q(frags,.5)}  p75 {q(frags,.75)}  max {q(frags,.999)}")
by_spk = collections.Counter(t['speaker_name'] for t in cand)
print("  turns by speaker:", dict(by_spk))

pre_reasons = collections.Counter(); kept = []
for t in cand:
    skip, reason = X.pre_filter_utterance(t['text'], utterance_id=t['first_uid'], event_id=args.event_id,
                                          speaker_id=t['speaker_id'], is_debate=True, conn=None)
    if skip: pre_reasons[reason] += 1
    else: kept.append(t)
print(f"\npre-filter: {len(cand) - len(kept)} dropped, {len(kept)} would reach the model")
for r, n in pre_reasons.most_common(): print(f"  {n:4d}  {r}")

if not args.call_api:
    print("\n(no API calls made; add --call-api to run the real extractor on the kept turns)")
    sys.exit(0)

found = []; post_reasons = collections.Counter(); calls = 0
for i, t in enumerate(kept):
    ad = X.utterance_to_article_dict(t['row'], args.event_id)
    ad['content'] = t['text']
    try:
        claims = X.extract_claims_from_article(ad, raise_on_failure=True) or []
    except Exception as e:
        print(f"  [{i+1}/{len(kept)}] ERROR {e}"); continue
    calls += 1
    keep_c = []
    for c in claims:
        ex, why = X.post_filter_claim(c.get('claim_text', ''))
        if ex: post_reasons[why.split(':')[0]] += 1
        else: keep_c.append(c)
    found.extend((t['speaker_name'], c.get('claim_text', '')) for c in keep_c)
    print(f"  [{i+1}/{len(kept)}] {t['speaker_name']} ({len(t['all_uids'])} frags, {len(t['text'])} chars) -> {len(keep_c)} claim(s)")
    for c in keep_c: print(f"       + {c.get('claim_text','')[:110]}")

print(f"\nRESULT: {calls} API calls, {len(found)} claims after post-filter (live capture: {baseline})")
print("  by speaker:", dict(collections.Counter(s for s, _ in found)))
for r, n in post_reasons.most_common(): print(f"  post-filtered {n}: {r}")
print("\nNothing was written to the database.")
