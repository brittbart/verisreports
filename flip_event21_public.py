#!/usr/bin/env python3
"""flip_event21_public.py - make event 21 (AZ Corp Commission debate, Sept 9)
publicly visible on the website and Android app. is_public=FALSE has been a
deliberate guardrail since S12 began, specifically so nothing could grab or
expose it before the pipeline was validated. The dress rehearsal has since
passed (clean voice-ID attribution across the full debate, shape check ok,
20 correctly-attributed claims) and this is an explicit, deliberate request
to unblock it now that the pipeline behind it is validated.
Verifies event 21's identity (id AND slug) before writing, so a stale id or
a renumbered event can never get flipped by mistake. Read-only unless --apply."""
import sys
from debate_stream import get_db_conn
EVENT_ID = 21
EXPECTED_SLUG = "arizona-corp-comm-2026-general"
def main():
    apply = "--apply" in sys.argv
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, slug, event_name, event_date, is_public FROM events WHERE id = %s",
                     (EVENT_ID,))
        row = cur.fetchone()
        if row is None:
            sys.exit("ABORT: event %d not found" % EVENT_ID)
        eid, slug, name, date, is_public = row
        print("Found: id=%d slug=%s name=%r date=%s is_public=%s" % (eid, slug, name, date, is_public))
        if slug != EXPECTED_SLUG:
            sys.exit("ABORT: slug is %r, expected %r -- refusing to touch the wrong event"
                      % (slug, EXPECTED_SLUG))
        if is_public is True:
            print("Already is_public=TRUE -- nothing to do.")
            return 0
        if not apply:
            print("\nDRY RUN - would set is_public=TRUE. Re-run with --apply")
            return 0
        cur.execute("UPDATE events SET is_public = TRUE WHERE id = %s", (EVENT_ID,))
        if cur.rowcount != 1:
            conn.rollback()
            sys.exit("ABORT: UPDATE affected %d rows, expected 1 -- rolled back" % cur.rowcount)
        cur.execute("SELECT id, slug, is_public FROM events WHERE id = %s", (EVENT_ID,))
        verify = cur.fetchone()
        if verify[2] is not True:
            conn.rollback()
            sys.exit("ABORT: post-update check shows is_public=%s, not TRUE -- rolled back" % verify[2])
        conn.commit()
        print("\nCOMMITTED. Event %d (%s) is now is_public=TRUE." % (EVENT_ID, slug))
        print("Rollback if needed: UPDATE events SET is_public = FALSE WHERE id = %d;" % EVENT_ID)
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
