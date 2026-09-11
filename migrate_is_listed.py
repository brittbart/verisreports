#!/usr/bin/env python3
"""migrate_is_listed.py - add events.is_listed and events.capture_enabled,
backfill is_listed = is_public, add the coherence CHECK constraint.
is_public keeps its exact existing meaning everywhere else (claims shown,
projected to api_debate_claims). is_listed controls visibility only.
capture_enabled controls automated-poller eligibility only, defaulting
FALSE for every row -- manual attended start is the agreed operational
path and no event should auto-capture without an explicit opt-in.
Reviewed and approved (with capture_enabled as the one design addition)
in IS_LISTED_PLAN_REVIEW_RESPONSE.docx, 2026-09-06.
Verifies current schema state before writing -- aborts cleanly if either
column already exists rather than risk a partial/duplicate migration.
Read-only unless --apply."""
import sys
from debate_stream import get_db_conn
def main():
    apply = "--apply" in sys.argv
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'events' AND column_name IN ('is_listed', 'capture_enabled')
        """)
        existing = [r[0] for r in cur.fetchall()]
        if existing:
            sys.exit("ABORT: column(s) already exist: %s -- re-verify schema state before proceeding" % existing)
        cur.execute("SELECT count(*), count(*) FILTER (WHERE is_public) FROM events")
        total, public_count = cur.fetchone()
        print("Current events table: %d rows, %d currently is_public=TRUE" % (total, public_count))
        print("Plan: add is_listed (backfill = is_public, so those %d rows become is_listed=TRUE too),"
              % public_count)
        print("      add capture_enabled (FALSE for all %d rows, no exceptions)," % total)
        print("      add CHECK (is_public = FALSE OR is_listed = TRUE).")
        if not apply:
            print("\nDRY RUN - no changes made. Re-run with --apply")
            return 0
        cur.execute("ALTER TABLE events ADD COLUMN is_listed boolean NOT NULL DEFAULT false")
        cur.execute("ALTER TABLE events ADD COLUMN capture_enabled boolean NOT NULL DEFAULT false")
        cur.execute("UPDATE events SET is_listed = is_public")
        cur.execute("""
            ALTER TABLE events ADD CONSTRAINT events_public_implies_listed
            CHECK (is_public = FALSE OR is_listed = TRUE)
        """)
        cur.execute("SELECT count(*) FILTER (WHERE is_listed), count(*) FILTER (WHERE capture_enabled) FROM events")
        listed_count, capture_count = cur.fetchone()
        if listed_count != public_count:
            conn.rollback()
            sys.exit("ABORT: is_listed count %d does not match pre-migration is_public count %d -- rolled back"
                      % (listed_count, public_count))
        if capture_count != 0:
            conn.rollback()
            sys.exit("ABORT: capture_enabled count is %d, expected 0 -- rolled back" % capture_count)
        conn.commit()
        print("\nCOMMITTED. is_listed backfilled to %d row(s) matching prior is_public. "
              "capture_enabled FALSE on all %d row(s). CHECK constraint added." % (listed_count, total))
    except SystemExit:
        raise
    except Exception as e:
        conn.rollback()
        sys.exit("ABORT: exception, rolled back: %s" % e)
    finally:
        cur.close()
        conn.close()
    return 0
if __name__ == "__main__":
    sys.exit(main())
