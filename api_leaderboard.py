
"""
Leaderboard API endpoint — Methodology v1.6 compliant.
"""

import time
import threading
from datetime import datetime, timezone
from flask import jsonify

METHODOLOGY_VERSION = "v1.6"
CACHE_TTL_SECONDS = 300
INCLUSION_THRESHOLD = 20

# ==============================================================================
# Methodology constants — single source of truth
# Methodology v1.6
# ==============================================================================

# All eight verdict types per methodology Section 4.2
VERDICT_TYPES = (
    'supported', 'plausible', 'corroborated',
    'overstated', 'disputed', 'not_supported',
    'not_verifiable', 'opinion',
)

# Scoring weights for the six scoreable verdicts (methodology Section 4.2)
WEIGHTS = {
    'supported':     1.0,
    'plausible':     0.5,
    'corroborated':  0.5,
    'overstated':   -0.5,
    'disputed':     -1.0,
    'not_supported':-1.5,
}

# Verdicts that contribute to scoring — derived from WEIGHTS keys
SCOREABLE_VERDICTS = frozenset(WEIGHTS.keys())

# Verdicts excluded from scoring (also enumerated for SQL composition)
EXCLUDED_VERDICTS = frozenset(['not_verifiable', 'opinion'])

# Display labels — used everywhere user-facing copy renders verdicts
VERDICT_LABELS = {
    'supported':      'SUPPORTED',
    'plausible':      'PLAUSIBLE',
    'corroborated':   'CORROBORATED',
    'overstated':     'OVERSTATED',
    'disputed':       'DISPUTED',
    'not_supported':  'NOT SUPPORTED',
    'not_verifiable': 'NOT VERIFIABLE',
    'opinion':        'OPINION',
}

# Display weight strings (for legends and reports)
WEIGHT_DISPLAY = {
    'supported':     '+1.0',
    'plausible':     '+0.5',
    'corroborated':  '+0.5',
    'overstated':    '-0.5',
    'disputed':      '-1.0',
    'not_supported': '-1.5',
    'not_verifiable':'excl.',
    'opinion':       'excl.',
}

# Pipeline thresholds (methodology Section 2)
# NOTE: Cache and consensus thresholds preserved at current production values for v1.6.
# These are deferred to a future cost-optimization session along with Finding 15.
# They will be documented in master Section 2 as-is during Patch 5.
PRIORITY_THRESHOLD = 30
CACHE_SIMILARITY_THRESHOLD = 0.85
CACHE_TIME_WINDOW_HOURS = 24
CACHE_FALLBACK_SIMILARITY = 0.6   # tier-2 cache, undocumented in v1.6
CONSENSUS_SIMILARITY_THRESHOLD = 0.5  # to-be-documented in v1.6
CONSENSUS_OUTLET_THRESHOLD = 5

# Breaking news gate (methodology Section 5.3)
# v1.6 uses static 6 hour gate. Dynamic gate by claim_type is v1.7+ target.
BREAKING_NEWS_GATE_HOURS = 6


LEADERBOARD_SQL = """
SELECT
    a.source_name AS domain,
    a.source_name AS name,
    COUNT(*) FILTER (WHERE c.verdict = 'supported')      AS supported_count,
    COUNT(*) FILTER (WHERE c.verdict = 'plausible')      AS plausible_count,
    COUNT(*) FILTER (WHERE c.verdict = 'corroborated')   AS corroborated_count,
    COUNT(*) FILTER (WHERE c.verdict = 'overstated')     AS overstated_count,
    COUNT(*) FILTER (WHERE c.verdict = 'disputed')       AS disputed_count,
    COUNT(*) FILTER (WHERE c.verdict = 'not_supported')  AS not_supported_count,
    COUNT(*) FILTER (WHERE c.verdict = 'not_verifiable') AS not_verifiable_count,
    COUNT(*) FILTER (WHERE c.verdict = 'opinion')        AS opinion_count,
    COUNT(*)                                             AS verdict_count,
    COUNT(*) FILTER (
        WHERE c.verdict NOT IN ('not_verifiable', 'opinion')
    )                                                    AS scoreable_count,
    SUM(CASE c.verdict
        WHEN 'supported'     THEN  1.0
        WHEN 'plausible'     THEN  0.5
        WHEN 'corroborated'  THEN  0.5
        WHEN 'overstated'    THEN -0.5
        WHEN 'disputed'      THEN -1.0
        WHEN 'not_supported' THEN -1.5
        ELSE 0
    END) FILTER (
        WHERE c.verdict NOT IN ('not_verifiable', 'opinion')
    )                                                    AS weighted_sum,
    MIN(c.first_seen) AS first_verdict_at,
    MAX(c.first_seen) AS last_verdict_at
FROM articles a
JOIN claims   c ON c.article_id = a.id
WHERE c.verdict IS NOT NULL
  AND c.claim_origin = 'outlet_claim'
  AND a.published_at IS NOT NULL
  AND a.published_at < NOW() - INTERVAL '6 hours'
GROUP BY a.source_name
HAVING COUNT(*) >= %s
ORDER BY a.source_name;
"""

SPEAKER_SCORE_SQL = """
SELECT
    s.id,
    s.name,
    s.normalized_name,
    s.slug,
    s.speaker_type,
    s.role,
    s.party,
    s.current_office,
    s.photo_url,
    COUNT(*) FILTER (WHERE c.verdict = 'supported')      AS supported_count,
    COUNT(*) FILTER (WHERE c.verdict = 'plausible')      AS plausible_count,
    COUNT(*) FILTER (WHERE c.verdict = 'corroborated')   AS corroborated_count,
    COUNT(*) FILTER (WHERE c.verdict = 'overstated')     AS overstated_count,
    COUNT(*) FILTER (WHERE c.verdict = 'disputed')       AS disputed_count,
    COUNT(*) FILTER (WHERE c.verdict = 'not_supported')  AS not_supported_count,
    COUNT(*) FILTER (WHERE c.verdict = 'not_verifiable') AS not_verifiable_count,
    COUNT(*) FILTER (WHERE c.verdict = 'opinion')        AS opinion_count,
    COUNT(*)                                             AS verdict_count,
    COUNT(*) FILTER (
        WHERE c.verdict NOT IN ('not_verifiable', 'opinion')
    )                                                    AS scoreable_count,
    SUM(CASE c.verdict
        WHEN 'supported'     THEN  1.0
        WHEN 'plausible'     THEN  0.5
        WHEN 'corroborated'  THEN  0.5
        WHEN 'overstated'    THEN -0.5
        WHEN 'disputed'      THEN -1.0
        WHEN 'not_supported' THEN -1.5
        ELSE 0
    END) FILTER (
        WHERE c.verdict NOT IN ('not_verifiable', 'opinion')
    )                                                    AS weighted_sum,
    MIN(c.first_seen) AS first_verdict_at,
    MAX(c.first_seen) AS last_verdict_at
FROM speakers s
JOIN claims c ON c.speaker_id = s.id
WHERE c.verdict IS NOT NULL
  AND c.claim_origin = 'attributed_claim'
  AND s.speaker_type != 'moderator'
GROUP BY s.id, s.name, s.normalized_name, s.slug,
         s.speaker_type, s.role, s.party, s.current_office, s.photo_url
HAVING COUNT(*) FILTER (
    WHERE c.verdict NOT IN ('not_verifiable', 'opinion')
) >= 1;
"""

SPEAKER_BY_ID_SQL = """
SELECT
    s.id,
    s.name,
    s.normalized_name,
    s.slug,
    s.speaker_type,
    s.role,
    s.party,
    s.current_office,
    s.photo_url,
    COUNT(*) FILTER (WHERE c.verdict = 'supported')      AS supported_count,
    COUNT(*) FILTER (WHERE c.verdict = 'plausible')      AS plausible_count,
    COUNT(*) FILTER (WHERE c.verdict = 'corroborated')   AS corroborated_count,
    COUNT(*) FILTER (WHERE c.verdict = 'overstated')     AS overstated_count,
    COUNT(*) FILTER (WHERE c.verdict = 'disputed')       AS disputed_count,
    COUNT(*) FILTER (WHERE c.verdict = 'not_supported')  AS not_supported_count,
    COUNT(*) FILTER (WHERE c.verdict = 'not_verifiable') AS not_verifiable_count,
    COUNT(*) FILTER (WHERE c.verdict = 'opinion')        AS opinion_count,
    COUNT(*)                                             AS verdict_count,
    COUNT(*) FILTER (
        WHERE c.verdict NOT IN ('not_verifiable', 'opinion')
    )                                                    AS scoreable_count,
    SUM(CASE c.verdict
        WHEN 'supported'     THEN  1.0
        WHEN 'plausible'     THEN  0.5
        WHEN 'corroborated'  THEN  0.5
        WHEN 'overstated'    THEN -0.5
        WHEN 'disputed'      THEN -1.0
        WHEN 'not_supported' THEN -1.5
        ELSE 0
    END) FILTER (
        WHERE c.verdict NOT IN ('not_verifiable', 'opinion')
    )                                                    AS weighted_sum,
    MIN(c.first_seen) AS first_verdict_at,
    MAX(c.first_seen) AS last_verdict_at
FROM speakers s
JOIN claims c ON c.speaker_id = s.id
WHERE s.id = %s
  AND c.verdict IS NOT NULL
  AND c.claim_origin = 'attributed_claim'
  AND LENGTH(c.claim_text) >= 100
GROUP BY s.id, s.name, s.normalized_name, s.slug,
         s.speaker_type, s.role, s.party, s.current_office, s.photo_url;
"""

SPEAKER_RECENT_CLAIMS_SQL = """
SELECT
    c.id,
    c.claim_text,
    c.verdict,
    c.verdict_summary,
    c.first_seen,
    a.source_name,
    a.id AS article_id,
    a.url AS article_url
FROM claims c
LEFT JOIN articles a ON a.id = c.article_id
WHERE c.speaker_id = %s
  AND c.verdict IS NOT NULL
  AND c.claim_origin = 'attributed_claim'
  AND LENGTH(c.claim_text) >= 100
  AND c.claim_text NOT ILIKE '%% is the %%'
  AND c.claim_text NOT ILIKE '%% was the %%'
  AND c.claim_text NOT ILIKE '%% are the %%'
  AND c.claim_text NOT ILIKE '%%in court%%'
  AND c.claim_text NOT ILIKE '%%fake news%%'
  AND c.claim_text NOT ILIKE '%%false reporting%%'
  AND c.claim_text NOT LIKE '%%...%%'
  AND c.claim_text NOT ILIKE '%%recalls being told%%'
  AND c.claim_text NOT ILIKE '%%as recalled by%%'
  AND c.claim_text NOT ILIKE '%%underwent surgery%%'
  AND c.claim_text NOT ILIKE '%%good news%%'
  AND c.claim_text NOT ILIKE '%%snake oil%%'
  AND c.claim_text NOT ILIKE '%%pretty good%%'
ORDER BY c.first_seen DESC
LIMIT 20;
"""


EXCLUDED_OUTLET_COUNT_SQL = """
SELECT COUNT(*) FROM (
    SELECT a.source_name
    FROM articles a
    JOIN claims c ON c.article_id = a.id
    WHERE c.verdict IS NOT NULL
      AND c.claim_origin = 'outlet_claim'
    GROUP BY a.source_name
    HAVING COUNT(*) > 0 AND COUNT(*) < %s
) sub;
"""


def compute_score(weighted_sum, scoreable_count):
    if scoreable_count is None or scoreable_count == 0:
        return None
    avg = float(weighted_sum) / float(scoreable_count)
    normalised = (avg + 1.5) / 2.5
    return round(min(max(normalised * 100, 0), 100))


def compute_tier(verdict_count):
    if verdict_count >= 100:
        return "Published"
    if verdict_count >= 50:
        return "Stabilizing"
    if verdict_count >= INCLUSION_THRESHOLD:
        return "Limited Data"
    return "Excluded"


def compute_score_band(score):
    if score is None:
        return "Unscored"
    if score >= 70:
        return "High"
    if score >= 40:
        return "Medium"
    return "Low"


def compute_speaker_score(weighted_sum, scoreable_count):
    """
    Identical math to compute_score(). Canonical reference per v1.7 methodology.
    Speaker scoring uses the same formula, weights, and normalization as outlet scoring.
    Both call the same underlying arithmetic — no parallel implementation.
    """
    return compute_score(weighted_sum, scoreable_count)


def _build_speaker_row(row):
    """Convert a DB row from SPEAKER_BY_ID_SQL into a JSON-serializable dict."""
    (
        speaker_id, name, normalized_name, slug, speaker_type, role, party,
        current_office, photo_url,
        supported, plausible, corroborated, overstated, disputed, not_supported,
        not_verifiable, opinion, verdict_count, scoreable_count, weighted_sum,
        first_verdict_at, last_verdict_at
    ) = row

    score = compute_speaker_score(weighted_sum, scoreable_count)
    tier = compute_tier(scoreable_count or 0)
    band = compute_score_band(score)

    return {
        'id': speaker_id,
        'name': name,
        'normalized_name': normalized_name,
        'slug': slug,
        'speaker_type': speaker_type,
        'role': role,
        'party': party,
        'current_office': current_office,
        'photo_url': photo_url,
        'score': score,
        'tier': tier,
        'band': band,
        'verdict_counts': {
            'supported':      supported or 0,
            'plausible':      plausible or 0,
            'corroborated':   corroborated or 0,
            'overstated':     overstated or 0,
            'disputed':       disputed or 0,
            'not_supported':  not_supported or 0,
            'not_verifiable': not_verifiable or 0,
            'opinion':        opinion or 0,
        },
        'verdict_count':   verdict_count or 0,
        'scoreable_count': scoreable_count or 0,
        'first_verdict_at': first_verdict_at.isoformat() if first_verdict_at else None,
        'last_verdict_at':  last_verdict_at.isoformat() if last_verdict_at else None,
        'methodology_version': METHODOLOGY_VERSION,
    }


_leaderboard_cache = {
    "data": None,
    "generated_at": None,
    "lock": threading.Lock(),
}


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row_to_outlet(row):
    weighted_sum = row["weighted_sum"] or 0
    scoreable_count = row["scoreable_count"] or 0
    score = compute_score(weighted_sum, scoreable_count)
    tier = compute_tier(row["verdict_count"])
    score_band = compute_score_band(score)

    def _iso(ts):
        if ts is None:
            return None
        if isinstance(ts, str):
            return ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat(timespec="seconds").replace("+00:00", "Z")

    return {
        "domain": row["domain"],
        "name": row["name"],
        "score": score,
        "tier": tier,
        "score_band": score_band,
        "verdict_count": row["verdict_count"],
        "scoreable_count": scoreable_count,
        "verdict_breakdown": {
            "supported":      row["supported_count"],
            "plausible":      row["plausible_count"],
            "corroborated":   row["corroborated_count"],
            "overstated":     row["overstated_count"],
            "disputed":       row["disputed_count"],
            "not_supported":  row["not_supported_count"],
            "not_verifiable": row["not_verifiable_count"],
            "opinion":        row["opinion_count"],
        },
        "first_verdict_at": _iso(row["first_verdict_at"]),
        "last_verdict_at":  _iso(row["last_verdict_at"]),
    }


def _compute_leaderboard_from_db(get_db_conn):
    conn = get_db_conn()
    try:
        try:
            from psycopg2.extras import RealDictCursor
            cur = conn.cursor(cursor_factory=RealDictCursor)
        except Exception:
            cur = conn.cursor()

        cur.execute(LEADERBOARD_SQL, (INCLUSION_THRESHOLD,))
        rows = cur.fetchall()

        if rows and not isinstance(rows[0], dict):
            colnames = [d[0] for d in cur.description]
            rows = [dict(zip(colnames, r)) for r in rows]

        outlets = [_row_to_outlet(r) for r in rows]

        cur.execute(EXCLUDED_OUTLET_COUNT_SQL, (INCLUSION_THRESHOLD,))
        excluded_row = cur.fetchone()
        if isinstance(excluded_row, dict):
            outlets_excluded = list(excluded_row.values())[0]
        else:
            outlets_excluded = excluded_row[0] if excluded_row else 0

        cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    in_published   = sum(1 for o in outlets if o["tier"] == "Published")
    in_stabilizing = sum(1 for o in outlets if o["tier"] == "Stabilizing")
    in_limited     = sum(1 for o in outlets if o["tier"] == "Limited Data")

    total_visible_verdicts = sum(o["verdict_count"] for o in outlets)
    total_published_verdicts = sum(
        o["verdict_count"] for o in outlets if o["tier"] == "Published"
    )

    return {
        "generated_at": _iso_now(),
        "methodology_version": METHODOLOGY_VERSION,
        "outlets": outlets,
        "totals": {
            "outlets_in_published":   in_published,
            "outlets_in_stabilizing": in_stabilizing,
            "outlets_in_limited":     in_limited,
            "outlets_excluded":       int(outlets_excluded or 0),
            "total_verdicts_across_published_outlets":  total_published_verdicts,
            "total_verdicts_across_all_visible_outlets": total_visible_verdicts,
        },
    }


def get_leaderboard_data(get_db_conn):
    now = time.time()
    cached = _leaderboard_cache.get("data")
    cached_at = _leaderboard_cache.get("generated_at")

    if cached and cached_at and (now - cached_at) < CACHE_TTL_SECONDS:
        out = dict(cached)
        out["cached"] = True
        out["cache_age_seconds"] = int(now - cached_at)
        return out

    with _leaderboard_cache["lock"]:
        cached = _leaderboard_cache.get("data")
        cached_at = _leaderboard_cache.get("generated_at")
        if cached and cached_at and (time.time() - cached_at) < CACHE_TTL_SECONDS:
            out = dict(cached)
            out["cached"] = True
            out["cache_age_seconds"] = int(time.time() - cached_at)
            return out

        fresh = _compute_leaderboard_from_db(get_db_conn)
        _leaderboard_cache["data"] = fresh
        _leaderboard_cache["generated_at"] = time.time()

        out = dict(fresh)
        out["cached"] = False
        out["cache_age_seconds"] = 0
        return out


def invalidate_leaderboard_cache():
    with _leaderboard_cache["lock"]:
        _leaderboard_cache["data"] = None
        _leaderboard_cache["generated_at"] = None


def register_leaderboard_routes(app, get_db_conn):

    @app.route('/api/speaker/<int:speaker_id>')
    def api_speaker(speaker_id):
        """
        Returns scoring data for a single speaker by ID.
        Uses canonical compute_speaker_score() = compute_score() per v1.7.
        """
        try:
            conn = get_db_conn()
            cur = conn.cursor()

            # Speaker score row
            cur.execute(SPEAKER_BY_ID_SQL, (speaker_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Speaker not found'}), 404

            speaker_data = _build_speaker_row(row)

            # Recent claims
            cur.execute(SPEAKER_RECENT_CLAIMS_SQL, (speaker_id,))
            claims = []
            for c in cur.fetchall():
                claims.append({
                    'id':             c[0],
                    'claim_text':     c[1],
                    'verdict':        c[2],
                    'verdict_label':  VERDICT_LABELS.get(c[2], c[2]),
                    'verdict_summary': c[3],
                    'first_seen':     c[4].isoformat() if c[4] else None,
                    'source_name':    c[5],
                    'short_hash':     c[6],
                })
            speaker_data['recent_claims'] = claims

            cur.close()
            conn.close()
            return jsonify(speaker_data)

        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/speakers')
    def api_speakers():
        """
        Returns all speakers with at least 1 scoreable verdict.
        Sorted by score descending by default.
        """
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute(SPEAKER_SCORE_SQL)
            rows = cur.fetchall()
            speakers = [_build_speaker_row(row) for row in rows]
            # Sort by score descending, unscored last
            speakers.sort(key=lambda s: (s['score'] is not None, s['score'] or 0), reverse=True)
            cur.close()
            conn.close()
            return jsonify({'speakers': speakers, 'count': len(speakers)})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    @app.route("/api/leaderboard")
    def api_leaderboard():
        return jsonify(get_leaderboard_data(get_db_conn))
        
