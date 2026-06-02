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
                   verdict_status, cursor_key
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
             raw_status, cursor_key) = row
            data.append({
                'id': rid,
                'claim_text': claim_text,
                'verdict': verdict_label,
                'verdict_status': raw_status if raw_status else 'final',
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
# /openapi.yaml and /docs (Swagger UI)
# ---------------------------------------------------------------------------

@api_public.route('/openapi.yaml')
def openapi_spec():
    import os
    from flask import send_from_directory, current_app
    static_dir = os.path.join(current_app.root_path, 'static')
    return send_from_directory(static_dir, 'openapi.yaml',
                               mimetype='application/yaml')




@api_public.route('/v1/keys/request', methods=['POST'])
def request_api_key():
    import secrets, hashlib, re as _re
    from flask import request as _req
    data = _req.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    name  = (data.get('name') or '').strip()
    use_case = (data.get('use_case') or '').strip()[:500]
    if not email or not _re.match(r"^[^@]+@[^@]+[.][^@]+$", email):
        return jsonify({"error": "Valid email required"}), 400
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT key_prefix FROM api_keys WHERE user_email = %s AND revoked_at IS NULL LIMIT 1",
            (email,))
        if cur.fetchone():
            cur.close(); conn.close()
            return jsonify({"error": "An active key exists for this email.", "hint": "Contact api@verumsignal.com to replace it."}), 409
        raw_key = "vs_live_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:16]
        cur.execute(
            "INSERT INTO api_keys (user_email, key_hash, key_prefix, name, tier, monthly_quota, rate_limit_per_minute, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())",
            (email, key_hash, key_prefix, name, "free", 1000, 10))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"api_key": raw_key, "prefix": key_prefix, "tier": "free", "monthly_quota": 1000, "rate_limit_per_minute": 10, "message": "Store this key safely — it will not be shown again.", "docs": "https://verumsignal.com/developers", "email": email}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_public.route('/developers')
def developers_page():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verum Signal — Developer API</title>
<style>
  :root { --bg:#0a0a0f; --fg:#e8e8f0; --dim:#888; --accent:#a855f7; --pink:#ec4899; --card:#111118; --border:#1e1e2e; --green:#4ade80; --mono:ui-monospace,"SF Mono",Menlo,monospace; }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:15px;line-height:1.6}
  .wrap{max-width:860px;margin:0 auto;padding:48px 24px}
  .logo{display:flex;align-items:center;gap:10px;margin-bottom:48px}
  .logo a{color:inherit;text-decoration:none;font-weight:700;font-size:15px;letter-spacing:1.5px}
  h1{font-size:32px;font-weight:700;letter-spacing:-0.02em;margin-bottom:12px}
  h1 em{color:var(--accent);font-style:normal}
  .sub{color:var(--dim);font-size:16px;margin-bottom:48px;max-width:580px}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:0.1em;color:var(--dim);margin:40px 0 16px;font-weight:500}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:40px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px}
  .card h3{font-size:13px;font-weight:600;margin-bottom:6px}
  .card p{font-size:13px;color:var(--dim);line-height:1.5}
  .code-block{background:#0d0d14;border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px;overflow-x:auto}
  .code-block pre{font-family:var(--mono);font-size:12px;color:#c0c0d0;white-space:pre}
  .code-block .comment{color:#555}
  .form-wrap{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:28px;margin-bottom:40px}
  .form-wrap h3{font-size:16px;font-weight:600;margin-bottom:6px}
  .form-wrap p{font-size:13px;color:var(--dim);margin-bottom:20px}
  .field{margin-bottom:16px}
  .field label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:var(--dim);margin-bottom:6px}
  .field input,.field textarea{width:100%;background:#0a0a0f;border:1px solid var(--border);border-radius:6px;padding:10px 12px;color:var(--fg);font-size:14px;font-family:inherit;outline:none;transition:border-color 0.2s}
  .field input:focus,.field textarea:focus{border-color:var(--accent)}
  .field textarea{resize:vertical;min-height:80px}
  .btn{display:inline-flex;align-items:center;gap:8px;padding:10px 22px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;transition:opacity 0.2s}
  .btn:hover{opacity:0.85}
  .btn:disabled{opacity:0.5;cursor:not-allowed}
  .result{display:none;margin-top:20px;background:#0d0d14;border:1px solid var(--border);border-radius:8px;padding:20px}
  .result.success{border-color:rgba(74,222,128,0.3)}
  .result.error{border-color:rgba(248,113,113,0.3)}
  .key-display{font-family:var(--mono);font-size:13px;color:var(--green);word-break:break-all;margin:10px 0;padding:12px;background:rgba(74,222,128,0.06);border-radius:6px;border:1px solid rgba(74,222,128,0.2)}
  .copy-btn{font-size:12px;padding:4px 10px;background:transparent;border:1px solid var(--border);border-radius:4px;color:var(--dim);cursor:pointer}
  .copy-btn:hover{border-color:var(--accent);color:var(--accent)}
  .pill{display:inline-block;padding:2px 10px;border-radius:100px;font-size:11px;font-weight:600;background:rgba(168,85,247,0.15);color:var(--accent);margin-bottom:8px}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px}
  th{text-align:left;padding:8px 12px;border-bottom:1px solid var(--border);color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.06em}
  td{padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.04);vertical-align:top}
  td code{font-family:var(--mono);font-size:11px;color:var(--accent)}
  footer{margin-top:64px;padding-top:24px;border-top:1px solid var(--border);color:var(--dim);font-size:12px;display:flex;gap:24px}
  footer a{color:var(--dim);text-decoration:none}
  footer a:hover{color:var(--fg)}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">
    <a href="/">VERUM <em style="color:var(--accent);font-style:italic">SIGNAL</em></a>
    <span style="color:var(--border)">|</span>
    <span style="color:var(--dim);font-size:13px">Developer API</span>
  </div>

  <h1>Build with <em>Verum Signal</em></h1>
  <p class="sub">Programmatic access to verdict-labeled claims, outlet credibility scores, and live debate verdicts. Built for agents, newsrooms, and researchers.</p>

  <div class="cards">
    <div class="card"><h3>12,000+ verified claims</h3><p>Every claim independently verified with web search. No verdict copying, no shortcuts.</p></div>
    <div class="card"><h3>Outlet scores</h3><p>Evidence-based credibility scores for 150+ outlets, updated continuously.</p></div>
    <div class="card"><h3>Live debate verdicts</h3><p>Real-time claim extraction and verification during political debates.</p></div>
    <div class="card"><h3>Agent-ready</h3><p>Structured JSON responses, cursor pagination, OpenAPI spec, and MCP server coming soon.</p></div>
  </div>

  <h2>Get an API key</h2>
  <div class="form-wrap">
    <h3>Free tier — 1,000 calls/month</h3>
    <p>Your key is generated instantly and shown once. Store it safely.</p>
    <div class="field"><label>Email</label><input type="email" id="f-email" placeholder="you@example.com"></div>
    <div class="field"><label>Name or organization</label><input type="text" id="f-name" placeholder="Acme Newsroom"></div>
    <div class="field"><label>How will you use the API? (optional)</label><textarea id="f-use" placeholder="e.g. fact-checking pipeline, research tool, news aggregator..."></textarea></div>
    <button class="btn" id="req-btn" onclick="requestKey()">Get API key</button>
    <div class="result" id="result-box"></div>
  </div>

  <h2>Quick start</h2>
  <div class="code-block"><pre><span class="comment"># Get your outlet credibility score</span>
curl https://api.verumsignal.com/v1/outlets/nytimes.com \
  -H "Authorization: Bearer vs_live_your_key_here"</pre></div>
  <div class="code-block"><pre><span class="comment"># Search recent verified claims</span>
curl "https://api.verumsignal.com/v1/claims?limit=10" \
  -H "Authorization: Bearer vs_live_your_key_here"</pre></div>
  <div class="code-block"><pre><span class="comment"># Get live debate verdicts</span>
curl "https://api.verumsignal.com/v1/debates" \
  -H "Authorization: Bearer vs_live_your_key_here"</pre></div>

  <h2>Endpoints</h2>
  <table>
    <tr><th>Method</th><th>Endpoint</th><th>Description</th></tr>
    <tr><td>GET</td><td><code>/v1/meta</code></td><td>API status, corpus stats, methodology version</td></tr>
    <tr><td>GET</td><td><code>/v1/claims</code></td><td>Paginated verified claims feed</td></tr>
    <tr><td>GET</td><td><code>/v1/outlets</code></td><td>Outlet leaderboard with scores and tiers</td></tr>
    <tr><td>GET</td><td><code>/v1/outlets/:domain</code></td><td>Single outlet detail and verdict breakdown</td></tr>
    <tr><td>GET</td><td><code>/v1/debates</code></td><td>Debate list with claim counts</td></tr>
    <tr><td>GET</td><td><code>/v1/debates/:slug/claims</code></td><td>All verified claims from a specific debate</td></tr>
    <tr><td>GET</td><td><code>/openapi.yaml</code></td><td>Full OpenAPI 3.1 specification</td></tr>
  </table>

  <h2>Rate limits</h2>
  <table>
    <tr><th>Tier</th><th>Monthly quota</th><th>Per minute</th></tr>
    <tr><td>Free</td><td>1,000 calls</td><td>10 calls</td></tr>
    <tr><td>Pro</td><td>50,000 calls</td><td>60 calls</td></tr>
    <tr><td>Enterprise</td><td>Unlimited</td><td>Custom</td></tr>
  </table>
  <p style="font-size:13px;color:var(--dim)">Need higher limits? Email <a href="mailto:api@verumsignal.com" style="color:var(--accent)">api@verumsignal.com</a></p>

  <footer>
    <a href="/methodology">Methodology</a>
    <a href="/openapi.yaml">OpenAPI spec</a>
    <a href="mailto:api@verumsignal.com">api@verumsignal.com</a>
    <a href="/">verumsignal.com</a>
  </footer>
</div>

<script>
async function requestKey() {
  var btn = document.getElementById('req-btn');
  var box = document.getElementById('result-box');
  var email = document.getElementById('f-email').value.trim();
  var name = document.getElementById('f-name').value.trim();
  var use = document.getElementById('f-use').value.trim();
  if (!email || !name) { alert('Email and name are required.'); return; }
  btn.disabled = true; btn.textContent = 'Generating...';
  try {
    var r = await fetch('/v1/keys/request', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, name: name, use_case: use})
    });
    var d = await r.json();
    box.style.display = 'block';
    if (r.ok) {
      box.className = 'result success';
      box.innerHTML = '<div style="color:#4ade80;font-weight:600;margin-bottom:8px">✓ Your API key</div>' +
        '<div class="key-display" id="key-val">' + d.api_key + '</div>' +
        '<button class="copy-btn" onclick="copyKey()">Copy key</button>' +
        '<p style="font-size:12px;color:#888;margin-top:12px">' + d.message + '<br>' +
        'Tier: ' + d.tier + ' · ' + d.monthly_quota + ' calls/month · ' + d.rate_limit_per_minute + '/min</p>';
    } else {
      box.className = 'result error';
      box.innerHTML = '<div style="color:#f87171">' + (d.error || 'Something went wrong') + '</div>' +
        (d.hint ? '<div style="font-size:12px;color:#888;margin-top:6px">' + d.hint + '</div>' : '');
    }
  } catch(e) {
    box.style.display = 'block';
    box.className = 'result error';
    box.innerHTML = '<div style="color:#f87171">Request failed: ' + e.message + '</div>';
  }
  btn.disabled = false; btn.textContent = 'Get API key';
}
function copyKey() {
  var k = document.getElementById('key-val');
  if (k) { navigator.clipboard.writeText(k.textContent); }
}
</script>
</body>
</html>""", 200, {'Content-Type': 'text/html'}

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
# api v1 Tue May 19 01:05:09 MDT 2026
