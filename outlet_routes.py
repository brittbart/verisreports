"""
Outlet detail routes:
  /outlet/<domain>            — HTML page (full v3 layout, server-rendered)
  /api/source/verdicts        — JSON: recent verdicts for an outlet
  /api/source/history         — JSON: score-as-of trajectory for an outlet
"""
import re
import time as _time
from flask import render_template, abort, request, jsonify
from api_leaderboard import (
    get_leaderboard_data,
    compute_score,
    compute_score_band,
    compute_tier,
    METHODOLOGY_VERSION,
    INCLUSION_THRESHOLD,
    WEIGHTS,
    SCOREABLE_VERDICTS,
)

DOMAIN_RE = re.compile(r"^[a-z0-9.-]+$")

_history_cache = {}
_HISTORY_TTL_SECONDS = 300

# Methodology constants (WEIGHTS, SCOREABLE_VERDICTS) imported from api_leaderboard.


def _get_outlet_aggregates(get_db_conn, domain_lc):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT
                COUNT(*) FILTER (WHERE c.verdict = 'supported')      AS supported,
                COUNT(*) FILTER (WHERE c.verdict = 'plausible')      AS plausible,
                COUNT(*) FILTER (WHERE c.verdict = 'corroborated')   AS corroborated,
                COUNT(*) FILTER (WHERE c.verdict = 'overstated')     AS overstated,
                COUNT(*) FILTER (WHERE c.verdict = 'disputed')       AS disputed,
                COUNT(*) FILTER (WHERE c.verdict = 'not_supported')  AS not_supported,
                COUNT(*) FILTER (WHERE c.verdict = 'not_verifiable') AS not_verifiable,
                COUNT(*) FILTER (WHERE c.verdict = 'opinion')        AS opinion,
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
                MAX(c.first_seen)                                    AS last_verdict_at
            FROM articles a
            JOIN claims   c ON c.article_id = a.id
            WHERE LOWER(a.source_name) = %s
              AND c.verdict IS NOT NULL
              AND c.claim_origin = 'outlet_claim'
        ''', (domain_lc,))
        row = cur.fetchone()
        cur.close()
        if row is None or (row[8] or 0) == 0:
            return None
        return {
            'supported':       row[0] or 0,
            'plausible':       row[1] or 0,
            'corroborated':    row[2] or 0,
            'overstated':      row[3] or 0,
            'disputed':        row[4] or 0,
            'not_supported':   row[5] or 0,
            'not_verifiable':  row[6] or 0,
            'opinion':         row[7] or 0,
            'verdict_count':   row[8] or 0,
            'scoreable_count': row[9] or 0,
            'weighted_sum':    row[10] or 0,
            'last_verdict_at': row[11],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_recent_verdicts(get_db_conn, domain_lc, limit):
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT
                c.id, c.verdict, c.claim_text,
                a.id, a.title, a.fetched_at, a.published_at, a.url
            FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE LOWER(a.source_name) = %s
              AND c.verdict IS NOT NULL
              AND c.claim_origin = 'outlet_claim'
            ORDER BY COALESCE(a.published_at, a.fetched_at) DESC NULLS LAST,
                     c.id DESC
            LIMIT %s
        ''', (domain_lc, limit))
        rows = cur.fetchall()
        cur.close()
        out = []
        for r in rows:
            cid, verdict, claim_text, article_id, title, fetched_at, published_at, url = r
            row_dt = published_at or fetched_at
            out.append({
                'id':         str(cid),
                'date':       row_dt.strftime('%Y-%m-%d') if row_dt else None,
                'verdict':    verdict,
                'headline':   title,
                'claim':      claim_text,
                'article_id': article_id,
                'report':     ('/report?url=' + url) if url else '#',
            })
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_score_history(get_db_conn, domain_lc):
    cached = _history_cache.get(domain_lc)
    if cached and (_time.time() - cached[0]) < _HISTORY_TTL_SECONDS:
        return cached[1]
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
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
        ''', (domain_lc,))
        rows = cur.fetchall()
        cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    history = []
    running_sum = 0.0
    running_n = 0
    for verdict, dt in rows:
        running_sum += WEIGHTS.get(verdict, 0)
        running_n += 1
        history.append({
            'date':  dt.strftime('%Y-%m-%d') if dt else None,
            'n':     running_n,
            'score': compute_score(running_sum, running_n),
        })
    _history_cache[domain_lc] = (_time.time(), history)
    return history


def _build_outlet_view(get_db_conn, domain_lc):
    agg = _get_outlet_aggregates(get_db_conn, domain_lc)
    history = _get_score_history(get_db_conn, domain_lc)
    verdicts = _get_recent_verdicts(get_db_conn, domain_lc, 20)
    if agg is None:
        return {
            'domain':             domain_lc,
            'state':              'sub_threshold',
            'score':              None,
            'score_band':         None,
            'tier':               'Excluded',
            'scored_as_of':       None,
            'verdict_count':      0,
            'scoreable_count':    0,
            'excluded_count':     0,
            'threshold':          INCLUSION_THRESHOLD,
            'verdict_breakdown':  {t: 0 for t in WEIGHTS},
            'excluded_breakdown': {'opinion': 0, 'not_verifiable': 0},
            'history':            [],
            'verdicts':           [],
        }
    is_scored = agg['verdict_count'] >= INCLUSION_THRESHOLD
    score = compute_score(agg['weighted_sum'], agg['scoreable_count']) if is_scored else None
    band = compute_score_band(score) if score is not None else None
    tier = compute_tier(agg['verdict_count'])
    return {
        'domain':            domain_lc,
        'state':             'scored' if is_scored else 'sub_threshold',
        'score':             score,
        'score_band':        band,
        'tier':              tier,
        'scored_as_of':      agg['last_verdict_at'].strftime('%Y-%m-%d') if agg['last_verdict_at'] else None,
        'verdict_count':     agg['verdict_count'],
        'scoreable_count':   agg['scoreable_count'],
        'excluded_count':    agg['verdict_count'] - agg['scoreable_count'],
        'threshold':         INCLUSION_THRESHOLD,
        'verdict_breakdown': {
            'supported':     agg['supported'],
            'plausible':     agg['plausible'],
            'corroborated':  agg['corroborated'],
            'overstated':    agg['overstated'],
            'disputed':      agg['disputed'],
            'not_supported': agg['not_supported'],
        },
        'excluded_breakdown': {
            'opinion':        agg['opinion'],
            'not_verifiable': agg['not_verifiable'],
        },
        'history':           history,
        'verdicts':          verdicts,
    }


def register_outlet_routes(app, get_db_conn):

    @app.route("/outlet/<domain>")
    def outlet_detail(domain):
        domain_lc = domain.lower()
        if not DOMAIN_RE.match(domain_lc):
            abort(400, description="Invalid outlet identifier")
        outlet = _build_outlet_view(get_db_conn, domain_lc)
        return render_template(
            "outlet.html",
            outlet=outlet,
            methodology_version=METHODOLOGY_VERSION,
            inclusion_threshold=INCLUSION_THRESHOLD,
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
            verdicts = _get_recent_verdicts(get_db_conn, core, limit)
            agg = _get_outlet_aggregates(get_db_conn, core)
            last_at = agg['last_verdict_at'] if agg else None
            api_verdicts = []
            for v in verdicts:
                api_verdicts.append({
                    'id':         v['id'],
                    'date':       v['date'],
                    'verdict':    v['verdict'],
                    'headline':   v['headline'],
                    'claim':      v['claim'],
                    'article_id': v['article_id'],
                    'report_url': v['report'],
                })
            return jsonify({
                "domain":              domain,
                "as_of":               last_at.strftime("%Y-%m-%d") if last_at else None,
                "methodology_version": METHODOLOGY_VERSION,
                "verdicts":            api_verdicts,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/source/history", methods=["GET"])
    def get_source_history():
        domain = request.args.get("domain", "").strip()
        if not domain:
            return jsonify({"error": "domain required"}), 400
        core = domain.replace("www.", "").lower()
        try:
            history = _get_score_history(get_db_conn, core)
            return jsonify({
                "domain":              domain,
                "methodology_version": METHODOLOGY_VERSION,
                "history":             history,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500
