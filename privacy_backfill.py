#!/usr/bin/env python3
"""S13 one-time privacy backfill. Run AFTER the IP-hashing code is live, with production's SECRET_KEY:
    railway run --service verisreports python3 privacy_backfill.py            (dry run)
    railway run --service verisreports python3 privacy_backfill.py --apply
1. Deletes page views, extension lookups and API usage older than 90 days, and anonymous-check
   counters older than 90 days (the same rules as privacy_utils.run_retention).
2. Hashes every raw IP left in page_views.ip, api_usage.ip, api_source_hit_log.ip,
   api_keys.last_used_ip and api_beta_requests.ip with the same sha256("<SECRET_KEY>:<first
   X-Forwarded-For entry>") as privacy_utils.ip_hash, computed in SQL (checked against Python first).
   Values that are already 64-character hashes are skipped, so it is safe to re-run.
3. Drops api_host_enforcement_evidence (the retired Session 5 evidence log; no code uses it).
One transaction. Irreversible by design: raw IPs cannot be recovered after --apply.
"""
import os, sys
from privacy_utils import ip_hash, _connect, RETENTION_DAYS

salt = os.environ.get('SECRET_KEY')
if not salt:
    sys.exit('REFUSE: SECRET_KEY is not set -- run this with: railway run --service verisreports python3 privacy_backfill.py')
HASHED = "^[0-9a-f]{64}$"
FIRST = "btrim(split_part({col}, ',', 1))"
EXPR = ("CASE WHEN " + FIRST + " IN ('', '-', 'unknown') THEN NULL "
        "ELSE encode(sha256(convert_to(%s || ':' || " + FIRST + ", 'UTF8')), 'hex') END")
TARGETS = [('page_views', 'ip'), ('api_usage', 'ip'), ('api_source_hit_log', 'ip'),
           ('api_keys', 'last_used_ip'), ('api_beta_requests', 'ip')]
DELETES = [('page_views', f"created_at < NOW() - INTERVAL '{RETENTION_DAYS} days'"),
           ('api_source_hit_log', f"hit_at < NOW() - INTERVAL '{RETENTION_DAYS} days'"),
           ('api_usage', f"created_at < NOW() - INTERVAL '{RETENTION_DAYS} days'"),
           ('anon_verify_counts', f"day < CURRENT_DATE - {RETENTION_DAYS}")]

conn = _connect()
cur = conn.cursor()
# self-check: the SQL hash must equal the Python hash the live code now writes
for sample in ('203.0.113.7', '198.51.100.2, 10.0.0.1', '2001:db8::1'):
    cur.execute("SELECT " + EXPR.format(col='%s'), (sample, salt, sample))
    got = cur.fetchone()[0]
    if got != ip_hash(sample):
        sys.exit(f'REFUSE: SQL hash differs from Python ip_hash for {sample!r}; nothing written')
print('hash self-check: SQL matches privacy_utils.ip_hash')
for table, where in DELETES:
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")
    print(f'delete  {table:22s} {cur.fetchone()[0]:>7} rows older than {RETENTION_DAYS} days')
for table, col in TARGETS:
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} !~ %s", (HASHED,))
    print(f'hash    {table + "." + col:32s} {cur.fetchone()[0]:>7} raw values')
cur.execute("SELECT to_regclass('public.api_host_enforcement_evidence')")
exists = cur.fetchone()[0] is not None
if exists:
    cur.execute("SELECT COUNT(*) FROM api_host_enforcement_evidence")
    print(f'drop    api_host_enforcement_evidence ({cur.fetchone()[0]} rows)')
if '--apply' not in sys.argv:
    conn.rollback(); print('DRY RUN -- nothing written'); sys.exit(0)
for table, where in DELETES:
    cur.execute(f"DELETE FROM {table} WHERE {where}")
    print(f'deleted {table}: {cur.rowcount}')
for table, col in TARGETS:
    cur.execute(f"UPDATE {table} SET {col} = " + EXPR.format(col=col) + f" WHERE {col} IS NOT NULL AND {col} !~ %s",
                (salt, HASHED))
    print(f'hashed  {table}.{col}: {cur.rowcount}')
if exists:
    cur.execute("DROP TABLE api_host_enforcement_evidence")
    print('dropped api_host_enforcement_evidence')
for table, col in TARGETS:
    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} !~ %s", (HASHED,))
    left = cur.fetchone()[0]
    if left:
        conn.rollback(); sys.exit(f'REFUSE: {left} raw values still in {table}.{col}; rolled back, nothing written')
conn.commit()
print('committed. Raw IP addresses are gone from all five columns; rate-limit buckets keep raw IPs for ~10 minutes.')
