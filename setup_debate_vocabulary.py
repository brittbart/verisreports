#!/usr/bin/env python3
"""
setup_debate_vocabulary.py — Register a Rev AI custom vocabulary for a debate event.

Creates a custom vocabulary with candidate names, debate-specific terms, and
known Rev AI misspellings, then stores the vocabulary ID on the events table
so debate_stream.py can pass it to the streaming API.

USAGE:
  python3 setup_debate_vocabulary.py --event-id 11 --dry-run
  python3 setup_debate_vocabulary.py --event-id 11
  python3 setup_debate_vocabulary.py --event-id 11 --wait  # poll until ready
"""

import argparse
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv('/home/veris/projects/veris/.env')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from rev_ai import custom_vocabularies_client
from rev_ai.models import CustomVocabulary

# ---------------------------------------------------------------------------
# Base vocabulary — applies to every debate
# These are terms Rev AI commonly mangles in political debate contexts
# ---------------------------------------------------------------------------
BASE_PHRASES = [
    # Platform / methodology terms
    "Verum Signal",
    "fact-based",
    "nonpartisan",

    # Colorado-specific political terms
    "Tren de Aragua",
    "Joint Budget Committee",
    "TABOR",
    "Jared Polis",
    "Colorado General Assembly",
    "Colorado Sun",
    "CPR News",
    "Colorado Newsline",
    "nine news",

    # Common debate terms Rev AI struggles with
    "cartel members",
    "sanctuary state",
    "property tax cut",
    "general fund",
    "contingency fund",
    "bipartisan",
    "constitutional amendment",
    "ICE agents",
    "sheriff's office",
    "asylum seekers",
    "Tina Peters",
    "Joe Oltman",
    "Phil Weiser",
    "attorney general",
    "gubernatorial",
    "Jena Griswola",
    "Dan Baer",
    "Colorado governor",
    "Democratic primary",
]

def get_connection():
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'),
        connect_timeout=10,
    )

def get_event_speakers(event_id):
    """Get active speaker names for this event."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.name, s.role
        FROM event_speakers es
        JOIN speakers s ON s.id = es.speaker_id
        WHERE es.event_id = %s AND es.is_active = TRUE
        ORDER BY es.speaker_order
    """, (event_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def build_speaker_phrases(speakers):
    """Build vocabulary phrases from speaker names."""
    phrases = []
    for name, role in speakers:
        if not name:
            continue
        # Full name
        phrases.append(name)
        # Last name only (how moderators address candidates)
        parts = name.strip().split()
        if len(parts) >= 2:
            phrases.append(parts[-1])
        # First + Last without middle
        if len(parts) >= 3:
            phrases.append(f"{parts[0]} {parts[-1]}")
    return phrases

def setup_vocabulary(event_id, dry_run=False, wait=False):
    print("=" * 68)
    print(f"Verum Signal — Rev AI custom vocabulary setup")
    print(f"Event ID: {event_id}  |  Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print("=" * 68)

    # Get speakers for this event
    speakers = get_event_speakers(event_id)
    if not speakers:
        print("ERROR: No active speakers found for this event")
        sys.exit(1)

    print(f"\nSpeakers: {[s[0] for s in speakers]}")

    # Build phrase list
    speaker_phrases = build_speaker_phrases(speakers)
    all_phrases = list(dict.fromkeys(BASE_PHRASES + speaker_phrases))  # dedupe, preserve order
    print(f"Total phrases: {len(all_phrases)}")
    print(f"Speaker phrases: {speaker_phrases}")

    if dry_run:
        print(f"\n[DRY RUN] Would register {len(all_phrases)} phrases with Rev AI")
        print("Phrases:")
        for p in all_phrases:
            print(f"  - {p}")
        return

    # Submit to Rev AI
    token = os.environ.get('REV_AI_TOKEN')
    if not token:
        print("ERROR: REV_AI_TOKEN not set")
        sys.exit(1)

    client = custom_vocabularies_client.RevAiCustomVocabulariesClient(token)
    custom_vocab = CustomVocabulary(all_phrases)

    print(f"\nSubmitting vocabulary to Rev AI...")
    job = client.submit_custom_vocabularies([custom_vocab])
    vocab_id = job['id']
    print(f"Vocabulary ID: {vocab_id}")
    print(f"Status: {job.get('status', 'unknown')}")

    # Poll until ready if requested
    if wait:
        print(f"\nPolling for completion...")
        for _ in range(20):
            time.sleep(10)
            info = client.get_custom_vocabularies_information(vocab_id)
            status = info.get('status', 'unknown')
            print(f"  Status: {status}")
            if status == 'complete':
                print("  Ready ✓")
                break
            elif status == 'failed':
                print(f"  FAILED: {info}")
                sys.exit(1)

    # Store vocabulary ID on event
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE events SET rev_ai_vocabulary_id = %s WHERE id = %s
    """, (vocab_id, event_id))
    conn.commit()
    print(f"\nStored vocabulary ID on event {event_id} ✓")

    # Verify
    cur.execute("SELECT rev_ai_vocabulary_id FROM events WHERE id = %s", (event_id,))
    stored = cur.fetchone()[0]
    print(f"Verified: events.rev_ai_vocabulary_id = {stored}")
    cur.close()
    conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-id', type=int, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--wait', action='store_true', help='Poll until vocabulary is ready')
    args = parser.parse_args()
    setup_vocabulary(args.event_id, dry_run=args.dry_run, wait=args.wait)
