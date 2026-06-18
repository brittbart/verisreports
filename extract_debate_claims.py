#!/usr/bin/env python3
"""
extract_debate_claims.py — Verum Signal v1.7 debate claim extractor.

Multi-layer quality filtering pipeline:

LAYER 1 — Pre-filter (no API call made):
  - Min 15 words
  - Filler starts (uh, um, well, so, thank you, welcome...)
  - Sponsor/intro copy keywords
  - Opinion/prediction signals
  - No specificity markers (numbers, dates, $, legislation)
  - Ends with ? (rhetorical question)
  - Biographical identity (is from, lives in, grew up in)
  - Relationship statements (married to, son of, father of)
  - Career timeline without policy (has served since, was elected in)
  - Debate process statements (in this debate, let me be clear)
  - Simple agreement (we both agree, we agree on)
  - Geographic/origin statements

LAYER 2 — AI extraction (extract_claims_from_article prompt):
  - Same quality bar as article claims (Day 23 refinements)
  - No biographical facts, vague sentiment, process statements
  - No opinion/characterization, secondhand recalls
  - Specificity requirement: if wrong, does it materially mislead?

LAYER 3 — Post-filter (after extraction, before insert):
  - Min 100 chars
  - No truncated claims (...)
  - Biographical role patterns (is the / was the / are the)
  - Meta-commentary (fake news, in court, recalls being told)
  - Location/relationship/career patterns
  - Multiple vague sentiment signals

INCLUDED by design:
  - Self-promotion with measurable specifics
  - Cross-referential attacks with specifics
  - Historical claims with specifics
  - Electoral results with specific numbers
  - Policy outcomes and economic facts
  - Legislative voting records with specifics

v1.7 METHODOLOGY:
  - Verdicts are statements about claims, not participants
  - Same evidence pipeline regardless of who is speaking
  - No aggregate score per speaker or event
  - Equal treatment across all participants

USAGE:
  python3 extract_debate_claims.py --event-id 1 --dry-run
  python3 extract_debate_claims.py --event-id 1 --limit 20
  python3 extract_debate_claims.py --event-id 1
"""

import argparse
import json
import os
import re
import sys

import psycopg2
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_claims import extract_claims_from_article
from speaker_context import check_speaker_consistency, check_first_person_role

CLAIM_ORIGIN = 'debate_claim'
METHODOLOGY_VERSION = 'v1.7'
SCOREABLE_SPEAKER_TYPES = {'politician', 'official'}
GENERIC_MODERATOR_ID = 3  # Reserved ID for generic moderator — see speakers table


# ---------------------------------------------------------------------------
# Layer 1 — Pre-filter
# ---------------------------------------------------------------------------

SKIP_PREFIXES = (
    'uh ', 'um ', 'uh,', 'um,',
    'well,', 'well ', 'so,', 'so ',
    'you know', 'i mean', 'i think',
    'thank you', 'thanks for', 'thanks,',
    'good evening', 'good morning', 'good afternoon',
    'welcome to', 'welcome back',
    'joining me', 'next question', 'next up',
    'funding for', 'this program was',
    'coming up', 'stay with us',
    'let me be clear',
    'in this debate',
    'over the course of',
    'what i want to say',
    'what i would say is',
)

SKIP_CONTAINS = (
    'funding provided by',
    'made possible by',
    'proud supporter',
    'educational programming',
    'iowa pbs foundation',
    'iowa public television',
    'welcome to the stage',
    'thank you for having',
    'thank you for being',
    'over the next hour',
    'commercial break',
    'associated general contractors',
    'municipal utility infrastructure',
    'celebrating more than',
    'statewide iowa pbs',
    'we both agree',
    'we agree on',
    'both candidates agree',
    'as you mentioned',
    'as my opponent mentioned',
    'that is a great question',
    'great question',
    # TV broadcast ad/sponsor segment filters (replay source quality)
    'call now to find out',
    'if you were injured',
    'injured in an accident',
    'legal help',
    'have you been injured',
    'you may be entitled',
    'our attorneys',
    'no fee unless we win',
    'aarp',
    'paid for by',
    'i approve this message',
    'brought to you by',
    'stay tuned',
    'after the break',
    'when we come back',
    'don\'t go anywhere',
    'we\'ll be right back',
    'weeknights on',
    'tune in to',
    'watch us at',
    'download the app',
    'stream live at',
    'new neighbor',
    'evidently significant improvement',
)

# From verdict_engine.py
OPINION_SIGNALS = (
    'price prediction', 'price target',
    'resistance level', 'support level',
    'breakout from its', 'stiff overhead resistance',
    'macro downtrend', 'signaling a potential',
    'could re-accelerate', 'mock draft',
    'is expected to', 'is predicted to', 'is projected to',
    'will likely', 'should be', 'must be',
    'deserves to',
)

# DEBATE-SPECIFIC filter overrides — looser than article filters
# Remove prefixes that are common in debate speech but precede real factual claims
DEBATE_SKIP_PREFIXES = tuple(
    p for p in SKIP_PREFIXES
    if p not in ('i think ', 'i mean ', 'let me be clear ', 'so ', 'so,', 'well ', 'well,')
)

# Remove opinion signals that catch policy claims with specific numbers in debate context
DEBATE_OPINION_SIGNALS = tuple(
    s for s in OPINION_SIGNALS
    if s not in ('should be', 'must be', 'deserves to', 'is expected to', 'will likely')
)

# Biographical identity — who someone IS, not what they've DONE
BIOGRAPHICAL_PATTERNS_PRE = (
    r'\bis from\b',
    r'\blives in\b',
    r'\bhave lived in\b',
    r'\bgrew up in\b',
    r'\brain in\b.*\bdistrict\b',
    r'\bborn in\b',
    r'\bcurrently resides\b',
    r'\bcalls .{2,30} home\b',
)

# Relationship statements
RELATIONSHIP_PATTERNS = (
    r'\bmarried to\b',
    r'\bmy wife\b',
    r'\bmy husband\b',
    r'\bmy son\b',
    r'\bmy daughter\b',
    r'\bmy father\b',
    r'\bmy mother\b',
    r'\bson of\b',
    r'\bdaughter of\b',
    r'\bfather of\b',
    r'\bmother of\b',
    r'\bhusband of\b',
    r'\bwife of\b',
    r'\bpartner of\b',
)

# Career timeline without policy content
CAREER_TIMELINE_PATTERNS = (
    r'\bhas served (?:since|for|as)\b',
    r'\bserving (?:since|for|as)\b',
    r'\bwas (?:first )?elected (?:in|to)\b',
    r'\bwon (?:his|her|my) (?:seat|race|election) in \d{4}\b',
    r'\brepresents? (?:the )?\d+(?:st|nd|rd|th) district\b',
    r'\bfirst (?:elected|won|ran) in \d{4}\b',
)

# Simple agreement/meta
AGREEMENT_PATTERNS = (
    r'\bwe both agree\b',
    r'\bboth candidates\b',
    r'\bmy opponent and i\b',
    r'\bwe share\b.{0,30}\bview\b',
)

# Moderator question patterns — not candidate claims
MODERATOR_PATTERNS = (
    r'\bboth of you\b',
    r'\beach of you\b',
    r'\bwould you\b',
    r'\bcan you\b',
    r'\bdo you\b.{0,30}\?',
    r'\bwhat would you\b',
    r'\bhow would you\b',
    r'\bwhy do you\b',
    r'\byour (response|reaction|thoughts|position)\b',
    r'\bspeaking to\b.{0,20}\bvoters\b',
    r'\bmy (first|next|final) question\b',
    r'\bi\'ll (turn|go) to\b',
    r'\blet\'s turn to\b',
    r'\bstaying with you\b',
    r'\bsame question\b',
)

# Sentence-start validation — garbled/fragment utterances
# Real sentences start with capital letter or "I"
INVALID_STARTS = re.compile(r'^[a-z]')  # starts lowercase = fragment


def _log_filtered(utterance_id, event_id, speaker_id, stage, reason, text, conn=None):
    """Log a filtered utterance to filtered_utterances table. Never blocks extraction.
    Pass conn to reuse an existing connection; omit to open a short-lived one."""
    if utterance_id is None or event_id is None:
        return  # backtest / dry-run mode — skip DB insert
    try:
        import psycopg2
        _own_conn = conn is None
        if _own_conn:
            conn = psycopg2.connect(
                dbname=os.getenv('DB_NAME', 'railway'), user=os.getenv('DB_USER', 'postgres'),
                password=os.getenv('DB_PASSWORD', 'PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP'), host=os.getenv('DB_HOST', 'shinkansen.proxy.rlwy.net'),
                port=os.getenv('DB_PORT', '35370'), connect_timeout=5,
            )
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO filtered_utterances
                   (utterance_id, event_id, speaker_id, filter_stage, filter_reason, utterance_text)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (utterance_id, event_id, speaker_id, stage, reason, text[:500])
            )
        if _own_conn:
            conn.commit()
            conn.close()
        else:
            conn.commit()
    except Exception:
        pass  # never block extraction for logging


def pre_filter_utterance(text: str, utterance_id=None, event_id=None, speaker_id=None, is_debate=False, conn=None) -> tuple:
    """
    Return (should_skip: bool, reason: str).
    True = skip this utterance before API call.
    """
    t = text.strip()
    tl = t.lower()
    words = t.split()

    # Minimum word count (debate utterances are shorter than articles — 8 word minimum)
    if len(words) < 8:
        _log_filtered(utterance_id, event_id, speaker_id, 'pre', 'too short', t, conn=conn)
        return True, 'too short'

    # Ends with question mark = rhetorical question
    if t.rstrip().endswith('?'):
        _log_filtered(utterance_id, event_id, speaker_id, 'pre', 'question', t, conn=conn)
        return True, 'question'

    # Strip leading filler words then check prefixes (multi-pass until stable)
    # 'represent/representing/represented' = Rev AI transcription artifact for sentence starters
    STRIP_LEADING = ('uh ', 'um ', 'uh, ', 'um, ', 'and ', 'but ', 'so ', 'well, ', 'well ',
                     'represent ', 'representing ', 'represented ', 'look, ', 'look ')
    cleaned = tl
    prev = None
    while cleaned != prev:
        prev = cleaned
        for strip in STRIP_LEADING:
            if cleaned.startswith(strip):
                cleaned = cleaned[len(strip):].strip()
                break
    _skip_prefixes = DEBATE_SKIP_PREFIXES if is_debate else SKIP_PREFIXES
    _opinion_signals = DEBATE_OPINION_SIGNALS if is_debate else OPINION_SIGNALS
    for prefix in _skip_prefixes:
        if cleaned.startswith(prefix):
            _log_filtered(utterance_id, event_id, speaker_id, 'pre', f'filler prefix: {prefix}', t, conn=conn)
            return True, f'filler prefix: {prefix}'

    # Junk keywords anywhere
    for kw in SKIP_CONTAINS:
        if kw in tl:
            _log_filtered(utterance_id, event_id, speaker_id, 'pre', f'skip keyword: {kw}', t, conn=conn)
            return True, f'skip keyword: {kw}'

    # Opinion signals
    for signal in _opinion_signals:
        if signal in tl:
            _log_filtered(utterance_id, event_id, speaker_id, 'pre', f'opinion signal: {signal}', t, conn=conn)
            return True, f'opinion signal: {signal}'

    # Biographical identity patterns
    for pat in BIOGRAPHICAL_PATTERNS_PRE:
        if re.search(pat, tl):
            return True, f'biographical: {pat}'

    # Relationship statements
    for pat in RELATIONSHIP_PATTERNS:
        if re.search(pat, tl):
            return True, f'relationship: {pat}'

    # Career timeline
    for pat in CAREER_TIMELINE_PATTERNS:
        if re.search(pat, tl):
            return True, f'career timeline: {pat}'

    # Simple agreement/meta
    for pat in AGREEMENT_PATTERNS:
        if re.search(pat, tl):
            return True, f'agreement/meta: {pat}'

    # NOTE: MODERATOR_PATTERNS intentionally not applied here —
    # fetch_politician_utterances already excludes moderator speaker_type,
    # so these patterns only fire on candidate speech containing words like
    # "your response" or "would you" when addressing opponents. Kept defined
    # for reference but removed from pre-filter evaluation.

    # Fragment/garbled utterance — starts lowercase (Rev AI artifact)
    if INVALID_STARTS.match(t):
        return True, 'fragment: starts lowercase'

    # Single-word sentence starter that isn't a valid opener
    # e.g. "Represent Trump won..." — "Represent" is a fragment
    first_word = t.split()[0].rstrip('.,;:').lower()
    INVALID_OPENERS = {
        'because', 'or', 'nor', 'yet',
        'although', 'though', 'however', 'therefore',
        'whereas', 'which', 'who', 'whom',
        'whose', 'when', 'where', 'while',
        # NOTE: 'that' removed — "That said, $350M..." is a valid claim opener
        # 'who'/'whom'/'whose' retained — these are always relative clause fragments
    }
    if first_word in INVALID_OPENERS:
        return True, f'fragment: invalid opener ({first_word})'

    # No specificity markers
    has_number    = bool(re.search(r'\b\d+(?:\.\d+)?%?\b', t))
    has_dollar    = '$' in t
    has_year      = bool(re.search(r'\b(19|20)\d{2}\b', t))
    has_bill      = bool(re.search(r'\b(bill|act|law|legislation|amendment|resolution|budget|plan)\b', tl))
    has_statistic = bool(re.search(r'\b(percent|million|billion|trillion|thousand|hundred)\b', tl))

    has_ranking = bool(re.search(r'\b(leads?|leading|ranked?|ranking|highest|lowest|most|least|first|last|worst|best|top|bottom|ahead|behind|surpass)\b', tl))
    has_policy  = bool(re.search(r'\b(tax|tariff|wage|medicare|medicaid|social security|insurance|subsid|foreclos|filibuster|immigration|healthcare|abortion|climate|energy|deficit|debt|budget|cut|reform|ban|mandate|repeal)\b', tl))
    has_legal   = bool(re.search(r'\b(indict|unconstitutional|fraud|corrupt|investigation|audit|lawsuit|sued|fired|prosecut|criminal|violat|illegal|felony|misdemeanor|perjur)\b', tl))
    has_agency  = bool(re.search(r'\b(cdot|fbi|cia|doj|epa|irs|fda|cdc|hhs|dhs|cms|gao|oig|inspector general|attorney general|department of|office of|nonpartisan|bipartisan)\b', tl))
    if not any([has_number, has_dollar, has_year, has_bill, has_statistic, has_ranking, has_policy, has_legal, has_agency]):
        _log_filtered(utterance_id, event_id, speaker_id, 'pre', 'no specificity markers', t, conn=conn)
        return True, 'no specificity markers'

    return False, ''


# ---------------------------------------------------------------------------
# Layer 3 — Post-filter (after AI extraction)
# ---------------------------------------------------------------------------

POST_BIOGRAPHICAL = (
    r'\bis the\b', r'\bwas the\b', r'\bare the\b',
    r'\bserves as\b', r'\bserved as\b',
    r'\bis a member of\b', r'\bwas a member of\b',
)

POST_LOCATION = (
    r'\bis from\b', r'\blives in\b', r'\bgrew up in\b',
    r'\bborn in\b', r'\bresident of\b',
)

POST_RELATIONSHIP = (
    r'\bmarried to\b', r'\bson of\b', r'\bdaughter of\b',
    r'\bfather of\b', r'\bmother of\b',
)

POST_META = (
    'in court', 'fake news', 'false reporting',
    'recalls being told', 'witch hunt', 'hoax',
    'that is not true', 'my opponent is wrong',
    'categorically false', 'that is a lie',
)

POST_VAGUE = (
    r'\bfighting for\b', r'\bstand up for\b',
    r'\bwork hard for\b', r'\bneed to fight\b',
    r'\bbelieve in\b.{0,30}(families|iowans|people|workers)',
    r'\bcommitted to\b.{0,20}$',
)


def post_filter_claim(claim_text: str) -> tuple:
    """
    Return (should_exclude: bool, reason: str).
    True = exclude this claim after extraction.
    """
    t = claim_text.strip()
    tl = t.lower()

    # Min length — debate claims can be concise
    if len(t) < 40:
        return True, 'too short'

    # Truncated — only reject if very short
    if '...' in t and len(t) < 60:
        return True, 'truncated'

    # Biographical role
    for pat in POST_BIOGRAPHICAL:
        if re.search(pat, tl):
            return True, f'biographical role: {pat}'

    # Location
    for pat in POST_LOCATION:
        if re.search(pat, tl):
            return True, f'location statement: {pat}'

    # Relationship
    for pat in POST_RELATIONSHIP:
        if re.search(pat, tl):
            return True, f'relationship statement: {pat}'

    # Meta-commentary
    for pat in POST_META:
        if pat in tl:
            return True, f'meta-commentary: {pat}'

    # Multiple vague sentiment signals
    vague_hits = sum(1 for p in POST_VAGUE if re.search(p, tl))
    if vague_hits >= 2:
        return True, 'vague sentiment'

    return False, ''


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_connection():
    # Hardcoded fallbacks: Railway Runtime V2 subprocess env var stripping
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME', 'railway'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', 'PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP'),
        host=os.getenv('DB_HOST', 'shinkansen.proxy.rlwy.net'),
        port=os.getenv('DB_PORT', '35370'),
        connect_timeout=10,
    )


def fetch_politician_utterances(conn, event_id, limit=None):
    """Fetch unprocessed politician utterances using round-robin per speaker.

    Without round-robin, ORDER BY utterance_order ASC with a small limit
    causes speaker starvation — the speaker with more early utterances fills
    every batch and the other speaker never gets extracted during live coverage.

    Round-robin: take limit//num_speakers utterances per speaker, interleaved
    by utterance_order so turns stay in chronological sequence.
    """
    with conn.cursor() as cur:
        # First get the list of active politician speakers for this event
        cur.execute("""
            SELECT DISTINCT su.speaker_id
            FROM speaker_utterances su
            JOIN speakers s ON s.id = su.speaker_id
            WHERE su.event_id = %s
              AND s.speaker_type IN ('politician', 'official')
              AND su.processed_at IS NULL
              AND su.id NOT IN (
                  SELECT utterance_id FROM claims
                  WHERE utterance_id IS NOT NULL
                    AND event_id = %s
                    AND claim_origin = 'debate_claim'
              )
        """, (event_id, event_id))
        speaker_ids = [r[0] for r in cur.fetchall()]

        if not speaker_ids:
            return []

        # Compute per-speaker limit for fair distribution
        num_speakers = len(speaker_ids)
        per_speaker = max(1, (limit // num_speakers)) if limit else None

        # Fetch utterances per speaker, then merge and sort by utterance_order
        all_rows = []
        for sid in speaker_ids:
            sql = """
                SELECT
                    su.id, su.utterance_text, su.utterance_order,
                    su.speaker_id, s.name, s.speaker_type, s.party,
                    e.event_name, e.event_date, e.slug,
                    su.timestamp_seconds,
                    su.attribution_uncertain
                FROM speaker_utterances su
                JOIN speakers s ON s.id = su.speaker_id
                JOIN events e ON e.id = su.event_id
                WHERE su.event_id = %s
                  AND su.speaker_id = %s
                  AND s.speaker_type IN ('politician', 'official')
                  AND su.processed_at IS NULL
                  AND su.id NOT IN (
                      SELECT utterance_id FROM claims
                      WHERE utterance_id IS NOT NULL
                        AND event_id = %s
                        AND claim_origin = 'debate_claim'
                  )
                ORDER BY su.utterance_order ASC
            """
            params = [event_id, sid, event_id]
            if per_speaker:
                sql += f" LIMIT {per_speaker}"
            cur.execute(sql, params)
            all_rows.extend(cur.fetchall())

        # Sort merged results by utterance_order to preserve chronological flow
        all_rows.sort(key=lambda r: r[2])
        return all_rows


def utterance_to_article_dict(row, event_id):
    (uid, utext, uorder, speaker_id, speaker_name,
     speaker_type, party, event_name, event_date, event_slug,
     timestamp_seconds, attribution_uncertain) = row
    return {
        'title':       f"{speaker_name} at {event_name} ({event_date})",
        'description': f"Statement by {speaker_name} during {event_name}",
        'content':     utext,
        'source':      {'name': event_name},
        'url':         f"https://verumsignal.com/debates/{event_slug}",
        'publishedAt': str(event_date),
        '_utterance_id': uid,
        '_speaker_id':   speaker_id,
        '_speaker_name': speaker_name,
        '_event_id':     event_id,
        'timestamp_seconds': timestamp_seconds,
    }


def insert_debate_claim(conn, claim, utterance_id, speaker_id, event_id, speaker_name, attribution_uncertain=False):
    claim_text = claim.get('claim_text', '').strip()

    exclude, reason = post_filter_claim(claim_text)
    if exclude:
        return None, f'post-filtered ({reason})'

    # Semantic consistency check: does claim content match attributed speaker?
    consistent, con_reason = check_speaker_consistency(claim_text, speaker_id, event_id)
    suspicious, sus_reason = check_first_person_role(claim_text, speaker_id, event_id)
    _attribution_flag = None
    if suspicious:
        _attribution_flag = f'SUSPICIOUS: {sus_reason}'
        print(f"    ⚠ ATTRIBUTION SUSPECT: {sus_reason}")
        print(f"      claim: {claim_text[:80]}")
        print(f"      attributed to speaker_id={speaker_id} ({speaker_name})")
    elif not consistent:
        _attribution_flag = f'INCONSISTENT: {con_reason}'
        print(f"    ⚠ ATTRIBUTION FLAG: {con_reason}")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM claims
            WHERE claim_text = %s AND event_id = %s LIMIT 1
        """, (claim_text, event_id))
        if cur.fetchone():
            return None, 'duplicate'
        # Near-duplicate check via trigram similarity (pg_trgm)
        cur.execute("""
            SELECT id FROM claims
            WHERE event_id = %s
              AND claim_origin = 'debate_claim'
              AND similarity(claim_text, %s) > 0.55
            LIMIT 1
        """, (event_id, claim_text))
        sim_row = cur.fetchone()
        if sim_row:
            return None, f'near-duplicate of claim {sim_row[0]}'

        cur.execute("""
            INSERT INTO claims (
                article_id, claim_text, speaker, claim_type,
                why_checkworthy, claim_origin, attribution_context,
                speaker_id, utterance_id, event_id,
                first_seen, last_seen, priority_score,
                verdict_status, methodology_version, timestamp_seconds
            ) VALUES (
                NULL, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, NOW(), NOW(), 70,
                'provisional', %s, %s
            ) RETURNING id
        """, (
            claim_text,
            speaker_name,
            claim.get('claim_type', 'factual'),
            claim.get('why_checkworthy', ''),
            CLAIM_ORIGIN,
            ('[attribution_uncertain] ' if attribution_uncertain else '') + claim.get('attribution_context', ''),
            speaker_id,
            utterance_id,
            event_id,
            METHODOLOGY_VERSION,
            claim.get('timestamp_seconds'),
        ))
        row = cur.fetchone()

        # If semantic check flagged this claim, write to revision_history
        if row and _attribution_flag:
            claim_id = row[0]
            import json as _json
            flag_entry = _json.dumps([{
                'action': 'attribution_flagged',
                'reason': _attribution_flag,
                'speaker_id': speaker_id,
                'speaker_name': speaker_name,
                'timestamp': 'auto',
            }])
            cur.execute("""
                UPDATE claims SET revision_history = %s::jsonb
                WHERE id = %s AND revision_history IS NULL
            """, (flag_entry, claim_id))

    conn.commit()
    return (row[0] if row else None), 'inserted'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def group_utterances_into_turns(utterances):
    """
    Group consecutive utterances from the same speaker into turns.
    Each turn is a dict with:
      - text: concatenated utterance text
      - speaker_id, speaker_name: from first utterance in turn
      - first_uid: utterance id of first utterance (used for logging/claim attribution)
      - all_uids: list of all utterance ids in the turn (all marked processed_at)
      - row: the first raw DB row (for utterance_to_article_dict compatibility)
    Splits on speaker change only — no length cap.
    """
    if not utterances:
        return []
    turns = []
    current_speaker_id = None
    current_texts = []
    current_uids = []
    current_row = None

    for row in utterances:
        uid, utext, uorder, speaker_id, speaker_name = row[0], row[1], row[2], row[3], row[4]
        attribution_uncertain = row[11] if len(row) > 11 else False
        if speaker_id != current_speaker_id:
            if current_uids:
                turns.append({
                    'text': ' '.join(current_texts),
                    'speaker_id': current_row[3],
                    'speaker_name': current_row[4],
                    'first_uid': current_uids[0],
                    'all_uids': current_uids,
                'attribution_uncertain': attribution_uncertain,
                    'row': current_row,
                })
            current_speaker_id = speaker_id
            current_texts = [utext]
            current_uids = [uid]
            current_row = row
        else:
            current_texts.append(utext)
            current_uids.append(uid)

    # Flush last turn
    if current_uids:
        turns.append({
            'text': ' '.join(current_texts),
            'speaker_id': current_row[3],
            'speaker_name': current_row[4],
            'first_uid': current_uids[0],
            'all_uids': current_uids,
            'row': current_row,
        })
    return turns


def get_fresh_connection():
    """Always returns a fresh connection. Use when long-running jobs risk timeout."""
    # Hardcoded fallbacks: Railway Runtime V2 subprocess env var stripping
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME', 'railway'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', 'PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP'),
        host=os.getenv('DB_HOST', 'shinkansen.proxy.rlwy.net'),
        port=os.getenv('DB_PORT', '35370'),
        connect_timeout=10,
    )


def run_extraction(event_id, limit=None, dry_run=False):
    print("=" * 68)
    print(f"Verum Signal — Debate claim extraction (v1.7)")
    print(f"Event ID: {event_id}  |  Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    if limit:
        print(f"Limit: {limit} utterances")
    print("=" * 68)

    conn = get_connection()
    utterances = fetch_politician_utterances(conn, event_id, limit)
    print(f"\nFound {len(utterances)} unprocessed politician utterances\n")

    if not utterances:
        print("Nothing to process.")
        conn.close()
        return

    turns = group_utterances_into_turns(utterances)
    print(f"Grouped into {len(turns)} speaker turns\n")

    stats = {
        'utterances': len(utterances),
        'turns': len(turns),
        'pre_filtered': 0,
        'api_calls': 0,
        'claims_extracted': 0,
        'post_filtered': 0,
        'duplicates': 0,
        'inserted': 0,
        'errors': 0,
    }

    for i, turn in enumerate(turns):
        uid = turn['first_uid']
        all_uids = turn['all_uids']
        speaker_id = turn['speaker_id']
        speaker_name = turn['speaker_name']
        turn_text = turn['text']
        row = turn['row']
        attribution_uncertain = turn.get('attribution_uncertain', False)

        print(f"[{i+1}/{len(turns)}] {speaker_name} ({len(all_uids)} utterance(s)): {turn_text[:65]}...")

        # Guard: never extract claims from generic moderator (speaker_id=3)
        if speaker_id == GENERIC_MODERATOR_ID:
            print(f'  -> skipped (moderator speaker_id={GENERIC_MODERATOR_ID})')
            stats['pre_filtered'] += 1
            continue
        # Layer 1: pre-filter on full turn text
        skip, reason = pre_filter_utterance(turn_text, utterance_id=uid, event_id=event_id, speaker_id=speaker_id, is_debate=True, conn=conn)
        if skip:
            print(f"  → pre-filtered: {reason}")
            stats['pre_filtered'] += 1
            # Mark all utterances in this turn as processed
            with conn.cursor() as _cur:
                _cur.execute(
                    "UPDATE speaker_utterances SET processed_at = NOW() WHERE id = ANY(%s)",
                    (all_uids,)
                )
            conn.commit()
            continue

        if dry_run:
            print(f"  → [dry-run] would call API")
            stats['api_calls'] += 1
            continue

        # Layer 2: AI extraction on full turn text (with retry on 529/overload)
        try:
            # Build article dict from first row but substitute full turn text as content
            article_dict = utterance_to_article_dict(row, event_id)
            article_dict['content'] = turn_text
            claims = None
            _retry_delays = [5, 15, 30]
            for _attempt in range(len(_retry_delays) + 1):
                try:
                    claims = extract_claims_from_article(article_dict)
                    break
                except Exception as _api_err:
                    err_str = str(_api_err)
                    is_retryable = '529' in err_str or 'overloaded' in err_str.lower() or '529' in getattr(_api_err, 'status_code', '')
                    if is_retryable and _attempt < len(_retry_delays):
                        delay = _retry_delays[_attempt]
                        print(f"  [RETRY] API overloaded (attempt {_attempt+1}/{len(_retry_delays)+1}), waiting {delay}s...")
                        import time as _time
                        _time.sleep(delay)
                    else:
                        raise
            if claims is None:
                claims = []
            stats['api_calls'] += 1
            stats['claims_extracted'] += len(claims)

            if not claims:
                print(f"  → 0 claims extracted")
            else:
                print(f"  → {len(claims)} claim(s):")
                for claim in claims:
                    # Carry timestamp from utterance into claim for display
                    if 'timestamp_seconds' not in claim or claim['timestamp_seconds'] is None:
                        claim['timestamp_seconds'] = article_dict.get('timestamp_seconds')
                    # Reconnect if connection was lost during API call
                    try:
                        conn.cursor().execute("SELECT 1")
                    except Exception:
                        print(f"  [reconnecting to DB...]")
                        conn = get_fresh_connection()
                    claim_id, outcome = insert_debate_claim(
                        conn, claim, uid, speaker_id, event_id, speaker_name,
                        attribution_uncertain=attribution_uncertain
                    )
                    ct = claim.get('claim_text', '')[:70]
                    if outcome == 'inserted':
                        stats['inserted'] += 1
                        print(f"    ✓ {ct}")
                    elif outcome.startswith('post-filtered'):
                        stats['post_filtered'] += 1
                        print(f"    ✗ {outcome}: {ct}")
                    elif outcome == 'duplicate':
                        stats['duplicates'] += 1
                        print(f"    ~ duplicate")

        except Exception as e:
            stats['errors'] += 1
            print(f"  ERROR: {e}")
            # Do NOT mark utterances as processed — they will be retried next cycle
            print(f"  → Utterances left unprocessed for retry")
            continue

        # Mark all utterances in this turn as processed — reconnect if needed
        try:
            conn.cursor().execute("SELECT 1")
        except Exception:
            print(f"  [reconnecting to DB for processed_at mark...]")
            conn = get_fresh_connection()
        try:
            with conn.cursor() as _cur:
                _cur.execute(
                    "UPDATE speaker_utterances SET processed_at = NOW() WHERE id = ANY(%s)",
                    (all_uids,)
                )
            conn.commit()
        except Exception as e:
            print(f"  WARNING: could not mark utterances processed: {e}")

    conn.close()

    print("\n" + "=" * 68)
    print(f"Complete")
    print(f"  Utterances:     {stats['utterances']}")
    print(f"  Turns:          {stats['turns']}")
    print(f"  Pre-filtered:   {stats['pre_filtered']} turns (saved {stats['pre_filtered']} API calls)")
    print(f"  API calls:      {stats['api_calls']}")
    print(f"  Claims found:   {stats['claims_extracted']}")
    print(f"  Post-filtered:  {stats['post_filtered']}")
    print(f"  Duplicates:     {stats['duplicates']}")
    print(f"  Inserted:       {stats['inserted']}")
    print(f"  Errors:         {stats['errors']}")
    print("=" * 68)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-id', type=int, required=True)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run_extraction(args.event_id, limit=args.limit, dry_run=args.dry_run)
