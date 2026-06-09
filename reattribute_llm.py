#!/usr/bin/env python3
"""
reattribute_llm.py — Verum Signal LLM-based post-debate attribution verification.

Sends the full debate transcript to Claude Sonnet with speaker context and asks
it to identify utterances that are likely attributed to the wrong speaker.

Runs AFTER the debate, within the 60-minute promotion window. Designed to catch
misattributions that keyword-based checks (speaker_context.py) miss.

Cost: ~$0.30-1.00 per debate depending on transcript length.

USAGE:
  python3 reattribute_llm.py --event-id 12 --dry-run     # review only
  python3 reattribute_llm.py --event-id 12                # review only (default safe)
  python3 reattribute_llm.py --event-id 12 --apply        # correct misattributions in DB
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

if os.path.exists('.env'):
    load_dotenv(override=False)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
import anthropic

CHUNK_SIZE = 60  # utterances per LLM call — keeps response under max_tokens

def get_connection():
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'),
        connect_timeout=10,
    )


def fetch_transcript(conn, event_id):
    """Fetch all utterances for an event, ordered by utterance_order."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT su.id, su.utterance_order, su.utterance_text,
                   su.speaker_id, s.name, s.speaker_type,
                   su.attribution_confidence, su.attribution_uncertain
            FROM speaker_utterances su
            LEFT JOIN speakers s ON s.id = su.speaker_id
            WHERE su.event_id = %s
            ORDER BY su.utterance_order ASC
        """, (event_id,))
        return cur.fetchall()


def fetch_event_speakers(conn, event_id):
    """Fetch speaker info for this event."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.id, s.name, s.speaker_type, s.party, es.speaker_order
            FROM event_speakers es
            JOIN speakers s ON s.id = es.speaker_id
            WHERE es.event_id = %s
            ORDER BY es.speaker_order
        """, (event_id,))
        return cur.fetchall()


def fetch_event_info(conn, event_id):
    """Fetch event metadata."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT event_name, event_date, start_time, timezone
            FROM events WHERE id = %s
        """, (event_id,))
        return cur.fetchone()


def build_transcript_text(utterances, speakers_by_id):
    """Build a numbered transcript for the LLM prompt."""
    lines = []
    for uid, uorder, utext, speaker_id, speaker_name, stype, conf, uncertain in utterances:
        name = speaker_name or 'UNKNOWN'
        flag = ' [UNCERTAIN]' if uncertain else ''
        lines.append(f"[{uorder}] {name}: {utext}{flag}")
    return '\n'.join(lines)


def build_speaker_context(speakers):
    """Build speaker description block for the prompt."""
    lines = []
    for sid, name, stype, party, order in speakers:
        role = stype or 'unknown'
        party_str = f' ({party})' if party else ''
        lines.append(f"- speaker_id={sid}: {name} — {role}{party_str}")
    return '\n'.join(lines)


def chunk_utterances(utterances, chunk_size):
    """Split utterances into chunks for sequential LLM processing."""
    return [utterances[i:i+chunk_size] for i in range(0, len(utterances), chunk_size)]

def call_llm_chunk(client, chunk, speaker_context, event_name, event_date, chunk_num, total_chunks):
    """Call Claude on a single chunk. Returns list of suspect dicts."""
    lines = []
    for uid, uorder, utext, speaker_id, speaker_name, stype, conf, uncertain in chunk:
        name = speaker_name or 'UNKNOWN'
        flag = ' [UNCERTAIN]' if uncertain else ''
        lines.append(f"[{uorder}] {name}: {utext}{flag}")
    transcript_chunk = '\n'.join(lines)

    prompt = f"""You are verifying speaker attributions in a political debate transcript.
EVENT: {event_name}
DATE: {event_date}
SPEAKERS:
{speaker_context}

TASK:
Review the transcript below. Each line is formatted as:
[utterance_order] Speaker Name: utterance text

Identify utterances attributed to the WRONG speaker. Focus on:
1. First-person claims about roles/experience that belong to a different speaker
2. Self-referential statements that don't match the attributed speaker's background
3. Obvious speaker transitions where the attribution didn't switch

Do NOT flag:
- Candidates referencing each other's records
- Moderator questions attributed to the moderator
- Policy disagreements where either candidate could plausibly make the statement
- Vague or generic statements

Respond with a JSON array only. Each element:
- "utterance_order": the [number] from the transcript
- "current_speaker": who it's currently attributed to
- "likely_speaker": who likely said it
- "confidence": "high" or "medium"
- "reason": brief explanation (10 words max)

If all attributions look correct, respond with: []
Be conservative. Only flag attributions you are genuinely confident are wrong.

TRANSCRIPT (chunk {chunk_num}/{total_chunks}):
{transcript_chunk}

Respond with ONLY the JSON array, no other text."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    result_text = response.content[0].text.strip()

    # Strip markdown fences
    clean = result_text
    if clean.startswith('```'):
        clean = clean.split('\n', 1)[1] if '\n' in clean else clean[3:]
    if clean.endswith('```'):
        clean = clean.rsplit('```', 1)[0]
    clean = clean.strip()
    if clean.startswith('json'):
        clean = clean[4:].strip()

    # Salvage truncated JSON
    if not clean.endswith(']'):
        last = clean.rfind('},')
        if last > 0:
            clean = clean[:last+1] + ']'
            print(f"    (chunk {chunk_num}: JSON truncated — salvaged partial response)")

    suspects = json.loads(clean)
    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    return suspects, tokens_in, tokens_out

def run_verification(event_id, apply_corrections=False):
    print("=" * 68)
    print(f"Verum Signal — LLM attribution verification")
    print(f"Event ID: {event_id}  |  Mode: {'APPLY' if apply_corrections else 'REVIEW ONLY'}")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 68)

    conn = get_connection()

    # Fetch data
    event_info = fetch_event_info(conn, event_id)
    if not event_info:
        print(f"ERROR: Event {event_id} not found")
        sys.exit(1)
    event_name, event_date, start_time, tz = event_info

    speakers = fetch_event_speakers(conn, event_id)
    utterances = fetch_transcript(conn, event_id)

    print(f"\n  Event: {event_name}")
    print(f"  Date: {event_date} · {start_time} {tz}")
    print(f"  Speakers: {len(speakers)}")
    print(f"  Utterances: {len(utterances)}")

    if not utterances:
        print("  No utterances found.")
        conn.close()
        return

    # Build speaker lookup
    speakers_by_id = {sid: (name, stype, party) for sid, name, stype, party, order in speakers}
    speaker_context = build_speaker_context(speakers)
    transcript = build_transcript_text(utterances, speakers_by_id)

    # Estimate cost
    word_count = len(transcript.split())
    est_input_tokens = int(word_count * 1.3)  # rough token estimate
    print(f"  Transcript: ~{word_count} words (~{est_input_tokens} input tokens)")
    print(f"  Estimated cost: ~${est_input_tokens * 3 / 1_000_000:.2f} input + output\n")

    # Call LLM in chunks to avoid response truncation
    chunks = chunk_utterances(utterances, CHUNK_SIZE)
    total_chunks = len(chunks)
    print(f"  Chunking into {total_chunks} chunk(s) of up to {CHUNK_SIZE} utterances each")

    client = anthropic.Anthropic()
    all_suspects = []
    total_tokens_in = 0
    total_tokens_out = 0

    for i, chunk in enumerate(chunks, 1):
        print(f"  Calling Claude — chunk {i}/{total_chunks} ({len(chunk)} utterances)...")
        try:
            suspects, t_in, t_out = call_llm_chunk(
                client, chunk, speaker_context, event_name, str(event_date),
                i, total_chunks
            )
            all_suspects.extend(suspects)
            total_tokens_in += t_in
            total_tokens_out += t_out
            print(f"    → {len(suspects)} suspect(s) | {t_in} in / {t_out} out tokens")
        except json.JSONDecodeError as e:
            print(f"    ERROR: Could not parse chunk {i} response as JSON: {e} — skipping")
        except Exception as e:
            print(f"    ERROR: chunk {i} failed: {e} — skipping")

    cost = (total_tokens_in * 3 + total_tokens_out * 15) / 1_000_000
    print(f"  Total tokens: {total_tokens_in} in / {total_tokens_out} out | Cost: ${cost:.4f}")
    suspects = all_suspects

    # Display results
    print(f"\n{'─' * 50}")
    print(f"  RESULTS: {len(suspects)} suspected misattribution(s)")
    print(f"{'─' * 50}")

    if not suspects:
        print("  ✓ All attributions look correct")
        conn.close()
        return

    # Build utterance lookup for corrections
    utterance_lookup = {}
    for uid, uorder, utext, speaker_id, speaker_name, stype, conf, uncertain in utterances:
        utterance_lookup[uorder] = {
            'uid': uid, 'text': utext, 'speaker_id': speaker_id,
            'speaker_name': speaker_name,
        }

    # Speaker name -> id lookup
    name_to_id = {name.lower(): sid for sid, name, stype, party, order in speakers}

    corrections = []
    for suspect in suspects:
        uorder = suspect.get('utterance_order')
        confidence = suspect.get('confidence', 'unknown')
        reason = suspect.get('reason', '')
        likely = suspect.get('likely_speaker', '')

        udata = utterance_lookup.get(uorder)
        if not udata:
            print(f"\n  ⚠ utterance_order={uorder} not found in DB — skipping")
            continue

        # Resolve likely speaker to ID
        likely_id = name_to_id.get(likely.lower())
        if not likely_id:
            # Try partial match
            for n, sid in name_to_id.items():
                if likely.lower() in n or n in likely.lower():
                    likely_id = sid
                    break

        print(f"\n  ⚠ Utterance {uorder} [{confidence} confidence]")
        print(f"    text: {udata['text'][:80]}")
        print(f"    current: {udata['speaker_name']} (speaker_id={udata['speaker_id']})")
        print(f"    likely:  {likely} (speaker_id={likely_id})")
        print(f"    reason:  {reason}")

        if likely_id and likely_id != udata['speaker_id']:
            corrections.append({
                'uid': udata['uid'],
                'uorder': uorder,
                'old_speaker_id': udata['speaker_id'],
                'new_speaker_id': likely_id,
                'old_name': udata['speaker_name'],
                'new_name': likely,
                'reason': reason,
                'confidence': confidence,
            })

    if not corrections:
        print("\n  No actionable corrections (suspects may already be correct or unresolvable)")
        conn.close()
        return

    print(f"\n{'─' * 50}")
    print(f"  {len(corrections)} actionable correction(s)")
    print(f"{'─' * 50}")

    if not apply_corrections:
        print("\n  Run with --apply to write corrections to DB")
        print("  Review each suspect above before applying.\n")
        conn.close()
        return

    # Apply corrections
    print("\n  Applying corrections...")
    cur = conn.cursor()
    for fix in corrections:
        if fix['confidence'] != 'high':
            print(f"    SKIP utterance {fix['uorder']} — {fix['confidence']} confidence (only applying high)")
            continue

        # Update speaker_utterances
        cur.execute("""
            UPDATE speaker_utterances SET speaker_id = %s
            WHERE id = %s AND event_id = %s
        """, (fix['new_speaker_id'], fix['uid'], event_id))

        # Update any claims sourced from this utterance
        cur.execute("""
            UPDATE claims SET
                speaker_id = %s,
                speaker = %s,
                verdict = NULL,
                verdict_status = 'provisional',
                revision_history = COALESCE(revision_history, '[]'::jsonb) || %s::jsonb
            WHERE utterance_id = %s AND event_id = %s
        """, (
            fix['new_speaker_id'],
            fix['new_name'],
            json.dumps([{
                'action': 'llm_reattribution',
                'old_speaker_id': fix['old_speaker_id'],
                'new_speaker_id': fix['new_speaker_id'],
                'reason': fix['reason'],
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }]),
            fix['uid'],
            event_id,
        ))

        # Write public correction note
        cur.execute(
            "UPDATE claims SET correction_note = %s WHERE utterance_id = %s AND event_id = %s AND correction_note IS NULL",
            ('Correction: Originally attributed to ' + fix['old_name'] + '. Attribution corrected to ' + fix['new_name'] + ' following post-debate review.',
             fix['uid'], event_id))

        print(f"    ✓ Utterance {fix['uorder']}: {fix['old_name']} → {fix['new_name']}")

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n  Done. Run railway_api_refresh.py to propagate to api_debate_claims.")
    print(f"  Then verify: python3 post_debate_check.py --event-id {event_id}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-id', type=int, required=True)
    parser.add_argument('--apply', action='store_true',
                        help='Write corrections to DB (default: review only)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Alias for default review-only mode (no DB writes)')
    args = parser.parse_args()

    # --dry-run and default both = review only. Only --apply writes.
    apply = args.apply and not args.dry_run

    run_verification(args.event_id, apply_corrections=apply)
