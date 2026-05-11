"""
Outlet detail routes:
  /outlet/<domain>            — HTML page (stub today, will become full page)
  /api/source/verdicts        — JSON: recent verdicts for an outlet
  /api/source/history         — JSON: score-as-of trajectory for an outlet
"""
import re
import time as _time
from flask import render_template, abort, request, jsonify
from api_leaderboard import (
    get_leaderboard_data,
    compute_score,
    METHODOLOGY_VERSION,
    INCLUSION_THRESHOLD,
)

DOMAIN_RE = re.compile(r"^[a-z0-9.-]+$")

_PRELIM_VERDICT_COUNT_SQL = """
SELECT COUNT(*) AS verdict_count
FROM articles a
JOIN claims c ON c.article_id = a.id
WHERE c.verdict IS NOT NULL
  AND c.claim_origin = 'outlet_claim'
  AND a.source_name = %s;
"""

_history_cache = {}
_HISTORY_TTL_SECONDS = 300

_VERDICT_WEIGHTS = {
    'supported':     1.0,
    'plausible':     0.5,
    'corroborated':  0.5,
    'overstated':   -0.5,
    'disputed':     -1.0,
    'not_supported':-1.5,
}


def _get_preliminary_count(get_db_conn, domain):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute(_PRELIM_VERDICT_COUNT_SQL, (domain,))
        row = cur.fetchone()
        cur.close()
        if row is None:
            return 0
        if isinstance(row, dict):
            return int(row.get("verdict_count", 0) or 0)
        return int(row[0] or 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def register_outlet_routes(app, get_db_conn):

    @app.route("/outlet/<domain>")
    def outlet_detail_stub(domain):
        domain_lc = domain.lower()
        if not DOMAIN_RE.match(domain_lc):
            abort(400, description="Invalid outlet identifier")
        leaderboard = get_leaderboard_data(get_db_conn)
        outlet = next(
            (o for o in leaderboard["outlets"] if o["domain"] == domain_lc),
            None,
        )
        if outlet is None:
            verdict_count = _get_preliminary_count(get_db_conn, domain_lc)
            return render_template(
                "outlet_not_yet_published.html",
                domain=domain_lc,
                verdict_count=verdict_count,
                threshold=INCLUSION_THRESHOLD,
                needed=max(0, INCLUSION_THRESHOLD - verdict_count),
                methodology_version=METHODOLOGY_VERSION,
            )
        return render_template(
            "outlet_stub.html",
            outlet=outlet,
            methodology_version=leaderboard["methodology_version"],
        )

    @app.route("/api/source/verdicts", methods=["GET"])
    def get_source_verdicts():
        domain = request.args.get("domain", "").strip()
        if not domain:
            return jsonify({"error": "domain required"}), 400
        core = domain.replace("www.", "").lower()

        try:
            limit = int(request.args.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 50))

        try:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    c.id, c.verdict, c.claim_text,
                    a.id, a.title, a.fetched_at, a.published_at
                FROM claims c
                JOIN articles a ON a.id = c.article_id
                WHERE LOWER(a.source_name) = %s
                  AND c.verdict IS NOT NULL
                  AND c.claim_origin = 'outlet_claim'
                ORDER BY COALESCE(a.published_at, a.fetched_at) DESC NULLS LAST,
                         c.id DESC
                LIMIT %s
            """, (core, limit))
            rows = cur.fetchall()

            cur.execute("""
                SELECT MAX(c.first_seen)
                FROM articles a
                JOIN claims c ON c.article_id = a.id
                WHERE LOWER(a.source_name) = %s
                  AND c.verdict IS NOT NULL
                  AND c.claim_origin = 'outlet_claim'
            """, (core,))
            last_at = cur.fetchone()[0]
            cur.close()
            conn.close()

            verdicts = []
            for r in rows:
                claim_id, verdict, claim_text, article_id, title, fetched_at, published_at = r
                row_dt = published_at or fetched_at
                verdicts.append({
                    "id":         str(claim_id),
                    "date":       row_dt.strftime("%Y-%m-%d") if row_dt else None,
                    "verdict":    verdict,
                    "headline":   title,
                    "claim":      claim_text,
                    "article_id": article_id,
                    "report_url": "/report?article_id=" + str(article_id),
                })

            return jsonify({
                "domain":              domain,
                "as_of":               last_at.strftime("%Y-%m-%d") if last_at else None,
                "methodology_version": METHODOLOGY_VERSION,
                "verdicts":            verdicts,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/source/history", methods=["GET"])
    def get_source_history():
        domain = request.args.get("domain", "").strip()
        if not domain:
            return jsonify({"error": "domain required"}), 400
        core = domain.replace("www.", "").lower()

        cached = _history_cache.get(core)
        if cached and (_time.time() - cached[0]) < _HISTORY_TTL_SECONDS:
            return jsonify(cached[1])

        try:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT c.verdict,
                       COALESCE(a.published_at, a.fetched_at) AS dt
                FROM claims c
                JOIN articles a ON a.id = c.article_id
                WHERE LOWER(a.source_name) = %s
                  AND c.verdict IS NOT NULL
                  AND c.claim_origin = 'outlet_claim'
                  AND c.verdict NOT IN ('not_verifiable', 'opinion')
                ORDER BY COALESCE(a.published_at, a.fetched_at) ASC NULLS LAST,
                         c.id ASC
            """, (core,))
            rows = cur.fetchall()
            cur.close()
            conn.close()

            history = []
            running_sum = 0.0
            running_n = 0
            for verdict, dt in rows:
                w = _VERDICT_WEIGHTS.get(verdict, 0)
                running_sum += w
                running_n += 1
                score = compute_score(running_sum, running_n)
                history.append({
                    "date":  dt.strftime("%Y-%m-%d") if dt else None,
                    "n":     running_n,
                    "score": score,
                })

            payload = {
                "domain":              domain,
                "methodology_version": METHODOLOGY_VERSION,
                "history":             history,
            }
            _history_cache[core] = (_time.time(), payload)
            return jsonify(payload)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
