"""
api_public.py

Verum Signal Public API — v1
Blueprint registered on the main Flask app (api.py).

Served at api.verumsignal.com/v1/*
Any request to api.verumsignal.com outside /v1, /docs, /openapi.yaml → 404.

Auth: Authorization: Bearer <api_key> header only.
      No ?api_key= query param accepted.

All DB access via get_db() — uses hardcoded fallback at api.py:33.

NEVER expose: verdict_summary, evidence_sources, priority_score,
              verification_attempts, or any internal scoring field.
"""

import hashlib
import time
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, request, jsonify, g

log = logging.getLogger(__name__)



api_public = Blueprint('api_public', __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_HOST = 'api.verumsignal.com'

ALLOWED_API_PATHS = ('/v1', '/docs', '/openapi.yaml')

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

# ---------------------------------------------------------------------------
# Host enforcement — api.verumsignal.com only serves /v1, /docs, /openapi.yaml
# ---------------------------------------------------------------------------

def is_api_host():
    return request.host.split(':')[0] == API_HOST


# Host enforcement disabled until api.verumsignal.com routing is stable.
# Auth (require_api_key) is the primary access control on all /v1 endpoints.


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def require_api_key(f):
    """
    Decorator for all /v1 endpoints.

    Flow:
    1. Extract Authorization: Bearer <key>. Missing → 401.
    2. SHA-256 hash → look up api_keys. Not found or revoked → 401.
    3. Check per-minute rate limit via api_usage. Exceeded → 429.
    4. Check monthly quota via api_monthly_usage. Exceeded → 429.
    5. Call handler.
    6. Post-response: insert api_usage row, increment monthly counter,
       update last_used_at and last_used_ip on api_keys.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        from api import get_db  # import here to avoid circular at module load

        # --- Step 1: Extract key ---
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return _auth_error('Missing or invalid Authorization header')
        raw_key = auth[len('Bearer '):]
        key_hash = hash_key(raw_key)

        conn = get_db()
        cur = conn.cursor()
        start_ms = int(time.time() * 1000)

        try:
            # --- Step 2: Look up key ---
            cur.execute("""
                SELECT id, tier, monthly_quota, rate_limit_per_minute
                FROM api_keys
                WHERE key_hash = %s AND revoked_at IS NULL
            """, (key_hash,))
            row = cur.fetchone()
            if not row:
                return _auth_error('Invalid API key')
            key_id, tier, monthly_quota, rate_limit_per_minute = row

            # --- Step 3: Rate limit (per minute) ---
            cur.execute("""
                SELECT COUNT(*) FROM api_usage
                WHERE key_id = %s AND created_at > NOW() - INTERVAL '1 minute'
            """, (key_id,))
            calls_last_minute = cur.fetchone()[0]
            if calls_last_minute >= rate_limit_per_minute:
                _log_usage(cur, conn, key_id, request.path, 429,
                           int(time.time() * 1000) - start_ms)
                return _rate_limit_error(
                    'Rate limit exceeded',
                    retry_after=60,
                    limit=rate_limit_per_minute,
                    remaining=0,
                )

            # --- Step 4: Monthly quota ---
            year_month = datetime.now(timezone.utc).strftime('%Y-%m')
            cur.execute("""
                SELECT call_count FROM api_monthly_usage
                WHERE key_id = %s AND year_month = %s
            """, (key_id, year_month))
            quota_row = cur.fetchone()
            call_count = quota_row[0] if quota_row else 0
            if call_count >= monthly_quota:
                _log_usage(cur, conn, key_id, request.path, 429,
                           int(time.time() * 1000) - start_ms)
                return _rate_limit_error(
                    f'Monthly quota of {monthly_quota} calls exceeded',
                    retry_after=None,
                    limit=monthly_quota,
                    remaining=0,
                    quota_error=True,
                )

            # Store on g for use in response headers and post-request logging
            g.api_key_id = key_id
            g.api_tier = tier
            g.api_rate_limit = rate_limit_per_minute
            g.api_calls_last_minute = calls_last_minute
            g.api_monthly_quota = monthly_quota
            g.api_monthly_used = call_count
            g.api_start_ms = start_ms
            g.api_year_month = year_month
            g.api_cur = cur
            g.api_conn = conn

            # --- Step 5: Call handler ---
            response = f(*args, **kwargs)

            # --- Step 6: Post-request logging ---
            status_code = response.status_code if hasattr(response, 'status_code') else 200
            elapsed_ms = int(time.time() * 1000) - start_ms
            _log_usage(cur, conn, key_id, request.path, status_code, elapsed_ms)
            _update_key_last_used(cur, conn, key_id)

            # Add rate limit headers
            remaining_minute = max(0, rate_limit_per_minute - calls_last_minute - 1)
            response.headers['X-RateLimit-Limit'] = str(rate_limit_per_minute)
            response.headers['X-RateLimit-Remaining'] = str(remaining_minute)
            response.headers['X-RateLimit-Reset'] = '60'
            response.headers['X-Quota-Limit'] = str(monthly_quota)
            response.headers['X-Quota-Remaining'] = str(max(0, monthly_quota - call_count - 1))

            return response

        except Exception as e:
            log.error(f"Auth middleware error: {e}", exc_info=True)
            conn.rollback()
            return jsonify({'error': 'Internal server error'}), 500
        finally:
            cur.close()
            conn.close()

    return decorated


def _auth_error(message):
    resp = jsonify({'error': message})
    resp.status_code = 401
    resp.headers['WWW-Authenticate'] = 'Bearer realm="Verum Signal API"'
    return resp


def _rate_limit_error(message, retry_after, limit, remaining, quota_error=False):
    resp = jsonify({'error': message})
    resp.status_code = 429
    if retry_after:
        resp.headers['Retry-After'] = str(retry_after)
    resp.headers['X-RateLimit-Limit'] = str(limit)
    resp.headers['X-RateLimit-Remaining'] = str(remaining)
    return resp


def _log_usage(cur, conn, key_id, endpoint, status_code, elapsed_ms):
    try:
        cur.execute("""
            INSERT INTO api_usage (key_id, endpoint, status_code, response_time_ms, ip)
            VALUES (%s, %s, %s, %s, %s)
        """, (key_id, endpoint, status_code, elapsed_ms,
              request.headers.get('X-Forwarded-For', request.remote_addr)))
        cur.execute("""
            INSERT INTO api_monthly_usage (key_id, year_month, call_count, last_updated)
            VALUES (%s, %s, 1, NOW())
            ON CONFLICT (key_id, year_month) DO UPDATE SET
                call_count = api_monthly_usage.call_count + 1,
                last_updated = NOW()
        """, (key_id, datetime.now(timezone.utc).strftime('%Y-%m')))
        conn.commit()
    except Exception as e:
        log.error(f"Failed to log usage: {e}")
        conn.rollback()


def _update_key_last_used(cur, conn, key_id):
    try:
        cur.execute("""
            UPDATE api_keys SET last_used_at = NOW(), last_used_ip = %s
            WHERE id = %s
        """, (request.headers.get('X-Forwarded-For', request.remote_addr), key_id))
        conn.commit()
    except Exception as e:
        log.error(f"Failed to update last_used: {e}")
        conn.rollback()


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

def get_pagination_params():
    """Parse and validate cursor + limit from query string."""
    try:
        limit = min(int(request.args.get('limit', DEFAULT_LIMIT)), MAX_LIMIT)
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT
    try:
        cursor = int(request.args.get('cursor', 0))
    except (ValueError, TypeError):
        cursor = 0
    return cursor, limit


# ---------------------------------------------------------------------------
# GET /v1/meta
# ---------------------------------------------------------------------------

@api_public.route('/v1/meta')
@require_api_key
def meta():
    from api import get_db
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM api_claims")
        claims_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM api_outlets")
        outlets_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT event_id) FROM api_debate_claims")
        debates_count = cur.fetchone()[0]

        cur.execute("SELECT MAX(updated_at) FROM api_claims")
        last_refresh = cur.fetchone()[0]

        return jsonify({
            'methodology_versions_served': ['v1.6'],
            'methodology_url': 'https://verumsignal.com/methodology',
            'outlets_count': outlets_count,
            'claims_count': claims_count,
            'debate_events_count': debates_count,
            'last_refresh': last_refresh.isoformat() if last_refresh else None,
        })
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# GET /v1/claims
# ---------------------------------------------------------------------------

@api_public.route('/v1/claims')
@require_api_key
def claims():
    from api import get_db
    cursor, limit = get_pagination_params()

    outlet   = request.args.get('outlet')
    verdict  = request.args.get('verdict')
    origin   = request.args.get('claim_origin')

    conn = get_db()
    cur = conn.cursor()
    try:
        filters = ['cursor_key > %s']
        params  = [cursor]

        if outlet:
            filters.append('outlet_id = %s')
            params.append(outlet.lower())
        if verdict:
            filters.append('verdict_label = %s')
            params.append(verdict)
        if origin:
            filters.append('claim_origin = %s')
            params.append(origin)

        where = ' AND '.join(filters)
        params.append(limit)

        cur.execute(f"""
            SELECT id, claim_text, claim_origin, verdict_label,
                   outlet_id, outlet_name,
                   article_title, article_url, article_published_at,
                   evaluated_at, methodology_version, report_url,
                   cursor_key
            FROM api_claims
            WHERE {where}
            ORDER BY cursor_key
            LIMIT %s
        """, params)

        rows = cur.fetchall()
        data = []
        for row in rows:
            (rid, claim_text, claim_origin, verdict_label,
             outlet_id, outlet_name,
             article_title, article_url, article_published_at,
             evaluated_at, methodology_version, report_url,
             cursor_key) = row
            data.append({
                'id': rid,
                'claim_text': claim_text,
                'claim_origin': claim_origin,
                'verdict': verdict_label,
                'outlet': {
                    'id': outlet_id,
                    'name': outlet_name,
                },
                'article': {
                    'title': article_title,
                    'url': article_url,
                    'published_at': article_published_at.isoformat() if article_published_at else None,
                },
                'evaluated_at': evaluated_at.isoformat() if evaluated_at else None,
                'methodology_version': methodology_version,
                'report_url': report_url,
            })

        next_cursor = rows[-1][12] if rows else None
        return jsonify({
            'data': data,
            'pagination': {
                'next_cursor': next_cursor,
                'has_more': len(rows) == limit,
            }
        })
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# GET /v1/outlets
# ---------------------------------------------------------------------------

@api_public.route('/v1/outlets')
@require_api_key
def outlets():
    from api import get_db
    cursor, limit = get_pagination_params()
    tier = request.args.get('tier')

    conn = get_db()
    cur = conn.cursor()
    try:
        filters = ['cursor_key > %s']
        params  = [cursor]
        if tier:
            filters.append('tier = %s')
            params.append(tier)
        where = ' AND '.join(filters)
        params.append(limit)

        cur.execute(f"""
            SELECT outlet_id, outlet_name, outlet_url, score, tier,
                   total_evaluated_claims, verdict_counts,
                   methodology_version, last_evaluated_at,
                   leaderboard_url, cursor_key
            FROM api_outlets
            WHERE {where}
            ORDER BY cursor_key
            LIMIT %s
        """, params)

        rows = cur.fetchall()
        data = [_format_outlet(row) for row in rows]
        next_cursor = rows[-1][10] if rows else None
        return jsonify({
            'data': data,
            'pagination': {
                'next_cursor': next_cursor,
                'has_more': len(rows) == limit,
            }
        })
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# GET /v1/outlets/<outlet_id>
# ---------------------------------------------------------------------------

@api_public.route('/v1/outlets/<path:outlet_id>')
@require_api_key
def outlet_detail(outlet_id):
    from api import get_db
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT outlet_id, outlet_name, outlet_url, score, tier,
                   total_evaluated_claims, verdict_counts,
                   methodology_version, last_evaluated_at,
                   leaderboard_url, cursor_key
            FROM api_outlets
            WHERE outlet_id = %s
        """, (outlet_id.lower(),))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': f'Outlet not found: {outlet_id}'}), 404
        return jsonify(_format_outlet(row))
    finally:
        cur.close()
        conn.close()


def _format_outlet(row):
    (outlet_id, outlet_name, outlet_url, score, tier,
     total_evaluated, verdict_counts,
     methodology_version, last_evaluated_at,
     leaderboard_url, cursor_key) = row
    return {
        'id': outlet_id,
        'name': outlet_name,
        'url': outlet_url,
        'score': float(score) if score is not None else None,
        'tier': tier,
        'total_evaluated_claims': total_evaluated,
        'verdict_counts': verdict_counts,
        'methodology_version': methodology_version,
        'last_evaluated_at': last_evaluated_at.isoformat() if last_evaluated_at else None,
        'leaderboard_url': leaderboard_url,
    }


# ---------------------------------------------------------------------------
# GET /v1/debates
# ---------------------------------------------------------------------------

@api_public.route('/v1/debates')
@require_api_key
def debates():
    from api import get_db
    cursor, limit = get_pagination_params()

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT event_id, event_slug, event_name, event_date,
                   COUNT(*) AS claim_count,
                   MIN(cursor_key) AS min_cursor
            FROM api_debate_claims
            WHERE cursor_key > %s
            GROUP BY event_id, event_slug, event_name, event_date
            ORDER BY min_cursor
            LIMIT %s
        """, (cursor, limit))

        rows = cur.fetchall()
        data = []
        for (event_id, event_slug, event_name, event_date,
             claim_count, min_cursor) in rows:
            data.append({
                'event_id': event_id,
                'slug': event_slug,
                'name': event_name,
                'date': event_date.isoformat() if event_date else None,
                'claim_count': claim_count,
                'claims_url': f'https://api.verumsignal.com/v1/debates/{event_slug}/claims',
                'event_url': f'https://verumsignal.com/debates/{event_slug}',
            })

        next_cursor = rows[-1][5] if rows else None
        return jsonify({
            'data': data,
            'pagination': {
                'next_cursor': next_cursor,
                'has_more': len(rows) == limit,
            }
        })
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# GET /v1/debates/<slug>/claims
# ---------------------------------------------------------------------------

@api_public.route('/v1/debates/<slug>/claims')
@require_api_key
def debate_claims(slug):
    from api import get_db
    cursor, limit = get_pagination_params()
    speaker = request.args.get('speaker')
    verdict = request.args.get('verdict')

    conn = get_db()
    cur = conn.cursor()
    try:
        # Verify event exists
        cur.execute("SELECT event_id FROM api_debate_claims WHERE event_slug = %s LIMIT 1", (slug,))
        if not cur.fetchone():
            return jsonify({'error': f'Debate not found: {slug}'}), 404

        filters = ['event_slug = %s', 'cursor_key > %s']
        params  = [slug, cursor]

        if speaker:
            filters.append('LOWER(speaker_name) = %s')
            params.append(speaker.lower())
        if verdict:
            filters.append('verdict_label = %s')
            params.append(verdict)

        where = ' AND '.join(filters)
        params.append(limit)

        cur.execute(f"""
            SELECT id, claim_text, verdict_label,
                   speaker_name, speaker_party,
                   event_slug, event_name, event_date,
                   evaluated_at, methodology_version, event_url,
                   cursor_key
            FROM api_debate_claims
            WHERE {where}
            ORDER BY cursor_key
            LIMIT %s
        """, params)

        rows = cur.fetchall()
        data = []
        for row in rows:
            (rid, claim_text, verdict_label,
             speaker_name, speaker_party,
             event_slug, event_name, event_date,
             evaluated_at, methodology_version, event_url,
             cursor_key) = row
            data.append({
                'id': rid,
                'claim_text': claim_text,
                'verdict': verdict_label,
                'speaker': {
                    'name': speaker_name,
                    'party': speaker_party,
                },
                'event': {
                    'slug': event_slug,
                    'name': event_name,
                    'date': event_date.isoformat() if event_date else None,
                },
                'evaluated_at': evaluated_at.isoformat() if evaluated_at else None,
                'methodology_version': methodology_version,
                'event_url': event_url,
            })

        next_cursor = rows[-1][11] if rows else None
        return jsonify({
            'data': data,
            'pagination': {
                'next_cursor': next_cursor,
                'has_more': len(rows) == limit,
            }
        })
    finally:
        cur.close()
        conn.close()

# ---------------------------------------------------------------------------
# /openapi.yaml and /docs (Swagger UI)
# ---------------------------------------------------------------------------

@api_public.route('/openapi.yaml')
def openapi_spec():
    import os
    from flask import send_from_directory, current_app
    static_dir = os.path.join(current_app.root_path, 'static')
    return send_from_directory(static_dir, 'openapi.yaml',
                               mimetype='application/yaml')


@api_public.route('/docs')
def swagger_ui():
    from flask import Response
    html = """<!DOCTYPE html>
<html>
<head>
  <title>Verum Signal API Docs</title>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" type="text/css"
        href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" >
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"> </script>
<script>
  SwaggerUIBundle({
    url: "/openapi.yaml",
    dom_id: '#swagger-ui',
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: "BaseLayout",
    tryItOutEnabled: true,
    persistAuthorization: true,
  })
</script>
</body>
</html>"""
    return Response(html, mimetype='text/html')
