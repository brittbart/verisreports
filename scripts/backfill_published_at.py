"""
Backfill published_at for articles where it's NULL but we have verdicts.

Importable: scheduler.py uses backfill_recent_articles() at end of each cycle.
Standalone CLI: python3 scripts/backfill_published_at.py --dry-run | --apply
"""
import os, sys, time, re, json
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import psycopg2

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def parse_date(s):
    """Parse various date formats. Returns datetime or None."""
    if not s:
        return None
    s = s.strip()
    formats = [
        '%Y-%m-%dT%H:%M:%S.%fZ',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
    ]
    s_clean = s.replace('+00:00', 'Z') if s.endswith('+00:00') else s
    for fmt in formats:
        try:
            return datetime.strptime(s_clean, fmt)
        except ValueError:
            continue
    m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', s)
    if m:
        try:
            return datetime.strptime(m.group(1), '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            pass
    return None


def extract_published_at_from_html(html):
    """Try multiple strategies to find publication date in HTML.
    Returns (datetime, method_str) or (None, None)."""
    soup = BeautifulSoup(html, 'html.parser')
    m = soup.find('meta', property='article:published_time')
    if m and m.get('content'):
        d = parse_date(m['content'])
        if d: return d, 'meta:article:published_time'
    m = soup.find('meta', attrs={'name': 'pubdate'})
    if m and m.get('content'):
        d = parse_date(m['content'])
        if d: return d, 'meta:pubdate'
    m = soup.find('meta', attrs={'name': 'DC.date.issued'})
    if m and m.get('content'):
        d = parse_date(m['content'])
        if d: return d, 'meta:dc.date.issued'
    t = soup.find('time', attrs={'datetime': True})
    if t:
        d = parse_date(t['datetime'])
        if d: return d, 'time-tag'
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '{}')
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    dp = item.get('datePublished')
                    if dp:
                        d = parse_date(dp)
                        if d: return d, 'json-ld:datePublished'
        except (json.JSONDecodeError, AttributeError):
            continue
    return None, None


def fetch_published_at(url, timeout=(8, 15)):
    """Fetch URL and extract published_at. Returns (datetime, method) or (None, reason_str)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        published_at, method = extract_published_at_from_html(r.text)
        if not published_at:
            return None, "no date in HTML"
        return published_at, method
    except requests.RequestException as e:
        return None, type(e).__name__


def _get_db_conn():
    """Connect using env vars. Used by both standalone and scheduler-imported paths."""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        dbname=os.getenv('DB_NAME')
    )


def backfill_recent_articles(hours=2, limit=50, logger=None):
    """Backfill published_at for articles ingested in the last N hours that have NULL published_at.
    Designed to run end-of-cycle in scheduler.py.
    Returns dict with success/failure counts. Returns True/False for scheduler step compatibility.
    """
    log_fn = logger.info if logger else print
    try:
        conn = _get_db_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, source_name, url
            FROM articles
            WHERE published_at IS NULL
              AND fetched_at >= NOW() - INTERVAL '%s hours'
            ORDER BY fetched_at DESC
            LIMIT %s
        """, (hours, limit))
        articles = cur.fetchall()
        if not articles:
            log_fn(f"  [backfill] no articles to backfill (NULL published_at in last {hours}h)")
            cur.close()
            conn.close()
            return True

        log_fn(f"  [backfill] checking {len(articles)} articles for missing published_at...")
        success = 0
        failed = 0
        for article_id, source_name, url in articles:
            published_at, method_or_reason = fetch_published_at(url)
            if published_at:
                cur.execute(
                    "UPDATE articles SET published_at = %s WHERE id = %s AND published_at IS NULL",
                    (published_at, article_id)
                )
                conn.commit()
                log_fn(f"  [backfill] {source_name} #{article_id}: {published_at} (via {method_or_reason})")
                success += 1
            else:
                # Don't log every failure - too noisy. Just count.
                failed += 1
            time.sleep(1)  # polite delay between fetches

        log_fn(f"  [backfill] complete: {success} backfilled, {failed} failed")
        cur.close()
        conn.close()
        return True
    except Exception as e:
        log_fn(f"  [backfill] ERROR: {type(e).__name__}: {e}")
        return False


# ── Standalone CLI mode ──
if __name__ == "__main__":
    DRY_RUN = '--dry-run' in sys.argv
    APPLY = '--apply' in sys.argv

    if not DRY_RUN and not APPLY:
        print("USAGE: python3 scripts/backfill_published_at.py --dry-run | --apply")
        sys.exit(1)

    conn = _get_db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT a.id, a.source_name, a.url
        FROM articles a
        JOIN claims c ON c.article_id = a.id
        WHERE a.published_at IS NULL
          AND c.verdict IS NOT NULL
          AND c.claim_origin = 'outlet_claim'
        ORDER BY a.source_name, a.id
    """)
    articles = cur.fetchall()
    print(f"Found {len(articles)} articles to backfill")
    print(f"Mode: {'DRY-RUN' if DRY_RUN else 'APPLY'}")
    print("-" * 80)

    success = 0
    failed = 0
    for article_id, source_name, url in articles:
        published_at, reason = fetch_published_at(url)
        if not published_at:
            print(f"  [{article_id}] {source_name}: {reason} -- SKIP")
            failed += 1
            time.sleep(1)
            continue
        if APPLY:
            cur.execute(
                "UPDATE articles SET published_at = %s WHERE id = %s AND published_at IS NULL",
                (published_at, article_id)
            )
            conn.commit()
            print(f"  [{article_id}] {source_name}: SET to {published_at} (via {reason})")
        else:
            print(f"  [{article_id}] {source_name}: would set to {published_at} (via {reason})")
        success += 1
        time.sleep(1)

    print()
    print("-" * 80)
    print(f"Summary: {success} successful, {failed} failed, {len(articles)} total")
    cur.close()
    conn.close()
