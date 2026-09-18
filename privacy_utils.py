"""S13 privacy (policy: /privacy).

ip_hash(raw)     How an IP address is stored anywhere except short-lived rate limiting: SHA-256 of
                 "<SECRET_KEY>:<first X-Forwarded-For entry>", hex -- the scheme anon_verify_counts
                 already uses. One-way; the same address always gives the same value.
run_retention()  1. Rolls each complete UTC day of page views, once, into page_view_daily (page without
                    its query string, app or web, views, distinct sessions) and
                    page_view_referrers_daily (referring site, views): totals only, nothing that
                    identifies a visitor. Written once per day, never overwritten; kept permanently.
                 2. Deletes page views and API usage from before the 90-day window, on whole UTC days
                    so a day is never half-counted, and anonymous-check counters older than 90 days.
                 3. Deletes disputes two years after they were reviewed, contact email included.
                 Per-key monthly API counts stay in api_monthly_usage. Called by railway_verdicts.py
                 as its own job_runs stage ("retention"). Returns the number of rows deleted.
"""
import hashlib
import os

RETENTION_DAYS = 90
_UTC_MIDNIGHT = "date_trunc('day', NOW() AT TIME ZONE 'UTC')"
TODAY_TZ = f"({_UTC_MIDNIGHT} AT TIME ZONE 'UTC')"
CUTOFF_TZ = f"(({_UTC_MIDNIGHT} - INTERVAL '{RETENTION_DAYS} days') AT TIME ZONE 'UTC')"
CUTOFF_NAIVE = f"({_UTC_MIDNIGHT} - INTERVAL '{RETENTION_DAYS} days')"
_URL_HOST = "'^[A-Za-z][A-Za-z0-9+.-]*://([^/:?#]+)'"

AGGREGATE_SQL = [
    ("page_view_daily", f"""
        INSERT INTO page_view_daily (day, path, is_mobile_app, views, sessions)
        SELECT (created_at AT TIME ZONE 'UTC')::date, split_part(path, '?', 1), COALESCE(is_mobile_app, false),
               COUNT(*), COUNT(DISTINCT session_id)
          FROM page_views
         WHERE created_at < {TODAY_TZ}
         GROUP BY 1, 2, 3
        ON CONFLICT (day, path, is_mobile_app) DO NOTHING"""),
    ("page_view_referrers_daily", f"""
        INSERT INTO page_view_referrers_daily (day, referrer_host, views)
        SELECT (created_at AT TIME ZONE 'UTC')::date, lower(substring(referrer from {_URL_HOST})), COUNT(*)
          FROM page_views
         WHERE created_at < {TODAY_TZ} AND substring(referrer from {_URL_HOST}) IS NOT NULL
         GROUP BY 1, 2
        ON CONFLICT (day, referrer_host) DO NOTHING"""),
]
RETENTION_SQL = [
    ("page_views", f"DELETE FROM page_views WHERE created_at < {CUTOFF_TZ}"),
    ("api_usage", f"DELETE FROM api_usage WHERE created_at < {CUTOFF_NAIVE}"),
    ("anon_verify_counts", f"DELETE FROM anon_verify_counts WHERE day < (NOW() AT TIME ZONE 'UTC')::date - {RETENTION_DAYS}"),
    ("outlet_disputes", "DELETE FROM outlet_disputes WHERE reviewed_at < (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 years'"),
]


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


def run_retention(conn=None):
    own = conn is None
    conn = conn or _connect()
    total = 0
    try:
        cur = conn.cursor()
        for table, sql in AGGREGATE_SQL:
            cur.execute(sql)
            print(f"[retention] {table}: {cur.rowcount} day totals written")
        for table, sql in RETENTION_SQL:
            cur.execute(sql)
            print(f"[retention] {table}: {cur.rowcount} rows from before the {RETENTION_DAYS}-day window deleted")
            total += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()
    return total
