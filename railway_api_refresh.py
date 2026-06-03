#!/usr/bin/env python3
"""
railway_api_refresh.py

Railway cron service: veris-api-refresh
Frequency: every 5 minutes

Upserts api_claims, api_outlets, and api_debate_claims from the source tables.
Queries only newly-evaluated claims since the last refresh (incremental).
api_outlets is rebuilt in full each run (~28 rows, cheap).

CRITICAL: Only publishes verdicts from PUBLIC_METHODOLOGY_VERSIONS.
Any verdict on a non-public version is silently excluded from API tables.
Update this list ONLY after the methodology version is publicly documented
AND attorney review is complete.

DO NOT query verdict_summary, evidence_sources, priority_score, or any
internal scoring field — those fields must never reach the API tables.

All DB connections via get_db() — uses hardcoded fallback at api.py:33.
"""

import json
import os
import sys
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Add project root to path so we can import get_db from api.py
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from api import get_db  # uses hardcoded DB password fallback (Railway V2 bug)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# METHODOLOGY GATE — hard exclusion at the refresh layer.
# Update ONLY when a new version is publicly documented + attorney-reviewed.
# ---------------------------------------------------------------------------
# PUBLIC_METHODOLOGY_VERSIONS controls which methodology versions the API serves.
#
# IMPORTANT: Do not add a new version to this list until:
#   1. Attorney review of that methodology version has completed
#   2. The methodology page at verumsignal.com/methodology has been updated
#   3. The landing page at verumsignal.com/api references the new version
#
# Adding a version here exposes verdicts to all API customers immediately.
# There is no rollback path for already-served records.
#
# When v1.7 launches: PUBLIC_METHODOLOGY_VERSIONS = ['v1.6', 'v1.7']
PUBLIC_METHODOLOGY_VERSIONS = ['v1.6']

METHODOLOGY_VERSION = 'v1.6'   # stamp on api_outlets (outlet scoring uses leaderboard formula)

from api_leaderboard import EXCLUDED_DOMAINS
# Tiers (from api_leaderboard.py — single source of truth for scoring)
INCLUSION_THRESHOLD = 20
TIER_PUBLISHED       = 100
TIER_STABILIZING     = 50
TIER_LIMITED_DATA    = 20


def get_methodology_version_for_claim(row_version: Optional[str]) -> Optional[str]:
    """Return version string if it's public, None otherwise."""
    v = row_version or METHODOLOGY_VERSION
    return v if v in PUBLIC_METHODOLOGY_VERSIONS else None


# ---------------------------------------------------------------------------
# REFRESH: api_claims
# ---------------------------------------------------------------------------

def refresh_claims(cur) -> int:
    """
    Upsert article claims that have been evaluated since the last refresh.
    Only includes outlet_claim and attributed_claim (NOT wire_reprint).
    Only includes verdicts from PUBLIC_METHODOLOGY_VERSIONS.
    Returns number of rows upserted.
    """
    # Find the high-water mark in the API table
    cur.execute("SELECT COALESCE(MAX(evaluated_at), '1970-01-01'::timestamp) FROM api_claims")
    since = cur.fetchone()[0]
    log.info(f"refresh_claims: fetching claims evaluated after {since}")

    versions_tuple = tuple(PUBLIC_METHODOLOGY_VERSIONS)

    cur.execute("""
        SELECT
            c.id                        AS claim_id,
            c.article_id,
            LOWER(a.source_name)        AS outlet_id,
            a.source_name               AS outlet_name,
            c.claim_text,
            c.claim_origin,
            c.verdict                   AS verdict_label,
            a.url                       AS article_url,
            a.title                     AS article_title,
            a.published_at              AS article_published_at,
            c.last_checked              AS evaluated_at,
            'v1.6'  AS methodology_version,
            'https://verumsignal.com/report?url=' || a.url  AS report_url
        FROM claims c
        JOIN articles a ON a.id = c.article_id
        WHERE c.claim_origin IN ('outlet_claim', 'attributed_claim')
          AND c.verdict IS NOT NULL
          AND c.last_checked > %s
        ORDER BY c.last_checked
        LIMIT 5000
    """, (since,))

    rows = cur.fetchall()
    if not rows:
        log.info("refresh_claims: no new rows")
        return 0

    upserted = 0
    for row in rows:
        (claim_id, article_id, outlet_id, outlet_name, claim_text,
         claim_origin, verdict_label, article_url, article_title,
         article_published_at, evaluated_at, methodology_version,
         report_url) = row

        # Skip if version not public (belt-and-suspenders on top of WHERE clause)
        if methodology_version not in PUBLIC_METHODOLOGY_VERSIONS:
            continue

        cur.execute("""
            INSERT INTO api_claims (
                claim_id, article_id, outlet_id, outlet_name,
                claim_text, claim_origin, verdict_label,
                article_url, article_title, article_published_at,
                evaluated_at, methodology_version, report_url,
                cursor_key, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      nextval('api_claims_cursor_seq'), NOW())
            ON CONFLICT (claim_id) DO UPDATE SET
                verdict_label        = EXCLUDED.verdict_label,
                evaluated_at         = EXCLUDED.evaluated_at,
                methodology_version  = EXCLUDED.methodology_version,
                cursor_key           = nextval('api_claims_cursor_seq'),
                updated_at           = NOW()
        """, (claim_id, article_id, outlet_id, outlet_name,
              claim_text, claim_origin, verdict_label,
              article_url, article_title, article_published_at,
              evaluated_at, methodology_version, report_url))
        upserted += 1

    return upserted


# ---------------------------------------------------------------------------
# REFRESH: api_outlets
# ---------------------------------------------------------------------------

def _compute_tier(scoreable_count: int) -> str:
    if scoreable_count >= TIER_PUBLISHED:
        return 'published'
    if scoreable_count >= TIER_STABILIZING:
        return 'stabilizing'
    if scoreable_count >= TIER_LIMITED_DATA:
        return 'limited_data'
    return 'tracked'


def _compute_score(supported, plausible, corroborated,
                   overstated, disputed, not_supported) -> Optional[float]:
    """Mirror of api_leaderboard.py scoring formula."""
    weighted_sum = (
        supported    * 1.0  +
        plausible    * 0.5  +
        corroborated * 0.75 +
        overstated   * -0.5 +
        disputed     * -1.0 +
        not_supported * -1.5
    )
    scoreable = (supported + plausible + corroborated +
                 overstated + disputed + not_supported)
    if scoreable == 0:
        return None
    raw = (weighted_sum / scoreable + 1.5) / 2.5 * 100
    return round(max(0.0, min(100.0, raw)), 2)


def refresh_outlets(cur) -> int:
    """
    Rebuild api_outlets in full from outlet_reliability + claims.
    ~28 rows, cheap full rebuild each cycle.
    Only outlets with any public-methodology verdicts are included.
    Returns number of rows upserted.
    """
    log.info("refresh_outlets: rebuilding all outlets")

    versions_list = list(PUBLIC_METHODOLOGY_VERSIONS)

    # Pull outlet-level verdict counts from claims table directly
    # (mirrors api_leaderboard.py logic)
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
          AND LOWER(a.source_name) != ALL(%s)
        GROUP BY LOWER(a.source_name), a.source_name
        HAVING COUNT(*) >= %s
    """, (list(EXCLUDED_DOMAINS), INCLUSION_THRESHOLD,))

    rows = cur.fetchall()
    if not rows:
        log.info("refresh_outlets: no outlets above threshold")
        return 0

    upserted = 0
    for row in rows:
        (outlet_id, outlet_name,
         supported, plausible, corroborated,
         overstated, disputed, not_supported, not_verifiable,
         total_evaluated, first_evaluated_at, last_evaluated_at) = row

        scoreable = (supported + plausible + corroborated +
                     overstated + disputed + not_supported)
        tier = _compute_tier(scoreable)
        score = _compute_score(supported, plausible, corroborated,
                               overstated, disputed, not_supported)

        # Score is NULL for 'tracked' tier (below INCLUSION_THRESHOLD)
        # but all rows here are above threshold, so score will not be NULL
        # unless the scoreable mix is somehow all zeros. Guard anyway.
        if tier == 'tracked':
            score = None

        verdict_counts = {
            'supported':     supported,
            'plausible':     plausible,
            'corroborated':  corroborated,
            'overstated':    overstated,
            'disputed':      disputed,
            'not_supported': not_supported,
            'not_verifiable': not_verifiable,
        }

        leaderboard_url = f'https://verumsignal.com/outlet/{outlet_id}'
        # outlet_url: derive protocol + domain from outlet_id
        outlet_url = f'https://{outlet_id}'

        cur.execute("""
            INSERT INTO api_outlets (
                outlet_id, outlet_name, outlet_url, score, tier,
                total_scoreable_claims, total_evaluated_claims,
                verdict_counts, methodology_version,
                first_evaluated_at, last_evaluated_at,
                leaderboard_url, cursor_key, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
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

    return upserted


# ---------------------------------------------------------------------------
# REFRESH: api_debate_claims
# ---------------------------------------------------------------------------

def refresh_debate_claims(cur) -> int:
    """
    Upsert debate claims evaluated since the last refresh.
    Only includes claims from PUBLIC_METHODOLOGY_VERSIONS.
    Returns number of rows upserted.
    """
    cur.execute("SELECT COALESCE(MAX(evaluated_at), '1970-01-01'::timestamp) FROM api_debate_claims")
    since = cur.fetchone()[0]
    log.info(f"refresh_debate_claims: fetching claims evaluated after {since}")

    versions_list = list(PUBLIC_METHODOLOGY_VERSIONS)

    cur.execute("""
        SELECT
            c.id                        AS claim_id,
            c.event_id,
            e.slug                      AS event_slug,
            e.event_name,
            e.event_date,
            c.utterance_id,
            su.speaker_id,
            s.name                      AS speaker_name,
            s.party                     AS speaker_party,
            c.claim_text,
            c.verdict                   AS verdict_label,
            c.last_checked              AS evaluated_at,
            'v1.6'  AS methodology_version,
            'https://verumsignal.com/debates/' || e.slug  AS event_url,
            c.verdict_status
        FROM claims c
        JOIN events e ON e.id = c.event_id
        LEFT JOIN speaker_utterances su ON su.id = c.utterance_id
        LEFT JOIN speakers s ON s.id = su.speaker_id
        WHERE c.claim_origin = 'debate_claim'
          AND c.verdict IS NOT NULL
          AND e.is_public = TRUE
          AND c.last_checked > %s
        ORDER BY c.last_checked
        LIMIT 5000
    """, (since,))

    rows = cur.fetchall()
    if not rows:
        log.info("refresh_debate_claims: no new rows")
        return 0

    upserted = 0
    for row in rows:
        (claim_id, event_id, event_slug, event_name, event_date,
         utterance_id, speaker_id, speaker_name, speaker_party,
         claim_text, verdict_label, evaluated_at, methodology_version,
         event_url, verdict_status) = row

        if methodology_version not in PUBLIC_METHODOLOGY_VERSIONS:
            continue

        cur.execute("""
            INSERT INTO api_debate_claims (
                claim_id, event_id, event_slug, event_name, event_date,
                speaker_id, speaker_name, speaker_party,
                utterance_id, claim_text, verdict_label,
                evaluated_at, methodology_version, event_url,
                verdict_status, cursor_key, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      nextval('api_debate_claims_cursor_seq'), NOW())
            ON CONFLICT (claim_id) DO UPDATE SET
                verdict_label       = EXCLUDED.verdict_label,
                evaluated_at        = EXCLUDED.evaluated_at,
                methodology_version = EXCLUDED.methodology_version,
                verdict_status      = EXCLUDED.verdict_status,
                cursor_key          = nextval('api_debate_claims_cursor_seq'),
                updated_at          = NOW()
        """, (claim_id, event_id, event_slug, event_name, event_date,
              speaker_id, speaker_name, speaker_party,
              utterance_id, claim_text, verdict_label,
              evaluated_at, methodology_version, event_url, verdict_status))
        upserted += 1

    return upserted


# ---------------------------------------------------------------------------
# MAINTENANCE: prune api_usage rows older than 90 days
# ---------------------------------------------------------------------------

def prune_usage(cur) -> int:
    cur.execute("""
        DELETE FROM api_usage
        WHERE created_at < NOW() - INTERVAL '90 days'
    """)
    deleted = cur.rowcount
    if deleted:
        log.info(f"prune_usage: deleted {deleted} rows older than 90 days")
    return deleted


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def promote_provisional_verdicts(cur) -> int:
    """
    Auto-promote provisional debate verdicts to final after the 60-minute window.

    The 60-minute clock starts from claims.first_seen — when Verum Signal
    extracts the claim from the live transcript (typically 1-5 minutes after
    utterance). Verdicts still marked provisional after 60 minutes from
    first_seen are promoted to final automatically.

    Safe for non-debate claims: regular article claims have verdict_status=NULL
    and are never touched by this query.
    """
    cur.execute("""
        UPDATE claims
        SET verdict_status = 'final'
        WHERE verdict_status = 'provisional'
          AND first_seen < NOW() - INTERVAL '60 minutes'
    """)
    promoted = cur.rowcount
    if promoted > 0:
        log.info(f"promote_provisional_verdicts: {promoted} promoted to final")
    return promoted


def main():
    start = datetime.now(timezone.utc)
    log.info("=== api_refresh start ===")

    conn = get_db()
    cur = conn.cursor()

    try:
        claims_n        = refresh_claims(cur)
        conn.commit()
        log.info(f"refresh_claims: {claims_n} rows upserted")

        outlets_n       = refresh_outlets(cur)
        conn.commit()
        log.info(f"refresh_outlets: {outlets_n} rows upserted")

        debate_n        = refresh_debate_claims(cur)
        conn.commit()
        log.info(f"refresh_debate_claims: {debate_n} rows upserted")

        promoted_n      = promote_provisional_verdicts(cur)
        conn.commit()

        # Verify any unverified debate claims across all public events
        # This handles claims extracted post-debate or missed during live surge
        try:
            from verdict_engine import verify_debate_claims_sync
            cur.execute("""
                SELECT DISTINCT c.event_id FROM claims c
                JOIN events e ON e.id = c.event_id
                WHERE c.claim_origin = 'debate_claim'
                  AND c.verdict IS NULL
                  AND c.claim_text IS NOT NULL
                  AND LENGTH(c.claim_text) > 20
                  AND COALESCE(c.verification_attempts, 0) < 3
                  AND e.is_public = TRUE
            """)
            pending_events = [r[0] for r in cur.fetchall()]
            if pending_events:
                log.info(f"debate_verify: {len(pending_events)} event(s) with unverified claims: {pending_events}")
                for eid in pending_events:
                    verified = verify_debate_claims_sync(eid, limit=20)
                    log.info(f"debate_verify: event_id={eid} verified={verified}")
            else:
                log.info("debate_verify: no unverified debate claims")
        except Exception:
            log.warning("debate_verify failed — non-fatal")
            traceback.print_exc()

        prune_usage(cur)

        # Clean up expired/used magic_link_tokens older than 24 hours.
        # Keeps the table from growing unbounded. Non-fatal.
        try:
            cur.execute("""
                DELETE FROM magic_link_tokens
                WHERE created_at < NOW() - INTERVAL '24 hours'
            """)
            deleted = cur.rowcount
            if deleted:
                log.info(f"token_cleanup: deleted {deleted} expired magic_link_tokens")
        except Exception:
            log.warning("token_cleanup failed — non-fatal")

        conn.commit()

    except Exception:
        conn.rollback()
        log.error("refresh failed — rolled back")
        traceback.print_exc()
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log.info(f"=== api_refresh complete in {elapsed:.1f}s "
             f"(claims={claims_n}, outlets={outlets_n}, debates={debate_n}) ===")


if __name__ == '__main__':
    main()
    # Mobile push notification jobs — run after main refresh, non-fatal
    try:
        from notification_jobs import (
            run_debate_start_alerts,
            run_verdict_alerts,
            run_daily_digest,
        )
        from datetime import datetime as _dt, timezone as _tz, time as _time
        _hour = _dt.now(_tz.utc).hour
        run_debate_start_alerts(get_db)
        run_verdict_alerts(get_db)
        # Daily digest: only run between 12:00-13:00 UTC (~6-7am MT)
        if _hour == 12:
            run_daily_digest(get_db)
    except Exception:
        import traceback as _tb
        log.warning("notification_jobs failed (non-fatal)")
        _tb.print_exc()
