#!/usr/bin/env python3
"""
speaker_context.py — Verum Signal speaker semantic consistency check.

Maps speaker_id → set of role/background keywords that ONLY this speaker
should be associated with. Used post-extraction to flag claims where content
doesn't match the attributed speaker.

Returns:
  - True if claim is consistent (or no context available for this speaker)
  - False + reason if claim contains keywords exclusive to a DIFFERENT speaker

Design:
  - Conservative: only flags when a claim contains keywords EXCLUSIVE to
    another speaker. Shared keywords (e.g. "Colorado", "governor") are ignored.
  - Does NOT block insertion — flags for post-debate review.
  - Context maps are per-event. Unknown events return no flags.
"""

import re

# ---------------------------------------------------------------------------
# Per-event speaker context maps
# ---------------------------------------------------------------------------
# Each entry: speaker_id → {'exclusive_keywords': [...], 'roles': [...]}
# exclusive_keywords: terms that ONLY this speaker should use in first person
# roles: titles/positions unique to this speaker

EVENT_SPEAKER_CONTEXT = {
    # Event 11 & 12: CO Governor Democratic Primary — Bennet vs Weiser
    11: {
        190: {  # Michael Bennet
            'roles': ['senator', 'superintendent'],
            'exclusive_keywords': [
                'denver public schools', 'dps', 'superintendent',
                'u.s. senate', 'united states senate', 'senate floor',
                'my time in the senate', 'as a senator',
                'largest school district',
            ],
        },
        191: {  # Phil Weiser
            'roles': ['attorney general', 'ag', 'solicitor general'],
            'exclusive_keywords': [
                'attorney general', 'ag office', 'solicitor general',
                'cu law', 'university of colorado law',
                'as attorney general', 'my time as ag',
                'eight years leading', 'eight years as',
                'consumer protection', 'antitrust',
            ],
        },
    },
    # Event 12 uses same candidates
    12: None,  # resolved below
}
# Event 12 = same candidates as Event 11
EVENT_SPEAKER_CONTEXT[12] = EVENT_SPEAKER_CONTEXT[11]



# ---------------------------------------------------------------------------
# DB-backed context loading (falls back to hardcoded dict)
# ---------------------------------------------------------------------------
_context_cache = {}

def _load_context(event_id):
    """Load speaker context from speaker_event_context table, fall back to hardcoded."""
    if event_id in _context_cache:
        return _context_cache[event_id]
    try:
        import psycopg2, os
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', '5432'), connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT speaker_id, exclusive_keywords, roles FROM speaker_event_context WHERE event_id = %s",
            (event_id,)
        )
        rows = cur.fetchall()
        cur.close(); conn.close()
        if rows:
            context = {}
            for sid, keywords, roles in rows:
                context[sid] = {
                    'exclusive_keywords': keywords if isinstance(keywords, list) else [],
                    'roles': roles if isinstance(roles, list) else [],
                }
            _context_cache[event_id] = context
            return context
    except Exception:
        pass
    # Fallback to hardcoded dict
    context = EVENT_SPEAKER_CONTEXT.get(event_id)
    _context_cache[event_id] = context
    return context


def check_speaker_consistency(claim_text: str, speaker_id: int, event_id: int) -> tuple:
    """
    Check if claim_text is semantically consistent with the attributed speaker.

    Returns:
        (is_consistent: bool, reason: str)
        - (True, '') if consistent or no context available
        - (False, 'contains exclusive keyword for speaker_id=X: ...')
    """
    context = _load_context(event_id)
    if not context:
        return True, ''

    tl = claim_text.lower()

    # Check if claim contains keywords exclusive to a DIFFERENT speaker
    for other_sid, other_ctx in context.items():
        if other_sid == speaker_id:
            continue  # skip self
        for kw in other_ctx.get('exclusive_keywords', []):
            if kw in tl:
                return False, f'contains keyword exclusive to speaker_id={other_sid}: "{kw}"'

    return True, ''


def check_first_person_role(claim_text: str, speaker_id: int, event_id: int) -> tuple:
    """
    Stronger check: if claim uses first-person language ("I", "my", "I've")
    combined with a role exclusive to another speaker, it's almost certainly
    misattributed.

    Returns:
        (is_suspicious: bool, reason: str)
    """
    context = _load_context(event_id)
    if not context:
        return False, ''

    tl = claim_text.lower()

    # First-person markers
    first_person = bool(re.search(r'\b(i\'ve|i have|i am|i was|my record|my time|my work|i served|i led|i ran)\b', tl))
    if not first_person:
        return False, ''

    for other_sid, other_ctx in context.items():
        if other_sid == speaker_id:
            continue
        for role in other_ctx.get('roles', []):
            if role in tl:
                return True, f'first-person + role exclusive to speaker_id={other_sid}: "{role}"'

    return False, ''
