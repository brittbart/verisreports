
"""
Leaderboard API endpoint — Methodology v1.5 compliant.
"""

import time
import threading
from datetime import datetime, timezone
from flask import jsonify

METHODOLOGY_VERSION = "v1.5"
CACHE_TTL_SECONDS = 300
INCLUSION_THRESHOLD = 20

LEADERBOARD_SQL = """
SELECT
    a.source_name AS domain,
    COALESCE(s.display_name, a.source_name) AS name,
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
    MIN(c.created_at) AS first_verdict_at,
    MAX(c.created_at) AS last_verdict_at
FROM articles a
JOIN claims   c ON c.article_id = a.id
LEFT JOIN sources s ON s.domain = a.source_name
WHERE c.verdict IS NOT NULL
  AND c.claim_origin = 'outlet_claim'
GROUP BY a.source_name, s.display_name
HAVING COUNT(*) >= %s
ORDER BY a.source_name;
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
    avg = weighted_sum / scoreable_count
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
        return None
    if score >= 70:
        return "High"
    if score >= 40:
        return "Medium"
    return "Low"


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
    @app.route("/api/leaderboard")
    def api_leaderboard():
        return jsonify(get_leaderboard_data(get_db_conn))
        
