#!/usr/bin/env python3
"""revert_event21_public.py - set event 21 (AZ Corp Commission debate,
Sept 9) back to is_public=FALSE. Mirrors flip_event21_public.py's exact
identity-verification pattern (id AND slug both checked before writing)
rather than generalizing that already-tested script under time pressure.

2026-09-06: reverting per Opus's investigation. veris-stream (a live
Railway service, confirmed Online) calls get_live_event_id(), which goes
true at is_public=TRUE + event date within 1 day + now within
[start-45min, start+3h]. Left TRUE, it starts its own capture of event 21
at 17:15 MST on 2026-09-09 alongside the planned manual start -- the same
concurrent-process corruption that hit event 24 earlier this session,
except during the actual live debate instead of a rehearsal.

Verifies event 21's identity (id AND slug) before writing, so a stale id
or a renumbered event can never be touched by mistake. Read-only unless
--apply."""
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
        if is_public is False:
            print("Already is_public=FALSE -- nothing to do.")
            return 0
        if not apply:
            print("\nDRY RUN - would set is_public=FALSE. Re-run with --apply")
            return 0
        cur.execute("UPDATE events SET is_public = FALSE WHERE id = %s", (EVENT_ID,))
        if cur.rowcount != 1:
            conn.rollback()
            sys.exit("ABORT: UPDATE affected %d rows, expected 1 -- rolled back" % cur.rowcount)
        cur.execute("SELECT id, slug, is_public FROM events WHERE id = %s", (EVENT_ID,))
        verify = cur.fetchone()
        if verify[2] is not False:
            conn.rollback()
            sys.exit("ABORT: post-update check shows is_public=%s, not FALSE -- rolled back" % verify[2])
        conn.commit()
        print("\nCOMMITTED. Event %d (%s) is now is_public=FALSE." % (EVENT_ID, slug))
        print("Rollback if needed: UPDATE events SET is_public = TRUE WHERE id = %d;" % EVENT_ID)
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
