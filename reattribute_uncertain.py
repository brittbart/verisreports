#!/usr/bin/env python3
"""
reattribute_uncertain.py — Verum Signal post-debate re-attribution pass.

For utterances flagged attribution_uncertain=TRUE, infers speaker from
surrounding context window. Logic:
  - If N utterances before AND after are all the same speaker → assign that speaker
  - If window is mixed → leave uncertain (manual review via /disputes)

USAGE:
  python3 reattribute_uncertain.py --event-id 10 --dry-run
  python3 reattribute_uncertain.py --event-id 10
  python3 reattribute_uncertain.py --event-id 10 --window 3  # context window size
"""

import argparse
import os
import sys
from dotenv import load_dotenv

load_dotenv('/home/veris/projects/veris/.env')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import psycopg2

CONTEXT_WINDOW = 3  # utterances on each side to examine

def get_connection():
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'),
        connect_timeout=10,
    )

def run_reattribution(event_id, window=CONTEXT_WINDOW, dry_run=False):
    print("=" * 68)
    print(f"Verum Signal — Post-debate re-attribution pass")
    print(f"Event ID: {event_id}  |  Window: ±{window}  |  Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print("=" * 68)

    conn = get_connection()
    cur = conn.cursor()

    # Fetch all uncertain utterances for this event
    cur.execute("""
        SELECT su.id, su.utterance_order, su.utterance_text,
               su.attribution_confidence, su.speaker_id
        FROM speaker_utterances su
        WHERE su.event_id = %s
          AND su.attribution_uncertain = TRUE
        ORDER BY su.utterance_order ASC
    """, (event_id,))
    uncertain = cur.fetchall()
    print(f"\nFound {len(uncertain)} uncertain utterances\n")

    if not uncertain:
        print("Nothing to re-attribute.")
        conn.close()
        return

    stats = {'resolved': 0, 'ambiguous': 0, 'errors': 0}

    for uid, uorder, utext, conf, current_speaker in uncertain:
        # Fetch context window: N utterances before and after by utterance_order
        cur.execute("""
            SELECT speaker_id FROM speaker_utterances
            WHERE event_id = %s
              AND attribution_uncertain = FALSE
              AND speaker_id IS NOT NULL
              AND utterance_order < %s
            ORDER BY utterance_order DESC
            LIMIT %s
        """, (event_id, uorder, window))
        before = [r[0] for r in cur.fetchall()]

        cur.execute("""
            SELECT speaker_id FROM speaker_utterances
            WHERE event_id = %s
              AND attribution_uncertain = FALSE
              AND speaker_id IS NOT NULL
              AND utterance_order > %s
            ORDER BY utterance_order ASC
            LIMIT %s
        """, (event_id, uorder, window))
        after = [r[0] for r in cur.fetchall()]

        context = before + after
        if not context:
            print(f"  [{uid}] order={uorder} conf={conf:.2f if conf else '?'} — no context, skipping")
            stats['ambiguous'] += 1
            continue

        # Check if all context utterances agree on the same speaker
        unique_speakers = set(context)
        if len(unique_speakers) == 1:
            inferred_speaker = unique_speakers.pop()
            print(f"  [{uid}] order={uorder} conf={conf:.2f if conf else '?'} → speaker_id={inferred_speaker} (context unanimous, n={len(context)})")
            print(f"         text: {utext[:70]}")

            if not dry_run:
                cur.execute("""
                    UPDATE speaker_utterances SET
                        speaker_id = %s,
                        attribution_uncertain = FALSE
                    WHERE id = %s
                """, (inferred_speaker, uid))
                # Propagate to any claims sourced from this utterance
                cur.execute("""
                    SELECT s.name FROM speakers s WHERE s.id = %s
                """, (inferred_speaker,))
                _sname_row = cur.fetchone()
                _sname = _sname_row[0] if _sname_row else str(inferred_speaker)
                cur.execute("""
                    UPDATE claims SET
                        speaker_id = %s,
                        speaker = %s,
                        revision_history = COALESCE(revision_history, '[]'::jsonb) || %s::jsonb
                    WHERE utterance_id = %s AND event_id = %s
                """, (
                    inferred_speaker, _sname,
                    json.dumps([{
                        'action': 'context_reattribution',
                        'old_speaker_id': current_speaker,
                        'new_speaker_id': inferred_speaker,
                        'method': 'context_window_unanimous',
                    }]),
                    uid, event_id,
                ))
                conn.commit()
            stats['resolved'] += 1
        else:
            # Mixed context — check if before and after agree independently
            before_set = set(before)
            after_set = set(after)
            if len(before_set) == 1 and len(after_set) == 1 and before_set == after_set:
                # Before and after both unanimous and agree with each other
                inferred_speaker = before_set.pop()
                print(f"  [{uid}] order={uorder} conf={conf:.2f if conf else '?'} → speaker_id={inferred_speaker} (before+after agree)")
                print(f"         text: {utext[:70]}")
                if not dry_run:
                    cur.execute("""
                        UPDATE speaker_utterances SET
                            speaker_id = %s,
                            attribution_uncertain = FALSE
                        WHERE id = %s
                    """, (inferred_speaker, uid))
                    cur.execute("""
                        SELECT s.name FROM speakers s WHERE s.id = %s
                    """, (inferred_speaker,))
                    _sname_row = cur.fetchone()
                    _sname = _sname_row[0] if _sname_row else str(inferred_speaker)
                    cur.execute("""
                        UPDATE claims SET
                            speaker_id = %s,
                            speaker = %s,
                            revision_history = COALESCE(revision_history, '[]'::jsonb) || %s::jsonb
                        WHERE utterance_id = %s AND event_id = %s
                    """, (
                        inferred_speaker, _sname,
                        json.dumps([{
                            'action': 'context_reattribution',
                            'old_speaker_id': current_speaker,
                            'new_speaker_id': inferred_speaker,
                            'method': 'context_window_before_after_agree',
                        }]),
                        uid, event_id,
                    ))
                    conn.commit()
                stats['resolved'] += 1
            else:
                print(f"  [{uid}] order={uorder} conf={conf:.2f if conf else '?'} — ambiguous context {unique_speakers}, leaving for manual review")
                print(f"         text: {utext[:70]}")
                stats['ambiguous'] += 1

    conn.close()

    print("\n" + "=" * 68)
    print(f"Complete")
    print(f"  Uncertain:  {len(uncertain)}")
    print(f"  Resolved:   {stats['resolved']}")
    print(f"  Ambiguous:  {stats['ambiguous']} (manual review needed)")
    print(f"  Errors:     {stats['errors']}")
    print("=" * 68)

    if stats['ambiguous'] > 0:
        print(f"\nAmbiguous utterances remain in DB with attribution_uncertain=TRUE.")
        print(f"Review via: SELECT id, utterance_order, utterance_text FROM speaker_utterances")
        print(f"            WHERE event_id={event_id} AND attribution_uncertain=TRUE ORDER BY utterance_order;")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-id', type=int, required=True)
    parser.add_argument('--window', type=int, default=CONTEXT_WINDOW)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run_reattribution(args.event_id, window=args.window, dry_run=args.dry_run)
