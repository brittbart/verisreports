#!/usr/bin/env python3
"""reset_event24.py - clean deletion of all captured data for event 24,
in dependency order, inside one transaction. Roster and event row untouched.
Read-only until the final commit; any exception anywhere rolls everything back."""
import sys
from debate_stream import get_db_conn
EVENT_ID = 24
STEPS = [
    ("claim_topics",
     "DELETE FROM claim_topics WHERE claim_id IN "
     "(SELECT id FROM claims WHERE utterance_id IN "
     "(SELECT id FROM speaker_utterances WHERE event_id = %s))"),
    ("outlet_disputes",
     "DELETE FROM outlet_disputes WHERE claim_id IN "
     "(SELECT id FROM claims WHERE utterance_id IN "
     "(SELECT id FROM speaker_utterances WHERE event_id = %s))"),
    ("triangulation_results",
     "DELETE FROM triangulation_results WHERE claim_id IN "
     "(SELECT id FROM claims WHERE utterance_id IN "
     "(SELECT id FROM speaker_utterances WHERE event_id = %s))"),
    ("verdict_history",
     "DELETE FROM verdict_history WHERE claim_id IN "
     "(SELECT id FROM claims WHERE utterance_id IN "
     "(SELECT id FROM speaker_utterances WHERE event_id = %s))"),
    ("api_claims",
     "DELETE FROM api_claims WHERE claim_id IN "
     "(SELECT id FROM claims WHERE utterance_id IN "
     "(SELECT id FROM speaker_utterances WHERE event_id = %s))"),
    ("api_debate_claims",
     "DELETE FROM api_debate_claims WHERE claim_id IN "
     "(SELECT id FROM claims WHERE utterance_id IN "
     "(SELECT id FROM speaker_utterances WHERE event_id = %s)) "
     "OR utterance_id IN (SELECT id FROM speaker_utterances WHERE event_id = %s)"),
    ("claims",
     "DELETE FROM claims WHERE utterance_id IN "
     "(SELECT id FROM speaker_utterances WHERE event_id = %s)"),
    ("filtered_utterances",
     "DELETE FROM filtered_utterances WHERE utterance_id IN "
     "(SELECT id FROM speaker_utterances WHERE event_id = %s)"),
    ("speaker_utterances",
     "DELETE FROM speaker_utterances WHERE event_id = %s"),
]
def main():
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT count(*) FROM speaker_utterances WHERE event_id = %s", (EVENT_ID,))
        utt_before = cur.fetchone()[0]
        print("utterances before: %d" % utt_before)
        results = []
        for name, sql in STEPS:
            params = (EVENT_ID, EVENT_ID) if sql.count("%s") == 2 else (EVENT_ID,)
            cur.execute(sql, params)
            results.append((name, cur.rowcount))
            print("  deleted from %s: %d row(s)" % (name, cur.rowcount))
        cur.execute("SELECT count(*) FROM speaker_utterances WHERE event_id = %s", (EVENT_ID,))
        utt_after = cur.fetchone()[0]
        if utt_after != 0:
            conn.rollback()
            print("ABORT: speaker_utterances for event %d is %d after delete, expected 0 -- rolled back"
                  % (EVENT_ID, utt_after))
            return 1
        speaker_utterances_deleted = dict(results)["speaker_utterances"]
        if speaker_utterances_deleted != utt_before:
            conn.rollback()
            print("ABORT: deleted %d speaker_utterances but %d existed before -- rolled back"
                  % (speaker_utterances_deleted, utt_before))
            return 1
        cur.execute("SELECT id, is_public FROM events WHERE id = %s", (EVENT_ID,))
        ev = cur.fetchone()
        if ev is None or ev[1] is not False:
            conn.rollback()
            print("ABORT: event %d missing or is_public not FALSE -- rolled back" % EVENT_ID)
            return 1
        conn.commit()
        print("\nCOMMITTED. Event %d reset: 0 utterances, 0 claims, all dependent rows cleared."
              % EVENT_ID)
        print("Event row and roster untouched (is_public still FALSE, confirmed).")
        return 0
    except Exception as e:
        conn.rollback()
        print("ABORT: exception during delete, rolled back everything: %s" % e)
        return 1
    finally:
        cur.close()
        conn.close()
if __name__ == "__main__":
    sys.exit(main())
