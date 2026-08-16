"""
api_public.py

Verum Signal Public API — v1
Blueprint registered on the main Flask app (api.py).

Served at api.verumsignal.com/v1/*
Any request to api.verumsignal.com outside /v1, /docs, /openapi.yaml → 404.

Auth: Authorization: Bearer <api_key> header only.
      No ?api_key= query param accepted.

All DB access via get_db() — uses the hardcoded fallback in api.py's get_db() (Session 6 follow-on: no line number, even though 93 happens to be correct today — see the comment there).

NEVER expose: verdict_summary, evidence_sources, priority_score,
              verification_attempts, or any internal scoring field.
"""

import hashlib
import time
import random
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, request, jsonify, g, make_response

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


# S9-011 (corrected 2026-08-10): host enforcement is LIVE, not disabled.
# It moved to api.py:31 (_enforce_api_host) as of commit 7c4f422 -- this
# is_api_host() helper is still the function that check calls. A prior
# version of this comment said enforcement was disabled pending stable
# routing; that stopped being true when 7c4f422 shipped and nobody
# updated this file. If you're here because you grepped is_api_host,
# read api.py:31 for the actual live enforcement logic.
# Auth (require_api_key) remains the primary access control on all /v1 endpoints.


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
            # Atomic increment-and-test, one round trip, no check-then-act gap.
            # Meters requests ATTEMPTED (every attempt counts, including this
            # one whether it's ultimately allowed or rejected) -- deliberately
            # separate from monthly quota (Step 4), which meters resource
            # CONSUMED and stays conditional on a 2xx outcome per FIX #4.
            cur.execute("""
                INSERT INTO api_rate_limit_buckets (key_id, minute_bucket, count)
                VALUES (%s, date_trunc('minute', NOW()), 1)
                ON CONFLICT (key_id, minute_bucket) DO UPDATE
                    SET count = api_rate_limit_buckets.count + 1
                RETURNING count
            """, (key_id,))
            calls_this_minute = cur.fetchone()[0]
            conn.commit()
            # Cheap, unlocked opportunistic cleanup -- ~1% of requests also
            # sweep buckets old enough to be irrelevant. Not required for
            # correctness (a stale bucket's minute_bucket value is never
            # matched by a current request, so it can't affect the check
            # above), only to keep the table from growing forever.
            if random.random() < 0.01:
                cur.execute("""
                    DELETE FROM api_rate_limit_buckets
                    WHERE minute_bucket < NOW() - INTERVAL '10 minutes'
                """)
                conn.commit()
            if calls_this_minute > rate_limit_per_minute:
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
                )

            # Store on g for use in response headers and post-request logging
            g.api_key_id = key_id
            g.api_tier = tier
            g.api_rate_limit = rate_limit_per_minute
            g.api_calls_last_minute = calls_this_minute
            g.api_monthly_quota = monthly_quota
            g.api_monthly_used = call_count
            g.api_start_ms = start_ms
            g.api_year_month = year_month
            g.api_cur = cur
            g.api_conn = conn

            # --- Step 5: Call handler ---
            response = f(*args, **kwargs)
            # Normalize whatever the wrapped view returned (tuple, str, dict,
            # Response) into a real Response object — mirrors what Flask's own
            # dispatcher would do, since this decorator sits inside that boundary
            # and the raw return value never reaches Flask's dispatcher directly.
            # Without this, a plain (body, status) tuple return — normal, idiomatic
            # Flask — has no .headers attribute, and the code below would throw.
            response = make_response(response)

            # --- Step 6: Post-request logging ---
            status_code = response.status_code if hasattr(response, 'status_code') else 200
            elapsed_ms = int(time.time() * 1000) - start_ms
            _log_usage(cur, conn, key_id, request.path, status_code, elapsed_ms)
            _update_key_last_used(cur, conn, key_id)

            # Add rate limit headers
            remaining_minute = max(0, rate_limit_per_minute - calls_this_minute)
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


def _rate_limit_error(message, retry_after, limit, remaining):
    resp = jsonify({'error': message})
    resp.status_code = 429
    if retry_after:
        resp.headers['Retry-After'] = str(retry_after)
    resp.headers['X-RateLimit-Limit'] = str(limit)
    resp.headers['X-RateLimit-Remaining'] = str(remaining)
    return resp


def _log_usage(cur, conn, key_id, endpoint, status_code, elapsed_ms):
    """
    Records every request attempt in api_usage regardless of outcome — that
    table is the per-minute rate-limit source AND the full observability
    log, so it stays unconditional (a rejected request still consumed
    rate-limiter capacity, which is the point of a rate limiter).

    api_monthly_usage is different: it meters the customer's monthly
    allowance, and only a request that actually got a successful (2xx)
    response consumed that allowance. A request rejected before reaching
    a handler (429) — or one whose handler itself errored — did not.
    """
    try:
        cur.execute("""
            INSERT INTO api_usage (key_id, endpoint, status_code, response_time_ms, ip)
            VALUES (%s, %s, %s, %s, %s)
        """, (key_id, endpoint, status_code, elapsed_ms,
              request.headers.get('X-Forwarded-For', request.remote_addr)))
        if 200 <= status_code < 300:
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
    """Parse and validate cursor + limit from query string.
    Returns (cursor, limit, error_response). error_response is None on
    success, or a (json_body, 400) tuple if the cursor is malformed or
    negative -- the caller must return it immediately when not None."""
    try:
        limit = min(int(request.args.get('limit', DEFAULT_LIMIT)), MAX_LIMIT)
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT
    cursor_raw = request.args.get('cursor', '0')
    try:
        cursor = int(cursor_raw)
    except (ValueError, TypeError):
        return None, None, (jsonify({"error": f"Invalid cursor: '{cursor_raw}' is not a valid integer"}), 400)
    if cursor < 0:
        return None, None, (jsonify({"error": f"Invalid cursor: {cursor} must be non-negative"}), 400)
    return cursor, limit, None


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

        # Tracked and scored are different numbers and the gap is large: an
        # outlet is tracked as soon as it has any evaluated claim, but only
        # carries a score once it meets the inclusion threshold. Reporting only
        # the first made agents answer "how many outlets do you cover" with a
        # number several times too large.
        cur.execute("SELECT COUNT(*) FROM api_outlets WHERE score IS NOT NULL")
        outlets_scored_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT event_id) FROM api_debate_claims")
        debates_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM api_debate_claims")
        debate_claims_count = cur.fetchone()[0]

        cur.execute("SELECT MAX(updated_at) FROM api_claims")
        last_refresh = cur.fetchone()[0]

        # Separate clock from last_refresh, which tracks the article-claim sync.
        # The two can sit months apart and both be correct, so an agent asking
        # "is this current" needs to see which is which rather than guess.
        cur.execute("SELECT MAX(last_evaluated_at) FROM api_outlets")
        last_outlet_evaluation = cur.fetchone()[0]

        return jsonify({
            'methodology_versions_served': ['v1.6'],
            'methodology_url': 'https://verumsignal.com/methodology',
            'methodology_summary': (
                'Individual factual claims are extracted from news articles and '
                'checked against sources. An outlet score aggregates only the '
                'claims the outlet makes in its own voice; claims it attributes '
                'to someone else are analysed but score nobody. An outlet is '
                'scored once it has enough evaluated claims to meet the '
                'inclusion threshold, and is listed as tracked before that.'
            ),
            'outlets_count': outlets_count,
            'outlets_scored_count': outlets_scored_count,
            'claims_count': claims_count,
            'debate_claims_count': debate_claims_count,
            'debate_events_count': debates_count,
            'last_refresh': last_refresh.isoformat() if last_refresh else None,
            'last_outlet_evaluation': (
                last_outlet_evaluation.isoformat() if last_outlet_evaluation else None
            ),
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
    cursor, limit, cursor_err = get_pagination_params()
    if cursor_err is not None:
        return cursor_err

    outlet   = request.args.get('outlet')
    verdict  = request.args.get('verdict')
    origin   = request.args.get('claim_origin')
    query    = request.args.get('q')

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
        if query:
            # Substring match on the claim text. Before this existed, an
            # unrecognised q was silently dropped and the caller got an
            # unfiltered page back with a 200 -- an agent had no way to tell
            # its search had been ignored.
            filters.append('claim_text ILIKE %s')
            params.append('%' + query.replace('%', r'\%').replace('_', r'\_') + '%')

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
    cursor, limit, cursor_err = get_pagination_params()
    if cursor_err is not None:
        return cursor_err
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
            resp = jsonify(_outlet_not_found_body(cur, outlet_id))
            resp.status_code = 404
            return resp
        return jsonify(_format_outlet(row))
    finally:
        cur.close()
        conn.close()


def _outlet_not_found_body(cur, outlet_id):
    """Actionable 404 for an outlet lookup that missed.

    The bare {"error": "Outlet not found: X"} gave an agent nothing to act on.
    Measured by an external agent-readiness audit (Aug 2026): 27 of 27 execution
    failures were this response, and models either gave up outright or burned
    retries cycling domain variants that were guaranteed to fail too.

    This body answers three questions the old one did not: why the lookup missed,
    whether a different domain would work, and what to do instead. Every lookup
    hits api_outlets (166 rows), so the near-match query is cheap, and it only
    runs on the miss path.
    """
    normalized = (outlet_id or '').strip().lower()
    if normalized.startswith('www.'):
        normalized = normalized[4:]

    suggestion = None
    try:
        # Same publisher under a different domain -- e.g. ap.org is not tracked
        # but apnews.com is. Match on the first label, which is the publisher
        # name in practice, and require 3+ chars so short labels do not match
        # half the table.
        label = normalized.split('.')[0]
        # >=2 deliberately: 'ap' (ap.org -> apnews.com) is the exact case the
        # audit found, and a 3-char floor silently excluded it.
        if len(label) >= 2:
            cur.execute("""
                SELECT outlet_id FROM api_outlets
                WHERE outlet_id LIKE %s AND outlet_id <> %s
                ORDER BY (score IS NULL), total_evaluated_claims DESC
                LIMIT 1
            """, (label + '%', normalized))
            hit = cur.fetchone()
            if hit:
                suggestion = hit[0]
    except Exception:
        suggestion = None

    body = {
        'error': f'Outlet not found: {outlet_id}',
        'reason': 'not_in_scored_set',
        'detail': ('This domain is not in the scored outlet set. An outlet appears '
                   'here once it has enough scoreable claims to meet the inclusion '
                   'threshold; below that, no score is published for it.'),
        'domain_variants': ('Variants of the same domain (www., .eu, .co and similar) '
                            'resolve to the same lookup and will also return 404. '
                            'Retrying them will not succeed.'),
        'recommended_action': ('Tell the user this outlet is not currently scored, '
                               'then call GET /v1/outlets to list the outlets that are.'),
        'list_outlets_url': '/v1/outlets',
    }
    if suggestion:
        body['suggestion'] = suggestion
        body['suggestion_detail'] = (
            f'{suggestion} is in the scored set and may be the same publisher. '
            f'Try GET /v1/outlets/{suggestion}.'
        )
    return body


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
    cursor, limit, cursor_err = get_pagination_params()
    if cursor_err is not None:
        return cursor_err

    conn = get_db()
    cur = conn.cursor()
    try:
        # Per-verdict counts are appended AFTER min_cursor deliberately:
        # next_cursor below reads rows[-1][5] positionally, and inserting
        # columns earlier would silently break pagination.
        cur.execute("""
            SELECT event_id, event_slug, event_name, event_date,
                   COUNT(*) AS claim_count,
                   MIN(cursor_key) AS min_cursor,
                   COUNT(*) FILTER (WHERE verdict_label = 'supported')      AS v_supported,
                   COUNT(*) FILTER (WHERE verdict_label = 'plausible')      AS v_plausible,
                   COUNT(*) FILTER (WHERE verdict_label = 'corroborated')   AS v_corroborated,
                   COUNT(*) FILTER (WHERE verdict_label = 'overstated')     AS v_overstated,
                   COUNT(*) FILTER (WHERE verdict_label = 'disputed')       AS v_disputed,
                   COUNT(*) FILTER (WHERE verdict_label = 'not_supported')  AS v_not_supported,
                   COUNT(*) FILTER (WHERE verdict_label = 'not_verifiable') AS v_not_verifiable
            FROM api_debate_claims
            WHERE cursor_key > %s
            GROUP BY event_id, event_slug, event_name, event_date
            ORDER BY min_cursor
            LIMIT %s
        """, (cursor, limit))

        rows = cur.fetchall()
        data = []
        for (event_id, event_slug, event_name, event_date,
             claim_count, min_cursor,
             v_supported, v_plausible, v_corroborated, v_overstated,
             v_disputed, v_not_supported, v_not_verifiable) in rows:
            data.append({
                'event_id': event_id,
                'slug': event_slug,
                'name': event_name,
                'date': event_date.isoformat() if event_date else None,
                'claim_count': claim_count,
                'verdict_counts': {
                    'supported': v_supported,
                    'plausible': v_plausible,
                    'corroborated': v_corroborated,
                    'overstated': v_overstated,
                    'disputed': v_disputed,
                    'not_supported': v_not_supported,
                    'not_verifiable': v_not_verifiable,
                },
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
    cursor, limit, cursor_err = get_pagination_params()
    if cursor_err is not None:
        return cursor_err
    speaker = request.args.get('speaker')
    verdict = request.args.get('verdict')

    conn = get_db()
    cur = conn.cursor()
    try:
        # Verify event exists
        cur.execute("SELECT event_id FROM api_debate_claims WHERE event_slug = %s LIMIT 1", (slug,))
        if not cur.fetchone():
            resp = jsonify({'error': f'Debate not found: {slug}'})
            resp.status_code = 404
            return resp

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
    """
    Self-service API key request. Rebuilt for Session 6 Phase 4 — the
    prior version was disabled (HOLD #5) after three stacked bugs:
    missing get_db import, a hardcoded tier="free" violating
    api_keys.tier's live CHECK (starter/pro/enterprise only), and a
    users.external_id NOT NULL UNIQUE violation from the (now-dropped)
    Clerk migration. All three fixed directly, not patched around.

    Writes users + api_keys + subscriptions in ONE transaction — the
    success criterion, and the first time this has ever completed in
    this system's history.
    """
    import secrets
    import re as _re
    from datetime import date
    from api import get_db

    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    name = (data.get('name') or '').strip()
    use_case = (data.get('use_case') or '').strip()[:500]

    if not email or not _re.match(r"^[^@]+@[^@]+[.][^@]+$", email):
        return jsonify({"error": "Valid email required"}), 400
    if not name:
        return jsonify({"error": "name required"}), 400

    # Closed-beta self-service allocation — deliberately smaller than
    # the $49/mo "API" tier advertised on pricing.html (5,000/mo,
    # 60/min). Stripe doesn't exist yet, so self-service can't actually
    # charge anyone; this mirrors the ORIGINAL pre-bug numbers exactly.
    # This is a product call, not just a bug fix — confirm before
    # treating it as final.
    PHASE4_MONTHLY_QUOTA = 100
    PHASE4_RATE_LIMIT_PER_MIN = 10

    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor()

        # Find or create the owning user row FIRST — every key must be
        # owned at birth (Decision 1), same pattern as Phase 2's
        # manual-issuance fix and the magic-link verify() upsert.
        cur.execute("""
            INSERT INTO users (email, email_verified, updated_at, last_seen_at)
            VALUES (%s, FALSE, NOW(), NOW())
            ON CONFLICT (email) DO UPDATE
                SET last_seen_at = NOW(),
                    updated_at = NOW()
            RETURNING id
        """, (email,))
        user_id = cur.fetchone()[0]

        # One active key per user — checked by ownership now, not by
        # raw user_email string matching.
        cur.execute(
            "SELECT key_prefix FROM api_keys WHERE user_id = %s AND revoked_at IS NULL LIMIT 1",
            (user_id,))
        if cur.fetchone():
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({
                "error": "An active key exists for this email.",
                "hint": "Contact api@verumsignal.com to replace it."
            }), 409

        raw_key = "vs_live_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:16]

        cur.execute("""
            INSERT INTO api_keys
                (user_id, user_email, key_hash, key_prefix, name,
                 tier, monthly_quota, rate_limit_per_minute, created_at)
            VALUES (%s, %s, %s, %s, %s, 'starter', %s, %s, NOW())
            RETURNING id
        """, (user_id, email, key_hash, key_prefix, name,
              PHASE4_MONTHLY_QUOTA, PHASE4_RATE_LIMIT_PER_MIN))
        api_key_id = cur.fetchone()[0]

        # subscriptions.tier='free' is correct here and unrelated to
        # api_keys.tier='starter' above — different column, different
        # vocabulary (subscriptions.tier's real CHECK is free/pro/scale).
        # This insert was actually fine in the original code; it just
        # never got reached, since bug 3 always fired first.
        today = date.today()
        reset = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
        cur.execute("""
            INSERT INTO subscriptions
                (user_id, product, tier, status, quota_used_this_month, quota_reset_at)
            VALUES (%s, 'api', 'free', 'active', 0, %s)
            ON CONFLICT (user_id, product) DO NOTHING
        """, (user_id, reset))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "api_key": raw_key,
            "prefix": key_prefix,
            "tier": "starter",
            "monthly_quota": PHASE4_MONTHLY_QUOTA,
            "rate_limit_per_minute": PHASE4_RATE_LIMIT_PER_MIN,
            "message": "Store this key safely — it will not be shown again.",
            "docs": "https://verumsignal.com/developers",
            "email": email
        }), 201

    except Exception as e:
        if conn:
            conn.rollback()
        if cur:
            cur.close()
        if conn:
            conn.close()
        log.error("[v1/keys/request] failed: %s", e)
        return jsonify({"error": "Internal error — please try again or contact api@verumsignal.com"}), 500


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
  .field input,.field textarea,.field select{width:100%;background:#0a0a0f;border:1px solid var(--border);border-radius:6px;padding:10px 12px;color:var(--fg);font-size:14px;font-family:inherit;outline:none;transition:border-color 0.2s}
  .field input:focus,.field textarea:focus,.field select:focus{border-color:var(--accent)}
  .field textarea{resize:vertical;min-height:80px}
  .btn{display:inline-flex;align-items:center;gap:8px;padding:10px 22px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer;transition:opacity 0.2s}
  .btn:hover{opacity:0.85}
  .btn:disabled{opacity:0.5;cursor:not-allowed}
  .result{display:none;margin-top:20px;background:#0d0d14;border:1px solid var(--border);border-radius:8px;padding:20px}
  .result.success{border-color:rgba(74,222,128,0.3)}
  .result.error{border-color:rgba(248,113,113,0.3)}
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
  <p class="sub">Verdict-labeled claims, outlet credibility scores, and speaker-attributed debate data — programmatic access to the Verum Signal corpus, under a documented methodology.</p>

  <div class="cards">
    <div class="card"><h3>3,000+ independently checked claims</h3><p>Every claim assessed with web search under a public, versioned methodology. No verdict copying, no shortcuts.</p></div>
    <div class="card"><h3>Outlet scores</h3><p>Evidence-based credibility scores for 127 tracked outlets, updated continuously.</p></div>
    <div class="card"><h3>Live debate verdicts</h3><p>Real-time claim extraction during political debates, with speaker attribution.</p></div>
    <div class="card"><h3>Agent-ready</h3><p>Structured JSON responses, cursor pagination, OpenAPI spec, and a self-hosted <a href="https://github.com/brittbart/verumsignal-mcp" style="color:var(--accent)">MCP server</a> you run with your own key.</p></div>
  </div>

  <h2>Get API access</h2>
  <div class="form-wrap">
    <span class="pill">Closed beta</span>
    <h3>Access is by request</h3>
    <p>Requests are reviewed within 48 hours. We're accepting a limited number of design partners — selected partners receive complimentary access in exchange for feedback.</p>
    <div class="field"><label>Name</label><input type="text" id="f-name" placeholder="Jane Doe"></div>
    <div class="field"><label>Email</label><input type="email" id="f-email" placeholder="you@example.com"></div>
    <div class="field"><label>Organization</label><input type="text" id="f-org" placeholder="Acme Newsroom"></div>
    <div class="field"><label>How will you use the API?</label><textarea id="f-use" placeholder="A few sentences is fine."></textarea></div>
    <div class="field"><label>Estimated monthly calls (optional)</label>
      <select id="f-volume">
        <option value="">Not sure yet</option>
        <option value="Under 5,000/mo">Under 5,000/mo</option>
        <option value="5,000-50,000/mo">5,000–50,000/mo</option>
        <option value="50,000+/mo">50,000+/mo</option>
      </select>
    </div>
    <button class="btn" id="req-btn" onclick="requestAccess()">Request access</button>
    <div class="result" id="result-box"></div>
  </div>

  <h2>Quick start</h2>
  <div class="code-block"><pre><span class="comment"># Get your outlet credibility score</span>
curl https://api.verumsignal.com/v1/outlets/nytimes.com \\
  -H "Authorization: Bearer vs_live_your_key_here"</pre></div>
  <div class="code-block"><pre><span class="comment"># Recent claims</span>
curl "https://api.verumsignal.com/v1/claims?limit=10" \\
  -H "Authorization: Bearer vs_live_your_key_here"</pre></div>
  <div class="code-block"><pre><span class="comment"># Get live debate verdicts</span>
curl "https://api.verumsignal.com/v1/debates" \\
  -H "Authorization: Bearer vs_live_your_key_here"</pre></div>

  <h2>Endpoints</h2>
  <table>
    <tr><th>Method</th><th>Endpoint</th><th>Description</th></tr>
    <tr><td>GET</td><td><code>/v1/meta</code></td><td>API status, corpus stats, methodology version</td></tr>
    <tr><td>GET</td><td><code>/v1/claims</code></td><td>Paginated claims feed</td></tr>
    <tr><td>GET</td><td><code>/v1/outlets</code></td><td>Outlet leaderboard with scores and tiers</td></tr>
    <tr><td>GET</td><td><code>/v1/outlets/:domain</code></td><td>Single outlet detail and verdict breakdown</td></tr>
    <tr><td>GET</td><td><code>/v1/debates</code></td><td>Debate list with claim counts</td></tr>
    <tr><td>GET</td><td><code>/v1/debates/:slug/claims</code></td><td>All claims from a specific debate</td></tr>
    <tr><td>GET</td><td><code>/openapi.yaml</code></td><td>Full OpenAPI 3.1 specification</td></tr>
  </table>

  <h2>Pricing &amp; limits</h2>
  <table>
    <tr><th>Tier</th><th>Price</th><th>Monthly quota</th><th>Per minute</th></tr>
    <tr><td>Starter</td><td>$49/mo</td><td>5,000 calls</td><td>60 calls</td></tr>
    <tr><td>Pro</td><td>$199/mo</td><td>50,000 calls</td><td>180 calls</td></tr>
    <tr><td>Enterprise</td><td>Custom</td><td>500,000+ calls</td><td>Custom</td></tr>
  </table>
  <p style="font-size:13px;color:var(--dim)">Now accepting design partners. Public pricing finalizes at general availability. Questions? Email <a href="mailto:api@verumsignal.com" style="color:var(--accent)">api@verumsignal.com</a></p>

  <footer>
    <a href="/methodology">Methodology</a>
    <a href="/openapi.yaml">OpenAPI spec</a>
    <a href="mailto:api@verumsignal.com">api@verumsignal.com</a>
    <a href="/">verumsignal.com</a>
  </footer>
</div>

<script>
async function requestAccess() {
  var btn = document.getElementById('req-btn');
  var box = document.getElementById('result-box');
  var name = document.getElementById('f-name').value.trim();
  var email = document.getElementById('f-email').value.trim();
  var organization = document.getElementById('f-org').value.trim();
  var use_case = document.getElementById('f-use').value.trim();
  var estimated_volume = document.getElementById('f-volume').value || null;
  if (!name || !email || !organization || !use_case) { alert('Name, email, organization, and use case are required.'); return; }
  btn.disabled = true; btn.textContent = 'Submitting...';
  try {
    var r = await fetch('/api/beta-request', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, email: email, organization: organization, use_case: use_case, estimated_volume: estimated_volume})
    });
    var d = await r.json();
    box.style.display = 'block';
    if (r.ok && d.success) {
      box.className = 'result success';
      box.innerHTML = '<div style="color:#4ade80;font-weight:600">Request received</div>' +
        '<p style="font-size:12px;color:#888;margin-top:8px">We review requests within 48 hours and will follow up at ' + email + '.</p>';
      btn.textContent = 'Submitted';
    } else {
      box.className = 'result error';
      box.innerHTML = '<div style="color:#f87171">Something went wrong. Please try again or email api@verumsignal.com directly.</div>';
      btn.disabled = false; btn.textContent = 'Request access';
    }
  } catch(e) {
    box.style.display = 'block';
    box.className = 'result error';
    box.innerHTML = '<div style="color:#f87171">Request failed. Please try again or email api@verumsignal.com directly.</div>';
    btn.disabled = false; btn.textContent = 'Request access';
  }
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
