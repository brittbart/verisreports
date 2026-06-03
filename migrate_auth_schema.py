"""
migrate_auth_schema.py
Verum Signal — Auth/subscription schema migration v2
Run: railway run python3 migrate_auth_schema.py
"""

import os
import sys
import psycopg2

def get_conn():
    try:
        return psycopg2.connect(
            host=os.environ.get('DB_HOST'),
            port=os.environ.get('DB_PORT', 5432),
            dbname=os.environ.get('DB_NAME'),
            user=os.environ.get('DB_USER'),
            password=os.environ.get('DB_PASSWORD'),
        )
    except Exception as e:
        print(f"[ERROR] DB connection failed: {e}")
        sys.exit(1)

STEPS = [
    (
        "Add users.stripe_customer_id",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT DEFAULT NULL;",
        "SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='stripe_customer_id'"
    ),
    (
        "Add users.updated_at",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;",
        "SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='updated_at'"
    ),
    (
        "Index users.stripe_customer_id",
        "CREATE INDEX IF NOT EXISTS idx_users_stripe_customer_id ON users (stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;",
        "SELECT 1 FROM pg_indexes WHERE indexname='idx_users_stripe_customer_id'"
    ),
    (
        "Create magic_link_tokens",
        """CREATE TABLE IF NOT EXISTS magic_link_tokens (
            id          BIGSERIAL PRIMARY KEY,
            token       TEXT NOT NULL UNIQUE,
            email       TEXT NOT NULL,
            expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,
            used_at     TIMESTAMP WITH TIME ZONE DEFAULT NULL,
            created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        );""",
        "SELECT 1 FROM information_schema.tables WHERE table_name='magic_link_tokens'"
    ),
    (
        "Index magic_link_tokens.token",
        "CREATE INDEX IF NOT EXISTS idx_mlt_token ON magic_link_tokens (token);",
        "SELECT 1 FROM pg_indexes WHERE indexname='idx_mlt_token'"
    ),
    (
        "Index magic_link_tokens.expires_at",
        "CREATE INDEX IF NOT EXISTS idx_mlt_expires_at ON magic_link_tokens (expires_at);",
        "SELECT 1 FROM pg_indexes WHERE indexname='idx_mlt_expires_at'"
    ),
    (
        "Create subscriptions",
        """CREATE TABLE IF NOT EXISTS subscriptions (
            id                      BIGSERIAL PRIMARY KEY,
            user_id                 BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product                 TEXT NOT NULL CHECK (product IN ('consumer', 'api')),
            tier                    TEXT NOT NULL CHECK (tier IN ('free', 'pro', 'scale')),
            status                  TEXT NOT NULL DEFAULT 'active'
                                        CHECK (status IN ('active', 'canceled', 'past_due', 'trialing', 'incomplete')),
            stripe_subscription_id  TEXT DEFAULT NULL,
            quota_used_this_month   INTEGER NOT NULL DEFAULT 0,
            quota_reset_at          TIMESTAMP WITH TIME ZONE DEFAULT NULL,
            created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, product)
        );""",
        "SELECT 1 FROM information_schema.tables WHERE table_name='subscriptions'"
    ),
    (
        "Index subscriptions (user_id, product)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_product ON subscriptions (user_id, product);",
        "SELECT 1 FROM pg_indexes WHERE indexname='idx_subscriptions_user_product'"
    ),
    (
        "Index subscriptions.stripe_subscription_id",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_sub_id ON subscriptions (stripe_subscription_id) WHERE stripe_subscription_id IS NOT NULL;",
        "SELECT 1 FROM pg_indexes WHERE indexname='idx_subscriptions_stripe_sub_id'"
    ),
    (
        "Add api_keys.user_id",
        "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS user_id BIGINT DEFAULT NULL REFERENCES users(id) ON DELETE SET NULL;",
        "SELECT 1 FROM information_schema.columns WHERE table_name='api_keys' AND column_name='user_id'"
    ),
    (
        "Index api_keys.user_id",
        "CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys (user_id) WHERE user_id IS NOT NULL;",
        "SELECT 1 FROM pg_indexes WHERE indexname='idx_api_keys_user_id'"
    ),
]

def verify(cur):
    checks = [
        ("users.stripe_customer_id",   "SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='stripe_customer_id'"),
        ("users.updated_at",           "SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='updated_at'"),
        ("magic_link_tokens table",    "SELECT 1 FROM information_schema.tables WHERE table_name='magic_link_tokens'"),
        ("subscriptions table",        "SELECT 1 FROM information_schema.tables WHERE table_name='subscriptions'"),
        ("api_keys.user_id",           "SELECT 1 FROM information_schema.columns WHERE table_name='api_keys' AND column_name='user_id'"),
        ("api_keys.user_email intact", "SELECT 1 FROM information_schema.columns WHERE table_name='api_keys' AND column_name='user_email'"),
        ("users.external_id intact",   "SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='external_id'"),
        ("users.tier intact",          "SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='tier'"),
    ]
    print("\n── Verification ──")
    all_ok = True
    for label, sql in checks:
        cur.execute(sql)
        ok = cur.fetchone() is not None
        print(f"  {'[OK]' if ok else '[MISSING]'}  {label}")
        if not ok:
            all_ok = False
    return all_ok

def run():
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    print("=== Verum Signal auth schema migration v2 ===\n")
    try:
        for label, sql, check_sql in STEPS:
            cur.execute(check_sql)
            if cur.fetchone():
                print(f"  [skip]  {label}")
                continue
            print(f"  [run]   {label}")
            cur.execute(sql)
        conn.commit()
        print("\n[OK] All steps committed.")
        ok = verify(cur)
        if not ok:
            print("\n[WARN] Verification failed — review above.")
            sys.exit(1)
        else:
            print("\n[OK] Migration complete and verified.")
    except Exception as e:
        conn.rollback()
        print(f"\n[FAIL] Rolled back: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    run()
