#!/usr/bin/env python3
"""
Migration: Add mobile app tables to Verum Signal schema
Tables: users, user_devices, user_follows_outlet, user_follows_event,
        user_follows_speaker, user_saved_reports, user_notification_prefs,
        notification_log

Run from ~/projects/veris with venv activated:
    python3 migrate_mobile_schema.py

Safe to run multiple times — all DDL uses IF NOT EXISTS.
"""

import os
import sys
import psycopg2
from datetime import datetime

# ── connection (mirrors api.py:31 fallback chain) ──────────────────────────
def get_db():
    if os.environ.get('DATABASE_URL'):
        return psycopg2.connect(dsn=os.environ['DATABASE_URL'])
    return psycopg2.connect(
        dbname=os.environ.get('DB_NAME', 'railway'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD'),
        host=os.environ.get('DB_HOST', 'shinkansen.proxy.rlwy.net'),
        port=int(os.environ.get('DB_PORT', 35370)),
    )

# ── migrations ─────────────────────────────────────────────────────────────
MIGRATIONS = []

def migration(fn):
    MIGRATIONS.append(fn)
    return fn

@migration
def create_users(cur):
    """Primary user table — mirrors Clerk source of truth locally."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          BIGSERIAL PRIMARY KEY,
            external_id TEXT NOT NULL UNIQUE,
            email       TEXT NOT NULL UNIQUE,
            email_verified BOOLEAN NOT NULL DEFAULT FALSE,
            tier        TEXT NOT NULL DEFAULT 'free'
                        CHECK (tier IN ('free', 'pro_monthly', 'pro_annual', 'complimentary')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ,
            deleted_at  TIMESTAMPTZ
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_external_id ON users(external_id);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    """)
    print("  ✓ users")

@migration
def create_user_devices(cur):
    """Per-device records for push notification routing."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_devices (
            id           BIGSERIAL PRIMARY KEY,
            user_id      BIGINT REFERENCES users(id) ON DELETE CASCADE,
            device_uuid  TEXT NOT NULL,
            push_token   TEXT,
            platform     TEXT NOT NULL CHECK (platform IN ('ios', 'android')),
            app_version  TEXT NOT NULL,
            os_version   TEXT,
            device_model TEXT,
            timezone     TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            push_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE(user_id, device_uuid)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_devices_user ON user_devices(user_id);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_devices_push_token
            ON user_devices(push_token)
            WHERE push_token IS NOT NULL;
    """)
    print("  ✓ user_devices")

@migration
def create_user_follows_outlet(cur):
    """Outlet follows — drives push notifications and personalization."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_follows_outlet (
            id            BIGSERIAL PRIMARY KEY,
            user_id       BIGINT REFERENCES users(id) ON DELETE CASCADE,
            outlet_domain TEXT NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, outlet_domain)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_follows_outlet_user
            ON user_follows_outlet(user_id);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_follows_outlet_domain
            ON user_follows_outlet(outlet_domain);
    """)
    print("  ✓ user_follows_outlet")

@migration
def create_user_follows_event(cur):
    """Event follows — drives debate start/end push notifications."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_follows_event (
            id         BIGSERIAL PRIMARY KEY,
            user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
            event_id   INTEGER REFERENCES events(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, event_id)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_follows_event_user
            ON user_follows_event(user_id);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_follows_event_event
            ON user_follows_event(event_id);
    """)
    print("  ✓ user_follows_event")

@migration
def create_user_follows_speaker(cur):
    """Speaker follows — infrastructure in v1, UI in v1.1."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_follows_speaker (
            id         BIGSERIAL PRIMARY KEY,
            user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
            speaker_id INTEGER REFERENCES speakers(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, speaker_id)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_follows_speaker_user
            ON user_follows_speaker(user_id);
    """)
    print("  ✓ user_follows_speaker")

@migration
def create_user_saved_reports(cur):
    """Saved articles/reports — synced across user devices."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_saved_reports (
            id         BIGSERIAL PRIMARY KEY,
            user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
            article_id INTEGER REFERENCES articles(id) ON DELETE CASCADE,
            saved_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(user_id, article_id)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_saved_user
            ON user_saved_reports(user_id, saved_at DESC);
    """)
    print("  ✓ user_saved_reports")

@migration
def create_user_notification_prefs(cur):
    """Per-user notification preferences with quiet hours."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_notification_prefs (
            user_id               BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            debate_start_alerts   BOOLEAN NOT NULL DEFAULT TRUE,
            debate_verdict_alerts BOOLEAN NOT NULL DEFAULT FALSE,
            daily_digest          BOOLEAN NOT NULL DEFAULT FALSE,
            outlet_score_alerts   BOOLEAN NOT NULL DEFAULT FALSE,
            quiet_hours_start     TIME DEFAULT '22:00',
            quiet_hours_end       TIME DEFAULT '07:00',
            timezone              TEXT DEFAULT 'America/Denver',
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    print("  ✓ user_notification_prefs")

@migration
def create_notification_log(cur):
    """Audit log of all push notifications sent — dedup and rate limiting."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id            BIGSERIAL PRIMARY KEY,
            user_id       BIGINT REFERENCES users(id) ON DELETE CASCADE,
            device_id     BIGINT REFERENCES user_devices(id) ON DELETE SET NULL,
            notification_type TEXT NOT NULL,
            payload       JSONB,
            sent_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            delivered     BOOLEAN,
            opened        BOOLEAN,
            related_event_id   INTEGER REFERENCES events(id) ON DELETE SET NULL,
            related_article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_notif_log_user
            ON notification_log(user_id, sent_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_notif_log_type
            ON notification_log(notification_type, sent_at DESC);
    """)
    print("  ✓ notification_log")

@migration
def add_vs_summary_to_articles(cur):
    """Add vs_summary column to articles for mobile report summaries."""
    # Check if column already exists before adding
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'articles'
          AND column_name = 'vs_summary';
    """)
    if cur.fetchone():
        print("  ✓ articles.vs_summary (already exists)")
        return
    cur.execute("""
        ALTER TABLE articles
            ADD COLUMN vs_summary TEXT,
            ADD COLUMN vs_summary_generated_at TIMESTAMPTZ;
    """)
    print("  ✓ articles.vs_summary + vs_summary_generated_at (added)")

# ── verify ─────────────────────────────────────────────────────────────────
EXPECTED_TABLES = [
    'users',
    'user_devices',
    'user_follows_outlet',
    'user_follows_event',
    'user_follows_speaker',
    'user_saved_reports',
    'user_notification_prefs',
    'notification_log',
]

def verify(cur):
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
        ORDER BY table_name;
    """, (EXPECTED_TABLES,))
    found = {row[0] for row in cur.fetchall()}
    missing = set(EXPECTED_TABLES) - found
    if missing:
        print(f"\n  ✗ Missing tables: {missing}")
        return False

    # Verify vs_summary column
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'articles'
          AND column_name = 'vs_summary';
    """)
    if not cur.fetchone():
        print("\n  ✗ articles.vs_summary column missing")
        return False

    print(f"\n  ✓ All {len(EXPECTED_TABLES)} tables present")
    print("  ✓ articles.vs_summary present")
    return True

# ── main ───────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"  Verum Signal — Mobile Schema Migration")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*60}\n")

    try:
        conn = get_db()
    except Exception as e:
        print(f"✗ DB connection failed: {e}")
        sys.exit(1)

    print("✓ Connected to database\n")
    print("Running migrations:")

    try:
        with conn:
            with conn.cursor() as cur:
                for fn in MIGRATIONS:
                    fn(cur)

        print("\nVerifying:")
        with conn.cursor() as cur:
            ok = verify(cur)

        if ok:
            print(f"\n{'='*60}")
            print("  Migration complete. Safe to deploy mobile_routes.py.")
            print(f"{'='*60}\n")
        else:
            print("\n✗ Verification failed — check output above.")
            sys.exit(1)

    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
