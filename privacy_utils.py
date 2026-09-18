"""S13 privacy (policy: /privacy).

ip_hash(raw)     How an IP address is stored anywhere except short-lived rate limiting: SHA-256 of
                 "<SECRET_KEY>:<first X-Forwarded-For entry>", hex -- the scheme anon_verify_counts
                 already uses. One-way; the same address always gives the same value.
run_retention()  Deletes page views, extension lookups and API usage rows older than 90 days, and
                 anonymous-check counters older than 90 days. Called by railway_verdicts.py as its own
                 job_runs stage ("retention"). Returns the number of rows deleted.
"""
import hashlib
import os

RETENTION_DAYS = 90


def ip_hash(raw):
    first = (raw or '').split(',')[0].strip()
    if not first or first in ('-', 'unknown'):
        return None
    salt = os.environ.get('SECRET_KEY')
    if not salt:
        if os.environ.get('RAILWAY_ENVIRONMENT'):
            raise RuntimeError('SECRET_KEY env var is required in production')
        salt = 'dev-insecure-salt'
    return hashlib.sha256(f"{salt}:{first}".encode()).hexdigest()


def _connect():
    import psycopg2
    if os.environ.get('DATABASE_URL'):
        return psycopg2.connect(os.environ['DATABASE_URL'])
    return psycopg2.connect(dbname=os.environ.get('DB_NAME', 'railway'), user=os.environ.get('DB_USER', 'postgres'),
                            password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'],
                            port=os.environ.get('DB_PORT', '5432'))


RETENTION_SQL = [
    ("page_views", f"DELETE FROM page_views WHERE created_at < NOW() - INTERVAL '{RETENTION_DAYS} days'"),
    ("api_source_hit_log", f"DELETE FROM api_source_hit_log WHERE hit_at < NOW() - INTERVAL '{RETENTION_DAYS} days'"),
    ("api_usage", f"DELETE FROM api_usage WHERE created_at < NOW() - INTERVAL '{RETENTION_DAYS} days'"),
    ("anon_verify_counts", f"DELETE FROM anon_verify_counts WHERE day < CURRENT_DATE - {RETENTION_DAYS}"),
]


def run_retention(conn=None):
    own = conn is None
    conn = conn or _connect()
    total = 0
    try:
        cur = conn.cursor()
        for table, sql in RETENTION_SQL:
            cur.execute(sql)
            print(f"[retention] {table}: {cur.rowcount} rows older than {RETENTION_DAYS} days deleted")
            total += cur.rowcount
        conn.commit()
    finally:
        if own:
            conn.close()
    return total
