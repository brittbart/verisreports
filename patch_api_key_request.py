"""
patch_api_key_request.py
Verum Signal — Wire /v1/keys/request to create users + free-tier subscriptions row.

What this does:
  1. Fixes monthly_quota from 1000 to 100 (matches api.html free tier spec).
  2. After api_keys insert succeeds: upserts users row by email, inserts
     free-tier subscriptions row for product='api'.
  3. Sets api_keys.user_id FK to the users.id.
  4. All existing behavior (key generation, hash, prefix, response) unchanged.

Run: python3 patch_api_key_request.py
"""

import sys
import shutil
from datetime import datetime

TARGET = 'api_public.py'
BACKUP = f'api_public.py.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# ── Anchor — the existing insert + commit block, appears exactly once ─────────
OLD = '''        cur.execute(
            "INSERT INTO api_keys (user_email, key_hash, key_prefix, name, tier, monthly_quota, rate_limit_per_minute, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())",
            (email, key_hash, key_prefix, name, "free", 1000, 10))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"api_key": raw_key, "prefix": key_prefix, "tier": "free", "monthly_quota": 1000, "rate_limit_per_minute": 10, "message": "Store this key safely — it will not be shown again.", "docs": "https://verumsignal.com/developers", "email": email}), 201'''

# ── Replacement — inserts users + subscriptions rows, fixes quota to 100 ──────
NEW = '''        # Insert api_keys row
        # monthly_quota=100 matches api.html free tier spec (was incorrectly 1000)
        cur.execute(
            "INSERT INTO api_keys (user_email, key_hash, key_prefix, name, tier, monthly_quota, rate_limit_per_minute, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) RETURNING id",
            (email, key_hash, key_prefix, name, "free", 100, 10))
        api_key_id = cur.fetchone()[0]

        # Upsert users row — create if new, touch last_seen_at if returning
        cur.execute("""
            INSERT INTO users (email, email_verified, updated_at, last_seen_at)
            VALUES (%s, FALSE, NOW(), NOW())
            ON CONFLICT (email) DO UPDATE
                SET last_seen_at = NOW(),
                    updated_at = NOW()
            RETURNING id
        """, (email,))
        user_id = cur.fetchone()[0]

        # Link api_keys.user_id to users.id
        cur.execute(
            "UPDATE api_keys SET user_id = %s WHERE id = %s",
            (user_id, api_key_id))

        # Insert free-tier API subscription row (idempotent — ignore if exists)
        # quota_reset_at: first of next month
        from datetime import date
        today = date.today()
        if today.month == 12:
            reset = date(today.year + 1, 1, 1)
        else:
            reset = date(today.year, today.month + 1, 1)

        cur.execute("""
            INSERT INTO subscriptions
                (user_id, product, tier, status, quota_used_this_month, quota_reset_at)
            VALUES (%s, 'api', 'free', 'active', 0, %s)
            ON CONFLICT (user_id, product) DO NOTHING
        """, (user_id, reset))

        conn.commit(); cur.close(); conn.close()
        return jsonify({"api_key": raw_key, "prefix": key_prefix, "tier": "free", "monthly_quota": 100, "rate_limit_per_minute": 10, "message": "Store this key safely — it will not be shown again.", "docs": "https://verumsignal.com/developers", "email": email}), 201'''


def run():
    with open(TARGET, 'r') as f:
        content = f.read()

    count = content.count(OLD)
    if count == 0:
        print('[FAIL] Anchor not found — patch cannot be applied.')
        sys.exit(1)
    if count > 1:
        print(f'[FAIL] Anchor found {count} times — ambiguous. Aborting.')
        sys.exit(1)

    print('[OK]   Anchor found exactly once.')

    shutil.copy2(TARGET, BACKUP)
    print(f'[OK]   Backup written to {BACKUP}')

    new_content = content.replace(OLD, NEW)

    if new_content == content:
        print('[FAIL] Replacement produced no change — aborting.')
        sys.exit(1)

    with open(TARGET, 'w') as f:
        f.write(new_content)

    print('[OK]   Patch applied.')

    with open(TARGET, 'r') as f:
        verify = f.read()

    if OLD in verify:
        print('[FAIL] Old anchor still present after patch.')
        sys.exit(1)

    if 'free-tier API subscription row' not in verify:
        print('[FAIL] New content not found after patch.')
        sys.exit(1)

    print('[OK]   Patch verified.')
    print('\nNext: python3 -c "from api_public import api_public; print(\'import OK\')"')


if __name__ == '__main__':
    run()
