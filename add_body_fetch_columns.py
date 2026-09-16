"""Adds articles.body_fetched_at / body_fetch_method (2026-09-16). Dry-run by default.
Rollback: ALTER TABLE articles DROP COLUMN body_fetched_at, DROP COLUMN body_fetch_method;"""
import sys
from load_to_database import get_connection
ddl = """ALTER TABLE articles ADD COLUMN IF NOT EXISTS body_fetched_at TIMESTAMPTZ,
                              ADD COLUMN IF NOT EXISTS body_fetch_method TEXT"""
print(ddl); print("rollback: ALTER TABLE articles DROP COLUMN body_fetched_at, DROP COLUMN body_fetch_method;")
if '--apply' not in sys.argv: print("DRY RUN"); sys.exit(0)
c = get_connection(); cur = c.cursor(); cur.execute(ddl); c.commit(); print("APPLIED"); c.close()
