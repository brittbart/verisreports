#!/usr/bin/env python3
"""
fix_google_news_source_name.py

Fixes the malformed source_name leak from Google News RSS feeds. v1.6 methodology
requires source_name be the outlet's hostname; this cleans rows that captured the
RSS query string instead.

Usage:
    python3 scripts/fix_google_news_source_name.py --audit
    python3 scripts/fix_google_news_source_name.py --apply
"""

import os
import re
import sys
import argparse
import psycopg2
from psycopg2.extras import RealDictCursor

# Captures site:HOST or site:HOST/path — strips the path so hostname comes out clean.
GOOGLE_NEWS_LEAK_RE = re.compile(
    r'"?site:([a-zA-Z0-9.\-]+)(?:/[^\s"]*)?"?\s*-\s*Google\s*News',
    re.IGNORECASE,
)

SUSPICIOUS_PATTERNS = [
    re.compile(r'^https?://', re.IGNORECASE),
    re.compile(r'\bhl=', re.IGNORECASE),
    re.compile(r'\bgl=', re.IGNORECASE),
    re.compile(r'\bceid=', re.IGNORECASE),
    re.compile(r'site:', re.IGNORECASE),
    re.compile(r'Google\s*News', re.IGNORECASE),
    re.compile(r'/rss/'),
    re.compile(r'/feed/'),
]


def connect():
    return psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', 5432),
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        dbname=os.environ['DB_NAME'],
    )


def find_google_news_leaks(conn):
    sql = """
        SELECT source_name, COUNT(*) AS row_count
        FROM articles
        WHERE source_name ~* 'site:.*Google.*News'
        GROUP BY source_name
        ORDER BY row_count DESC
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        return cur.fetchall()


def find_other_suspicious(conn):
    suspicious = []
    sql = "SELECT DISTINCT source_name FROM articles WHERE source_name IS NOT NULL"
    with conn.cursor() as cur:
        cur.execute(sql)
        for (name,) in cur.fetchall():
            if not name:
                continue
            if GOOGLE_NEWS_LEAK_RE.search(name):
                continue
            for pat in SUSPICIOUS_PATTERNS:
                if pat.search(name):
                    suspicious.append(name)
                    break
    return suspicious


def extract_clean_hostname(malformed_source):
    m = GOOGLE_NEWS_LEAK_RE.search(malformed_source)
    if m:
        return m.group(1).lower().lstrip('.').strip()
    return None


def apply_cleanup(conn, dry_run=True):
    leaks = find_google_news_leaks(conn)
    if not leaks:
        print("No Google News source_name leaks found.")
        return 0

    print(f"Found {len(leaks)} distinct malformed source_names:")
    total_rows_affected = 0
    updates = []
    for row in leaks:
        bad = row['source_name']
        count = row['row_count']
        clean = extract_clean_hostname(bad)
        if not clean:
            print(f"  [SKIP] Could not extract hostname from: {bad!r}  ({count} rows)")
            continue
        print(f"  {bad!r}  -->  {clean!r}  ({count} rows)")
        updates.append((bad, clean, count))
        total_rows_affected += count

    print(f"\nTotal rows that would be updated: {total_rows_affected}")

    if dry_run:
        print("(dry-run -- no changes written.)")
        return total_rows_affected

    print("\nApplying updates...")
    with conn.cursor() as cur:
        for bad, clean, count in updates:
            cur.execute("SELECT COUNT(*) FROM articles WHERE source_name = %s", (clean,))
            existing = cur.fetchone()[0]
            if existing:
                print(f"  Note: {clean!r} already has {existing} rows -- these will merge.")
            cur.execute(
                "UPDATE articles SET source_name = %s WHERE source_name = %s",
                (clean, bad),
            )
            print(f"  Updated {cur.rowcount} rows: {bad!r} --> {clean!r}")
    conn.commit()
    print(f"\nCommitted {total_rows_affected} updates.")
    return total_rows_affected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', action='store_true', help='Show malformed rows without writing')
    parser.add_argument('--apply', action='store_true', help='Apply the cleanup')
    args = parser.parse_args()

    if not args.audit and not args.apply:
        print("Specify --audit (read-only) or --apply (write).")
        sys.exit(1)

    conn = connect()

    print("=== Google News source_name leaks ===")
    apply_cleanup(conn, dry_run=not args.apply)

    print("\n=== Other suspicious source_name values ===")
    others = find_other_suspicious(conn)
    if not others:
        print("None found.")
    else:
        print(f"Found {len(others)} other suspicious names -- review and clean manually:")
        for name in others[:30]:
            print(f"  {name!r}")
        if len(others) > 30:
            print(f"  ... and {len(others) - 30} more")

    print("\n=== Reminder ===")
    print("This script cleans the DB. The ingestion-side bug must also be fixed in")
    print("fetch_articles.py to prevent recurrence.")

    conn.close()


if __name__ == '__main__':
    main()
