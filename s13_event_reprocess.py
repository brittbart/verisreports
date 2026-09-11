#!/usr/bin/env python3
"""s13_event_reprocess.py -- re-run extraction + verdicts for one NON-PUBLIC debate event
under the current pipeline (closed-turn extraction, attributed-prompt verdicts).

  venv/bin/python3 s13_event_reprocess.py --event-id 21          # dry run: plan + counts, no writes
  venv/bin/python3 s13_event_reprocess.py --event-id 21 --apply  # backup, reset, re-extract, re-verify

Refuses to run on a public event. Backs up the event's debate claims to a table and every
utterance's processed_at to logs/ before writing. Prints rollback commands at the end.
"""
import argparse, os, sys, json, time, datetime
import psycopg2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ap = argparse.ArgumentParser()
ap.add_argument('--event-id', type=int, required=True)
ap.add_argument('--apply', action='store_true')
ap.add_argument('--max-verify-rounds', type=int, default=20)
args = ap.parse_args()
eid = args.event_id
stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

def db():
    return psycopg2.connect(dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
                            password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ['DB_PORT'])

def counts(cur):
    cur.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE processed_at IS NOT NULL)
                   FROM speaker_utterances su JOIN speakers s ON s.id = su.speaker_id
                   WHERE su.event_id = %s AND s.speaker_type IN ('politician','official')""", (eid,))
    u_total, u_proc = cur.fetchone()
    cur.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE verdict IS NOT NULL)
                   FROM claims WHERE event_id = %s AND claim_origin = 'debate_claim'""", (eid,))
    c_total, c_verd = cur.fetchone()
    return u_total, u_proc, c_total, c_verd

conn = db(); cur = conn.cursor()
cur.execute("SELECT slug, is_public FROM events WHERE id = %s", (eid,))
row = cur.fetchone()
if not row: sys.exit(f"ABORT: event {eid} not found")
slug, is_public = row
if is_public: sys.exit(f"ABORT: event {eid} ({slug}) is is_public=TRUE -- reprocess only non-public events")
u_total, u_proc, c_total, c_verd = counts(cur)
print(f"event {eid} ({slug}) is_public={is_public}")
print(f"  candidate utterances: {u_total} ({u_proc} processed)   debate claims: {c_total} ({c_verd} with verdict)")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'claims'")
claim_cols = {r[0] for r in cur.fetchall()}
reset_cols = [c for c in ('verdict', 'verdict_summary', 'confidence_score', 'full_analysis', 'sources_used',
                          'sources_structured', 'last_checked', 'correction_note') if c in claim_cols]
plan = ", ".join(f"{c}=DEFAULT" for c in reset_cols)   # DEFAULT respects NOT NULL columns (e.g. sources_structured jsonb)
if 'verification_attempts' in claim_cols: plan += ", verification_attempts=0"
if 'verdict_status' in claim_cols: plan += ", verdict_status='provisional'"
backup_table = f"claims_event{eid}_backup_{stamp}"
backup_json = f"logs/event{eid}_processed_at_backup_{stamp}.json"
print(f"\nPLAN:\n  1. CREATE TABLE {backup_table} AS SELECT * FROM claims WHERE event_id={eid} AND claim_origin='debate_claim'")
print(f"  2. write processed_at for all {u_total} candidate utterances to {backup_json}")
print(f"  3. UPDATE speaker_utterances SET processed_at=NULL (candidate speakers only)")
print(f"  4. UPDATE claims SET {plan}  (existing {c_total} debate claims -> re-verified under the corrected prompt)")
print(f"  5. extract_debate_claims.run_extraction({eid})  -- closed turns, APPLY")
print(f"  6. verdict_engine.verify_debate_claims_sync({eid}, limit=10) until it returns 0")
if not args.apply:
    print("\nDRY RUN -- nothing written. Re-run with --apply."); sys.exit(0)

os.makedirs('logs', exist_ok=True)
cur.execute(f"CREATE TABLE {backup_table} AS SELECT * FROM claims WHERE event_id = %s AND claim_origin = 'debate_claim'", (eid,))
cur.execute("""SELECT su.id, su.processed_at FROM speaker_utterances su JOIN speakers s ON s.id = su.speaker_id
               WHERE su.event_id = %s AND s.speaker_type IN ('politician','official')""", (eid,))
json.dump({str(i): (p.isoformat() if p else None) for i, p in cur.fetchall()}, open(backup_json, 'w'))
cur.execute("""UPDATE speaker_utterances su SET processed_at = NULL FROM speakers s
               WHERE s.id = su.speaker_id AND su.event_id = %s AND s.speaker_type IN ('politician','official')""", (eid,))
print(f"\n[1-3] backup table {backup_table}, {backup_json}, processed_at reset on {cur.rowcount} rows")
cur.execute(f"UPDATE claims SET {plan} WHERE event_id = %s AND claim_origin = 'debate_claim'", (eid,))
print(f"[4] verdict fields reset on {cur.rowcount} existing claims")
conn.commit(); cur.close(); conn.close()

print("\n[5] extraction on closed turns")
from extract_debate_claims import run_extraction
run_extraction(eid, limit=None, dry_run=False)

print("\n[6] verdicts (attributed prompt)")
from verdict_engine import verify_debate_claims_sync
for rnd in range(args.max_verify_rounds):
    n = verify_debate_claims_sync(eid, limit=10)
    print(f"  round {rnd+1}: {n} verified")
    if not n: break
    time.sleep(2)

conn = db(); cur = conn.cursor()
u_total, u_proc, c_total, c_verd = counts(cur)
print(f"\nRESULT: utterances {u_proc}/{u_total} processed; debate claims {c_total}, {c_verd} with verdict")
cur.execute("""SELECT s.name, c.verdict, COUNT(*) FROM claims c LEFT JOIN speakers s ON s.id = c.speaker_id
               WHERE c.event_id = %s AND c.claim_origin = 'debate_claim' GROUP BY 1, 2 ORDER BY 1, 3 DESC""", (eid,))
for r in cur.fetchall(): print(f"  {r[0]:18s} {str(r[1]):16s} {r[2]}")
print(f"\nROLLBACK if needed:\n  DELETE FROM claims WHERE event_id = {eid} AND claim_origin = 'debate_claim';\n  INSERT INTO claims SELECT * FROM {backup_table};\n  processed_at values are in {backup_json}")
