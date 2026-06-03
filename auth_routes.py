"""
auth_routes.py
Verum Signal — Auth blueprint (magic link, stubbed)

Routes:
    POST /auth/request-link   — request a magic link email
    GET  /auth/verify         — verify a magic link token, set session
    POST /auth/logout         — clear session
    GET  /auth/me             — return current user (JSON, for JS/mobile use)

Helper (import from here):
    get_current_user(get_db)  — returns users row dict or None

Email sending is STUBBED — send_magic_link() logs to stdout.
Drop Resend (or any provider) in by filling that one function.

Session: Flask signed cookie (secret_key must be set on app before registering).
Cookie name: vs_session. HttpOnly, SameSite=Lax.
"""

import os
import uuid
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, session, redirect

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# ── Constants ─────────────────────────────────────────────────────────────────

TOKEN_TTL_MINUTES = 15
SESSION_KEY = 'user_id'


# ── Email stub ────────────────────────────────────────────────────────────────

def send_magic_link(email: str, token: str, base_url: str) -> None:
    """
    Send a magic link email to the user.

    STUBBED — logs to stdout. Replace the body of this function with
    Resend (or any transactional email provider) when ready.
    The function signature and call site do not change.

    Args:
        email:    recipient address
        token:    the raw UUID token (not hashed)
        base_url: request base URL, e.g. https://verumsignal.com
    """
    link = f"{base_url}/auth/verify?token={token}"
    logger.warning(
        "[AUTH STUB] Magic link for %s — would send:\n  %s\n"
        "  (replace send_magic_link() body with Resend to activate)",
        email, link
    )
    # When Resend is ready, replace above with:
    #
    # import resend
    # resend.api_key = os.environ['RESEND_API_KEY']
    # resend.Emails.send({
    #     "from": "Verum Signal <noreply@verumsignal.com>",
    #     "to": email,
    #     "subject": "Your Verum Signal sign-in link",
    #     "html": f'<a href="{link}">Sign in to Verum Signal</a> (expires in 15 minutes)',
    # })


# ── Helper: get current user ──────────────────────────────────────────────────

def get_current_user(get_db):
    """
    Return the current user as a dict, or None if not authenticated.

    Usage in any route:
        from auth_routes import get_current_user
        user = get_current_user(get_db)
        if not user:
            return jsonify({'error': 'authentication required'}), 401

    Returns dict with keys matching users table columns, or None.
    """
    user_id = session.get(SESSION_KEY)
    if not user_id:
        return None

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, email, stripe_customer_id, tier, created_at, last_seen_at
            FROM users
            WHERE id = %s AND deleted_at IS NULL
        """, (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            # User deleted — clear stale session
            session.pop(SESSION_KEY, None)
            return None

        return {
            'id':                 row[0],
            'email':              row[1],
            'stripe_customer_id': row[2],
            'tier':               row[3],
            'created_at':         row[4].isoformat() if row[4] else None,
            'last_seen_at':       row[5].isoformat() if row[5] else None,
        }

    except Exception as e:
        logger.error("[AUTH] get_current_user error: %s", e)
        return None


# ── Helper: get user subscription ─────────────────────────────────────────────

def get_subscription(get_db, user_id: int, product: str) -> dict | None:
    """
    Return the subscription row for a user+product, or None.

    product: 'consumer' | 'api'

    Returns dict or None. Callers treat None as free/no subscription.
    """
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, product, tier, status,
                   quota_used_this_month, quota_reset_at,
                   stripe_subscription_id
            FROM subscriptions
            WHERE user_id = %s AND product = %s
        """, (user_id, product))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return None

        return {
            'id':                    row[0],
            'product':               row[1],
            'tier':                  row[2],
            'status':                row[3],
            'quota_used_this_month': row[4],
            'quota_reset_at':        row[5].isoformat() if row[5] else None,
            'stripe_subscription_id': row[6],
        }

    except Exception as e:
        logger.error("[AUTH] get_subscription error: %s", e)
        return None


# ── Helper: quota check ────────────────────────────────────────────────────────

# Quota limits per tier per product.
# Free consumer: 2 on-demand reports/month (matches free report sample on pricing page).
# Pro consumer:  50 on-demand reports/month (matches pricing.html).
# Free API:      100 calls/month (matches api.html).
# Pro API:       1000 calls/month (matches api.html).
# Scale API:     25000 calls/month (matches api.html).
QUOTA_LIMITS = {
    'consumer': {'free': 2,     'pro': 50,    'scale': 50},
    'api':      {'free': 100,   'pro': 1000,  'scale': 25000},
}

def check_quota(get_db, user_id: int, product: str) -> dict:
    """
    Check whether a user has quota remaining for a product.

    Returns:
        {
            'allowed': bool,
            'tier': str,
            'used': int,
            'limit': int,
            'reason': str   # only present if not allowed
        }

    Does NOT increment quota — call increment_quota() on success.
    Callers that receive allowed=False should return HTTP 402.
    """
    sub = get_subscription(get_db, user_id, product)

    if sub is None:
        # No subscription row — treat as free tier
        tier = 'free'
        used = 0
    else:
        if sub['status'] not in ('active', 'trialing'):
            return {
                'allowed': False,
                'tier': sub['tier'],
                'used': sub['quota_used_this_month'],
                'limit': QUOTA_LIMITS[product][sub['tier']],
                'reason': f"subscription status is '{sub['status']}'"
            }
        tier = sub['tier']
        used = sub['quota_used_this_month']

    limit = QUOTA_LIMITS[product][tier]
    allowed = used < limit

    result = {'allowed': allowed, 'tier': tier, 'used': used, 'limit': limit}
    if not allowed:
        result['reason'] = f"{product} quota exhausted ({used}/{limit} this month)"
    return result


def increment_quota(get_db, user_id: int, product: str) -> bool:
    """
    Increment quota_used_this_month for a user+product subscription.
    Creates a free-tier subscription row if one doesn't exist.
    Returns True on success.
    """
    try:
        conn = get_db()
        cur = conn.cursor()

        # Upsert: create free row if absent, increment if present
        cur.execute("""
            INSERT INTO subscriptions (user_id, product, tier, status, quota_used_this_month, updated_at)
            VALUES (%s, %s, 'free', 'active', 1, NOW())
            ON CONFLICT (user_id, product) DO UPDATE
                SET quota_used_this_month = subscriptions.quota_used_this_month + 1,
                    updated_at = NOW()
        """, (user_id, product))

        conn.commit()
        cur.close()
        conn.close()
        return True

    except Exception as e:
        logger.error("[AUTH] increment_quota error: %s", e)
        return False


# ── Routes ────────────────────────────────────────────────────────────────────

@auth_bp.route('/request-link', methods=['POST'])
def request_link():
    """
    POST /auth/request-link
    Body: {"email": "user@example.com"}

    Always returns {"ok": true} — never confirm whether email exists.
    Creates a magic_link_tokens row and calls send_magic_link() (stubbed).
    """
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()

    if not email or '@' not in email:
        return jsonify({'ok': False, 'error': 'valid email required'}), 400

    try:
        conn = get_db_from_app()
        cur = conn.cursor()

        token = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)

        cur.execute("""
            INSERT INTO magic_link_tokens (token, email, expires_at)
            VALUES (%s, %s, %s)
        """, (token, email, expires_at))

        conn.commit()
        cur.close()
        conn.close()

        base_url = request.host_url.rstrip('/')
        send_magic_link(email, token, base_url)

    except Exception as e:
        logger.error("[AUTH] request_link error: %s", e)
        # Still return ok — don't leak server errors to client

    return jsonify({'ok': True})


@auth_bp.route('/verify', methods=['GET'])
def verify():
    """
    GET /auth/verify?token=<uuid>

    Validates the token, creates user if new, sets session cookie.
    Redirects to /account on success, /auth/error on failure.
    """
    token = request.args.get('token', '').strip()
    if not token:
        return redirect('/auth/error?reason=missing_token')

    try:
        conn = get_db_from_app()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        # Fetch token row
        cur.execute("""
            SELECT id, email, expires_at, used_at
            FROM magic_link_tokens
            WHERE token = %s
        """, (token,))
        row = cur.fetchone()

        if not row:
            cur.close()
            conn.close()
            return redirect('/auth/error?reason=invalid_token')

        token_id, email, expires_at, used_at = row

        if used_at is not None:
            cur.close()
            conn.close()
            return redirect('/auth/error?reason=token_already_used')

        if expires_at < now:
            cur.close()
            conn.close()
            return redirect('/auth/error?reason=token_expired')

        # Mark token used
        cur.execute("""
            UPDATE magic_link_tokens SET used_at = %s WHERE id = %s
        """, (now, token_id))

        # Upsert user — create if new, update last_seen_at if returning
        cur.execute("""
            INSERT INTO users (email, email_verified, updated_at, last_seen_at)
            VALUES (%s, TRUE, NOW(), NOW())
            ON CONFLICT (email) DO UPDATE
                SET last_seen_at = NOW(),
                    email_verified = TRUE,
                    updated_at = NOW()
            RETURNING id
        """, (email,))
        user_id = cur.fetchone()[0]

        conn.commit()
        cur.close()
        conn.close()

        # Set session
        session[SESSION_KEY] = user_id
        session.permanent = True

        logger.info("[AUTH] verified: user_id=%s email=%s", user_id, email)
        return redirect('/account')

    except Exception as e:
        logger.error("[AUTH] verify error: %s", e)
        return redirect('/auth/error?reason=server_error')


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """
    POST /auth/logout
    Clears the session cookie.
    """
    session.pop(SESSION_KEY, None)
    return jsonify({'ok': True})


@auth_bp.route('/me', methods=['GET'])
def me():
    """
    GET /auth/me
    Returns current user as JSON, or 401 if not authenticated.
    Used by frontend JS and mobile to check auth state.
    """
    from flask import current_app
    get_db = current_app.config.get('GET_DB')
    if not get_db:
        return jsonify({'error': 'server misconfiguration'}), 500

    user = get_current_user(get_db)
    if not user:
        return jsonify({'authenticated': False}), 401

    return jsonify({'authenticated': True, 'user': user})


# ── Internal: get_db from app config ─────────────────────────────────────────
# Routes need get_db but can't import it directly (circular).
# It's stored on app.config['GET_DB'] at registration time.

def get_db_from_app():
    from flask import current_app
    get_db = current_app.config.get('GET_DB')
    if not get_db:
        raise RuntimeError("GET_DB not set on app.config")
    return get_db()


# ── Registration ──────────────────────────────────────────────────────────────

def register_auth_routes(app, get_db):
    """
    Call from api.py:
        from auth_routes import register_auth_routes
        register_auth_routes(app, get_db)
    """
    # Store get_db on app config so routes can reach it without circular import
    app.config['GET_DB'] = get_db

    # Secret key — required for signed session cookies
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        if os.environ.get('RAILWAY_ENVIRONMENT'):
            raise RuntimeError("SECRET_KEY env var is required in production")
        # Local dev fallback — not secure, warns loudly
        logger.warning("[AUTH] SECRET_KEY not set — using insecure dev default. Set SECRET_KEY in .env")
        secret_key = 'dev-insecure-placeholder-set-SECRET_KEY-in-env'

    app.secret_key = secret_key

    # Session cookie config
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('RAILWAY_ENVIRONMENT'))
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

    app.register_blueprint(auth_bp)
    logger.info("[AUTH] auth_routes registered")
