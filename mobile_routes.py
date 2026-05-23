"""
mobile_routes.py — Verum Signal Mobile API
==========================================
Flask blueprint for /mobile/v1/* endpoints.
Registered in api.py via: register_mobile_routes(app, get_db)

Authentication
--------------
All endpoints accept an optional Authorization: Bearer <token> header.
In Phase 1 (pre-Clerk), auth is stubbed — the header is parsed but not
validated. Clerk JWT validation wires in via mobile_auth.py in Phase 1.

Endpoints in this file (Phase 1 skeleton)
------------------------------------------
Public (no auth required):
  GET  /mobile/v1/health
  GET  /mobile/v1/articles
  GET  /mobile/v1/articles/<article_id>/report
  GET  /mobile/v1/outlets/leaderboard
  GET  /mobile/v1/outlets/<domain>
  GET  /mobile/v1/debates
  GET  /mobile/v1/debates/<slug>
  GET  /mobile/v1/methodology

Auth-required (stubs — return 401 until Clerk wired):
  POST /mobile/v1/auth/sync
  GET  /mobile/v1/me
  POST /mobile/v1/devices/register
  POST /mobile/v1/articles/<article_id>/save
  DELETE /mobile/v1/articles/<article_id>/save
  POST /mobile/v1/outlets/<domain>/follow
  DELETE /mobile/v1/outlets/<domain>/follow
  POST /mobile/v1/events/<event_id>/follow
  DELETE /mobile/v1/events/<event_id>/follow
  GET  /mobile/v1/me/saved
  GET  /mobile/v1/me/follows
  GET  /mobile/v1/me/notifications
  PATCH /mobile/v1/me/notifications
"""

from flask import Blueprint, jsonify, request, g
from datetime import datetime, timezone
import traceback

MOBILE_API_VERSION = "1.0.0"
MOBILE_FORCE_DEEP_TIER = True  # v1 override — everyone sees deep reports
                                # v1.1: remove this, read from user.tier

# ── blueprint ──────────────────────────────────────────────────────────────

mobile_bp = Blueprint('mobile', __name__, url_prefix='/mobile/v1')

# ── helpers ────────────────────────────────────────────────────────────────

def ok(data, status=200):
    return jsonify({"status": "ok", "data": data}), status

def err(message, status=400, code=None):
    body = {"status": "error", "message": message}
    if code:
        body["code"] = code
    return jsonify(body), status

def get_current_user():
    """
    Stub: returns None until Clerk JWT validation is wired in mobile_auth.py.
    Phase 1: always returns None (anonymous).
    Phase 2: validate Bearer token, return user dict or None.
    """
    return None

def require_auth():
    """Returns (user, error_response). If error_response is not None, return it immediately."""
    user = get_current_user()
    if user is None:
        return None, err("Authentication required", 401, "UNAUTHENTICATED")
    return user, None

def format_verdict(verdict):
    """Normalize verdict string for mobile display."""
    if not verdict:
        return None
    return verdict.lower().replace(" ", "_")

def format_time_ago(dt):
    """Convert datetime to relative string for mobile cards."""
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except Exception:
            return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 3600:
        mins = max(1, seconds // 60)
        return f"{mins}m ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    elif seconds < 86400 * 2:
        return "Yesterday"
    else:
        return dt.strftime("%-d %b")

def paginate_cursor(request, default_limit=20, max_limit=50):
    """Parse cursor pagination params from request."""
    try:
        limit = min(int(request.args.get('limit', default_limit)), max_limit)
    except (ValueError, TypeError):
        limit = default_limit
    cursor = request.args.get('cursor')  # article_id or timestamp-based
    return limit, cursor

# ── health ─────────────────────────────────────────────────────────────────

@mobile_bp.route('/health')
def health():
    return ok({
        "version": MOBILE_API_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

# ── articles ───────────────────────────────────────────────────────────────

@mobile_bp.route('/articles')
def articles():
    """
    GET /mobile/v1/articles
    Query params:
      sort    = recent | top_scored (default: recent)
      filter  = news | politics | international | crypto (optional)
      limit   = int (default 20, max 50)
      cursor  = article_id for pagination (articles with id < cursor)
    """
    db = mobile_bp.get_db()
    cur = db.cursor()

    sort = request.args.get('sort', 'recent')
    filter_cat = request.args.get('filter')
    limit, cursor = paginate_cursor(request)

    try:
        # Build query
        where_clauses = [
            "a.excluded_from_extraction = FALSE",
            "a.published_at IS NOT NULL",
        ]
        params = []

        if cursor:
            try:
                cursor_id = int(cursor)
                where_clauses.append("a.id < %s")
                params.append(cursor_id)
            except ValueError:
                pass

        if filter_cat:
            where_clauses.append("o.category ILIKE %s")
            params.append(f"%{filter_cat}%")

        where_sql = " AND ".join(where_clauses)

        order_sql = {
            'top_scored': "o.score DESC NULLS LAST, a.published_at DESC",
            'recent':     "a.published_at DESC",
        }.get(sort, "a.published_at DESC")

        params.append(limit)

        cur.execute(f"""
            SELECT
                a.id,
                a.url,
                a.title,
                a.byline,
                a.published_at,
                a.lead_image_url,
                o.outlet_id     AS outlet_domain,
                o.outlet_name   AS outlet_name,
                o.score         AS outlet_score,
                o.tier          AS outlet_tier,
                COUNT(c.id)     AS claim_count,
                COUNT(c.id) FILTER (WHERE c.verdict IS NOT NULL AND c.verdict != 'opinion' AND c.verdict != 'not_verifiable') AS scoreable_count
            FROM articles a
            LEFT JOIN api_outlets o ON o.outlet_id = a.source_domain
            LEFT JOIN claims c ON c.article_id = a.id
              AND c.claim_origin = 'outlet_claim'
            WHERE {where_sql}
            GROUP BY a.id, a.url, a.title, a.byline, a.published_at,
                     a.lead_image_url, o.domain, o.name, o.score, o.tier
            ORDER BY {order_sql}
            LIMIT %s
        """, params)

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        # Fetch verdict distributions for returned article IDs
        article_ids = [r[0] for r in rows]
        verdict_dist = {}
        if article_ids:
            cur.execute("""
                SELECT article_id, verdict, COUNT(*) as cnt
                FROM claims
                WHERE article_id = ANY(%s)
                  AND claim_origin = 'outlet_claim'
                  AND verdict IS NOT NULL
                GROUP BY article_id, verdict
            """, (article_ids,))
            for art_id, verdict, cnt in cur.fetchall():
                if art_id not in verdict_dist:
                    verdict_dist[art_id] = {}
                verdict_dist[art_id][verdict] = cnt

        articles_out = []
        for row in rows:
            r = dict(zip(cols, row))
            art_id = r['id']
            articles_out.append({
                "id":           art_id,
                "url":          r['url'],
                "headline":     r['title'],
                "byline":       r['byline'],
                "published_at": r['published_at'].isoformat() if r['published_at'] else None,
                "time_ago":     format_time_ago(r['published_at']),
                "lead_image_url": r['lead_image_url'],
                "outlet": {
                    "domain": r['outlet_domain'],
                    "name":   r['outlet_name'],
                    "score":  float(r['outlet_score']) if r['outlet_score'] is not None else None,
                    "tier":   r['outlet_tier'],
                },
                "claim_count":    int(r['claim_count']),
                "scoreable_count": int(r['scoreable_count']),
                "analysis_ready": int(r['claim_count']) > 0,
                "verdict_distribution": verdict_dist.get(art_id, {}),
            })

        next_cursor = str(rows[-1][0]) if len(rows) == limit else None

        return ok({
            "articles": articles_out,
            "next_cursor": next_cursor,
            "count": len(articles_out),
        })

    except Exception as e:
        traceback.print_exc()
        return err("Failed to fetch articles", 500, "SERVER_ERROR")
    finally:
        cur.close()
        db.close()

@mobile_bp.route('/articles/<int:article_id>/report')
def article_report(article_id):
    """
    GET /mobile/v1/articles/<article_id>/report
    Returns the full Verum Signal report for an article.
    Tier: always 'deep' in v1 via MOBILE_FORCE_DEEP_TIER.
    """
    db = mobile_bp.get_db()
    cur = db.cursor()

    user = get_current_user()
    tier = "deep" if MOBILE_FORCE_DEEP_TIER else (user.get('tier', 'free') if user else 'free')
    claim_limit = 99 if tier == 'deep' else 2

    try:
        # Article
        cur.execute("""
            SELECT a.id, a.url, a.title, a.byline, a.published_at,
                   a.lead_image_url, a.source_domain, a.vs_summary,
                   o.domain, o.name, o.score, o.tier,
                   rl.hash AS report_hash
            FROM articles a
            LEFT JOIN api_outlets o ON o.outlet_id = a.source_domain
            LEFT JOIN report_links rl ON rl.article_id = a.id
            WHERE a.id = %s
        """, (article_id,))
        row = cur.fetchone()

        if not row:
            return err("Article not found", 404, "NOT_FOUND")

        (art_id, url, title, byline, published_at, lead_image_url,
         source_domain, vs_summary, outlet_domain, outlet_name,
         outlet_score, outlet_tier, report_hash) = row

        # Claims
        cur.execute("""
            SELECT
                c.id, c.claim_text, c.verdict, c.confidence_score,
                c.verdict_summary, c.full_analysis, c.sources_used,
                c.claim_origin, c.attribution_context,
                c.verdict_status, c.methodology_version,
                c.first_seen
            FROM claims c
            WHERE c.article_id = %s
              AND c.claim_origin = 'outlet_claim'
              AND c.verdict IS NOT NULL
            ORDER BY c.confidence_score DESC NULLS LAST
            LIMIT %s
        """, (article_id, claim_limit))

        claim_rows = cur.fetchall()
        claim_cols = [d[0] for d in cur.description]

        claims_out = []
        for cr in claim_rows:
            c = dict(zip(claim_cols, cr))
            claims_out.append({
                "id":               c['id'],
                "claim_text":       c['claim_text'],
                "verdict":          format_verdict(c['verdict']),
                "confidence_score": float(c['confidence_score']) if c['confidence_score'] else None,
                "verdict_summary":  c['verdict_summary'],
                "full_analysis":    c['full_analysis'],
                "sources_used":     c['sources_used'] if c['sources_used'] else [],
                "claim_origin":     c['claim_origin'],
                "attribution_context": c['attribution_context'],
                "is_provisional":   c['verdict_status'] == 'provisional',
                "methodology_version": c['methodology_version'],
            })

        # Article score (reuse api_outlets score for now;
        # per-article score computed server-side in future)
        article_score = None
        if len(claims_out) >= 3:
            article_score = float(outlet_score) if outlet_score else None

        return ok({
            "article": {
                "id":             art_id,
                "url":            url,
                "headline":       title,
                "byline":         byline,
                "published_at":   published_at.isoformat() if published_at else None,
                "lead_image_url": lead_image_url,
                "report_hash":    report_hash,
                "share_url":      f"https://verumsignal.com/r/{report_hash}" if report_hash else None,
            },
            "outlet": {
                "domain": outlet_domain or source_domain,
                "name":   outlet_name,
                "score":  float(outlet_score) if outlet_score is not None else None,
                "tier":   outlet_tier,
            },
            "report": {
                "tier":          tier,
                "article_score": article_score,
                "is_scored":     article_score is not None,
                "vs_summary":    vs_summary,
                "claim_count":   len(claims_out),
                "claims":        claims_out,
                "methodology_version": "v1.7",  # TODO: read from PUBLIC_METHODOLOGY_VERSIONS
            },
        })

    except Exception as e:
        traceback.print_exc()
        return err("Failed to fetch report", 500, "SERVER_ERROR")
    finally:
        cur.close()
        db.close()

# ── outlets ────────────────────────────────────────────────────────────────

@mobile_bp.route('/outlets/leaderboard')
def outlets_leaderboard():
    """
    GET /mobile/v1/outlets/leaderboard
    Query params:
      limit  = int (default 50, max 200)
      cursor = rank offset for pagination
    """
    db = mobile_bp.get_db()
    cur = db.cursor()

    try:
        limit = min(int(request.args.get('limit', 50)), 200)
        offset = int(request.args.get('offset', 0))

        cur.execute("""
            SELECT
                outlet_id, outlet_name, score, tier,
                total_scoreable_claims, verdict_counts, last_evaluated_at,
                rank() OVER (ORDER BY score DESC NULLS LAST) AS rank
            FROM api_outlets
            WHERE score IS NOT NULL
            ORDER BY score DESC NULLS LAST
            LIMIT %s OFFSET %s
        """, (limit, offset))

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        outlets_out = []
        for row in rows:
            r = dict(zip(cols, row))
            outlets_out.append({
                "domain":      r['outlet_id'],
                "name":        r['outlet_name'],
                "score":       float(r['score']) if r['score'] is not None else None,
                "tier":        r['tier'],
                "category":    None,
                "claim_count": r['total_scoreable_claims'],
                "rank":        r['rank'],
                "verdict_counts": r['verdict_counts'] if r['verdict_counts'] else {},
                "last_evaluated_at": r['last_evaluated_at'].isoformat() if r['last_evaluated_at'] else None,
            })

        return ok({
            "outlets": outlets_out,
            "count": len(outlets_out),
            "offset": offset,
        })

    except Exception as e:
        traceback.print_exc()
        return err("Failed to fetch leaderboard", 500, "SERVER_ERROR")
    finally:
        cur.close()
        db.close()

@mobile_bp.route('/outlets/<path:domain>')
def outlet_detail(domain):
    """
    GET /mobile/v1/outlets/<domain>
    Returns outlet detail with recent claims.
    """
    db = mobile_bp.get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT outlet_id, outlet_name, score, tier,
                   total_scoreable_claims, verdict_counts, last_evaluated_at
            FROM api_outlets
            WHERE outlet_id = %s
        """, (domain,))
        row = cur.fetchone()

        if not row:
            return err("Outlet not found", 404, "NOT_FOUND")

        cols = [d[0] for d in cur.description]
        o = dict(zip(cols, row))

        # Recent claims for this outlet
        cur.execute("""
            SELECT c.id, c.claim_text, c.verdict, c.confidence_score,
                   c.verdict_summary, c.first_seen, a.title AS article_title,
                   a.id AS article_id
            FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE a.source_domain = %s
              AND c.claim_origin = 'outlet_claim'
              AND c.verdict IS NOT NULL
            ORDER BY c.first_seen DESC NULLS LAST
            LIMIT 20
        """, (domain,))

        claim_rows = cur.fetchall()
        claim_cols = [d[0] for d in cur.description]

        recent_claims = []
        for cr in claim_rows:
            c = dict(zip(claim_cols, cr))
            recent_claims.append({
                "id":             c['id'],
                "claim_text":     c['claim_text'],
                "verdict":        format_verdict(c['verdict']),
                "confidence_score": float(c['confidence_score']) if c['confidence_score'] else None,
                "verdict_summary": c['verdict_summary'],
                "first_seen":     c['first_seen'].isoformat() if c['first_seen'] else None,
                "article_id":     c['article_id'],
                "article_title":  c['article_title'],
            })

        return ok({
            "outlet": {
                "domain":      o['outlet_id'],
                "name":        o['outlet_name'],
                "score":       float(o['score']) if o['score'] is not None else None,
                "tier":        o['tier'],
                "category":    None,
                "claim_count": o['total_scoreable_claims'],
                "verdict_counts": o['verdict_counts'] if o['verdict_counts'] else {},
                "last_evaluated_at": o['last_evaluated_at'].isoformat() if o['last_evaluated_at'] else None,
            },
            "recent_claims": recent_claims,
        })

    except Exception as e:
        traceback.print_exc()
        return err("Failed to fetch outlet", 500, "SERVER_ERROR")
    finally:
        cur.close()
        db.close()

# ── debates ────────────────────────────────────────────────────────────────

@mobile_bp.route('/debates')
def debates_list():
    """
    GET /mobile/v1/debates
    Returns upcoming and past debates.
    """
    db = mobile_bp.get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT
                e.id, e.slug, e.event_name, e.event_subtitle,
                e.event_date, e.venue, e.stream_url, e.notes,
                COUNT(DISTINCT su.id) AS utterance_count,
                COUNT(DISTINCT c.id)  AS claim_count
            FROM events e
            LEFT JOIN speaker_utterances su ON su.event_id = e.id
            LEFT JOIN claims c ON c.event_id = e.id
              AND c.verdict IS NOT NULL
            GROUP BY e.id, e.slug, e.event_name, e.event_subtitle,
                     e.event_date, e.venue, e.stream_url, e.notes
            ORDER BY e.event_date DESC NULLS LAST
            LIMIT 50
        """)

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        now = datetime.now(timezone.utc).date()
        debates_out = []
        for row in rows:
            e = dict(zip(cols, row))
            event_date = e['event_date']

            is_upcoming = event_date and event_date > now
            is_live = False

            debates_out.append({
                "id":          e['id'],
                "slug":        e['slug'],
                "title":       e['event_name'],
                "description": e['event_subtitle'],
                "event_date":  event_date.isoformat() if event_date else None,
                "venue":       e['venue'],
                "notes":       e['notes'],
                "stream_url":  e['stream_url'],
                "is_live":     is_live,
                "is_upcoming": is_upcoming,
                "claim_count": int(e['claim_count']),
                "utterance_count": int(e['utterance_count']),
            })

        # Separate live/upcoming from past for mobile display
        live = [d for d in debates_out if d['is_live']]
        upcoming = [d for d in debates_out if d['is_upcoming']]
        past = [d for d in debates_out if not d['is_live'] and not d['is_upcoming']]

        return ok({
            "live":     live,
            "upcoming": upcoming,
            "past":     past,
        })

    except Exception as e:
        traceback.print_exc()
        return err("Failed to fetch debates", 500, "SERVER_ERROR")
    finally:
        cur.close()
        db.close()

@mobile_bp.route('/debates/<slug>')
def debate_detail(slug):
    """
    GET /mobile/v1/debates/<slug>
    Returns full debate detail with speakers and claims.
    """
    db = mobile_bp.get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT id, slug, event_name, event_subtitle,
                   event_date, venue, stream_url, notes
            FROM events
            WHERE slug = %s
        """, (slug,))
        row = cur.fetchone()

        if not row:
            return err("Debate not found", 404, "NOT_FOUND")

        cols = [d[0] for d in cur.description]
        e = dict(zip(cols, row))
        event_id = e['id']

        # Speakers
        cur.execute("""
            SELECT s.id, s.name, s.title, s.party,
                   es.speaker_order
            FROM event_speakers es
            JOIN speakers s ON s.id = es.speaker_id
            WHERE es.event_id = %s
            ORDER BY es.speaker_order NULLS LAST, s.name
        """, (event_id,))
        speaker_rows = cur.fetchall()
        speaker_cols = [d[0] for d in cur.description]
        speakers = [dict(zip(speaker_cols, sr)) for sr in speaker_rows]

        # Claims
        cur.execute("""
            SELECT
                c.id, c.claim_text, c.verdict, c.confidence_score,
                c.verdict_summary, c.attribution_context,
                c.verdict_status, c.first_seen,
                s.id AS speaker_id, s.name AS speaker_name
            FROM claims c
            LEFT JOIN speakers s ON s.id = c.speaker_id
            WHERE c.event_id = %s
              AND c.verdict IS NOT NULL
            ORDER BY c.first_seen ASC NULLS LAST
        """, (event_id,))

        claim_rows = cur.fetchall()
        claim_cols = [d[0] for d in cur.description]

        claims_out = []
        for cr in claim_rows:
            c = dict(zip(claim_cols, cr))
            claims_out.append({
                "id":           c['id'],
                "claim_text":   c['claim_text'],
                "verdict":      format_verdict(c['verdict']),
                "confidence_score": float(c['confidence_score']) if c['confidence_score'] else None,
                "verdict_summary": c['verdict_summary'],
                "speaker_id":   c['speaker_id'],
                "speaker_name": c['speaker_name'] or c['attribution_context'],
                "is_provisional": c['verdict_status'] == 'provisional',
                "first_seen":   c['first_seen'].isoformat() if c['first_seen'] else None,
            })

        return ok({
            "debate": {
                "id":          event_id,
                "slug":        e['slug'],
                "title":       e['event_name'],
                "description": e['event_subtitle'],
                "event_date":  e['event_date'].isoformat() if e['event_date'] else None,
                "venue":       e['venue'],
                "notes":       e['notes'],
                "is_live":     False,
            },
            "speakers": speakers,
            "claims":   claims_out,
            "claim_count": len(claims_out),
        })

    except Exception as e:
        traceback.print_exc()
        return err("Failed to fetch debate", 500, "SERVER_ERROR")
    finally:
        cur.close()
        db.close()

# ── methodology ────────────────────────────────────────────────────────────

@mobile_bp.route('/methodology')
def methodology():
    """
    GET /mobile/v1/methodology
    Returns current public methodology version info.
    App renders methodology in a webview; this endpoint provides
    the version string and URL so the app can link correctly.
    """
    import os
    raw = os.environ.get('PUBLIC_METHODOLOGY_VERSIONS', "['v1.6']")
    try:
        import ast
        versions = ast.literal_eval(raw)
    except Exception:
        versions = ['v1.6']

    current = versions[-1] if versions else 'v1.6'

    return ok({
        "current_version": current,
        "available_versions": versions,
        "methodology_url": f"https://verumsignal.com/methodology",
        "render_mode": "webview",  # app opens this URL in-app browser
    })

# ── auth stubs (Phase 1 — return 501 until Clerk wired) ───────────────────

@mobile_bp.route('/auth/sync', methods=['POST'])
def auth_sync():
    return err("Auth not yet configured", 501, "AUTH_NOT_IMPLEMENTED")

@mobile_bp.route('/me')
def me():
    return err("Auth not yet configured", 501, "AUTH_NOT_IMPLEMENTED")

@mobile_bp.route('/devices/register', methods=['POST'])
def devices_register():
    return err("Auth not yet configured", 501, "AUTH_NOT_IMPLEMENTED")

# ── save/follow stubs ──────────────────────────────────────────────────────

@mobile_bp.route('/articles/<int:article_id>/save', methods=['POST', 'DELETE'])
def article_save(article_id):
    user, error = require_auth()
    if error:
        return error
    return err("Not yet implemented", 501, "NOT_IMPLEMENTED")

@mobile_bp.route('/outlets/<path:domain>/follow', methods=['POST', 'DELETE'])
def outlet_follow(domain):
    user, error = require_auth()
    if error:
        return error
    return err("Not yet implemented", 501, "NOT_IMPLEMENTED")

@mobile_bp.route('/events/<int:event_id>/follow', methods=['POST', 'DELETE'])
def event_follow(event_id):
    user, error = require_auth()
    if error:
        return error
    return err("Not yet implemented", 501, "NOT_IMPLEMENTED")

@mobile_bp.route('/me/saved')
def me_saved():
    user, error = require_auth()
    if error:
        return error
    return err("Not yet implemented", 501, "NOT_IMPLEMENTED")

@mobile_bp.route('/me/follows')
def me_follows():
    user, error = require_auth()
    if error:
        return error
    return err("Not yet implemented", 501, "NOT_IMPLEMENTED")

@mobile_bp.route('/me/notifications', methods=['GET', 'PATCH'])
def me_notifications():
    user, error = require_auth()
    if error:
        return error
    return err("Not yet implemented", 501, "NOT_IMPLEMENTED")

# ── registration ───────────────────────────────────────────────────────────

def register_mobile_routes(app, get_db_fn):
    """
    Register mobile routes with the Flask app.
    Called from api.py: register_mobile_routes(app, get_db)
    """
    # Attach get_db to blueprint so endpoints can call mobile_bp.get_db()
    mobile_bp.get_db = get_db_fn
    app.register_blueprint(mobile_bp)
    print("[mobile_routes] registered /mobile/v1/* endpoints")
