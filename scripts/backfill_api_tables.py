#!/usr/bin/env python3
"""
scripts/backfill_api_tables.py

One-time full-corpus backfill of api_claims, api_outlets, api_debate_claims.
Run once before enabling the railway_api_refresh cron.
After backfill, the incremental refresh takes over.

Uses the same upsert logic as railway_api_refresh.py but without the
`last_checked > since` filter — it processes the entire corpus.

Safety:
  - Idempotent: ON CONFLICT DO UPDATE; safe to re-run.
  - Batches of 1000 rows with a commit after each batch.
  - Prints progress every 5000 rows.
  - Validates row counts against source tables after completion.
  - Methodology gate: only PUBLIC_METHODOLOGY_VERSIONS are backfilled.

Usage:
    cd ~/projects/veris && source venv/bin/activate
    python3 scripts/backfill_api_tables.py
    python3 scripts/backfill_api_tables.py --dry-run   # count only, no writes
"""

import os
import sys
import json
import logging
import argparse
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api import get_db

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

PUBLIC_METHODOLOGY_VERSIONS = ['v1.6']
METHODOLOGY_VERSION = 'v1.6'
BATCH_SIZE = 1000
PROGRESS_EVERY = 5000
INCLUSION_THRESHOLD = 20


def parse_args():
    p = argparse.ArgumentParser(description="Backfill Verum Signal API tables")
    p.add_argument('--dry-run', action='store_true',
                   help="Count eligible rows without writing anything")
    p.add_argument('--table', choices=['claims', 'outlets', 'debates', 'all'],
                   default='all',
                   help="Which table to backfill (default: all)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_tier(scoreable: int) -> str:
    if scoreable >= 100:  return 'published'
    if scoreable >= 50:   return 'stabilizing'
    if scoreable >= 20:   return 'limited_data'
    return 'tracked'


def _compute_score(supported, plausible, corroborated,
                   overstated, disputed, not_supported):
    weighted = (
        supported    * 1.0  +
        plausible    * 0.5  +
        corroborated * 0.5  +
        overstated   * -0.5 +
        disputed     * -1.0 +
        not_supported * -1.5
    )
    scoreable = (supported + plausible + corroborated +
                 overstated + disputed + not_supported)
    if scoreable == 0:
        return None
    raw = (weighted / scoreable + 1.5) / 2.5 * 100
    return round(max(0.0, min(100.0, raw)), 2)


# ---------------------------------------------------------------------------
# Backfill: api_claims
# ---------------------------------------------------------------------------

def backfill_claims(cur, dry_run=False) -> int:
    log.info("--- Backfilling api_claims ---")
    versions_list = list(PUBLIC_METHODOLOGY_VERSIONS)

    cur.execute("""
        SELECT COUNT(*) FROM claims c
        JOIN articles a ON a.id = c.article_id
        WHERE c.claim_origin IN ('outlet_claim', 'attributed_claim')
          AND c.verdict IS NOT NULL
          AND c.last_checked IS NOT NULL
    """)
    total_eligible = cur.fetchone()[0]
    log.info(f"  Eligible rows: {total_eligible}")

    if dry_run:
        return total_eligible

    offset = 0
    total_upserted = 0

    while True:
        cur.execute("""
            SELECT
                c.id,
                c.article_id,
                LOWER(a.source_name),
                a.source_name,
                c.claim_text,
                c.claim_origin,
                c.verdict,
                a.url,
                a.title,
                a.published_at,
                c.last_checked,
                'v1.6',
                'https://verumsignal.com/report?url=' || a.url
            FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin IN ('outlet_claim', 'attributed_claim')
              AND c.verdict IS NOT NULL
          AND c.last_checked IS NOT NULL
              AND c.last_checked IS NOT NULL
            ORDER BY c.id
            LIMIT %s OFFSET %s
        """, (BATCH_SIZE, offset))

        rows = cur.fetchall()
        if not rows:
            break

        for row in rows:
            (claim_id, article_id, outlet_id, outlet_name, claim_text,
             claim_origin, verdict_label, article_url, article_title,
             article_published_at, evaluated_at, methodology_version,
             report_url) = row

            if methodology_version not in PUBLIC_METHODOLOGY_VERSIONS:
                continue

            cur.execute("""
                INSERT INTO api_claims (
                    claim_id, article_id, outlet_id, outlet_name,
                    claim_text, claim_origin, verdict_label,
                    article_url, article_title, article_published_at,
                    evaluated_at, methodology_version, report_url,
                    cursor_key, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          nextval('api_claims_cursor_seq'), NOW())
                ON CONFLICT (claim_id) DO UPDATE SET
                    verdict_label       = EXCLUDED.verdict_label,
                    evaluated_at        = EXCLUDED.evaluated_at,
                    methodology_version = EXCLUDED.methodology_version,
                    cursor_key          = nextval('api_claims_cursor_seq'),
                    updated_at          = NOW()
            """, (claim_id, article_id, outlet_id, outlet_name,
                  claim_text, claim_origin, verdict_label,
                  article_url, article_title, article_published_at,
                  evaluated_at, methodology_version, report_url))
            total_upserted += 1

        offset += BATCH_SIZE
        cur.connection.commit()

        if total_upserted % PROGRESS_EVERY < BATCH_SIZE or not rows:
            pct = (total_upserted / total_eligible * 100) if total_eligible else 0
            log.info(f"  api_claims: {total_upserted}/{total_eligible} ({pct:.1f}%)")

    log.info(f"  api_claims done: {total_upserted} rows")
    return total_upserted


# ---------------------------------------------------------------------------
# Backfill: api_outlets
# ---------------------------------------------------------------------------

def backfill_outlets(cur, dry_run=False) -> int:
    log.info("--- Backfilling api_outlets ---")
    versions_list = list(PUBLIC_METHODOLOGY_VERSIONS)

    cur.execute("""
        SELECT COUNT(DISTINCT LOWER(a.source_name))
        FROM claims c
        JOIN articles a ON a.id = c.article_id
        WHERE c.claim_origin = 'outlet_claim'
          AND c.verdict IS NOT NULL
          AND c.last_checked IS NOT NULL
    """)
    total_outlets = cur.fetchone()[0]
    log.info(f"  Distinct outlets with verdicts: {total_outlets}")

    if dry_run:
        return total_outlets

    cur.execute("""
        SELECT
            LOWER(a.source_name)            AS outlet_id,
            a.source_name                   AS outlet_name,
            COUNT(*) FILTER (WHERE c.verdict = 'supported')      AS supported,
            COUNT(*) FILTER (WHERE c.verdict = 'plausible')      AS plausible,
            COUNT(*) FILTER (WHERE c.verdict = 'corroborated')   AS corroborated,
            COUNT(*) FILTER (WHERE c.verdict = 'overstated')     AS overstated,
            COUNT(*) FILTER (WHERE c.verdict = 'disputed')       AS disputed,
            COUNT(*) FILTER (WHERE c.verdict = 'not_supported')  AS not_supported,
            COUNT(*) FILTER (WHERE c.verdict = 'not_verifiable') AS not_verifiable,
            COUNT(*)                                              AS total_evaluated,
            MIN(c.last_checked)             AS first_evaluated_at,
            MAX(c.last_checked)             AS last_evaluated_at
        FROM claims c
        JOIN articles a ON a.id = c.article_id
        WHERE c.claim_origin = 'outlet_claim'
          AND c.verdict IS NOT NULL
          AND c.last_checked IS NOT NULL
        GROUP BY LOWER(a.source_name), a.source_name
    """)

    rows = cur.fetchall()
    upserted = 0
    skipped_below_threshold = 0

    for row in rows:
        (outlet_id, outlet_name,
         supported, plausible, corroborated,
         overstated, disputed, not_supported, not_verifiable,
         total_evaluated, first_evaluated_at, last_evaluated_at) = row

        scoreable = (supported + plausible + corroborated +
                     overstated + disputed + not_supported)
        tier = _compute_tier(scoreable)

        # Include ALL outlets in api_outlets (even 'tracked') so they're
        # queryable. Score is NULL for tracked; leaderboard omits them.
        score = _compute_score(supported, plausible, corroborated,
                               overstated, disputed, not_supported)
        if tier == 'tracked':
            score = None

        verdict_counts = {
            'supported':      supported,
            'plausible':      plausible,
            'corroborated':   corroborated,
            'overstated':     overstated,
            'disputed':       disputed,
            'not_supported':  not_supported,
            'not_verifiable': not_verifiable,
        }
        outlet_url = f'https://{outlet_id}'
        leaderboard_url = f'https://verumsignal.com/outlet/{outlet_id}'

        cur.execute("""
            INSERT INTO api_outlets (
                outlet_id, outlet_name, outlet_url, score, tier,
                total_scoreable_claims, total_evaluated_claims,
                verdict_counts, methodology_version,
                first_evaluated_at, last_evaluated_at,
                leaderboard_url, cursor_key, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,
                      nextval('api_outlets_cursor_seq'), NOW())
            ON CONFLICT (outlet_id) DO UPDATE SET
                outlet_name            = EXCLUDED.outlet_name,
                score                  = EXCLUDED.score,
                tier                   = EXCLUDED.tier,
                total_scoreable_claims = EXCLUDED.total_scoreable_claims,
                total_evaluated_claims = EXCLUDED.total_evaluated_claims,
                verdict_counts         = EXCLUDED.verdict_counts,
                methodology_version    = EXCLUDED.methodology_version,
                last_evaluated_at      = EXCLUDED.last_evaluated_at,
                cursor_key             = nextval('api_outlets_cursor_seq'),
                updated_at             = NOW()
        """, (outlet_id, outlet_name, outlet_url, score, tier,
              scoreable, total_evaluated,
              json.dumps(verdict_counts), METHODOLOGY_VERSION,
              first_evaluated_at, last_evaluated_at,
              leaderboard_url))
        upserted += 1

    cur.connection.commit()
    log.info(f"  api_outlets done: {upserted} rows (incl. tracked-tier outlets)")
    return upserted


# ---------------------------------------------------------------------------
# Backfill: api_debate_claims
# ---------------------------------------------------------------------------

def backfill_debate_claims(cur, dry_run=False) -> int:
    log.info("--- Backfilling api_debate_claims ---")
    versions_list = list(PUBLIC_METHODOLOGY_VERSIONS)

    cur.execute("""
        SELECT COUNT(*) FROM claims c
        JOIN events e ON e.id = c.event_id
        WHERE c.claim_origin = 'debate_claim'
          AND c.verdict IS NOT NULL
          AND c.last_checked IS NOT NULL
          AND e.is_public = TRUE
    """)
    total_eligible = cur.fetchone()[0]
    log.info(f"  Eligible debate claims: {total_eligible}")

    if dry_run:
        return total_eligible

    offset = 0
    total_upserted = 0

    while True:
        cur.execute("""
            SELECT
                c.id,
                c.event_id,
                e.slug,
                e.event_name,
                e.event_date,
                c.utterance_id,
                su.speaker_id,
                s.name,
                s.party,
                c.claim_text,
                c.verdict,
                c.last_checked,
                'v1.6',
                'https://verumsignal.com/debates/' || e.slug
            FROM claims c
            JOIN events e ON e.id = c.event_id
            LEFT JOIN speaker_utterances su ON su.id = c.utterance_id
            LEFT JOIN speakers s ON s.id = su.speaker_id
            WHERE c.claim_origin = 'debate_claim'
              AND c.verdict IS NOT NULL
              AND c.last_checked IS NOT NULL
              AND e.is_public = TRUE
            ORDER BY c.id
            LIMIT %s OFFSET %s
        """, (BATCH_SIZE, offset))

        rows = cur.fetchall()
        if not rows:
            break

        for row in rows:
            (claim_id, event_id, event_slug, event_name, event_date,
             utterance_id, speaker_id, speaker_name, speaker_party,
             claim_text, verdict_label, evaluated_at, methodology_version,
             event_url) = row

            if methodology_version not in PUBLIC_METHODOLOGY_VERSIONS:
                continue

            cur.execute("""
                INSERT INTO api_debate_claims (
                    claim_id, event_id, event_slug, event_name, event_date,
                    speaker_id, speaker_name, speaker_party,
                    utterance_id, claim_text, verdict_label,
                    evaluated_at, methodology_version, event_url,
                    cursor_key, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          nextval('api_debate_claims_cursor_seq'), NOW())
                ON CONFLICT (claim_id) DO UPDATE SET
                    verdict_label       = EXCLUDED.verdict_label,
                    evaluated_at        = EXCLUDED.evaluated_at,
                    methodology_version = EXCLUDED.methodology_version,
                    cursor_key          = nextval('api_debate_claims_cursor_seq'),
                    updated_at          = NOW()
            """, (claim_id, event_id, event_slug, event_name, event_date,
                  speaker_id, speaker_name, speaker_party,
                  utterance_id, claim_text, verdict_label,
                  evaluated_at, methodology_version, event_url))
            total_upserted += 1

        offset += BATCH_SIZE
        cur.connection.commit()

        if total_upserted % PROGRESS_EVERY < BATCH_SIZE or not rows:
            pct = (total_upserted / total_eligible * 100) if total_eligible else 0
            log.info(f"  api_debate_claims: {total_upserted}/{total_eligible} ({pct:.1f}%)")

    log.info(f"  api_debate_claims done: {total_upserted} rows")
    return total_upserted


# ---------------------------------------------------------------------------
# Validation: row counts vs source tables
# ---------------------------------------------------------------------------

def validate(cur):
    log.info("--- Validation ---")

    cur.execute("SELECT COUNT(*) FROM api_claims")
    api_claims_n = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM claims c
        JOIN articles a ON a.id = c.article_id
        WHERE c.claim_origin IN ('outlet_claim', 'attributed_claim')
          AND c.verdict IS NOT NULL
          AND c.last_checked IS NOT NULL
    """)
    source_claims_n = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM api_outlets")
    api_outlets_n = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM api_debate_claims")
    api_debate_n = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM claims c
        WHERE c.claim_origin = 'debate_claim'
          AND c.verdict IS NOT NULL
          AND c.last_checked IS NOT NULL
          AND e.is_public = TRUE
    """)
    source_debate_n = cur.fetchone()[0]

    all_ok = True

    def check(label, actual, expected, tolerance=0):
        nonlocal all_ok
        diff = abs(actual - expected)
        if diff <= tolerance:
            log.info(f"  ✓ {label}: {actual} (expected ~{expected})")
        else:
            log.error(f"  ✗ {label}: {actual} vs {expected} — diff {diff}")
            all_ok = False

    check("api_claims",        api_claims_n,   source_claims_n)
    check("api_debate_claims", api_debate_n,   source_debate_n)
    log.info(f"  ✓ api_outlets: {api_outlets_n} outlets")

    if all_ok:
        log.info("  Validation passed.")
    else:
        log.error("  Validation FAILED — investigate before enabling refresh cron.")

    return all_ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    start = datetime.now(timezone.utc)

    if args.dry_run:
        log.info("=== DRY RUN — counting only, no writes ===")
    else:
        log.info("=== backfill_api_tables start ===")

    conn = get_db()
    cur = conn.cursor()

    try:
        if args.table in ('claims', 'all'):
            backfill_claims(cur, dry_run=args.dry_run)

        if args.table in ('outlets', 'all'):
            backfill_outlets(cur, dry_run=args.dry_run)

        if args.table in ('debates', 'all'):
            backfill_debate_claims(cur, dry_run=args.dry_run)

        if not args.dry_run:
            validate(cur)

    except Exception:
        conn.rollback()
        log.error("backfill failed — rolled back")
        traceback.print_exc()
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log.info(f"=== done in {elapsed:.1f}s ===")


if __name__ == '__main__':
    main()
