#!/usr/bin/env python3
"""
backfill_claim_origin.py

Backfills claim_origin for claims where it's NULL.

Methodology v1.6 categories:
  outlet_claim     -- claim asserted directly by the outlet
  attributed_claim -- claim attributed to a named speaker (politician, official, etc.)
  wire_reprint     -- verbatim reprint of Reuters/AP/AFP/Bloomberg copy

Strategy: hybrid.
  Phase 1 (free, deterministic) -- heuristics:
    * Source domain in WIRE_SOURCES => outlet_claim (wire scoring its own work)
    * Article body opens with wire byline marker => wire_reprint
    * Speaker + attribution context with quotation marker => attributed_claim
  Phase 2 -- Sonnet via Anthropic SDK for residual unclassified claims.

Usage:
    python3 scripts/backfill_claim_origin.py --dry-run
    python3 scripts/backfill_claim_origin.py --apply
    python3 scripts/backfill_claim_origin.py --apply --limit 50    # cap for safety
    python3 scripts/backfill_claim_origin.py --apply --skip-sonnet # heuristics only
"""

import os
import sys
import re
import argparse
import json
from collections import Counter

import psycopg2
from psycopg2.extras import RealDictCursor

# Mirrors api.py:865 -- keep in sync
WIRE_SOURCES = {'reuters.com', 'apnews.com', 'ap.org', 'bloomberg.com', 'afp.com'}

WIRE_BYLINE_PATTERNS = [
    re.compile(r'\(\s*Reuters\s*\)', re.IGNORECASE),
    re.compile(r'\(\s*AP\s*\)'),                       # case-sensitive
    re.compile(r'\(\s*Associated\s+Press\s*\)', re.IGNORECASE),
    re.compile(r'\(\s*AFP\s*\)'),
    re.compile(r'\(\s*Bloomberg\s*\)', re.IGNORECASE),
]
BYLINE_SEARCH_CHARS = 800
ATTRIBUTION_MARKERS = ('said', 'told', 'according to', 'argued', 'claimed',
                       'stated', 'reported', 'wrote')


def heuristic_classify(claim_row):
    """Returns (origin, reason) on confident classification, (None, why) otherwise."""
    source = (claim_row['source_name'] or '').lower().strip()
    body = claim_row.get('article_content') or ''
    speaker = (claim_row.get('speaker') or '').strip()
    attribution = (claim_row.get('attribution_context') or '').strip()

    # Wire publishing on its own domain -> outlet_claim (Position B)
    if source in WIRE_SOURCES:
        return ('outlet_claim', f'source {source} is a wire on its own domain')

    # Non-wire outlet with a wire byline marker -> wire_reprint
    head = body[:BYLINE_SEARCH_CHARS]
    for pat in WIRE_BYLINE_PATTERNS:
        m = pat.search(head)
        if m:
            return ('wire_reprint', f'wire byline marker: {m.group(0).strip()!r}')

    # Speaker + attribution -> attributed_claim
    if speaker and len(speaker) > 2 and speaker.lower() not in ('unknown', 'n/a', 'none'):
        if attribution and any(m in attribution.lower() for m in ATTRIBUTION_MARKERS):
            return ('attributed_claim', f'speaker={speaker!r} with attribution')

    return (None, 'no heuristic matched')


def sonnet_classify(claim_row, client, model_name):
    """Single-claim Sonnet classification. Returns (origin, reason) or (None, error)."""
    body_excerpt = (claim_row.get('article_content') or '')[:2000]
    prompt = f"""You are classifying a news claim against the Verum Signal v1.6 methodology.

Three categories:
  outlet_claim     -- the outlet asserts this directly (its own reporting, analysis, framing).
  attributed_claim -- the outlet attributes this to a named speaker (politician, official,
                      expert, CEO, etc.). The outlet is reporting WHAT SOMEONE SAID,
                      not asserting it.
  wire_reprint     -- this article is a verbatim reprint of Reuters/AP/AFP/Bloomberg wire
                      copy being republished by a non-wire outlet.

Source outlet: {claim_row['source_name']}
Article title: {claim_row.get('article_title') or '(no title)'}
Article opening (first 2000 chars):
\"\"\"
{body_excerpt}
\"\"\"

Claim text: {claim_row['claim_text']}
Speaker field (may be empty): {claim_row.get('speaker') or '(empty)'}
Attribution context (may be empty): {claim_row.get('attribution_context') or '(empty)'}

Respond with ONLY a JSON object, no preamble or code fences:
{{"category": "outlet_claim" | "attributed_claim" | "wire_reprint", "reason": "<one short sentence>"}}
"""
    try:
        resp = client.messages.create(
            model=model_name,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
        parsed = json.loads(text)
        cat = parsed.get('category')
        reason = parsed.get('reason', '(no reason)')
        if cat in ('outlet_claim', 'attributed_claim', 'wire_reprint'):
            return (cat, f'sonnet: {reason}')
        return (None, f'sonnet returned unrecognized category: {cat!r}')
    except Exception as e:
        return (None, f'sonnet error: {e}')


def fetch_null_claims(conn, limit=None):
    sql = """
        SELECT
            c.id,
            c.claim_text,
            c.speaker,
            c.attribution_context,
            c.verdict,
            c.first_seen,
            a.id           AS article_id,
            a.source_name,
            a.title        AS article_title,
            a.content      AS article_content,
            a.url          AS article_url
        FROM claims c
        JOIN articles a ON c.article_id = a.id
        WHERE c.claim_origin IS NULL
        ORDER BY c.first_seen DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        return cur.fetchall()


def update_claim_origin(conn, claim_id, new_origin):
    with conn.cursor() as cur:
        cur.execute("UPDATE claims SET claim_origin = %s WHERE id = %s",
                    (new_origin, claim_id))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Write changes (default: dry-run)')
    parser.add_argument('--dry-run', action='store_true', help='Force dry-run mode')
    parser.add_argument('--limit', type=int, default=None, help='Cap rows processed')
    parser.add_argument('--skip-sonnet', action='store_true', help='Heuristics only')
    parser.add_argument('--save-results', metavar='FILE',
                        help='Write classifications to JSON for later --from-results reuse')
    parser.add_argument('--from-results', metavar='FILE',
                        help='Read classifications from JSON (skips heuristics + Sonnet, just writes)')
    parser.add_argument('--model', default='claude-sonnet-4-5-20250929',
                        help='Anthropic model for residual classification')
    args = parser.parse_args()

    dry_run = not args.apply or args.dry_run
    print("=== DRY RUN -- no DB writes ===" if dry_run else "=== APPLY MODE ===")

    conn = psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=os.environ.get('DB_PORT', 5432),
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        dbname=os.environ['DB_NAME'],
    )
    conn.autocommit = False

    # Short-circuit: load classifications from a previous --save-results run
    if args.from_results:
        import json as _json
        with open(args.from_results) as _f:
            saved = _json.load(_f)
        # JSON keys are strings; coerce to int to match DB ids
        classified = {int(k): tuple(v) for k, v in saved['classifications'].items()}
        print(f"Loaded {len(classified)} classifications from {args.from_results}")
        if dry_run:
            print(f"Would write {len(classified)} updates. Sample:")
            for cid, (origin, reason) in list(classified.items())[:10]:
                print(f"  claim_id={cid}: {origin}  ({reason})")
        else:
            print(f"Writing {len(classified)} updates...")
            for cid, (origin, _reason) in classified.items():
                update_claim_origin(conn, cid, origin)
            conn.commit()
            print(f"Committed.")
        from collections import Counter as _Counter
        final_counts = _Counter(v[0] for v in classified.values())
        print("\nFinal distribution:")
        for k, v in sorted(final_counts.items()):
            print(f"  {k}: {v}")
        conn.close()
        return

    rows = fetch_null_claims(conn, limit=args.limit)
    print(f"Found {len(rows)} NULL-origin claims to classify.\n")

    # Phase 1: heuristics
    classified = {}
    needs_sonnet = []
    for r in rows:
        origin, reason = heuristic_classify(r)
        if origin:
            classified[r['id']] = (origin, reason)
        else:
            needs_sonnet.append(r)

    print(f"Heuristic phase:")
    print(f"  Classified: {len(classified)}")
    print(f"  Need Sonnet: {len(needs_sonnet)}")
    h_counts = Counter(v[0] for v in classified.values())
    for k, v in sorted(h_counts.items()):
        print(f"    {k}: {v}")
    print()

    # Phase 2: Sonnet
    if needs_sonnet and not args.skip_sonnet:
        try:
            from anthropic import Anthropic
            client = Anthropic()  # picks up ANTHROPIC_API_KEY from env
        except Exception as e:
            print(f"Anthropic init failed: {e}")
            print("Continuing with heuristics only.")
            client = None

        if client:
            print(f"Sonnet phase: classifying {len(needs_sonnet)} residual claims...")
            for i, claim in enumerate(needs_sonnet, 1):
                origin, reason = sonnet_classify(claim, client, args.model)
                if origin:
                    classified[claim['id']] = (origin, reason)
                if i % 25 == 0:
                    print(f"  ...{i}/{len(needs_sonnet)}")
            print(f"  done.")
            s_subset = {cid: v for cid, v in classified.items()
                        if cid in {c['id'] for c in needs_sonnet}}
            s_counts = Counter(v[0] for v in s_subset.values() if v[0])
            for k, v in sorted(s_counts.items()):
                print(f"    {k}: {v}")
            unclassified = sum(1 for c in needs_sonnet if c['id'] not in classified)
            if unclassified:
                print(f"    (unclassified after Sonnet: {unclassified})")
            print()

    # Optionally save classifications to a JSON file so --apply can reuse them
    if args.save_results:
        import json as _json
        save_payload = {
            'classifications': {str(cid): list(v) for cid, v in classified.items()},
            'total_input_rows': len(rows),
            'total_classified': len(classified),
        }
        with open(args.save_results, 'w') as _f:
            _json.dump(save_payload, _f, indent=2)
        print(f"Saved {len(classified)} classifications to {args.save_results}")
        print(f"Re-run with: python3 scripts/backfill_claim_origin.py --apply --from-results {args.save_results}")
        print()

    # Phase 3: write or report
    if dry_run:
        print(f"Would write {len(classified)} updates. Sample:")
        for cid, (origin, reason) in list(classified.items())[:10]:
            print(f"  claim_id={cid}: {origin}  ({reason})")
    else:
        print(f"Writing {len(classified)} updates...")
        for cid, (origin, _reason) in classified.items():
            update_claim_origin(conn, cid, origin)
        conn.commit()
        print(f"Committed.")

    final_counts = Counter(v[0] for v in classified.values())
    print("\nFinal distribution:")
    for k, v in sorted(final_counts.items()):
        print(f"  {k}: {v}")
    print(f"  Still NULL: {len(rows) - len(classified)}")

    conn.close()


if __name__ == '__main__':
    main()
