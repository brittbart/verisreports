"""speaker_divergence.py — read-only checks that the three speaker columns agree.

A debate claim's speaker lives in three places that must agree:
  claims.speaker_id            what the debate page renders (debate_routes)
  speaker_utterances.speaker_id the row the claim was extracted from; the
                                public API table is refreshed FROM this column
                                (railway_api_refresh.py, su.speaker_id)
  api_debate_claims.speaker_id  what api.verumsignal.com and the MCP serve

On 2026-09-14 sixteen rows had claims != utterances (events 11/16/19, 13 on
public pages), and a June 4 correction made to claims only never reached the
API. This module is the one query behind /status, pre_debate_check.py and
post_debate_check.py so the three surfaces cannot drift unnoticed again.

    from speaker_divergence import divergence
    d = divergence(cur, event_id)   # event_id optional

Every query is a SELECT; the module never writes.
"""

FK_SQL = """
    SELECT c.event_id, COUNT(*)
    FROM claims c
    JOIN speaker_utterances u ON u.id = c.utterance_id
    WHERE c.claim_origin = 'debate_claim'
      AND u.speaker_id IS NOT NULL
      AND c.speaker_id IS DISTINCT FROM u.speaker_id
    GROUP BY c.event_id ORDER BY c.event_id
"""

API_SQL = """
    SELECT a.event_id, COUNT(*)
    FROM api_debate_claims a
    JOIN claims c ON c.id = a.claim_id
    WHERE a.speaker_id IS DISTINCT FROM c.speaker_id
    GROUP BY a.event_id ORDER BY a.event_id
"""

DUP_SLOTS_SQL = """
    SELECT COUNT(*) - COUNT(DISTINCT utterance_order)
    FROM speaker_utterances WHERE event_id = %s
"""

DUP_TEXTS_SQL = """
    SELECT COUNT(*) FROM (
        SELECT claim_text FROM claims
        WHERE event_id = %s AND claim_origin = 'debate_claim'
        GROUP BY claim_text HAVING COUNT(DISTINCT speaker_id) > 1
    ) t
"""


def divergence(cur, event_id=None):
    """Return the divergence picture; per-event duplicate counts only when event_id is given.

    fk_by_event / api_by_event: {event_id: rows}; fk_total / api_total: sums.
    dup_slots: rows minus distinct utterance_order (double-write family).
    dup_texts: claim texts present under more than one speaker_id.
    """
    cur.execute(FK_SQL)
    fk = {int(e): int(n) for e, n in cur.fetchall() if e is not None}
    cur.execute(API_SQL)
    api = {int(e): int(n) for e, n in cur.fetchall() if e is not None}
    out = {
        'fk_by_event': fk, 'fk_total': sum(fk.values()),
        'api_by_event': api, 'api_total': sum(api.values()),
        'dup_slots': None, 'dup_texts': None,
    }
    if event_id is not None:
        cur.execute(DUP_SLOTS_SQL, (event_id,))
        out['dup_slots'] = int(cur.fetchone()[0] or 0)
        cur.execute(DUP_TEXTS_SQL, (event_id,))
        out['dup_texts'] = int(cur.fetchone()[0] or 0)
    return out


def summary(d):
    """One line for /status and logs, e.g. 'claims/utterances 0, API 0'."""
    parts = [f"claims/utterances {d['fk_total']}", f"API {d['api_total']}"]
    if d['fk_by_event']:
        parts.append("fk by event " + ", ".join(f"{e}:{n}" for e, n in sorted(d['fk_by_event'].items())))
    if d['api_by_event']:
        parts.append("api by event " + ", ".join(f"{e}:{n}" for e, n in sorted(d['api_by_event'].items())))
    return "; ".join(parts)


if __name__ == '__main__':
    import os
    import sys

    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()
    eid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    c = psycopg2.connect(
        dbname=os.getenv('DB_NAME', 'railway'), user=os.getenv('DB_USER', 'postgres'),
        password=os.environ['DB_PASSWORD'],
        host=os.getenv('DB_HOST', 'shinkansen.proxy.rlwy.net'),
        port=os.getenv('DB_PORT', '35370'))
    c.set_session(readonly=True, autocommit=True)
    d = divergence(c.cursor(), eid)
    print(summary(d))
    if eid is not None:
        print(f"event {eid}: dup_slots {d['dup_slots']}, dup_texts {d['dup_texts']}")
