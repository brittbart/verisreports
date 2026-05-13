#!/usr/bin/env python3
"""
ingest_debate_transcript.py — Verum Signal v1.7 debate transcript ingester.

Parses a structured debate transcript text file and creates:
  - An event record in the events table
  - Speaker records for all participants (via resolve_speaker)
  - speaker_utterances rows for each speaking turn

Transcript format expected (one speaker turn per block):
  SPEAKER NAME: text of what they said

  SPEAKER NAME: more text

Speakers are classified automatically:
  - Trump, Harris → politician
  - David Muir, Linsey Davis → moderator

Only politician utterances are flagged for extraction.

USAGE:
  cd ~/projects/veris && source venv/bin/activate
  python3 ingest_debate_transcript.py --transcript /tmp/trump_harris_2024.txt --dry-run
  python3 ingest_debate_transcript.py --transcript /tmp/trump_harris_2024.txt

OUTPUT:
  Prints ingestion summary. On success, event_id is printed for use
  in the extraction step.
"""

import argparse
import os
import re
import sys

import psycopg2
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_speaker import find_or_create_speaker, normalize_name

# ---------------------------------------------------------------------------
# Event metadata
# ---------------------------------------------------------------------------

EVENT = {
    'slug':               'iowa-senate-dem-2026-r2',
    'event_type':         'debate',
    'event_name':         'Iowa Senate: Democratic primary debate — Round 2',
    'event_date':         '2026-05-14',
    'venue':              'Iowa',
    'transcript_url':     'https://www.iowapublicradio.org',
    'transcript_source':  'Iowa Public Radio',
    'is_public':          True,
    'methodology_version': 'v1.7',
}

# ---------------------------------------------------------------------------
# Speaker classification
# Speaker type assigned based on role at time of event.
# ---------------------------------------------------------------------------

SPEAKER_CLASSIFICATIONS = {
    'donald trump':   {'speaker_type': 'politician', 'role': 'Presidential candidate (Republican)', 'party': 'Republican'},
    'trump':          {'speaker_type': 'politician', 'role': 'Presidential candidate (Republican)', 'party': 'Republican'},
    'former president donald trump': {'speaker_type': 'politician', 'role': 'Presidential candidate (Republican)', 'party': 'Republican'},
    'vice president kamala harris': {'speaker_type': 'politician', 'role': 'Presidential candidate (Democrat)', 'party': 'Democratic'},
    'vice president harris': {'speaker_type': 'politician', 'role': 'Presidential candidate (Democrat)', 'party': 'Democratic'},
    'president trump':  {'speaker_type': 'politician', 'role': 'Presidential candidate (Republican)', 'party': 'Republican'},
    'former president donald trump': {'speaker_type': 'politician', 'role': 'Presidential candidate (Republican)', 'party': 'Republican'},
    'vice president kamala harris': {'speaker_type': 'politician', 'role': 'Presidential candidate (Democrat)', 'party': 'Democratic'},
    'vice president harris': {'speaker_type': 'politician', 'role': 'Presidential candidate (Democrat)', 'party': 'Democratic'},
    'president trump':  {'speaker_type': 'politician', 'role': 'Presidential candidate (Republican)', 'party': 'Republican'},
    'kamala harris':  {'speaker_type': 'politician', 'role': 'Presidential candidate (Democrat)', 'party': 'Democratic'},
    'harris':         {'speaker_type': 'politician', 'role': 'Presidential candidate (Democrat)', 'party': 'Democratic'},
    'david muir':     {'speaker_type': 'moderator',  'role': 'Debate moderator (ABC News)', 'party': None},
    'linsey davis':   {'speaker_type': 'moderator',  'role': 'Debate moderator (ABC News)', 'party': None},
    'lindsey davis':  {'speaker_type': 'moderator',  'role': 'Debate moderator (ABC News)', 'party': None},
}

# Only these speaker types get utterances pushed to extraction
SCOREABLE_TYPES = {'politician', 'official'}

# ---------------------------------------------------------------------------
# Transcript parser
# ---------------------------------------------------------------------------

# Matches "SPEAKER NAME: text" or "Speaker Name (timestamp): text"
SPEAKER_LINE_RE = re.compile(
    r'^([A-Z][A-Za-z\s\.\-\']+?)(?:\s*\([\d:]+\))?\s*:\s*(.+)',
    re.DOTALL
)

def parse_transcript(text):
    """
    Parse a debate transcript into a list of (speaker_name, utterance_text) tuples.
    Handles multi-line utterances by joining continuation lines.
    """
    utterances = []
    current_speaker = None
    current_text = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current_speaker and current_text:
                utterances.append((current_speaker, ' '.join(current_text)))
                current_speaker = None
                current_text = []
            continue

        m = SPEAKER_LINE_RE.match(line)
        if m:
            # Save previous utterance
            if current_speaker and current_text:
                utterances.append((current_speaker, ' '.join(current_text)))
            current_speaker = m.group(1).strip()
            current_text = [m.group(2).strip()]
        else:
            # Continuation of current utterance
            if current_speaker:
                current_text.append(line)

    # Flush last utterance
    if current_speaker and current_text:
        utterances.append((current_speaker, ' '.join(current_text)))

    return utterances


def group_into_extraction_units(utterances, min_words=20, max_words=120):
    """
    Group short utterances from the same speaker into extraction units
    of 2-4 sentences. Each unit should be self-contained for claim extraction.
    Returns list of (speaker_name, combined_text).
    """
    units = []
    i = 0
    while i < len(utterances):
        speaker, text = utterances[i]
        words = text.split()

        # If utterance is already in good range, use as-is
        if min_words <= len(words) <= max_words:
            units.append((speaker, text))
            i += 1
            continue

        # If too short, try to join with next utterance from same speaker
        if len(words) < min_words and i + 1 < len(utterances):
            next_speaker, next_text = utterances[i + 1]
            if next_speaker == speaker:
                combined = text + ' ' + next_text
                if len(combined.split()) <= max_words:
                    units.append((speaker, combined))
                    i += 2
                    continue

        # If too long, split on sentence boundaries
        if len(words) > max_words:
            sentences = re.split(r'(?<=[.!?])\s+', text)
            chunk = []
            chunk_words = 0
            for sent in sentences:
                sent_words = sent.split()
                if chunk_words + len(sent_words) > max_words and chunk:
                    units.append((speaker, ' '.join(chunk)))
                    chunk = [sent]
                    chunk_words = len(sent_words)
                else:
                    chunk.append(sent)
                    chunk_words += len(sent_words)
            if chunk:
                units.append((speaker, ' '.join(chunk)))
            i += 1
            continue

        units.append((speaker, text))
        i += 1

    return units


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


def get_or_create_event(conn, event_data, dry_run):
    """Insert event record if not exists. Returns event_id."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM events WHERE slug = %s", (event_data['slug'],))
        row = cur.fetchone()
        if row:
            print(f"  Event already exists: id={row[0]}")
            return row[0]

        if dry_run:
            print(f"  [dry-run] Would create event: {event_data['slug']}")
            return None

        cur.execute("""
            INSERT INTO events (
                slug, event_type, event_name, event_date, venue,
                transcript_url, transcript_source, is_public, methodology_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            event_data['slug'], event_data['event_type'], event_data['event_name'],
            event_data['event_date'], event_data['venue'], event_data['transcript_url'],
            event_data['transcript_source'], event_data['is_public'],
            event_data['methodology_version'],
        ))
        event_id = cur.fetchone()[0]
        conn.commit()
        print(f"  Created event: id={event_id}")
        return event_id


def classify_and_update_speaker(conn, speaker_id, normalized, dry_run):
    """Apply classification metadata to a speaker record."""
    classification = SPEAKER_CLASSIFICATIONS.get(normalized, {})
    if not classification:
        return

    speaker_type = classification.get('speaker_type', 'other')
    role = classification.get('role')
    party = classification.get('party')

    if dry_run:
        return

    with conn.cursor() as cur:
        cur.execute("""
            UPDATE speakers
            SET speaker_type = %s,
                role = %s,
                party = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (speaker_type, role, party, speaker_id))
    conn.commit()


def insert_utterance(conn, speaker_id, event_id, text, order, dry_run):
    """Insert a speaker_utterances row."""
    if dry_run:
        return None

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO speaker_utterances
                (speaker_id, event_id, utterance_text, utterance_order)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (speaker_id, event_id, text, order))
        row = cur.fetchone()
    conn.commit()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Main ingestion
# ---------------------------------------------------------------------------

def ingest(transcript_path, dry_run):
    print("=" * 68)
    print(f"Verum Signal — Debate transcript ingestion")
    print(f"Event:  {EVENT['event_name']}")
    print(f"Source: {transcript_path}")
    print(f"Mode:   {'DRY RUN' if dry_run else 'APPLY'}")
    print("=" * 68)

    with open(transcript_path, 'r', encoding='utf-8') as f:
        text = f.read()

    # Parse
    utterances = parse_transcript(text)
    print(f"\nParsed {len(utterances)} raw utterances")

    # Group into extraction units
    units = group_into_extraction_units(utterances)
    print(f"Grouped into {len(units)} extraction units")

    # Unique speakers
    unique_speakers = list(dict.fromkeys(s for s, _ in units))
    print(f"Unique speakers: {unique_speakers}")

    conn = get_connection()

    # Create event
    print("\n--- Event ---")
    event_id = get_or_create_event(conn, EVENT, dry_run)

    # Resolve speakers
    print("\n--- Speakers ---")
    speaker_ids = {}
    for name in unique_speakers:
        normalized = normalize_name(name)
        speaker_id = find_or_create_speaker(name, conn=conn) if not dry_run else None
        if speaker_id:
            classify_and_update_speaker(conn, speaker_id, normalized, dry_run)
        classification = SPEAKER_CLASSIFICATIONS.get(normalized, {})
        speaker_type = classification.get('speaker_type', 'other')
        speaker_ids[name] = speaker_id
        print(f"  {name!r:<35} -> id={speaker_id}  type={speaker_type}")

    # Insert utterances
    print(f"\n--- Utterances ---")
    politician_count = 0
    moderator_count = 0
    skipped_count = 0
    order = 0

    for speaker_name, utext in units:
        normalized = normalize_name(speaker_name)
        classification = SPEAKER_CLASSIFICATIONS.get(normalized, {})
        speaker_type = classification.get('speaker_type', 'other')
        speaker_id = speaker_ids.get(speaker_name)

        if speaker_type in SCOREABLE_TYPES:
            politician_count += 1
        elif speaker_type == 'moderator':
            moderator_count += 1
        else:
            skipped_count += 1

        if not dry_run and event_id and speaker_id:
            insert_utterance(conn, speaker_id, event_id, utext, order, dry_run)

        order += 1

    conn.close()

    print(f"\n  Politician utterances: {politician_count} (eligible for extraction)")
    print(f"  Moderator utterances:  {moderator_count} (stored, not extracted)")
    print(f"  Other/unknown:         {skipped_count}")
    print(f"  Total:                 {order}")

    print("\n" + "=" * 68)
    if dry_run:
        print("DRY RUN complete. Re-run without --dry-run to apply.")
    else:
        print(f"Ingestion complete. event_id={event_id}")
        print(f"Next step: run extraction against event_id={event_id}")
    print("=" * 68)

    return event_id


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--transcript', required=True, help='Path to transcript .txt file')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not os.path.exists(args.transcript):
        print(f"ERROR: transcript file not found: {args.transcript}")
        sys.exit(1)

    ingest(args.transcript, dry_run=args.dry_run)
