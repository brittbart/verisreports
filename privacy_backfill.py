#!/usr/bin/env python3
"""S13 one-time privacy backfill (v2). Run AFTER migrate_s13_pv_totals.py --apply, with production's
SECRET_KEY:
    railway run --service verisreports python3 privacy_backfill.py            (dry run)
    railway run --service verisreports python3 privacy_backfill.py --apply
1. Rolls every complete UTC day of page views into page_view_daily / page_view_referrers_daily
   (privacy_utils.AGGREGATE_SQL): totals only, kept permanently.
2. Deletes page views and API usage from before the 90-day window (whole UTC days) and old
   anonymous-check counters (privacy_utils.RETENTION_SQL) -- after checking that the day totals
   account for every page view being deleted.
3. Hashes every raw IP left in page_views.ip, api_usage.ip, api_keys.last_used_ip and
   api_beta_requests.ip with privacy_utils.ip_hash's scheme, computed in SQL (checked against Python first).
4. Drops api_source_hit_log (extension lookups are not kept at all; nothing writes this table) and
   api_host_enforcement_evidence (the retired Session 5 evidence log; nothing uses it).
One transaction: rolls back if the totals don't add up or any raw IP remains. Irreversible once applied.
"""
import os, sys
from privacy_utils import ip_hash, _connect, AGGREGATE_SQL, RETENTION_SQL, CUTOFF_TZ, TODAY_TZ, RETENTION_DAYS

salt = os.environ.get('SECRET_KEY')
if not salt:
    sys.exit('REFUSE: SECRET_KEY is not set -- run this with: railway run --service verisreports python3 privacy_backfill.py')
HASHED = "^[0-9a-f]{64}$"
FIRST = "btrim(split_part({col}, ',', 1))"
EXPR = ("CASE WHEN " + FIRST + " IN ('', '-', 'unknown') THEN NULL "
        "ELSE encode(sha256(convert_to(%s || ':' || " + FIRST + ", 'UTF8')), 'hex') END")
TARGETS = [('page_views', 'ip'), ('api_usage', 'ip'), ('api_keys', 'last_used_ip'), ('api_beta_requests', 'ip')]
DROPS = ['api_source_hit_log', 'api_host_enforcement_evidence']

conn = _connect()
cur = conn.cursor()
cur.execute("SELECT to_regclass('public.page_view_daily'), to_regclass('public.page_view_referrers_daily')")
if None in cur.fetchone():
    sys.exit('REFUSE: the daily totals tables do not exist -- run migrate_s13_pv_totals.py --apply first')
for sample in ('203.0.113.7', '198.51.100.2, 10.0.0.1', '2001:db8::1'):
    cur.execute("SELECT " + EXPR.format(col='%s'), (sample, salt, sample))
    if cur.fetchone()[0] != ip_hash(sample):
        sys.exit(f'REFUSE: SQL hash differs from Python ip_hash for {sample!r}; nothing written')
print('hash self-check: SQL matches privacy_utils.ip_hash')
cur.execute(f"SELECT COUNT(DISTINCT (created_at AT TIME ZONE 'UTC')::date), COUNT(*) FROM page_views WHERE created_at < {TODAY_TZ}")
days, complete = cur.fetchone()
print(f'totals  page views on {days} complete days ({complete} rows) rolled into daily totals')
cur.execute(f"SELECT COUNT(*) FROM page_views WHERE created_at < {CUTOFF_TZ}")
old_pv = cur.fetchone()[0]
for table, sql in RETENTION_SQL:
    cur.execute(sql.replace('DELETE FROM', 'SELECT COUNT(*) FROM', 1))
    print(f'delete  {table:22s} {cur.fetchone()[0]:>7} rows from before the {RETENTION_DAYS}-day window')
for table, col in TARGETS:
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} !~ %s", (HASHED,))
    print(f'hash    {table + "." + col:32s} {cur.fetchone()[0]:>7} raw values (before deletes)')
drops = []
for t in DROPS:
    cur.execute("SELECT to_regclass(%s)", (f'public.{t}',))
    if cur.fetchone()[0] is not None:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f'drop    {t} ({cur.fetchone()[0]} rows)')
        drops.append(t)
if '--apply' not in sys.argv:
    conn.rollback(); print('DRY RUN -- nothing written'); sys.exit(0)
for table, sql in AGGREGATE_SQL:
    cur.execute(sql)
    print(f'totals  {table}: {cur.rowcount} day rows written')
cur.execute(f"SELECT COALESCE(SUM(views), 0) FROM page_view_daily WHERE day < ({CUTOFF_TZ} AT TIME ZONE 'UTC')::date")
kept = cur.fetchone()[0]
if kept != old_pv:
    conn.rollback(); sys.exit(f'REFUSE: day totals before the window add up to {kept}, but {old_pv} page views would be deleted; rolled back, nothing written')
print(f'check   day totals account for all {old_pv} page views being deleted')
for table, sql in RETENTION_SQL:
    cur.execute(sql)
    print(f'deleted {table}: {cur.rowcount}')
for table, col in TARGETS:
    cur.execute(f"UPDATE {table} SET {col} = " + EXPR.format(col=col) + f" WHERE {col} IS NOT NULL AND {col} !~ %s",
                (salt, HASHED))
    print(f'hashed  {table}.{col}: {cur.rowcount}')
for t in drops:
    cur.execute(f"DROP TABLE {t}")
    print(f'dropped {t}')
for table, col in TARGETS:
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} !~ %s", (HASHED,))
    left = cur.fetchone()[0]
    if left:
        conn.rollback(); sys.exit(f'REFUSE: {left} raw values still in {table}.{col}; rolled back, nothing written')
conn.commit()
print('committed. No raw IP addresses remain; extension lookups are not stored; daily totals keep traffic history.')
