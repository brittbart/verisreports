#!/usr/bin/env python3
"""
extract_debate_claims.py — Verum Signal v1.7 debate claim extractor.

Fetches politician utterances from speaker_utterances for a given event_id,
runs them through extract_claims_from_article(), and writes claims to the
claims table with:
  - article_id = NULL
  - utterance_id = <utterance id>
  - event_id = <event id>
  - speaker_id = <speaker id>
  - claim_origin = 'debate_claim'

SAFETY: Does NOT touch outlet_claim or attributed_claim rows.
        All existing scoring queries filter on claim_origin = 'outlet_claim'
        and JOIN articles — debate claims are invisible to them.

USAGE:
  cd ~/projects/veris && source venv/bin/activate
  python3 extract_debate_claims.py --event-id 1 --dry-run   # preview
  python3 extract_debate_claims.py --event-id 1 --limit 20  # first 20 utterances
  python3 extract_debate_claims.py --event-id 1              # all utterances

COST ESTIMATE: ~$0.002-0.005 per utterance. 196 politician utterances ≈ $0.40-1.00 total.
"""

import argparse
import json
import os
import sys
from datetime import datetime

import psycopg2
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_claims import extract_claims_from_article

CLAIM_ORIGIN = 'debate_claim'
SCOREABLE_SPEAKER_TYPES = {'politician', 'official'}


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
    """Fetch all politician utterances for an event, ordered by utterance_order."""
    with conn.cursor() as cur:
        sql = """
            SELECT
                su.id AS utterance_id,
                su.utterance_text,
                su.utterance_order,
                su.speaker_id,
                s.name AS speaker_name,
                s.speaker_type,
                s.party,
                e.event_name,
                e.event_date,
                e.slug AS event_slug
            FROM speaker_utterances su
            JOIN speakers s ON s.id = su.speaker_id
            JOIN events e ON e.id = su.event_id
            WHERE su.event_id = %s
              AND s.speaker_type IN ('politician', 'official')
            ORDER BY su.utterance_order ASC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur.execute(sql, (event_id,))
        return cur.fetchall()


def utterance_to_article_dict(row):
    """Convert an utterance row into the article dict format expected by extract_claims_from_article."""
    (utterance_id, utterance_text, utterance_order, speaker_id,
     speaker_name, speaker_type, party, event_name, event_date, event_slug) = row

    # Format as pseudo-article so extract_claims_from_article works unchanged
    return {
        'title': f"{speaker_name} at {event_name} ({event_date})",
        'description': f"Statement by {speaker_name} during {event_name}",
        'content': utterance_text,
        'source': {'name': event_name},
        'url': f"https://verumsignal.com/debates/{event_slug}",
        'publishedAt': str(event_date),
        # Pass through for use in claim insertion
        '_utterance_id': utterance_id,
        '_speaker_id':   speaker_id,
        '_speaker_name': speaker_name,
        '_event_id':     None,  # filled in by caller
    }


def insert_debate_claim(conn, claim, utterance_id, speaker_id, event_id, speaker_name):
    """Insert a single debate claim into the claims table."""
    with conn.cursor() as cur:
        # Check for duplicate claim text for this event
        cur.execute("""
            SELECT id FROM claims
            WHERE claim_text = %s AND event_id = %s
            LIMIT 1
        """, (claim['claim_text'], event_id))
        if cur.fetchone():
            return None  # duplicate, skip

        cur.execute("""
            INSERT INTO claims (
                article_id, claim_text, speaker, claim_type,
                why_checkworthy, claim_origin, attribution_context,
                speaker_id, utterance_id, event_id,
                first_seen, last_seen, priority_score
            ) VALUES (
                NULL, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                NOW(), NOW(), 50
            ) RETURNING id
        """, (
            claim['claim_text'],
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
    return row[0] if row else None


def run_extraction(event_id, limit=None, dry_run=False):
    print("=" * 68)
    print(f"Verum Signal — Debate claim extraction")
    print(f"Event ID: {event_id}")
    print(f"Mode:     {'DRY RUN' if dry_run else 'APPLY'}")
    if limit:
        print(f"Limit:    {limit} utterances")
    print("=" * 68)

    conn = get_connection()

    utterances = fetch_politician_utterances(conn, event_id, limit)
    print(f"\nFound {len(utterances)} politician utterances to process\n")

    if not utterances:
        print("No utterances found. Check event_id and that ingestion ran.")
        conn.close()
        return

    total_claims = 0
    total_inserted = 0
    errors = 0

    for i, row in enumerate(utterances):
        utterance_id = row[0]
        speaker_name = row[4]
        text_preview = row[1][:60]

        print(f"[{i+1}/{len(utterances)}] {speaker_name}: {text_preview}...")

        article_dict = utterance_to_article_dict(row)
        article_dict['_event_id'] = event_id

        if dry_run:
            print(f"  [dry-run] Would extract claims from this utterance")
            continue

        try:
            claims = extract_claims_from_article(article_dict)
            total_claims += len(claims)

            if not claims:
                print(f"  No claims extracted")
                continue

            print(f"  Extracted {len(claims)} claims:")
            for claim in claims:
                if dry_run:
                    print(f"    - {claim['claim_text'][:80]}...")
                    continue

                claim_id = insert_debate_claim(
                    conn, claim, utterance_id, row[3], event_id, speaker_name
                )
                if claim_id:
                    total_inserted += 1
                    print(f"    + claim {claim_id}: {claim['claim_text'][:70]}...")
                else:
                    print(f"    ~ duplicate skipped")

        except Exception as e:
            errors += 1
            print(f"  ERROR: {e}")

    conn.close()

    print("\n" + "=" * 68)
    print(f"Extraction complete")
    print(f"  Utterances processed: {len(utterances)}")
    print(f"  Claims extracted:     {total_claims}")
    print(f"  Claims inserted:      {total_inserted}")
    print(f"  Errors:               {errors}")
    print("=" * 68)
    print(f"\nNext step: run verdict engine against event_id={event_id}")
    print(f"  python3 verify_debate_claims.py --event-id {event_id}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-id', type=int, required=True)
    parser.add_argument('--limit', type=int, default=None, help='Max utterances to process')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    run_extraction(args.event_id, limit=args.limit, dry_run=args.dry_run)
