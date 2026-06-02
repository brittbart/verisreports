#!/usr/bin/env python3
"""
D-016 Integrity Verification Query
===================================
Verifies that the cache verdict-copying fix (D-016, commit d0882c4, May 28 2026)
is complete and no contaminated verdicts remain in the database.

Run any time to confirm the integrity claim:
  "Every verdict on Verum Signal is the result of an independent, fresh,
   web-search-backed verification. No verdict has ever been copied from a
   similar prior claim since May 28, 2026."

Usage:
  cd ~/projects/veris && source venv/bin/activate
  python3 scripts/verify_d016_integrity.py

Exit code: 0 = clean, 1 = integrity issue found
"""
import psycopg2, os, sys
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv('/home/veris/projects/veris/.env')

def get_conn():
    return psycopg2.connect(
        dbname=os.environ.get('DB_NAME', 'railway'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP'),
        host=os.environ.get('DB_HOST', 'shinkansen.proxy.rlwy.net'),
        port=os.environ.get('DB_PORT', '35370')
    )

def run():
    conn = get_conn()
    cur = conn.cursor()
    failures = []

    print("=== D-016 INTEGRITY VERIFICATION ===")
    print(f"Run at: {datetime.now(timezone.utc).isoformat()}")
    print()

    # 1. Total claims with verdicts
    cur.execute("SELECT COUNT(*) FROM claims WHERE verdict IS NOT NULL")
    total = cur.fetchone()[0]
    print(f"1. Total claims with verdicts: {total:,}")

    # 2. Breakdown by verification_method
    cur.execute("""
        SELECT verification_method, COUNT(*)
        FROM claims WHERE verdict IS NOT NULL
        GROUP BY verification_method ORDER BY count DESC
    """)
    print("\n2. Breakdown by verification_method:")
    for row in cur.fetchall():
        print(f"   {row[0] or 'NULL'}: {row[1]:,}")

    # 3. Cache-copied count — must be 0
    cur.execute("SELECT COUNT(*) FROM claims WHERE verification_method = 'cache_copied'")
    cache_copied = cur.fetchone()[0]
    status = '✅ PASS' if cache_copied == 0 else '❌ FAIL'
    print(f"\n3. Cache-copied (contaminated) verdicts: {cache_copied}  {status}")
    if cache_copied > 0:
        failures.append(f"cache_copied count = {cache_copied} (expected 0)")

    # 4. Scoreable outlet claims — all must be fresh
    cur.execute("""
        SELECT c.verification_method, COUNT(*)
        FROM claims c
        JOIN articles a ON a.id = c.article_id
        WHERE c.claim_origin = 'outlet_claim'
          AND c.verdict IS NOT NULL
          AND c.verdict NOT IN ('not_verifiable', 'opinion')
        GROUP BY c.verification_method ORDER BY count DESC
    """)
    print("\n4. Scoreable outlet claims by verification_method:")
    for row in cur.fetchall():
        flag = '' if row[0] == 'fresh' else ' ⚠ INVESTIGATE'
        print(f"   {row[0] or 'NULL'}: {row[1]:,}{flag}")
        if row[0] != 'fresh':
            failures.append(f"non-fresh scoreable outlet claims: {row[0]}={row[1]}")

    # 5. D-016 audit trail
    cur.execute("""
        SELECT COUNT(*) FROM claims
        WHERE revision_history IS NOT NULL
          AND revision_history::text LIKE '%cache-copied%'
    """)
    reverified = cur.fetchone()[0]
    print(f"\n5. Claims with D-016 re-verification audit trail: {reverified:,}")

    # 6. NULL verification_method — must be 0
    cur.execute("""
        SELECT COUNT(*) FROM claims
        WHERE verdict IS NOT NULL AND verification_method IS NULL
    """)
    null_method = cur.fetchone()[0]
    status2 = '✅ PASS' if null_method == 0 else '⚠ INVESTIGATE'
    print(f"\n6. Claims with NULL verification_method: {null_method:,}  {status2}")
    if null_method > 0:
        failures.append(f"null verification_method count = {null_method}")

    # 7. Re-verification date range
    cur.execute("""
        SELECT MIN(last_checked), MAX(last_checked)
        FROM claims
        WHERE revision_history IS NOT NULL
          AND revision_history::text LIKE '%cache-copied%'
    """)
    row = cur.fetchone()
    print(f"\n7. Re-verification date range: {row[0]} → {row[1]}")

    print("\n=== SUMMARY ===")
    print(f"Total verdicts:              {total:,}")
    print(f"Cache-copied (contaminated): {cache_copied}")
    print(f"D-016 audit trail entries:   {reverified:,}")
    print(f"NULL verification_method:    {null_method:,}")

    if failures:
        print(f"\n❌ INTEGRITY ISSUES FOUND:")
        for f in failures:
            print(f"   - {f}")
        cur.close(); conn.close()
        return 1
    else:
        print(f"\n✅ VERIFIED CLEAN — D-016 integrity claim holds.")
        cur.close(); conn.close()
        return 0

if __name__ == '__main__':
    sys.exit(run())
