#!/usr/bin/env python3
"""set_event21_listed.py - set event 21 (AZ Corp Commission debate, Sept 9)
is_listed=TRUE while leaving is_public and capture_enabled untouched
(expected FALSE for both, verified before writing). This is the actual
resolution to tonight's original request: the event reappears on the
website and app without publishing its claims or enabling automated
capture, built on the is_listed/capture_enabled separation.
Verifies event 21's identity (id AND slug) before writing, and refuses to
touch anything if is_public or capture_enabled are not both FALSE, since
that would mean some other change happened that this script doesn't know
about. Read-only unless --apply."""
import sys
from debate_stream import get_db_conn
EVENT_ID = 21
EXPECTED_SLUG = "arizona-corp-comm-2026-general"
def main():
    apply = "--apply" in sys.argv
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, slug, event_name, is_listed, is_public, capture_enabled FROM events WHERE id = %s",
                     (EVENT_ID,))
        row = cur.fetchone()
        if row is None:
            sys.exit("ABORT: event %d not found" % EVENT_ID)
        eid, slug, name, is_listed, is_public, capture_enabled = row
        print("Found: id=%d slug=%s name=%r is_listed=%s is_public=%s capture_enabled=%s"
              % (eid, slug, name, is_listed, is_public, capture_enabled))
        if slug != EXPECTED_SLUG:
            sys.exit("ABORT: slug is %r, expected %r -- refusing to touch the wrong event"
                      % (slug, EXPECTED_SLUG))
        if is_public is not False:
            sys.exit("ABORT: is_public is %s, expected False -- refusing, re-verify state before proceeding" % is_public)
        if capture_enabled is not False:
            sys.exit("ABORT: capture_enabled is %s, expected False -- refusing, re-verify state before proceeding" % capture_enabled)
        if is_listed is True:
            print("Already is_listed=TRUE -- nothing to do.")
            return 0
        if not apply:
            print("\nDRY RUN - would set is_listed=TRUE (is_public and capture_enabled stay FALSE). Re-run with --apply")
            return 0
        cur.execute("UPDATE events SET is_listed = TRUE WHERE id = %s", (EVENT_ID,))
        if cur.rowcount != 1:
            conn.rollback()
            sys.exit("ABORT: UPDATE affected %d rows, expected 1 -- rolled back" % cur.rowcount)
        cur.execute("SELECT id, slug, is_listed, is_public, capture_enabled FROM events WHERE id = %s", (EVENT_ID,))
        verify = cur.fetchone()
        if verify[2] is not True or verify[3] is not False or verify[4] is not False:
            conn.rollback()
            sys.exit("ABORT: post-update state is_listed=%s is_public=%s capture_enabled=%s, expected True/False/False -- rolled back"
                      % (verify[2], verify[3], verify[4]))
        conn.commit()
        print("\nCOMMITTED. Event %d (%s): is_listed=TRUE, is_public=FALSE, capture_enabled=FALSE." % (EVENT_ID, slug))
        print("Rollback if needed: UPDATE events SET is_listed = FALSE WHERE id = %d;" % EVENT_ID)
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
