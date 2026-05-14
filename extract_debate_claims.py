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

CLAIM_ORIGIN = 'debate_claim'
SCOREABLE_SPEAKER_TYPES = {'politician', 'official'}


# ---------------------------------------------------------------------------
# Layer 1 — Pre-filter
# ---------------------------------------------------------------------------

SKIP_PREFIXES = (
    'uh ', 'um ', 'uh,', 'um,',
    'well,', 'well ', 'so,', 'so ',
    'and ', 'but ',
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

# Biographical identity — who someone IS, not what they've DONE
BIOGRAPHICAL_PATTERNS_PRE = (
    r'\bis from\b',
    r'\blives in\b',
    r'\bgrew up in\b',
    r'\brain in\b.*\bdistrict\b',
    r'\bborn in\b',
    r'\bcurrently resides\b',
    r'\bcalls .{2,30} home\b',
)

# Relationship statements
RELATIONSHIP_PATTERNS = (
    r'\bmarried to\b',
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


def pre_filter_utterance(text: str) -> tuple:
    """
    Return (should_skip: bool, reason: str).
    True = skip this utterance before API call.
    """
    t = text.strip()
    tl = t.lower()
    words = t.split()

    # Minimum word count
    if len(words) < 15:
        return True, 'too short'

    # Ends with question mark = rhetorical question
    if t.rstrip().endswith('?'):
        return True, 'question'

    # Filler starts
    for prefix in SKIP_PREFIXES:
        if tl.startswith(prefix):
            return True, f'filler prefix: {prefix}'

    # Junk keywords anywhere
    for kw in SKIP_CONTAINS:
        if kw in tl:
            return True, f'skip keyword: {kw}'

    # Opinion signals
    for signal in OPINION_SIGNALS:
        if signal in tl:
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

    # Moderator questions — not candidate claims
    for pat in MODERATOR_PATTERNS:
        if re.search(pat, tl):
            return True, f'moderator pattern: {pat}'

    # Fragment/garbled utterance — starts lowercase (Rev AI artifact)
    if INVALID_STARTS.match(t):
        return True, 'fragment: starts lowercase'

    # No specificity markers
    has_number    = bool(re.search(r'\b\d+(?:\.\d+)?%?\b', t))
    has_dollar    = '$' in t
    has_year      = bool(re.search(r'\b(19|20)\d{2}\b', t))
    has_bill      = bool(re.search(r'\b(bill|act|law|legislation|amendment|resolution|budget|plan)\b', tl))
    has_statistic = bool(re.search(r'\b(percent|million|billion|trillion|thousand|hundred)\b', tl))

    if not any([has_number, has_dollar, has_year, has_bill, has_statistic]):
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

    # Min length
    if len(t) < 100:
        return True, 'too short'

    # Truncated
    if '...' in t:
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
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'),
        connect_timeout=10,
    )


def fetch_politician_utterances(conn, event_id, limit=None):
    with conn.cursor() as cur:
        sql = """
            SELECT
                su.id, su.utterance_text, su.utterance_order,
                su.speaker_id, s.name, s.speaker_type, s.party,
                e.event_name, e.event_date, e.slug
            FROM speaker_utterances su
            JOIN speakers s ON s.id = su.speaker_id
            JOIN events e ON e.id = su.event_id
            WHERE su.event_id = %s
              AND s.speaker_type IN ('politician', 'official')
              AND su.id NOT IN (
                  SELECT utterance_id FROM claims
                  WHERE utterance_id IS NOT NULL
                    AND event_id = %s
                    AND claim_origin = 'debate_claim'
              )
            ORDER BY su.utterance_order ASC
        """
        params = [event_id, event_id]
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur.execute(sql, params)
        return cur.fetchall()


def utterance_to_article_dict(row, event_id):
    (uid, utext, uorder, speaker_id, speaker_name,
     speaker_type, party, event_name, event_date, event_slug) = row
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
    }


def insert_debate_claim(conn, claim, utterance_id, speaker_id, event_id, speaker_name):
    claim_text = claim.get('claim_text', '').strip()

    exclude, reason = post_filter_claim(claim_text)
    if exclude:
        return None, f'post-filtered ({reason})'

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id FROM claims
            WHERE claim_text = %s AND event_id = %s LIMIT 1
        """, (claim_text, event_id))
        if cur.fetchone():
            return None, 'duplicate'

        cur.execute("""
            INSERT INTO claims (
                article_id, claim_text, speaker, claim_type,
                why_checkworthy, claim_origin, attribution_context,
                speaker_id, utterance_id, event_id,
                first_seen, last_seen, priority_score
            ) VALUES (
                NULL, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, NOW(), NOW(), 70
            ) RETURNING id
        """, (
            claim_text,
            speaker_name,
            claim.get('claim_type', 'factual'),
            claim.get('why_checkworthy', ''),
            CLAIM_ORIGIN,
            claim.get('attribution_context', ''),
            speaker_id,
            utterance_id,
            event_id,
        ))
        row = cur.fetchone()
    conn.commit()
    return (row[0] if row else None), 'inserted'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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

    stats = {
        'utterances': len(utterances),
        'pre_filtered': 0,
        'api_calls': 0,
        'claims_extracted': 0,
        'post_filtered': 0,
        'duplicates': 0,
        'inserted': 0,
        'errors': 0,
    }

    for i, row in enumerate(utterances):
        uid, utext, uorder, speaker_id, speaker_name = row[0], row[1], row[2], row[3], row[4]
        print(f"[{i+1}/{len(utterances)}] {speaker_name}: {utext[:65]}...")

        # Layer 1: pre-filter
        skip, reason = pre_filter_utterance(utext)
        if skip:
            print(f"  → pre-filtered: {reason}")
            stats['pre_filtered'] += 1
            continue

        if dry_run:
            print(f"  → [dry-run] would call API")
            stats['api_calls'] += 1
            continue

        # Layer 2: AI extraction
        try:
            article_dict = utterance_to_article_dict(row, event_id)
            claims = extract_claims_from_article(article_dict)
            stats['api_calls'] += 1
            stats['claims_extracted'] += len(claims)

            if not claims:
                print(f"  → 0 claims extracted")
                continue

            print(f"  → {len(claims)} claim(s):")
            for claim in claims:
                claim_id, outcome = insert_debate_claim(
                    conn, claim, uid, speaker_id, event_id, speaker_name
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

    conn.close()

    print("\n" + "=" * 68)
    print(f"Complete")
    print(f"  Utterances:     {stats['utterances']}")
    print(f"  Pre-filtered:   {stats['pre_filtered']} (saved {stats['pre_filtered']} API calls)")
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
