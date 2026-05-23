#!/usr/bin/env python3
"""
Patch 3 — Add CBS4 moderator to speakers table and link to event 9
Risk addressed: D-15 (moderator attributed to first candidate via order fallback)

Inserts Shaun Boyd (CBS4 political specialist, assumed moderator for May 26)
as speaker_type='moderator' and links to event_id=9 with speaker_order=0.

speaker_order=0 places her before candidates (1=Bottoms, 2=Kirkmeyer, 3=Marx)
so order fallback assigns her correctly if she speaks first.

SCOREABLE_SPEAKER_TYPES = {'politician', 'official'} in extract_debate_claims.py
already excludes 'moderator' from generating scoreable claims.
"""

import sys
sys.path.insert(0, '.')
from debate_stream_deepgram import get_db_conn

EVENT_ID = 9
MODERATOR_NAME = 'Shaun Boyd'
MODERATOR_TYPE = 'moderator'
SPEAKER_ORDER = 0  # Before candidates (1=Bottoms, 2=Kirkmeyer, 3=Marx)

def apply():
    conn = get_db_conn()
    cur = conn.cursor()

    try:
        # 1. Confirm not already in speakers
        cur.execute("SELECT id FROM speakers WHERE name = %s", (MODERATOR_NAME,))
        existing = cur.fetchone()
        if existing:
            speaker_id = existing[0]
            print(f"Speaker already exists: id={speaker_id}, name={MODERATOR_NAME}")
        else:
            # Check speakers table columns before inserting
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='speakers' ORDER BY ordinal_position
            """)
            cols = [r[0] for r in cur.fetchall()]
            print(f"speakers columns: {cols}")

            # Insert moderator — only set columns we know exist
            cur.execute("""
                INSERT INTO speakers (name, speaker_type)
                VALUES (%s, %s)
                RETURNING id
            """, (MODERATOR_NAME, MODERATOR_TYPE))
            speaker_id = cur.fetchone()[0]
            print(f"Inserted speaker: id={speaker_id}, name={MODERATOR_NAME}, type={MODERATOR_TYPE}")

        # 2. Link to event 9 — check not already linked
        cur.execute("""
            SELECT 1 FROM event_speakers
            WHERE event_id = %s AND speaker_id = %s
        """, (EVENT_ID, speaker_id))
        if cur.fetchone():
            print(f"Already linked to event {EVENT_ID} — skipping event_speakers insert")
        else:
            cur.execute("""
                INSERT INTO event_speakers (event_id, speaker_id, speaker_order)
                VALUES (%s, %s, %s)
            """, (EVENT_ID, speaker_id, SPEAKER_ORDER))
            print(f"Linked speaker {speaker_id} to event {EVENT_ID} with speaker_order={SPEAKER_ORDER}")

        conn.commit()

        # 3. Verify final state
        cur.execute("""
            SELECT es.speaker_order, s.id, s.name, s.speaker_type
            FROM event_speakers es
            JOIN speakers s ON s.id = es.speaker_id
            WHERE es.event_id = %s
            ORDER BY es.speaker_order
        """, (EVENT_ID,))
        rows = cur.fetchall()
        print(f"\nFinal event_speakers for event {EVENT_ID}:")
        for row in rows:
            print(f"  order={row[0]} id={row[1]} name={row[2]} type={row[3]}")

        # 4. Verify name map includes moderator fragments
        from debate_stream_deepgram import build_name_map, get_event_speakers
        speakers = get_event_speakers(EVENT_ID)
        name_map = build_name_map(speakers)
        print(f"\nName map fragments for event {EVENT_ID}:")
        for fragment, sid in sorted(name_map.items()):
            print(f"  '{fragment}' -> speaker_id={sid}")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    apply()
