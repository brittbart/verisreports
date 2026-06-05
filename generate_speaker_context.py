#!/usr/bin/env python3
"""
generate_speaker_context.py — Auto-generate speaker context for debate events.

Calls Claude Sonnet with speaker names and asks it to generate exclusive
keywords and roles for each speaker. Writes results to speaker_event_context table.

USAGE:
  python3 generate_speaker_context.py --event-id 13              # generate + save
  python3 generate_speaker_context.py --event-id 13 --dry-run    # preview only
  python3 generate_speaker_context.py --event-id 13 --regenerate # overwrite existing
"""

import argparse
import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

if os.path.exists('.env'):
    load_dotenv(override=False)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
import anthropic


def get_connection():
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'), connect_timeout=10,
    )


def fetch_event_speakers(conn, event_id):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.id, s.name, s.speaker_type, s.party,
                   es.speaker_order
            FROM event_speakers es
            JOIN speakers s ON s.id = es.speaker_id
            WHERE es.event_id = %s
              AND s.speaker_type IN ('politician', 'official')
            ORDER BY es.speaker_order
        """, (event_id,))
        return cur.fetchall()


def fetch_event_info(conn, event_id):
    with conn.cursor() as cur:
        cur.execute("SELECT event_name, event_date FROM events WHERE id = %s", (event_id,))
        return cur.fetchone()


def check_existing(conn, event_id):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT speaker_id FROM speaker_event_context
            WHERE event_id = %s
        """, (event_id,))
        return [r[0] for r in cur.fetchall()]


def generate_context(event_name, speakers):
    """Call Sonnet to generate exclusive keywords for each speaker."""
    speaker_descriptions = []
    for sid, name, stype, party, order in speakers:
        party_str = f" ({party})" if party else ""
        speaker_descriptions.append(f"- {name}: {stype}{party_str}, speaker_id={sid}")

    prompt = f"""You are generating speaker attribution context for a political debate verification system.

EVENT: {event_name}

SPEAKERS:
{chr(10).join(speaker_descriptions)}

For each speaker, generate two lists:

1. **roles**: Official titles and positions UNIQUE to this speaker (e.g., "attorney general", "senator", "superintendent"). These should be roles that ONLY this speaker holds — not shared titles.

2. **exclusive_keywords**: Phrases that would strongly indicate this speaker is talking about their OWN record or experience. These should be terms that, if found in a claim attributed to a DIFFERENT speaker, would suggest a misattribution. Include:
   - Their unique institutional affiliations
   - Programs or initiatives they are known for
   - Specific accomplishments tied to their role
   - Phrases combining first-person language with their role ("as attorney general", "my time in the senate")

IMPORTANT RULES:
- Only include terms that are genuinely EXCLUSIVE to one speaker
- Do NOT include generic policy terms (e.g., "education", "healthcare") — both candidates can discuss these
- Do NOT include the speaker's name — the system already handles name detection separately
- Keep keywords lowercase
- Prefer 2-4 word phrases over single words (more specific = fewer false positives)

Respond with ONLY a JSON object mapping speaker_id to their context:
{{
  "190": {{
    "roles": ["senator", "superintendent"],
    "exclusive_keywords": ["denver public schools", "as a senator", "largest school district"]
  }},
  "191": {{
    "roles": ["attorney general"],
    "exclusive_keywords": ["attorney general", "ag office", "consumer protection"]
  }}
}}

No other text. No markdown fences."""

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    result_text = response.content[0].text.strip()

    # Clean up response
    clean = result_text
    if clean.startswith('```'):
        clean = clean.split('\n', 1)[1]
    if clean.endswith('```'):
        clean = clean.rsplit('```', 1)[0]
    clean = clean.strip()
    if clean.startswith('json'):
        clean = clean[4:].strip()

    parsed = json.loads(clean)

    # Report cost
    usage = response.usage
    cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
    print(f"  Tokens: {usage.input_tokens} in / {usage.output_tokens} out (${cost:.4f})")

    return parsed


def run(event_id, dry_run=False, regenerate=False):
    print("=" * 60)
    print(f"Verum Signal — Speaker context generation")
    print(f"Event ID: {event_id}  |  Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print("=" * 60)

    conn = get_connection()

    event_info = fetch_event_info(conn, event_id)
    if not event_info:
        print(f"  ✗ Event {event_id} not found")
        sys.exit(1)
    event_name, event_date = event_info
    print(f"  Event: {event_name} ({event_date})")

    speakers = fetch_event_speakers(conn, event_id)
    if not speakers:
        print(f"  ✗ No politician/official speakers found for event {event_id}")
        sys.exit(1)
    print(f"  Speakers: {', '.join(f'{name} ({sid})' for sid, name, *_ in speakers)}")

    # Check existing
    existing = check_existing(conn, event_id)
    if existing and not regenerate:
        print(f"  ✓ Context already exists for speaker_ids {existing}")
        print(f"    Use --regenerate to overwrite")
        conn.close()
        return

    # Generate via LLM
    print(f"\n  Generating context via Claude Sonnet...")
    try:
        context = generate_context(event_name, speakers)
    except Exception as e:
        print(f"  ✗ Generation failed: {e}")
        conn.close()
        return

    # Display results
    print(f"\n  Generated context:")
    for sid_str, ctx in context.items():
        sid = int(sid_str)
        name = next((n for s, n, *_ in speakers if s == sid), f"speaker_{sid}")
        print(f"\n  {name} (speaker_id={sid}):")
        print(f"    roles: {ctx.get('roles', [])}")
        print(f"    exclusive_keywords: {ctx.get('exclusive_keywords', [])}")

    if dry_run:
        print(f"\n  [DRY RUN] Would write to speaker_event_context")
        conn.close()
        return

    # Write to DB
    cur = conn.cursor()
    for sid_str, ctx in context.items():
        sid = int(sid_str)
        roles = json.dumps(ctx.get('roles', []))
        keywords = json.dumps(ctx.get('exclusive_keywords', []))

        if regenerate:
            cur.execute("""
                INSERT INTO speaker_event_context
                    (event_id, speaker_id, exclusive_keywords, roles, generated_by, updated_at)
                VALUES (%s, %s, %s::jsonb, %s::jsonb, 'llm', NOW())
                ON CONFLICT (event_id, speaker_id) DO UPDATE SET
                    exclusive_keywords = EXCLUDED.exclusive_keywords,
                    roles = EXCLUDED.roles,
                    generated_by = 'llm',
                    updated_at = NOW()
            """, (event_id, sid, keywords, roles))
        else:
            cur.execute("""
                INSERT INTO speaker_event_context
                    (event_id, speaker_id, exclusive_keywords, roles, generated_by)
                VALUES (%s, %s, %s::jsonb, %s::jsonb, 'llm')
                ON CONFLICT (event_id, speaker_id) DO NOTHING
            """, (event_id, sid, keywords, roles))

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n  ✓ Context saved to speaker_event_context")
    print(f"  ✓ Ready for debate extraction")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-id', type=int, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--regenerate', action='store_true',
                        help='Overwrite existing context')
    args = parser.parse_args()
    run(args.event_id, dry_run=args.dry_run, regenerate=args.regenerate)
