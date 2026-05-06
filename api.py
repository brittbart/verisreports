
from flask import Flask, jsonify, request, redirect, send_from_directory
from flask_cors import CORS
import psycopg2
import os
import sys
from dotenv import load_dotenv
import secrets
import string
from datetime import datetime


app = Flask(__name__)
app.config['THREADED'] = True
CORS(app)

def get_db():
    return psycopg2.connect(
        **(dict(dsn=os.environ['DATABASE_URL']) if os.environ.get('DATABASE_URL') else dict(
            dbname=os.environ.get('DB_NAME', 'railway'),
            user=os.environ.get('DB_USER', 'postgres'),
            password=os.environ.get('DB_PASSWORD', 'PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP'),
            host=os.environ.get('DB_HOST', 'shinkansen.proxy.rlwy.net'),
            port=os.environ.get('DB_PORT', '35370')
        ))
    )

from api_leaderboard import register_leaderboard_routes
from api_leaderboard import compute_score, compute_score_band, compute_tier, WEIGHTS, SCOREABLE_VERDICTS, INCLUSION_THRESHOLD
from outlet_routes import register_outlet_routes
register_leaderboard_routes(app, get_db)
register_outlet_routes(app, get_db)
# ---------- Short URL helpers (Phase 2) ----------

_HASH_ALPHABET = string.digits + string.ascii_lowercase  # base36
_HASH_LENGTH = 12


def _generate_hash():
    return ''.join(secrets.choice(_HASH_ALPHABET) for _ in range(_HASH_LENGTH))


def get_or_create_short_hash(article_id):
    """Return the short URL hash for an article, creating one if needed.
    Uses a collision-safe insert: tries up to 5 random hashes before raising.
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        # If a hash already exists for this article, return it
        cur.execute(
            "SELECT hash FROM report_links WHERE article_id = %s LIMIT 1",
            (article_id,),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        # Otherwise generate a new one (with collision retry)
        for _ in range(5):
            candidate = _generate_hash()
            try:
                cur.execute(
                    "INSERT INTO report_links (hash, article_id) VALUES (%s, %s)",
                    (candidate, article_id),
                )
                conn.commit()
                return candidate
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                continue
        raise RuntimeError("Could not generate unique short hash after 5 attempts")
    finally:
        cur.close()
        conn.close()# ---------- Short URL helpers (Phase 2) ----------

_HASH_ALPHABET = string.digits + string.ascii_lowercase  # base36
_HASH_LENGTH = 12


def _generate_hash():
    return ''.join(secrets.choice(_HASH_ALPHABET) for _ in range(_HASH_LENGTH))


def get_or_create_short_hash(article_id):
    """Return the short URL hash for an article, creating one if needed.
    Uses a collision-safe insert: tries up to 5 random hashes before raising.
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        # If a hash already exists for this article, return it
        cur.execute(
            "SELECT hash FROM report_links WHERE article_id = %s LIMIT 1",
            (article_id,),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        # Otherwise generate a new one (with collision retry)
        for _ in range(5):
            candidate = _generate_hash()
            try:
                cur.execute(
                    "INSERT INTO report_links (hash, article_id) VALUES (%s, %s)",
                    (candidate, article_id),
                )
                conn.commit()
                return candidate
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                continue
        raise RuntimeError("Could not generate unique short hash after 5 attempts")
    finally:
        cur.close()
        conn.close()



# ---------- Phase 4: depth-aware claim verification ----------
def verify_and_insert_claims(claims, art_id, title, source_name, cursor, depth=None):
    """Verify the top `depth` claims and insert all claims into the DB.
    
    depth=None means verify all claims (paid behavior).
    depth=2 means verify top 2 (free behavior).
    
    Outlet claims are processed first (they're what scoring uses).
    Remaining claims insert with verdict=NULL, verification_depth=NULL
    so a later upgrade can verify them.
    """
    from verdict_engine import analyse_claim
    def _sort_key(c):
        return 0 if c.get('claim_origin', 'outlet_claim') == 'outlet_claim' else 1
    sorted_claims = sorted(claims, key=_sort_key)
    verify_count = len(sorted_claims) if depth is None else min(depth, len(sorted_claims))
    
    out_rows = []
    for i, c in enumerate(sorted_claims):
        claim_text = c.get('claim_text', '')
        speaker = c.get('speaker', '')
        claim_type = c.get('claim_type', 'factual')
        claim_origin = c.get('claim_origin', 'outlet_claim')
        attribution_context = c.get('attribution_context', '')
        
        if i < verify_count:
            result = analyse_claim(
                claim_text, speaker, claim_type, title, source_name,
                cursor=cursor, claim_origin=claim_origin,
                attribution_context=attribution_context,
            )
            if result is None:
                print(f"[verify] analyse_claim returned None for: {claim_text[:80]}... -- skipping")
                continue
            cursor.execute(
                "INSERT INTO claims (article_id, claim_text, speaker, claim_type, "
                "claim_origin, verdict, confidence_score, verdict_summary, "
                "full_analysis, sources_used, priority_score, verification_depth) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (art_id, claim_text, speaker, claim_type, claim_origin,
                 result.get('verdict'), result.get('confidence_score'),
                 result.get('verdict_summary'), result.get('full_analysis'),
                 result.get('sources_used'), 50, depth or 99),
            )
            cid = cursor.fetchone()[0]
            out_rows.append((cid, claim_text, speaker, claim_type, claim_origin,
                             result.get('verdict'), result.get('confidence_score'),
                             result.get('verdict_summary'), result.get('full_analysis'),
                             result.get('sources_used')))
        else:
            cursor.execute(
                "INSERT INTO claims (article_id, claim_text, speaker, claim_type, "
                "claim_origin, verdict, confidence_score, verdict_summary, "
                "full_analysis, sources_used, priority_score, verification_depth) "
                "VALUES (%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,NULL,%s,NULL) RETURNING id",
                (art_id, claim_text, speaker, claim_type, claim_origin, 50),
            )
            cid = cursor.fetchone()[0]
            out_rows.append((cid, claim_text, speaker, claim_type, claim_origin,
                             None, None, None, None, None))
    return out_rows



# ---------- Phase 6: email stub + verification tokens ----------

def send_email(to, subject, html_body):
    """Send an email. Currently a stub — logs to console.
    
    TODO: Wire to Resend once domain DNS + API key are configured.
    Resend Python SDK: pip install resend; resend.api_key=os.environ['RESEND_API_KEY']
    Then: resend.Emails.send({"from": "noreply@verumsignal.com", "to": to, "subject": subject, "html": html_body})
    """
    print(f"[email-stub] TO: {to}")
    print(f"[email-stub] SUBJECT: {subject}")
    print(f"[email-stub] BODY (first 200 chars): {html_body[:200]}")
    print(f"[email-stub] Email NOT actually sent. Configure Resend to enable.")
    return True


def generate_verification_token(email, intended_url):
    """Create a single-use token for an email + intended URL.
    
    Returns the token string. Caller is responsible for sending an email
    with a link like /verify?token=<token>.
    """
    token = secrets.token_urlsafe(32)[:64]  # ~43 chars after urlsafe encoding
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO verification_tokens (token, email, intended_url) VALUES (%s, %s, %s)",
            (token, email.lower().strip(), intended_url),
        )
        conn.commit()
        return token
    finally:
        cur.close()
        conn.close()


def consume_verification_token(token):
    """Exchange a token for (email, intended_url). Marks token used.
    
    Returns (email, intended_url) on success, None if token is invalid,
    expired (>24h old), or already used.
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT email, intended_url, used_at, created_at "
            "FROM verification_tokens WHERE token = %s",
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return None
        email, intended_url, used_at, created_at = row
        if used_at is not None:
            return None  # already used
        # 24-hour expiry
        from datetime import datetime, timedelta
        if datetime.now() - created_at > timedelta(hours=24):
            return None
        # Mark used
        cur.execute(
            "UPDATE verification_tokens SET used_at = NOW() WHERE token = %s",
            (token,),
        )
        conn.commit()
        return (email, intended_url)
    finally:
        cur.close()
        conn.close()



@app.route('/upgrade', methods=['GET'])
def upgrade_gate():
    """Render the email gate for unlocking a free Pro sample report."""
    url = request.args.get('url', '').strip()
    if not url:
        return redirect('/')
    with open(os.path.join(os.path.dirname(__file__), 'templates', 'upgrade_gate.html'), 'r') as _tf:
        html = _tf.read()
    html = html.replace('{{url}}', url)
    html = html.replace('{{error_html}}', '')
    return html, 200, {'Content-Type': 'text/html'}



@app.route('/upgrade/submit', methods=['POST'])
def upgrade_submit():
    """Process email gate form. Generates a verification token,
    sends email (currently a stub), and shows the check-your-email page.
    """
    email = (request.form.get('email') or '').strip().lower()
    url = (request.form.get('url') or '').strip()
    
    # Basic email validation
    if '@' not in email or '.' not in email or len(email) < 5:
        with open(os.path.join(os.path.dirname(__file__), 'templates', 'upgrade_gate.html'), 'r') as _tf:
            html = _tf.read()
        error_html = '<div class="error">Please enter a valid email address.</div>'
        html = html.replace('{{url}}', url)
        html = html.replace('{{error_html}}', error_html)
        return html, 400, {'Content-Type': 'text/html'}
    
    # Check if this email has already used their free Pro sample
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM pro_samples WHERE email = %s LIMIT 1", (email,))
        already_sampled = cur.fetchone() is not None
    finally:
        cur.close()
        conn.close()
    
    if already_sampled:
        return redirect('/upgrade-paid?url=' + url)
    
    # Generate token and "send" verification email
    token = generate_verification_token(email, url)
    verify_link = request.host_url.rstrip('/') + '/verify?token=' + token
    email_body = '<html><body style="font-family:sans-serif;color:#333;max-width:520px;margin:0 auto;padding:24px;">' \
        '<h2 style="color:#d4537e;">Your free Pro report is ready</h2>' \
        '<p>Click the link below to unlock your one-time Pro sample report:</p>' \
        '<p><a href="' + verify_link + '" style="display:inline-block;padding:11px 24px;background:#D4537E;color:#fff;text-decoration:none;border-radius:6px;">Open my Pro report</a></p>' \
        '<p style="color:#666;font-size:13px;">This link expires in 24 hours and can only be used once. If you didn\'t request this, you can ignore this email.</p>' \
        '<p style="color:#999;font-size:11px;border-top:1px solid #eee;padding-top:12px;margin-top:24px;">Verum Signal &middot; Methodology v1.6</p>' \
        '</body></html>'
    send_email(email, "Your free Pro report from Verum Signal", email_body)
    
    # Render check-your-email page (template)
    with open(os.path.join(os.path.dirname(__file__), 'templates', 'upgrade_check_email.html'), 'r') as _tf:
        html = _tf.read()
    html = html.replace('{{email}}', email)
    return html, 200, {'Content-Type': 'text/html'}



@app.route('/verify', methods=['GET'])
def verify_token():
    """Exchange a verification token for a Pro sample.
    Sets a cookie marking the email as having used their sample,
    inserts into pro_samples, and redirects to the article report at depth=99.
    """
    token = (request.args.get('token') or '').strip()
    if not token:
        return _render_verify_error('Missing token.')
    
    result = consume_verification_token(token)
    if result is None:
        return _render_verify_error('This link has expired, been used already, or is invalid. Each link works once and lasts 24 hours.')
    
    email, intended_url = result
    
    # Find the article ID for the intended URL (so we can record the sample)
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM articles WHERE url = %s LIMIT 1", (intended_url,))
        row = cur.fetchone()
        article_id = row[0] if row else None
        # Record the sample (idempotent — UNIQUE on email+article_id from Phase 1 schema)
        if article_id:
            try:
                cur.execute(
                    "INSERT INTO pro_samples (email, article_id) VALUES (%s, %s) "
                    "ON CONFLICT (email, article_id) DO NOTHING",
                    (email, article_id),
                )
                conn.commit()
            except Exception as e:
                print(f"[verify] Could not record pro_sample: {e}")
                conn.rollback()
    finally:
        cur.close()
        conn.close()
    
    # Redirect to paid report. Set cookie so future visits know this email had a sample.
    target = '/report?url=' + intended_url + '&depth=99'
    response = redirect(target)
    # Cookie expires in 1 year; readable on subsequent /upgrade visits
    response.set_cookie('vs_email', email, max_age=60*60*24*365, secure=True, httponly=True, samesite='Lax')
    return response


def _render_verify_error(message):
    """Render a friendly error page when verify fails."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Link expired — Verum Signal</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #1a0d2e; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 32px 16px; color: #e7dffb; }}
.stage {{ width: 100%; max-width: 460px; }}
.panel {{ background: #100820; border: 0.5px solid rgba(180, 150, 230, 0.18); border-radius: 10px; padding: 32px 24px; text-align: center; }}
h1 {{ font-family: 'Iowan Old Style', Georgia, serif; font-size: 22px; font-weight: 500; margin: 0 0 12px; color: #f3edff; }}
p {{ font-size: 13px; line-height: 1.6; color: #cfc1ec; margin: 0 0 16px; }}
a {{ color: #b48cff; text-decoration: none; font-family: ui-monospace, monospace; font-size: 12px; }}
</style></head>
<body>
<div class="stage"><div class="panel">
<h1>Link expired or invalid</h1>
<p>{message}</p>
<a href="/">&larr; Back to Verum Signal</a>
</div></div>
</body></html>""", 400, {'Content-Type': 'text/html'}



@app.route('/upgrade-paid', methods=['GET'])
def upgrade_paid():
    """Second paywall — shown when a user has already used their free Pro sample.
    Renders mockup 02 with the waitlist form."""
    url = (request.args.get('url') or '').strip()
    prefilled_email = request.cookies.get('vs_email', '')
    with open(os.path.join(os.path.dirname(__file__), 'templates', 'upgrade_paid.html'), 'r') as _tf:
        html = _tf.read()
    html = html.replace('{{url}}', url)
    html = html.replace('{{prefilled_email}}', prefilled_email)
    html = html.replace('{{error_html}}', '')
    return html, 200, {'Content-Type': 'text/html'}



@app.route('/waitlist/submit', methods=['POST'])
def waitlist_submit():
    """Process waitlist signup. Inserts into waitlist table, sends confirmation
    email (currently a stub), and redirects to the confirmation page.
    """
    email = (request.form.get('email') or '').strip().lower()
    source_page = (request.form.get('source_page') or 'unknown').strip()
    url = (request.form.get('url') or '').strip()
    
    # Basic email validation
    if '@' not in email or '.' not in email or len(email) < 5:
        # Re-render second paywall with error
        with open(os.path.join(os.path.dirname(__file__), 'templates', 'upgrade_paid.html'), 'r') as _tf:
            html = _tf.read()
        error_html = '<div class="error">Please enter a valid email address.</div>'
        html = html.replace('{{url}}', url)
        html = html.replace('{{prefilled_email}}', email)
        html = html.replace('{{error_html}}', error_html)
        return html, 400, {'Content-Type': 'text/html'}
    
    # Insert into waitlist (idempotent — UNIQUE constraint on email)
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO waitlist (email, source_page) VALUES (%s, %s) "
            "ON CONFLICT (email) DO NOTHING",
            (email, source_page),
        )
        conn.commit()
    except Exception as e:
        print(f"[waitlist] Insert error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
    
    # Send confirmation email (stub for now)
    email_body = '<html><body style="font-family:sans-serif;color:#333;max-width:520px;margin:0 auto;padding:24px;">' \
        '<h2 style="color:#d4537e;">You\'re on the list</h2>' \
        '<p>Thanks for joining the Verum Signal Pro waitlist. We\'ll email you the moment Pro is live.</p>' \
        '<p>As an early member, you\'ll get <strong>50% off for 6 months</strong> &mdash; $12.50/mo to start.</p>' \
        '<p style="color:#999;font-size:11px;border-top:1px solid #eee;padding-top:12px;margin-top:24px;">Verum Signal &middot; Methodology v1.6</p>' \
        '</body></html>'
    send_email(email, "You're on the Verum Signal Pro waitlist", email_body)
    
    return redirect('/waitlist-confirmed')


@app.route('/waitlist-confirmed', methods=['GET'])
def waitlist_confirmed():
    """Render the waitlist confirmation page (mockup 03)."""
    with open(os.path.join(os.path.dirname(__file__), 'templates', 'waitlist_confirmed.html'), 'r') as _tf:
        html = _tf.read()
    return html, 200, {'Content-Type': 'text/html'}


@app.route('/api/source', methods=['GET'])
def get_source():
    # Aligned with api_leaderboard.compute_score / LEADERBOARD_SQL so the
    # extension and the public leaderboard never disagree.
    from api_leaderboard import compute_score, compute_score_band, compute_tier, INCLUSION_THRESHOLD

    domain = request.args.get('domain', '').strip()
    if not domain:
        return jsonify({'error': 'domain required'}), 400
    core = domain.replace('www.', '').lower()

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            SELECT
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
                MAX(c.first_seen)                                    AS last_verdict_at
            FROM articles a
            JOIN claims   c ON c.article_id = a.id
            WHERE LOWER(a.source_name) = %s
              AND c.verdict IS NOT NULL
              AND c.claim_origin = 'outlet_claim'
              AND a.published_at IS NOT NULL
              AND a.published_at < NOW() - INTERVAL '6 hours'
        ''', (core,))
        row = cur.fetchone()
        conn.close()

        if not row or (row[8] or 0) == 0:
            return jsonify({'domain': domain, 'status': 'not_found'})

        verdict_count    = row[8] or 0
        scoreable_count  = row[9] or 0
        weighted_sum     = row[10] or 0
        last_verdict_at  = row[11]

        if verdict_count < INCLUSION_THRESHOLD:
            return jsonify({
                'domain': domain,
                'status': 'not_found',
                'reason': 'below_inclusion_threshold',
                'verdict_count': verdict_count,
            })

        score = compute_score(weighted_sum, scoreable_count)
        tier  = compute_tier(verdict_count)
        band  = compute_score_band(score)

        as_of = last_verdict_at.strftime('%B %d, %Y') if last_verdict_at else None

        return jsonify({
            'domain':            domain,
            'status':            'found',
            'score':             score,
            'rating':            band,
            'tier':              tier,
            'verdict_count':     verdict_count,
            'total_claims':      verdict_count,
            'scoreable_count':   scoreable_count,
            'supported':         row[0] or 0,
            'plausible':         row[1] or 0,
            'corroborated':      row[2] or 0,
            'overstated':        row[3] or 0,
            'disputed':          row[4] or 0,
            'not_supported':     row[5] or 0,
            'not_verifiable':    row[6] or 0,
            'opinion':           row[7] or 0,
            'as_of':             as_of,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'version': '1.0'})

@app.route('/api/stats', methods=['GET'])
def stats():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM articles')
        articles = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM claims')
        claims = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM claims WHERE verdict IS NOT NULL')
        verdicts = cur.fetchone()[0]
        conn.close()
        return jsonify({
            'articles': articles,
            'claims': claims,
            'verdicts': verdicts,
            'as_of': datetime.now().strftime('%B %d, %Y')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/report', methods=['GET'])
def get_report():
    """
    Accept a full article URL and return a complete Verum Signal report.
    If the article has been pre-verified, returns instantly.
    If not, triggers real-time verification.
    """
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'error': 'url required'}), 400

    try:
        conn = get_db()
        cur = conn.cursor()

        # Step 1: Check if article is already in database
        cur.execute("""
            SELECT a.id, a.title, a.source_name, a.url,
                   a.claims_verified, a.verified_at
            FROM articles a
            WHERE a.url = %s
            LIMIT 1
        """, (url,))
        article = cur.fetchone()

        if not article:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace('www.', '')
            slug = parsed.path.replace('-', ' ').replace('/', ' ').strip()
            keywords = [w for w in slug.split() if len(w) > 4][:6]
            if keywords and domain:
                search_terms = ' | '.join(keywords)
                cur.execute("""
                    SELECT a.id, a.title, a.source_name, a.url,
                           a.claims_verified, a.verified_at
                    FROM articles a
                    WHERE a.source_name ILIKE %s
                    AND to_tsvector('english', a.title) @@ to_tsquery('english', %s)
                    ORDER BY a.fetched_at DESC
                    LIMIT 1
                """, (f'%{domain}%', search_terms))
        article = cur.fetchone()

        if not article:
            conn.close()
            return jsonify({
                'status': 'not_found',
                'url': url,
                'message': 'Article not in database yet. Try again in a few hours or use real-time verification.'
            })

        art_id, title, source_name, art_url, claims_verified, verified_at = article

        # Step 2: Get all claims for this article
        cur.execute("""
            SELECT id, claim_text, speaker, claim_type, claim_origin,
                   verdict, confidence_score, verdict_summary,
                   full_analysis, sources_used
            FROM claims
            WHERE article_id = %s
            ORDER BY priority_score DESC
        """, (art_id,))
        claims = cur.fetchall()
        conn.close()

        if not claims:
            return jsonify({
                'status': 'no_claims',
                'url': url,
                'title': title,
                'source': source_name
            })

        # Step 3: Build report
        supported_count    = sum(1 for c in claims if c[5] == 'supported')
        plausible_count    = sum(1 for c in claims if c[5] == 'plausible')
        overstated_count   = sum(1 for c in claims if c[5] == 'overstated')
        disputed_count     = sum(1 for c in claims if c[5] == 'disputed')
        not_supported_count= sum(1 for c in claims if c[5] == 'not_supported')
        opinion_count      = sum(1 for c in claims if c[5] == 'opinion')
        unverified_count   = sum(1 for c in claims if c[5] is None)

        # Weighted scoring — opinion, not_verifiable, and unverified excluded.
        # Uses api_leaderboard.WEIGHTS / compute_score / compute_score_band as
        # the single source of truth (Patch 1, methodology v1.6 prep).
        weighted_sum = sum(WEIGHTS[c[5]] for c in claims if c[5] in WEIGHTS)
        scoreable = sum(1 for c in claims if c[5] in WEIGHTS)
        score = compute_score(weighted_sum, scoreable)
        rating = compute_score_band(score)

        claims_data = []
        for cid, claim_text, speaker, claim_type, claim_origin, verdict, confidence, summary, analysis, sources in claims:
            claims_data.append({
                'id': cid,
                'claim_text': claim_text,
                'speaker': speaker,
                'claim_type': claim_type,
                'claim_origin': claim_origin,
                'verdict': verdict,
                'confidence_score': confidence,
                'verdict_summary': summary,
                'full_analysis': analysis,
                'sources_used': sources
            })

        return jsonify({
            'status': 'found',
            'pre_verified': claims_verified or False,
            'verified_at': verified_at.isoformat() if verified_at else None,
            'url': art_url,
            'title': title,
            'source': source_name,
            'rating': rating,
            'score': score,
            'stats': {
                'supported': supported_count,
                'plausible': plausible_count,
                'overstated': overstated_count,
                'disputed': disputed_count,
                'not_supported': not_supported_count,
                'opinion': opinion_count,
                'unverified': unverified_count,
                'total': len(claims)
            },
            'claims': claims_data,
            'as_of': datetime.now().strftime('%B %d, %Y')
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/dispute", methods=["POST"])
def submit_dispute():
    data = request.get_json(silent=True) or {}
    domain = data.get("domain","").strip().lower().replace("www.","")
    claim_id = data.get("claim_id")
    contact_email = data.get("contact_email","").strip()
    dispute_text = data.get("dispute_text","").strip()
    outlet_response = data.get("outlet_response","").strip()
    if not domain: return jsonify({"error":"domain required"}),400
    if not dispute_text and not outlet_response: return jsonify({"error":"dispute_text or outlet_response required"}),400
    try:
        conn = get_db()
        cur = conn.cursor()
        if claim_id:
            cur.execute("SELECT id FROM claims WHERE id = %s",(claim_id,))
            if not cur.fetchone():
                conn.close()
                return jsonify({"error":"claim_id not found"}),404
        cur.execute("INSERT INTO outlet_disputes (domain,claim_id,contact_email,dispute_text,outlet_response,status) VALUES (%s,%s,%s,%s,%s,'pending') RETURNING id,submitted_at",(domain,claim_id,contact_email or None,dispute_text or None,outlet_response or None))
        dispute_id,submitted_at = cur.fetchone()
        conn.commit()
        conn.close()
        return jsonify({"status":"received","dispute_id":dispute_id,"domain":domain,"submitted_at":submitted_at.isoformat(),"message":"Your dispute has been logged and will appear publicly on the Verum Signal leaderboard. All disputes are reviewed within 10 business days. If a verdict is found incorrect it will be re-assessed and updated."})
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.route("/api/disputes", methods=["GET"])
def get_disputes():
    domain = request.args.get("domain","").strip().lower().replace("www.","")
    if not domain: return jsonify({"error":"domain required"}),400
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id,domain,claim_id,dispute_text,outlet_response,status,submitted_at,resolution FROM outlet_disputes WHERE domain=%s ORDER BY submitted_at DESC",(domain,))
        rows = cur.fetchall()
        conn.close()
        return jsonify({"domain":domain,"total":len(rows),"disputes":[{"id":r[0],"domain":r[1],"claim_id":r[2],"dispute_text":r[3],"outlet_response":r[4],"status":r[5],"submitted_at":r[6].isoformat() if r[6] else None,"resolution":r[7]} for r in rows]})
    except Exception as e:
        return jsonify({"error":str(e)}),500



@app.route('/methodology/data.js', methods=['GET'])
def methodology_data():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'data.js')

@app.route('/methodology/Report.jsx', methods=['GET'])
def methodology_report():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'Report.jsx')

@app.route('/methodology/tweaks-panel.jsx', methods=['GET'])
def methodology_tweaks():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'tweaks-panel.jsx')

@app.route('/methodology/report.css', methods=['GET'])
def methodology_css():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'report.css')
@app.route('/methodology', methods=['GET'])
def methodology_page():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'index.html')

@app.route('/methodology/archive/v1.5', methods=['GET'])
def methodology_v15():
    # Serves the v1.5-era static methodology page (preserved verbatim).
    # Live methodology is at /methodology (currently v1.6).
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'methodology.html')


@app.route('/how-it-works.html', methods=['GET'])
def how_it_works():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'how-it-works.html')

@app.route('/leaderboard.html', methods=['GET'])
def leaderboard():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'leaderboard.html')

@app.route('/pricing.html', methods=['GET'])
def pricing():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'pricing.html')

@app.route('/styles.css', methods=['GET'])
def styles():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'styles.css')

@app.route('/how-it-works', methods=['GET'])
def how_it_works_clean():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'how-it-works.html')

@app.route('/leaderboard', methods=['GET'])
def leaderboard_clean():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'leaderboard.html')

@app.route('/pricing', methods=['GET'])
def pricing_clean():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'pricing.html')

@app.route('/privacy.html', methods=['GET'])
def privacy_html():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'privacy.html')

@app.route('/privacy', methods=['GET'])
def privacy_clean():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'privacy.html')

@app.route('/index.html', methods=['GET'])
def index_html():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'index.html')

@app.route('/chrome.js', methods=['GET'])
def chrome_js():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'chrome.js')

@app.route('/report.css', methods=['GET'])
def methodology_report_css():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'report.css')

@app.route('/data.js', methods=['GET'])
def methodology_data_js():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'data.js')

@app.route('/Report.jsx', methods=['GET'])
def methodology_report_jsx():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'Report.jsx')

@app.route('/tweaks-panel.jsx', methods=['GET'])
def methodology_tweaks_jsx():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'tweaks-panel.jsx')

# ── fetch_article_content (Opus architecture brief, April 28 2026) ────────────

_BOT_TITLES = {
    'just a moment', 'just a moment...', 'checking your browser',
    'access denied', 'please verify you are a human', 'ddos protection',
    'attention required', 'cloudflare', 'one moment...', 'verifying you are human',
}
_USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
               'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')

# Source tier sets — used by render block AND background signal lookup
INDEPENDENT_SOURCES = {
    'reuters.com', 'apnews.com', 'ap.org', 'npr.org', 'bbc.com', 'bbc.co.uk',
    'nytimes.com', 'washingtonpost.com', 'theguardian.com', 'wikipedia.org',
    'britannica.com', 'brookings.edu', 'rand.org', 'cfr.org', 'pbs.org',
    'cbsnews.com', 'nbcnews.com', 'abcnews.go.com', 'politico.com', 'thehill.com',
    'axios.com', 'bloomberg.com', 'wsj.com', 'economist.com', 'ft.com',
    'federalnewsnetwork.com', 'militarytimes.com'
}
WIRE_SOURCES = {'reuters.com', 'apnews.com', 'ap.org', 'bloomberg.com', 'afp.com'}


def _is_bot_protection(title):
    if not title: return False
    return title.lower().strip().rstrip('.').strip() in _BOT_TITLES

import re as _re_slug


def _is_paywall(title, body):
    """Detect paywall preview content. Returns True if the retrieved content
    appears to be a paywall lede + CTA wall rather than the full article.
    Uses high-signal body-text markers only — phrases that don't appear in
    legitimate article bodies. Title is currently unused but kept for
    parity with _is_bot_protection signature.
    """
    if not body:
        return False
    body_lower = body.lower()
    paywall_markers = [
        'subscribe to read',
        'subscribe to continue',
        'sign in to continue reading',
        'for subscribers only',
        'create a free account to continue',
        'become a subscriber',
        'already a subscriber',
        'unlimited access',
        'this article is for subscribers',
    ]
    return any(marker in body_lower for marker in paywall_markers)


def _clean_url_slug(url):
    """Last-resort title fallback. Walks back through URL path segments
    looking for one with readable content (not just a GUID or numeric ID).
    """
    try:
        from urllib.parse import urlparse as _up
        path = _up(url).path
        segments = [s for s in path.rstrip('/').split('/') if s]
        if not segments:
            return ''
        for raw_segment in reversed(segments):
            seg = raw_segment.split('?')[0].split('#')[0]
            seg = seg.replace('-', ' ').replace('_', ' ')
            seg = _re_slug.sub(
                r'[0-9a-f]{8}[\s-]?[0-9a-f]{4}[\s-]?[0-9a-f]{4}[\s-]?[0-9a-f]{4}[\s-]?[0-9a-f]{12}',
                '', seg, flags=_re_slug.IGNORECASE
            )
            words = seg.split()
            while words and words[0].isdigit():
                words.pop(0)
            words = [w for w in words if not (len(w) >= 8 and all(c in '0123456789abcdefABCDEF' for c in w))]
            words = [w for w in words if not (w.isdigit() and len(w) <= 4)]
            if len(words) >= 2:
                return ' '.join(words[:12]).title().strip()
        return ''
    except Exception:
        return ''

def _try_direct_scrape(url):
    try:
        import requests as _rq; from bs4 import BeautifulSoup
        r = _rq.get(url, timeout=(8,15), headers={'User-Agent': _USER_AGENT})
        if r.status_code != 200: print(f"[direct] HTTP {r.status_code}"); return None
        soup = BeautifulSoup(r.text, 'html.parser')
        # Title fallback chain: <title> -> og:title -> twitter:title -> first <h1>
        title_tag = soup.find('title')
        title = title_tag.text.strip() if title_tag else ''
        if _is_bot_protection(title): print(f"[direct] Bot protection: '{title}'"); return None
        if not title:
            og = soup.find('meta', property='og:title')
            if og and og.get('content'):
                title = og['content'].strip()
        if not title:
            tw = soup.find('meta', attrs={'name': 'twitter:title'})
            if tw and tw.get('content'):
                title = tw['content'].strip()
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text().strip()
        body = ' '.join(p.get_text() for p in soup.find_all('p'))[:8000]
        if len(body) < 500: print(f"[direct] Too thin ({len(body)} chars)"); return None
        if _is_paywall(title, body): print(f"[direct] Paywall detected"); return None
        return {'title': title, 'body': body, 'method': 'direct'}
    except Exception as e: print(f"[direct] Failed: {e}"); return None

def _try_jina_reader(url):
    try:
        import requests as _rq
        headers = {'Accept': 'text/plain', 'X-Return-Format': 'markdown'}
        jk = os.getenv('JINA_API_KEY')
        if jk: headers['Authorization'] = f'Bearer {jk}'
        r = _rq.get(f'https://r.jina.ai/{url}', headers=headers, timeout=(10,25))
        if r.status_code != 200: print(f"[jina] HTTP {r.status_code}"); return None
        text = r.text
        if len(text) < 500: print(f"[jina] Too thin ({len(text)} chars)"); return None
        title = ''
        for line in text.split('\n')[:30]:
            if line.strip().startswith('Title:'):
                title = line.strip()[len('Title:'):].strip(); break
        body = text.split('Markdown Content:', 1)[1].strip() if 'Markdown Content:' in text else '\n'.join(text.split('\n')[5:]).strip()
        body = body[:8000]
        if _is_bot_protection(title): print(f"[jina] Bot protection: '{title}'"); return None
        if len(body) < 500: print(f"[jina] Body too thin ({len(body)} chars)"); return None
        if _is_paywall(title, body): print(f"[jina] Paywall detected"); return None
        # Title fallback chain: Jina Title: -> first markdown heading -> cleaned URL slug
        if not title:
            for line in body.split('\n')[:50]:
                stripped = line.strip()
                if stripped.startswith('# '):
                    title = stripped[2:].strip(); break
                if stripped.startswith('## '):
                    title = stripped[3:].strip(); break
        if not title:
            title = _clean_url_slug(url)
        return {'title': title, 'body': body, 'method': 'jina'}
    except Exception as e: print(f"[jina] Failed: {e}"); return None

def _try_web_search(url, anthropic_client):
    try:
        from urllib.parse import urlparse as _up
        domain = _up(url).netloc.replace('www.','')
        slug = url.rstrip('/').split('/')[-1]
        keywords = ' '.join(slug.replace('-',' ').split()[:8])
        query = (f"I need to analyze a news article from {domain}. URL: {url}\n"
                 f"Topic: {keywords}\n\nSearch OTHER outlets (Reuters, AP, NPR, BBC, etc.) and return:\n"
                 f"HEADLINE: [headline]\n\nCONTENT:\n[1500+ words of factual content]\n\n"
                 f"Do NOT return {domain} content — bot-protected.")
        msg = anthropic_client.messages.create(
            model='claude-sonnet-4-6', max_tokens=2500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{'role': 'user', 'content': query}])
        text = ''.join(b.text for b in msg.content if hasattr(b, 'text'))
        if not text or len(text) < 500: print(f"[web_search] Too thin"); return None
        title = ''
        for line in text.split('\n')[:10]:
            if line.startswith('HEADLINE:'): title = line[9:].strip(); break
        body = text.split('CONTENT:', 1)[1].strip() if 'CONTENT:' in text else text
        body = body[:8000]
        if _is_bot_protection(title): title = ''
        if len(body) < 500: return None
        if _is_paywall(title, body): print(f"[web_search] Paywall detected"); return None
        if not title:
            title = _clean_url_slug(url)
        return {'title': title, 'body': body, 'method': 'web_search'}
    except Exception as e: print(f"[web_search] Failed: {e}"); return None

def fetch_article_content(url, anthropic_client=None):
    result = _try_direct_scrape(url)
    if result: print(f"[fetch] direct: {len(result['body'])} chars"); return result
    result = _try_jina_reader(url)
    if result: print(f"[fetch] jina: {len(result['body'])} chars"); return result
    if anthropic_client:
        result = _try_web_search(url, anthropic_client)
        if result: print(f"[fetch] web_search: {len(result['body'])} chars"); return result
    try:
        import requests as _rq
        from bs4 import BeautifulSoup
        r = _rq.get(url, timeout=(8,15), headers={'User-Agent': _USER_AGENT})
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            probe_body = ' '.join(p.get_text() for p in soup.find_all('p'))[:8000]
            if _is_paywall('', probe_body):
                print(f"[fetch] Paywall confirmed for {url}")
                return {'status': 'paywall'}
    except Exception as e:
        print(f"[fetch] Paywall probe failed: {e}")
    print(f"[fetch] All methods failed for {url}"); return None

# ── Background Signal: prior verified claims related to current article ──

def get_background_signal(article_claim_texts, anthropic_client=None):
    """
    Return prior verified claims related to the current article's claims.

    DB-first: queries verified claims (supported/corroborated/plausible) using
    pg_trgm similarity against each of the current article's claim texts.
    Threshold 0.30. Filters to claims whose sources_used contains at least one
    independent-tier domain.

    Optional web search fallback (gated by BACKGROUND_SIGNAL_WEB_FALLBACK=1)
    runs only if DB returns fewer than 3 facts.

    Returns a list of {fact, source, source_url, relevance_tag} dicts, max 3.
    Returns [] if nothing meets the threshold.
    """
    if not article_claim_texts:
        return []

    claims_for_query = [c for c in article_claim_texts if c and len(c) > 10][:5]
    if not claims_for_query:
        return []

    results = []

    # ── DB lookup via pg_trgm ──
    try:
        conn = get_db()
        cur = conn.cursor()

        sim_exprs = ', '.join(['similarity(c.claim_text, %s)'] * len(claims_for_query))
        sql = (
            "SELECT c.claim_text, c.verdict_summary, c.sources_used, "
            f"GREATEST({sim_exprs}) AS sim_score "
            "FROM claims c "
            "WHERE c.verdict IN ('supported', 'corroborated', 'plausible') "
            "  AND c.verdict_summary IS NOT NULL "
            "  AND c.sources_used IS NOT NULL "
            f"  AND GREATEST({sim_exprs}) >= 0.30 "
            "ORDER BY sim_score DESC "
            "LIMIT 20"
        )
        params = list(claims_for_query) + list(claims_for_query)
        cur.execute(sql, params)
        candidates = cur.fetchall()
        conn.close()
    except Exception as e:
        print(f"[bg_signal] DB lookup failed: {e}")
        candidates = []

    for claim_text, summary, sources, sim in candidates:
        if not sources:
            continue
        sources_lower = sources.lower()
        if not any(domain in sources_lower for domain in INDEPENDENT_SOURCES):
            continue

        relevance_tag = 'related claim'
        for atext in claims_for_query:
            common_words = set(claim_text.lower().split()) & set(atext.lower().split())
            common_words = {w.strip('.,;:"\'()[]') for w in common_words if len(w) > 4}
            common_words = {w for w in common_words if w and not w.isdigit()}
            if common_words:
                relevance_tag = ', '.join(sorted(common_words)[:3])
                break

        results.append({
            'fact': (summary or '')[:300],
            'source': (sources or '')[:200],
            'source_url': '',
            'relevance_tag': relevance_tag,
            'similarity': round(float(sim), 3)
        })
        if len(results) >= 3:
            break

    print(f"[bg_signal] DB returned {len(results)} fact(s) above threshold 0.30")

    # ── Web search fallback (env-gated) ──
    if len(results) < 3 and os.getenv('BACKGROUND_SIGNAL_WEB_FALLBACK') == '1' and anthropic_client:
        needed = 3 - len(results)
        try:
            claim_summary = ' / '.join(claims_for_query[:3])[:500]
            prompt = (
                f"Find {needed} prior factual claims related to the topics in these article claims:\n"
                f"{claim_summary}\n\n"
                "REQUIREMENTS - strict:\n"
                "(a) Each fact must be verifiable through reputable independent sources only: "
                "Reuters, AP, NPR, BBC, NYT, Washington Post, Guardian, Lawfare, Brookings, "
                "government primary sources, peer-reviewed academic sources, or Wikipedia for settled facts.\n"
                "(b) Each fact must be a discrete factual statement, not analysis or interpretation.\n"
                "(c) Each fact must be directly relevant to the article's topic, not background filler.\n"
                f"(d) If you cannot find {needed} facts meeting ALL criteria, return fewer or none. "
                "Never fill with weaker sources.\n\n"
                "Return ONLY valid JSON - an array of objects, no prose:\n"
                '[{"fact": "...", "source": "...", "source_url": "...", "relevance_tag": "..."}]'
            )
            msg = anthropic_client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=1000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{'role': 'user', 'content': prompt}]
            )
            text = ''.join(b.text for b in msg.content if hasattr(b, 'text'))

            import json as _json
            json_start = text.find('[')
            json_end = text.rfind(']') + 1
            if json_start >= 0 and json_end > json_start:
                fallback_facts = _json.loads(text[json_start:json_end])
                added = 0
                for f in fallback_facts[:needed]:
                    if isinstance(f, dict) and f.get('fact'):
                        results.append({
                            'fact': str(f.get('fact', ''))[:300],
                            'source': str(f.get('source', ''))[:200],
                            'source_url': str(f.get('source_url', ''))[:300],
                            'relevance_tag': str(f.get('relevance_tag', 'related topic'))[:100],
                            'similarity': None
                        })
                        added += 1
                print(f"[bg_signal] Web fallback added {added} fact(s) (total now {len(results)})")
        except Exception as e:
            print(f"[bg_signal] Web fallback failed: {e}")

    return results[:3]

# ─────────────────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def homepage():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'index.html')
def homepage_old():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Verum Signal — Media Reliability Engine</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #0a0a12; color: #f4f4f7; font-family: 'DM Sans', system-ui, sans-serif; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .wrap { max-width: 560px; padding: 48px 32px; text-align: center; }
    .logo { display: inline-flex; align-items: center; gap: 10px; margin-bottom: 40px; }
    .logo svg { display: block; }
    .logo-word { font-size: 20px; font-weight: 600; letter-spacing: 0.04em; color: #f4f4f7; }
    .logo-signal { font-family: Georgia, serif; font-style: italic; font-weight: 400; color: #c084fc; margin-left: 5px; }
    h1 { font-family: Georgia, serif; font-size: 32px; line-height: 1.25; margin-bottom: 16px; letter-spacing: -0.01em; }
    p { font-size: 15px; color: #8a8aa0; line-height: 1.65; margin-bottom: 32px; }
    .form { display: flex; gap: 8px; margin-bottom: 16px; }
    input { flex: 1; background: #12121e; border: 1px solid #1f1f30; border-radius: 8px; padding: 12px 16px; color: #f4f4f7; font-size: 14px; font-family: inherit; outline: none; transition: border-color .15s; }
    input:focus { border-color: #a855f7; }
    input::placeholder { color: #5a5a70; }
    button { background: #a855f7; border: none; border-radius: 8px; padding: 12px 20px; color: #fff; font-size: 14px; font-weight: 500; font-family: inherit; cursor: pointer; white-space: nowrap; transition: background .15s; }
    button:hover { background: #9333ea; }
    .links { display: flex; justify-content: center; gap: 24px; }
    .links a { font-size: 13px; color: #5a5a70; text-decoration: none; transition: color .15s; }
    .links a:hover { color: #c084fc; }
    .tagline { font-size: 12px; color: #5a5a70; margin-top: 40px; letter-spacing: 0.05em; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="logo">
      <svg width="40" height="28" viewBox="0 0 54 40" fill="none">
        <path d="M3 20 Q 11 4, 18 20 T 33 20" stroke="#a855f7" stroke-width="3.2" fill="none" stroke-linecap="round"/>
        <circle cx="37" cy="18" r="4.2" fill="#ec4899"/>
      </svg>
      <span class="logo-word">VERUM<span class="logo-signal">SIGNAL</span></span>
    </div>
    <h1>We provide the signals.<br>You decide.</h1>
    <p>An automated media reliability engine. We extract factual claims from news articles, verify them against independent sources, and publish outlet reliability scores.</p>
    <div class="form">
      <input type="url" id="url-input" placeholder="Paste an article URL to analyse..." />
      <button onclick="analyse()">Analyse</button>
    </div>
    <div class="links">
      <a href="/methodology">Methodology</a>
      <a href="/api/stats">Stats</a>
      <a href="/api/health">Status</a>
    </div>
    <div class="tagline">METHODOLOGY v1.6 &nbsp;·&nbsp; AUTOMATED &nbsp;·&nbsp; AUDITABLE</div>
  </div>
  <script>
    function analyse() {
      const url = document.getElementById('url-input').value.trim();
      if (!url) return;
      window.location.href = '/report?url=' + encodeURIComponent(url);
    }
    document.getElementById('url-input').addEventListener('keydown', function(e) {
      if (e.key === 'Enter') analyse();
    });
  </script>
</body>
</html>""", 200, {'Content-Type': 'text/html'}


@app.route('/api/report-status', methods=['GET'])
def report_status():
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'status': 'error'})
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT a.id FROM articles a WHERE a.url = %s LIMIT 1", (url,))
        article = cur.fetchone()
        if article:
            cur.execute("SELECT COUNT(*) FROM claims WHERE article_id = %s AND verdict IS NOT NULL", (article[0],))
            count = cur.fetchone()[0]
            conn.close()
            if count > 0:
                return jsonify({'status': 'ready'})
        conn.close()
        return jsonify({'status': 'processing'})
    except:
        return jsonify({'status': 'processing'})
@app.route('/r/<hash_value>', methods=['GET'])
def short_report(hash_value):
    """Resolve a short URL hash to an article and render its report."""
    # Validate format before hitting the DB (cheap defense against scanning)
    if len(hash_value) != _HASH_LENGTH or not all(c in _HASH_ALPHABET for c in hash_value):
        return redirect('/')
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT a.url FROM report_links r "
            "JOIN articles a ON a.id = r.article_id "
            "WHERE r.hash = %s LIMIT 1",
            (hash_value,),
        )
        row = cur.fetchone()
        if not row:
            return redirect('/')
        article_url = row[0]
    finally:
        cur.close()
        conn.close()
    # Delegate to existing /report?url=... flow
    # (preserves async loading, error handling, etc.)
    from urllib.parse import urlencode
    return redirect('/report?' + urlencode({'url': article_url}))



@app.route('/report', methods=['GET'])


def report_page():
    url = request.args.get('url', '').strip()
    if not url:
        return redirect('/')
    # Phase 4: depth-aware verification. ?depth=2 (free) or ?depth=99 (paid).
    # Default = None (full verification, current behavior).
    try:
        depth = int(request.args.get('depth')) if request.args.get('depth') else None
    except (ValueError, TypeError):
        depth = None

    from urllib.parse import urlparse
    from datetime import datetime as dt
    if not url.startswith('http://') and not url.startswith('https://'):
        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Verum Signal</title><style>body{{background:#080810;color:#f0f0f8;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}}.w{{text-align:center;padding:48px}}.logo{{color:#a855f7;font-size:22px;margin-bottom:24px}}a{{color:#a855f7}}</style></head><body><div class="w"><div class="logo">Verum Signal</div><h2>Invalid URL</h2><p style="color:rgba(240,240,248,.55);margin:16px 0 24px">Please paste a valid article URL starting with https://</p><a href="/">&#8592; Back to search</a></div></body></html>""", 400, {{'Content-Type': 'text/html'}}
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT a.id, a.title, a.source_name, a.url, a.claims_verified, a.verified_at FROM articles a WHERE a.url = %s LIMIT 1", (url,))
        article = cur.fetchone()
        # Skip fuzzy match — go straight to on-demand extraction if exact URL not found
        if not article:
            # Check if this is a first visit - show loading page and process in background
            is_async = request.args.get('_async') == '1'
            if not is_async:
                # Return loading page immediately, JS will poll for completion
                depth_param = ('&depth=' + str(depth)) if depth is not None else ''
                loading_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verum Signal &mdash; Analysing...</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#080810;color:#e8e8f0;font-family:'DM Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.wrap{{max-width:540px;width:100%;text-align:center}}
.topbar{{display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:48px}}
.brand{{font-size:11px;letter-spacing:0.2em;font-weight:700;color:#fff}}
.brand em{{color:#e879f9;font-style:italic;font-weight:400}}
.card{{background:rgba(255,255,255,0.03);border:0.5px solid rgba(168,85,247,0.2);border-radius:12px;padding:36px}}
.pulse{{width:48px;height:48px;border-radius:50%;background:rgba(168,85,247,0.2);border:2px solid #a855f7;margin:0 auto 24px;animation:pulse 1.5s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{transform:scale(1);opacity:1}}50%{{transform:scale(1.15);opacity:0.6}}}}
.heading{{font-size:20px;font-weight:600;color:#fff;margin-bottom:12px}}
.msg{{font-size:14px;color:rgba(232,232,240,0.55);line-height:1.65;margin-bottom:24px}}
.steps{{text-align:left;display:flex;flex-direction:column;gap:8px;margin-bottom:24px}}
.step{{display:flex;align-items:center;gap:10px;font-size:13px;color:rgba(232,232,240,0.4);padding:8px 12px;border-radius:6px;background:rgba(255,255,255,0.02)}}
.step.active{{color:rgba(232,232,240,0.8);background:rgba(168,85,247,0.08);border:0.5px solid rgba(168,85,247,0.2)}}
.step-dot{{width:6px;height:6px;border-radius:50%;background:rgba(168,85,247,0.3);flex-shrink:0}}
.step.active .step-dot{{background:#a855f7;box-shadow:0 0 6px #a855f7}}
.url-disp{{font-family:monospace;font-size:10px;color:rgba(255,255,255,0.18);word-break:break-all;margin-top:8px}}
.back{{display:inline-block;margin-top:20px;font-family:monospace;font-size:11px;color:rgba(168,85,247,0.5)}}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <svg width="32" height="22" viewBox="0 0 54 40" fill="none"><path d="M3 20 Q 11 4 18 20 T 33 20" stroke="#a855f7" stroke-width="3.2" fill="none" stroke-linecap="round"/><circle cx="37" cy="18" r="4.2" fill="#e879f9"/></svg>
    <span style="font-weight:700;color:#fff;letter-spacing:0.15em;font-size:13px;">VERUM</span> <em style="font-weight:400;color:#c084fc;font-style:italic;letter-spacing:0.15em;font-size:13px;">SIGNAL</em>
  </div>
  <div class="card">
    <div class="pulse"></div>
    <div class="heading">Analysing this article</div>
    <div class="msg">This is a fresh article. Our pipeline is retrieving content, extracting claims, and verifying each one against independent sources.</div>
    <div class="steps">
      <div class="step active" id="step1"><div class="step-dot"></div>Retrieving article content</div>
      <div class="step" id="step2"><div class="step-dot"></div>Extracting factual claims</div>
      <div class="step" id="step3"><div class="step-dot"></div>Verifying claims against sources</div>
      <div class="step" id="step4"><div class="step-dot"></div>Generating analysis</div>
    </div>
    <div class="url-disp">{url[:80]}{'...' if len(url) > 80 else ''}</div>
    <a href="/" class="back">&#8592; Cancel</a>
  </div>
</div>
<script>
const steps = ['step1','step2','step3','step4'];
let current = 0;
let attempts = 0;
const maxAttempts = 40;
const encodedUrl = encodeURIComponent('{url}');
        const depthParam = '{depth_param}';

function advanceStep() {{
  if (current < steps.length - 1) {{
    document.getElementById(steps[current]).classList.remove('active');
    current++;
    document.getElementById(steps[current]).classList.add('active');
  }}
}}

function checkStatus() {{
  attempts++;
  if (attempts > maxAttempts) {{
    window.location.href = '/report?url=' + encodedUrl + '&_async=1' + depthParam;
    return;
  }}
  fetch('/api/report-status?url=' + encodedUrl + depthParam)
    .then(r => r.json())
    .then(data => {{
      if (data.status === 'ready') {{
        window.location.href = '/report?url=' + encodedUrl + '&_async=1' + depthParam;
      }} else {{
        if (attempts === 3) advanceStep();
        if (attempts === 8) advanceStep();
        if (attempts === 15) advanceStep();
        setTimeout(checkStatus, 3000);
      }}
    }})
    .catch(() => setTimeout(checkStatus, 3000));
}}

// Trigger background processing
fetch('/report?url=' + encodedUrl + '&_async=1' + depthParam);
setTimeout(checkStatus, 3000);
</script>
</body>
</html>"""
                return loading_html, 200, {'Content-Type': 'text/html'}
            # On-demand extraction — clean rewrite per Opus architecture brief
            import anthropic as _anth
            from extract_claims import extract_claims_from_article
            from verdict_engine import analyse_claim
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.replace('www.','')
            _anth_client = _anth.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
            data = None
            try:
                fetch_result = fetch_article_content(url, _anth_client)
                if fetch_result is None:
                    data = {'status': 'scrape_failed', 'url': url, 'domain': domain}
                elif fetch_result.get('status') == 'paywall':
                    data = {'status': 'paywall', 'url': url, 'source': domain}
                else:
                    title_text = fetch_result['title']
                    body_text = fetch_result['body']
                    extraction_method = fetch_result['method']
                    article_dict = {'title': title_text, 'description': body_text[:500], 'content': body_text, 'source': {'name': domain}, 'url': url, 'publishedAt': ''}
                    claims = extract_claims_from_article(article_dict)
                    if not claims:
                        data = {'status': 'no_claims', 'title': title_text, 'source': domain, 'url': url, 'extraction_method': extraction_method}
                    else:
                        conn2 = get_db()
                        cur2 = conn2.cursor()
                        cur2.execute("INSERT INTO articles (title, source_name, url, fetched_at, claims_verified) VALUES (%s, %s, %s, NOW(), FALSE) RETURNING id", (title_text, domain, url))
                        art_id = cur2.fetchone()[0]
                        verified_claims = verify_and_insert_claims(claims, art_id, title_text, domain, cur2, depth=depth)
                        conn2.commit()
                        conn2.close()
                        rows = verified_claims
                        sc = sum(1 for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                        ws = sum(WEIGHTS[c[5]] for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                        score = compute_score(ws, sc)
                        rating = compute_score_band(score)
                        data = {'status':'found','url':url,'title':title_text,'source':domain,'score':score,'rating':rating,'extraction_method':extraction_method,'as_of':dt.now().strftime('%B %d, %Y'),'methodology_callout':f"This article contained {len(rows)} claim{'s' if len(rows)!=1 else ''} assessed after extraction. {sum(1 for c in rows if c[5]=='supported')} supported, {sum(1 for c in rows if c[5] in ('overstated','disputed','not_supported'))} flagged.",'stats':{'supported':sum(1 for c in rows if c[5]=='supported'),'plausible':sum(1 for c in rows if c[5]=='plausible'),'corroborated':sum(1 for c in rows if c[5]=='corroborated'),'overstated':sum(1 for c in rows if c[5]=='overstated'),'disputed':sum(1 for c in rows if c[5]=='disputed'),'not_supported':sum(1 for c in rows if c[5]=='not_supported'),'opinion':sum(1 for c in rows if c[5]=='opinion'),'total':len(rows)},'claims':[{'id':c[0],'claim_text':c[1],'speaker':c[2],'claim_type':c[3],'claim_origin':c[4],'verdict':c[5],'confidence_score':c[6],'verdict_summary':c[7],'full_analysis':c[8],'sources_used':c[9]} for c in rows]}
            except Exception as e:
                import traceback
                print(f"[report_page] Unexpected error: {e}")
                print(traceback.format_exc())
                data = {'status': 'error', 'message': str(e), 'url': url}
            finally:
                try:
                    conn.close()
                except:
                    pass
        else:
            art_id, title_db, source_name, art_url, cv, vat = article
            cur.execute("SELECT id, claim_text, speaker, claim_type, claim_origin, verdict, confidence_score, verdict_summary, full_analysis, sources_used FROM claims WHERE article_id = %s ORDER BY priority_score DESC", (art_id,))
            rows = cur.fetchall()
            conn.close()
            if not rows:
                # Trigger on-demand extraction for articles in DB but not yet extracted
                try:
                    import requests as _req
                    from bs4 import BeautifulSoup
                    from extract_claims import extract_claims_from_article
                    from verdict_engine import analyse_claim
                    _r = _req.get(art_url, timeout=(8,15), headers={'User-Agent': 'Mozilla/5.0'})
                    soup = BeautifulSoup(_r.text, 'html.parser')
                    body_text = ' '.join(p.get_text() for p in soup.find_all('p'))[:8000]
                    article_dict = {'title': title_db, 'description': body_text[:500], 'content': body_text, 'source': {'name': source_name}, 'url': art_url, 'publishedAt': ''}
                    claims = extract_claims_from_article(article_dict)
                    if not claims:
                        data = {'status': 'no_claims', 'title': title_db, 'source': source_name}
                    else:
                        conn2 = get_db()
                        cur2 = conn2.cursor()
                        verified_claims = []
                        verified_claims = verify_and_insert_claims(claims, art_id, title_db, source_name, cur2, depth=depth)
                        cur2.execute("UPDATE articles SET claims_verified=TRUE WHERE id=%s", (art_id,))
                        conn2.commit()
                        conn2.close()
                        rows = verified_claims
                        sc = sum(1 for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                        ws = sum(WEIGHTS[c[5]] for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                        score = compute_score(ws, sc)
                        rating = compute_score_band(score)
                        data = {'status':'found','url':art_url,'title':title_db,'source':source_name,'score':score,'rating':rating,'as_of':dt.now().strftime('%B %d, %Y'),'methodology_callout':f"This article contained {len(rows)} claim{'s' if len(rows)!=1 else ''} assessed after extraction. {sum(1 for c in rows if c[5]=='supported')} supported, {sum(1 for c in rows if c[5] in ('overstated','disputed','not_supported'))} flagged.",'stats':{'supported':sum(1 for c in rows if c[5]=='supported'),'plausible':sum(1 for c in rows if c[5]=='plausible'),'corroborated':sum(1 for c in rows if c[5]=='corroborated'),'overstated':sum(1 for c in rows if c[5]=='overstated'),'disputed':sum(1 for c in rows if c[5]=='disputed'),'not_supported':sum(1 for c in rows if c[5]=='not_supported'),'opinion':sum(1 for c in rows if c[5]=='opinion'),'total':len(rows)},'claims':[{'id':c[0],'claim_text':c[1],'speaker':c[2],'claim_type':c[3],'claim_origin':c[4],'verdict':c[5],'confidence_score':c[6],'verdict_summary':c[7],'full_analysis':c[8],'sources_used':c[9]} for c in rows]}
                except Exception as e:
                    print(f"On-demand extraction (no_claims path) failed: {e}")
                    data = {'status': 'no_claims', 'title': title_db, 'source': source_name}
            else:
                sc = sum(1 for c in rows if c[5] in WEIGHTS)
                ws = sum(WEIGHTS[c[5]] for c in rows if c[5] in WEIGHTS)
                score = compute_score(ws, sc)
                rating = compute_score_band(score)
                scoreable_labels = {'supported':'supported by independent sources','plausible':'consistent with one credible source','corroborated':'corroborated by 5+ outlets','overstated':'overstated relative to evidence','disputed':'disputed by a credible source','not_supported':'actively contradicted by evidence'}
                parts = []
                for vk, lbl in scoreable_labels.items():
                    cnt = sum(1 for c in rows if c[5] == vk)
                    if cnt:
                        parts.append(f"{cnt} {'was' if cnt == 1 else 'were'} {lbl}")
                wire_count = sum(1 for c in rows if c[4] == 'wire_reprint')
                quote_count = sum(1 for c in rows if c[4] == 'accurate_quote')
                excl = []
                if wire_count: excl.append(f"{wire_count} wire reprint{'s' if wire_count>1 else ''} excluded from outlet score")
                if quote_count: excl.append(f"{quote_count} accurately reported quote{'s' if quote_count>1 else ''} excluded from outlet score")
                n_supported = sum(1 for c in rows if c[5] == 'supported')
                n_corroborated = sum(1 for c in rows if c[5] == 'corroborated')
                n_plausible = sum(1 for c in rows if c[5] == 'plausible')
                n_flagged = sum(1 for c in rows if c[5] in ('overstated', 'disputed', 'not_supported'))
                bucket_parts = []
                if n_supported: bucket_parts.append(f"{n_supported} supported")
                if n_corroborated: bucket_parts.append(f"{n_corroborated} corroborated")
                if n_plausible: bucket_parts.append(f"{n_plausible} plausible")
                if n_flagged: bucket_parts.append(f"{n_flagged} flagged")
                bucket_summary = ", ".join(bucket_parts) + ". " if bucket_parts else ""
                callout_text = f"This article contained {len(rows)} claim{'s' if len(rows)!=1 else ''} assessed after extraction. {bucket_summary}"
                if parts:
                    callout_text += (', '.join(parts[:-1]) + f", and {parts[-1]}. ") if len(parts)>1 else (parts[0] + ". ")
                if excl:
                    callout_text += ' '.join(excl) + ". "
                callout_text += "The independence rule and wire-service exclusion were applied where relevant."
                data = {'status':'found','url':art_url,'title':title_db,'source':source_name,'score':score,'rating':rating,'as_of':dt.now().strftime('%B %d, %Y'),'methodology_callout':callout_text,'stats':{'supported':sum(1 for c in rows if c[5]=='supported'),'plausible':sum(1 for c in rows if c[5]=='plausible'),'corroborated':sum(1 for c in rows if c[5]=='corroborated'),'overstated':sum(1 for c in rows if c[5]=='overstated'),'disputed':sum(1 for c in rows if c[5]=='disputed'),'not_supported':sum(1 for c in rows if c[5]=='not_supported'),'opinion':sum(1 for c in rows if c[5]=='opinion'),'total':len(rows)},'claims':[{'id':c[0],'claim_text':c[1],'speaker':c[2],'claim_type':c[3],'claim_origin':c[4],'verdict':c[5],'confidence_score':c[6],'verdict_summary':c[7],'full_analysis':c[8],'sources_used':c[9]} for c in rows]}
    except Exception as e:
        data = {'status':'error','message':str(e)}

    status = data.get('status', 'error')

    # ── Not found ──
    if status in ('not_found', 'no_claims', 'error', 'paywall', 'scrape_failed'):
        STATUS_INFO = {
            'not_found': {
                'icon': '\u231b',
                'heading': 'Not yet analysed',
                'msg': "This article hasn\u2019t been processed by our pipeline yet.",
                'detail': 'Our scheduler ingests 40+ outlets every 3 hours. If this outlet is tracked, the article will appear automatically. You can also try submitting a different URL from the same story.',
            },
            'no_claims': {
                'icon': '\u26a0\ufe0f',
                'heading': 'No scoreable claims found',
                'msg': 'We retrieved this article but could not extract verifiable factual claims from it.',
                'detail': 'This happens when an article is primarily opinion, uses JavaScript rendering our scraper cannot access, or is behind a paywall. Try a different article from a text-heavy news report, or choose one of our tracked outlets: Fox News, CNN, BBC, NPR, The Guardian, or Politico.',
            },
            'paywall': {
                'icon': '\U0001f512',
                'heading': 'Paywall detected',
                'msg': 'This article appears to be behind a paywall or login wall.',
                'detail': 'Verum Signal can only analyse publicly accessible articles. Try a free article from this outlet, or choose a different source.',
            },
            'scrape_failed': {
                'icon': '\U0001f6ab',
                'heading': 'Article could not be retrieved',
                'msg': 'We were unable to access this article.',
                'detail': 'Some outlets block automated access. Try one of our tracked outlets: Fox News, CNN, BBC, NPR, The Guardian, Politico, or The Hill.',
            },
            'error': {
                'icon': '\u26a0\ufe0f',
                'heading': 'Something went wrong',
                'msg': data.get('message', 'An unexpected error occurred.'),
                'detail': 'Please try again. If the problem persists, the article may be temporarily unavailable.',
            }
        }
        info = STATUS_INFO.get(status, STATUS_INFO['error'])
        url_display = url[:80] + ('...' if len(url) > 80 else '')
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verum Signal &mdash; Report Unavailable</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#080810;color:#e8e8f0;font-family:'DM Sans',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.wrap{{max-width:540px;width:100%}}
.topbar{{display:flex;align-items:center;gap:8px;margin-bottom:48px}}
.brand{{font-size:11px;letter-spacing:0.2em;font-weight:700;color:#fff}}
.brand em{{color:#e879f9;font-style:italic;font-weight:400}}
.card{{background:rgba(255,255,255,0.03);border:0.5px solid rgba(168,85,247,0.2);border-radius:12px;padding:36px;text-align:center}}
.icon{{font-size:36px;margin-bottom:20px}}
.heading{{font-size:22px;font-weight:600;color:#fff;margin-bottom:12px}}
.msg{{font-size:15px;color:rgba(232,232,240,0.7);line-height:1.65;margin-bottom:16px}}
.detail{{font-size:13px;color:rgba(232,232,240,0.5);line-height:1.65;margin-bottom:28px;padding:12px 16px;background:rgba(168,85,247,0.05);border:0.5px solid rgba(168,85,247,0.15);border-radius:6px;text-align:left}}
.btn{{display:inline-block;padding:10px 24px;background:rgba(168,85,247,0.1);border:0.5px solid rgba(168,85,247,0.3);border-radius:6px;color:#a855f7;font-size:13px;font-family:monospace;letter-spacing:0.04em;text-decoration:none}}
.btn:hover{{background:rgba(168,85,247,0.2)}}
.url-disp{{font-family:monospace;font-size:10px;color:rgba(255,255,255,0.18);margin-top:20px;word-break:break-all}}
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <svg width="32" height="22" viewBox="0 0 54 40" fill="none"><path d="M3 20 Q 11 4 18 20 T 33 20" stroke="#a855f7" stroke-width="3.2" fill="none" stroke-linecap="round"/><circle cx="37" cy="18" r="4.2" fill="#e879f9"/></svg>
    <span style="font-weight:700;color:#fff;letter-spacing:0.15em;font-size:13px;">VERUM</span> <em style="font-weight:400;color:#c084fc;font-style:italic;letter-spacing:0.15em;font-size:13px;">SIGNAL</em>
  </div>
  <div class="card">
    <div class="icon">{info['icon']}</div>
    <div class="heading">{info['heading']}</div>
    <div class="msg">{info['msg']}</div>
    <div class="detail">{info['detail']}</div>
    <a href="/" class="btn">&#8592; Try a different article</a>
    <div class="url-disp">{url_display}</div>
  </div>
</div>
</body>
</html>""", 404 if status == 'not_found' else 200, {'Content-Type': 'text/html'}
    # ── Build verdict pills ──
    stats   = data.get('stats', {})
    claims  = data.get('claims', [])
    title   = data.get('title', 'Article Report')
    source  = data.get('source', '')
    score   = data.get('score', 0)
    # Patch 1: coerce None → 0 for rendering math. The 'Unscored' rating
    # comes through 'rating' below.
    _score_was_none = (score is None)
    if score is None:
        score = 0
    rating  = data.get('rating', 'Unscored')
    as_of   = data.get('as_of', '')
    methodology_callout = data.get('methodology_callout', 'Each factual claim passes through a three-step pipeline: cache check, internal consensus check, then web search.')
    url = data.get('url', '')
    sc = sum(1 for c in claims if c.get('verdict') in ('supported','plausible','corroborated','overstated','disputed','not_supported'))
    supported_n = stats.get('supported', 0)
    overstated_n = stats.get('overstated', 0)
    disputed_n = stats.get('disputed', 0)
    not_supported_n = stats.get('not_supported', 0)
    total_n = stats.get('total', 0)
    if rating == 'High':
        overall_signal = f"This article scores in the High tier. The factual claims assessed were well-sourced and confirmed by independent reporting. Verdicts reflect the evidence available at time of analysis."
    elif rating == 'Medium':
        overall_signal = f"This article scores in the Medium tier. Of {total_n} claims assessed, {supported_n} were supported by independent sources. {overstated_n + disputed_n + not_supported_n} claim(s) showed evidence of overstatement or factual dispute."
    else:
        overall_signal = f"This article scores in the Low tier. Multiple claims showed signs of overstatement or direct contradiction by independent sources. Readers should consult additional sources before drawing conclusions." 

    VERDICT_COLOR = {
        'supported':    ('#4ade80', 'rgba(74,222,128,0.12)',  'rgba(74,222,128,0.3)'),
        'plausible':    ('#fbbf24', 'rgba(251,191,36,0.12)',  'rgba(251,191,36,0.3)'),
        'corroborated': ('#60a5fa', 'rgba(96,165,250,0.12)',  'rgba(96,165,250,0.3)'),
        'overstated':   ('#fb923c', 'rgba(251,146,60,0.10)',  'rgba(251,146,60,0.3)'),
        'disputed':     ('#f43f5e', 'rgba(244,63,94,0.10)',   'rgba(244,63,94,0.3)'),
        'not_supported':('#f87171', 'rgba(248,113,113,0.12)', 'rgba(248,113,113,0.3)'),
        'not_verifiable':('#6b7280','rgba(107,114,128,0.10)', 'rgba(107,114,128,0.3)'),
        'opinion':      ('#6b7280', 'rgba(107,114,128,0.10)', 'rgba(107,114,128,0.3)'),
    }
    VERDICT_LABEL = {
        'supported': 'Supported', 'plausible': 'Plausible',
        'corroborated': 'Corroborated', 'overstated': 'Overstated',
        'disputed': 'Disputed', 'not_supported': 'Not supported',
        'not_verifiable': 'Not verifiable', 'opinion': 'Opinion',
    }
    VERDICT_WEIGHT = {
        'supported': '+1.0', 'plausible': '+0.5', 'corroborated': '+0.5',
        'overstated': '\u22120.5', 'disputed': '\u22121.0', 'not_supported': '\u22121.5',
        'not_verifiable': 'excluded', 'opinion': 'excluded',
    }

    if score is None:
        score_color = 'rgba(255,255,255,0.3)'
    elif score >= 70:
        score_color = '#4ade80'
    elif score >= 40:
        score_color = '#fbbf24'
    else:
        score_color = '#f87171'

    def pill(verdict, count):
        if count == 0:
            return ''
        c = VERDICT_COLOR.get(verdict, ('#aaa','rgba(170,170,170,0.1)','rgba(170,170,170,0.3)'))
        lbl = VERDICT_LABEL.get(verdict, verdict)
        return (f'<span style="display:inline-flex;align-items:center;gap:6px;padding:5px 12px;'
                f'border-radius:100px;font-size:12px;font-weight:500;border:1px solid {c[2]};'
                f'background:{c[1]};color:{c[0]};white-space:nowrap;">'
                f'<span style="width:6px;height:6px;border-radius:50%;background:{c[0]};flex-shrink:0"></span>'
                f'{count} {lbl}</span>')

    pills_html = ''.join([
        pill('supported',     stats.get('supported', 0)),
        pill('plausible',     stats.get('plausible', 0)),
        pill('corroborated',  stats.get('corroborated', 0)),
        pill('overstated',    stats.get('overstated', 0)),
        pill('disputed',      stats.get('disputed', 0)),
        pill('not_supported', stats.get('not_supported', 0)),
        pill('opinion',       stats.get('opinion', 0)),
    ])
    total_pill = (f'<span style="display:inline-flex;align-items:center;gap:6px;padding:5px 12px;'
                  f'border-radius:100px;font-size:12px;font-weight:500;border:1px solid rgba(255,255,255,0.12);'
                  f'background:rgba(255,255,255,0.05);color:#f0f0f8;white-space:nowrap;">'
                  f'<span style="width:6px;height:6px;border-radius:50%;background:rgba(240,240,248,0.4);flex-shrink:0"></span>'
                  f'{stats.get("total",0)} total</span>')

    def smartquotes(text):
        if not text:
            return text
        import re
        text = re.sub(r'"(?=\w)', '\u201c', text)
        text = re.sub(r'"', '\u201d', text)
        text = re.sub(r"'(?=\w)", '\u2018', text)
        text = re.sub(r"'", '\u2019', text)
        return text

    def claim_row(c, idx):

        v = c.get('verdict') or 'not_verifiable'
        VBAR = {'supported':'#4ade80','plausible':'#60a5fa','corroborated':'#34d399','overstated':'#fb923c','disputed':'#f87171','not_supported':'#ef4444','opinion':'rgba(255,255,255,0.1)','not_verifiable':'rgba(255,255,255,0.1)'}
        VPILL = {'supported':'p-sup','plausible':'p-pla','corroborated':'p-cor','overstated':'p-ove','disputed':'p-dis','not_supported':'p-nsu','opinion':'p-opi','not_verifiable':'p-nve'}
        VLBL = {'supported':'SUPPORTED','plausible':'PLAUSIBLE','corroborated':'CORROBORATED','overstated':'OVERSTATED','disputed':'DISPUTED','not_supported':'NOT SUPPORTED','opinion':'OPINION','not_verifiable':'NOT VERIFIABLE'}
        CONF_EXPLAIN = {1:'One source found — claim is plausible but not independently confirmed.', 2:'One strong primary source confirmed this claim.', 3:'Two or more genuinely independent sources confirmed this claim.'}
        bar_col = VBAR.get(v, 'rgba(255,255,255,0.1)')
        pill_cls = VPILL.get(v, 'p-opi')
        lbl = VLBL.get(v, v.upper())
        text = smartquotes(c.get('claim_text', ''))
        summary = c.get('verdict_summary', '') or ''
        full = c.get('full_analysis', '') or ''
        sources = c.get('sources_used', '') or ''
        claim_type = c.get('claim_type', 'factual') or 'factual'
        confidence = int(c.get('confidence_score', 2) or 2)
        origin = c.get('claim_origin', 'outlet_claim') or 'outlet_claim'
        is_wire = origin == 'wire_reprint'
        # Confidence dots
        conf_dots = ''
        for d in range(3):
            cls = 'vs-conf-on' if d < confidence else 'vs-conf-off'
            conf_dots += '<span class="vs-conf-dot ' + cls + '"></span>'
        conf_label = {1: "Confidence: 1/3 — one source found", 2: "Confidence: 2/3 — two sources found", 3: "Confidence: 3/3 — supported by multiple independent sources"}.get(confidence, "")
        conf_num_html = '<span class="vs-conf-num">' + str(confidence) + '/3</span>'
        conf_html = '<div class="vs-conf" title="' + conf_label + '">' + conf_num_html + conf_dots + '</div>'
        # Wire tag
        wire_html = '<span class="vs-wire">WIRE REPRINT &mdash; excluded from score</span>' if is_wire else ''
        # Source pills
        valid_tlds3 = {'com','org','gov','edu','net','io','co','uk','de','fr'}
        src_domains = []
        for word in sources.replace(',',' ').split():
            w = word.strip('().-').lower()
            parts = w.split('.')
            if len(parts) >= 2 and parts[-1] in valid_tlds3 and len(parts[0]) > 1:
                src_domains.append(w)
        contradicts = v in ('disputed','not_supported')
        src_pills = ''
        for d in src_domains[:6]:
            cls = 'vs-src-c' if contradicts else 'vs-src-p'
            src_pills += '<a href="https://' + d + '" target="_blank" rel="noopener noreferrer" class="vs-src ' + cls + '">' + d + '</a>'
        src_pills_html = '<div class="vs-src-pills">' + src_pills + '</div>' if src_pills else ''
        conf_exp = CONF_EXPLAIN.get(confidence, '')
        detail_body = full if full and len(full) > len(summary) else sources
        return (
            '<div class="vs-claim">'
            '<div class="vs-claim-header">'
            '<div class="vs-claim-bar" style="background:' + bar_col + ';min-height:48px;"></div>'
            '<div class="vs-claim-main">'
            '<div class="vs-claim-top">'
            '<span class="vs-claim-num">CLAIM ' + str(idx) + '</span>'
            + wire_html +
            '</div>'
            '<div class="vs-claim-quote">' + text + '</div>'
            '<div class="vs-claim-brief">' + summary + '</div>'
            '</div>'
            '<div class="vs-claim-right">'
            '<div class="vs-pill ' + pill_cls + '">' + lbl + '</div>'
            + conf_html +
            '</div>'
            '<span class="vs-toggle">Show details ▾</span>'
            '</div>'
            '<div class="vs-claim-body">'
            '<div class="vs-detail-grid">'
            '<div><div class="vs-detail-label">Full Analysis</div><div class="vs-detail-val">' + (full or summary) + '</div></div>'
            '<div><div class="vs-detail-label">Sources</div><div class="vs-detail-val">' + sources + '</div></div>'
            '</div>'
            '<div class="vs-conf-explain">' + conf_exp + '</div>'
            + src_pills_html +
            '</div>'
            '</div>'
        )

    claims_html = "".join(claim_row(c, i+1) for i, c in enumerate(claims))

    # Phase 5: simpler claim card for free report template
    def claim_row_free(c, idx):
        v = c.get('verdict') or 'not_verifiable'
        VLBL_FREE = {'supported':'SUPPORTED','plausible':'PLAUSIBLE','corroborated':'CORROBORATED','overstated':'OVERSTATED','disputed':'DISPUTED','not_supported':'NOT SUPPORTED','opinion':'OPINION','not_verifiable':'NOT VERIFIABLE'}
        text = smartquotes(c.get('claim_text', ''))
        sources = c.get('sources_used', '') or ''
        confidence = int(c.get('confidence_score', 2) or 2)
        lbl = VLBL_FREE.get(v, v.upper())
        return (
            '<div class="claim-card ' + v + '">'
            '<div class="claim-head">'
            '<div class="claim-num">CLAIM ' + str(idx) + '</div>'
            '<span class="verdict-pill ' + v + '">' + lbl + ' &middot; ' + str(confidence) + '/3</span>'
            '</div>'
            '<p class="claim-text">' + text + '</p>'
            '<div class="sources-label">SOURCES</div>'
            '<p class="sources-list">' + sources + '</p>'
            '</div>'
        )
    # Only show verified claims (verdict not None) in free report
    verified_only = [c for c in claims if c.get('verdict')]
    claims_html_free = "".join(claim_row_free(c, i+1) for i, c in enumerate(verified_only))

    score_bar_pct = score
    rating_color  = score_color

    verdict_bar_segs = ''
    total_sc = stats.get('total', 1) or 1
    for v in ['supported','plausible','corroborated','overstated','disputed','not_supported']:
        cnt = stats.get(v, 0)
        if cnt:
            pct = round(cnt / total_sc * 100)
            col = VERDICT_COLOR.get(v, ('#aaa','',''))[0]
            verdict_bar_segs += f'<div style="height:100%;width:{pct}%;background:{col};"></div>'

    # Build distribution bar and legend
    VCOLORS = {'supported':'#4ade80','plausible':'#fbbf24','corroborated':'#34d399','overstated':'#fbbf24','disputed':'#f87171','not_supported':'#ef4444','opinion':'rgba(255,255,255,0.1)','not_verifiable':'rgba(255,255,255,0.1)'}
    total_sc2 = stats.get('total',1) or 1
    dist_bar_html = ''
    dist_legend_html = ''
    for v, col in VCOLORS.items():
        cnt = stats.get(v,0)
        if cnt:
            pct = round(cnt/total_sc2*100)
            dist_bar_html += f'<div class="vs-dist-seg" style="flex:{cnt};background:{col};"></div>'
            lbl = v.replace('_',' ').title()
            dist_legend_html += f'<span class="vs-leg"><span class="vs-leg-dot" style="background:{col}"></span>{cnt} {lbl}</span>'

    # ── Background Signal: render block ──
    background_signal_html = ''
    if len(claims) >= 3:
        try:
            article_claim_texts = [c.get('claim_text', '') for c in claims if c.get('claim_text')]
            import anthropic as _bg_anth
            _bg_client = _bg_anth.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
            bg_facts = get_background_signal(article_claim_texts, _bg_client)
            if len(bg_facts) >= 3:
                _items = ''
                for f in bg_facts:
                    _src_url = (f.get('source_url') or '').strip()
                    _src_text = (f.get('source') or '').strip()[:80]
                    if _src_url:
                        _src_html = (
                            '<a href="' + _src_url + '" target="_blank" '
                            'style="color:rgba(168,85,247,0.7);text-decoration:none;">'
                            + _src_text + '</a>'
                        )
                    else:
                        _src_html = (
                            '<span style="color:rgba(255,255,255,0.4);">'
                            + _src_text + '</span>'
                        )
                    _items += (
                        '<div class="vs-bgsig-item" style="padding:14px 16px;'
                        'background:rgba(255,255,255,0.02);'
                        'border-left:2px solid rgba(168,85,247,0.4);'
                        'border-radius:4px;margin-bottom:10px;">'
                        '<div style="font-size:13px;color:rgba(232,232,240,0.85);'
                        'line-height:1.55;margin-bottom:8px;">'
                        '\u2192 ' + (f.get('fact') or '') + '</div>'
                        '<div style="font-size:10px;font-family:monospace;'
                        'color:rgba(255,255,255,0.4);">'
                        'SOURCE: ' + _src_html + ' &nbsp;\u00b7&nbsp; '
                        'RELEVANCE: ' + (f.get('relevance_tag') or '') + '</div>'
                        '</div>'
                    )
                background_signal_html = (
                    '<div class="vs-section" style="margin-top:32px;">'
                    '<div class="vs-section-label" '
                    'style="font-family:monospace;font-size:11px;'
                    'letter-spacing:0.15em;color:#c084fc;margin-bottom:6px;">'
                    'BACKGROUND SIGNAL</div>'
                    '<div style="font-size:11px;color:rgba(255,255,255,0.4);'
                    'margin-bottom:14px;font-style:italic;">'
                    'Related claims, previously assessed</div>'
                    + _items +
                    '</div>'
                )
                print('[bg_signal] Rendered ' + str(len(bg_facts)) + ' fact(s) in report')
        except Exception as e:
            print('[bg_signal] Render block failed: ' + str(e))
            background_signal_html = ''

    # Build compare perspectives
    COMPARE_OUTLETS = ['Fox News','NPR','Guardian','Politico','CNN','BBC']
    compare_html = ''
    for outlet in COMPARE_OUTLETS:
        if outlet.lower().replace(' ','') not in source.lower().replace(' ',''):
            search_url = 'https://www.google.com/search?q=' + title.replace(' ','+')[:60] + '+' + outlet.replace(' ','+')
            compare_html += f'<a href="{search_url}" target="_blank" class="vs-compare-btn">{outlet} ↗</a>'

    # Build all sources list
    # Build all sources list
    all_domains = set()
    for c in claims:
        src = c.get('sources_used','') or ''
        for word in src.replace(',',' ').split():
            if '.' in word and len(word) > 3:
                all_domains.add(word.strip('().-').lower())
    # Filter to real domains only (must have valid TLD)
    import re
    valid_tlds = {'com','org','gov','edu','net','io','co','uk','de','fr'}
    clean_domains = set()
    for d in all_domains:
        parts = d.split('.')
        if len(parts) >= 2 and parts[-1] in valid_tlds and len(parts[0]) > 1:
            clean_domains.add(d)
    # Exclude the publishing outlet from sources consulted (independence rule)
    _pub_root = source.replace('www.','').lower().split('/')[0]
    clean_domains = {d for d in clean_domains if not d.replace('www.','').endswith(_pub_root) and _pub_root not in d.replace('www.','')}
    # Type-code sources
    _GOV_TLD = {'gov'}
    def _src_class(d):
        d = d.replace('www.','').lower()
        if d.split('.')[-1] in _GOV_TLD or 'house.gov' in d or 'senate.gov' in d or 'congress.gov' in d:
            return 'vs-src-p'  # primary/green
        if d in WIRE_SOURCES:
            return 'vs-src-w'  # wire/gray
        if d in INDEPENDENT_SOURCES:
            return 'vs-src-i'  # independent/blue
        return 'vs-src-c'  # interested/amber
    if clean_domains:
        all_sources_html = ''.join('<a href="https://' + d + '" target="_blank" rel="noopener noreferrer" class="vs-src ' + _src_class(d) + '">' + d + '</a>' for d in sorted(clean_domains))
    else:
        all_sources_html = '<span style="font-family:monospace;font-size:11px;color:rgba(255,255,255,0.3);">No independent sources found — see Methodology v1.6 Section 5 (independence rule)</span>'


    # --- Defaults for generated content ---
    watch_for = []
    article_summary = ''
    overall_signal = ''

    # --- Claude-generated content ---
    try:
        import anthropic as _anth
        _client = _anth.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        _claims_parts = []
        for _i, _c in enumerate(claims):
            _claims_parts.append('Claim ' + str(_i+1) + ': "' + (_c.get('claim_text','') or '') + '" - Verdict: ' + ((_c.get('verdict','') or '')).upper() + ' - Reasoning: ' + (_c.get('verdict_summary','') or ''))
        _claims_text = chr(10).join(_claims_parts)
        _prompt = ('You are the editorial analysis layer for Verum Signal, an independent claim analysis platform governed by Methodology v1.6.' + chr(10) +
            'Your job is to produce three sections: article_summary, overall_signal, and watch_for.' + chr(10) +
            'CRITICAL RULES — follow exactly:' + chr(10) +
            '- DESCRIBE what the evidence shows. Do NOT speculate about the outlet motive, intent, or editorial agenda.' + chr(10) +
            '- Do NOT use words like: fumble, slip, conveniently, softens, blurs, narrows in connection with the outlet.' + chr(10) +
            '- Do NOT make political characterizations of subjects (e.g. conservative ally, progressive movement).' + chr(10) +
            '- Do NOT assert what the outlet has structural incentive to do.' + chr(10) +
            '- When describing why a claim was overstated/disputed: stick to the specific evidence discrepancy only.' + chr(10) +
            '- article_summary: describe WHAT events the article reports on. No why-this-matters framing. No threatening/suppression/pressure characterizations unless direct quotes. 1-2 sentences for short articles, 2-3 for longer.' + chr(10) +
            '- overall_signal: describe what verdicts were assigned and why specifically. Reference methodology mechanics (weights applied, sources consulted). Note the factual core of overstated claims. DO NOT editorialize about the outlet.' + chr(10) +
            '- watch_for: 3 specific actionable follow-up items naming real people, documents, or institutions. What evidence would confirm or contradict these claims?' + chr(10) +
            '- claim brief (verdict_summary): MUST NOT restate the claim itself. Lead with verdict outcome and number/type of sources. Max 1-2 sentences. If your brief contains more than 8 consecutive words from the original claim, rewrite to focus on the verification evidence pattern.' + chr(10) +
            'ARTICLE: ' + title + chr(10) +
            'SOURCE: ' + source + chr(10) +
            'OUTLET SCORE: ' + str(score) + '/100 (' + rating + ')' + chr(10) +
            'CLAIMS: ' + str(total_n) + ' total | ' + str(supported_n) + ' supported | ' + str(overstated_n) + ' overstated | ' + str(disputed_n) + ' disputed | ' + str(not_supported_n) + ' not supported' + chr(10) +
            'ASSESSED CLAIMS:' + chr(10) + _claims_text + chr(10) +
            'Return ONLY valid JSON: {"article_summary": "...", "overall_signal": "...", "watch_for": ["...", "...", "..."]}'
        )
        _msg = _client.messages.create(model='claude-sonnet-4-6', max_tokens=800, messages=[{'role':'user','content':_prompt}])
        _text = _msg.content[0].text.strip()
        _result = __import__('json').loads(_text[_text.find('{'):_text.rfind('}')+1])
        article_summary = smartquotes(_result.get('article_summary', ''))
        overall_signal = smartquotes(_result.get('overall_signal', ''))
        watch_for = [smartquotes(w) for w in _result.get('watch_for', [])]
    except Exception as _e:
        import traceback
        print(f'Content generation failed: {_e}')
        print(traceback.format_exc())
        overall_signal = 'This article scores ' + str(score) + '/100 (' + rating + '). Of ' + str(total_n) + ' claims assessed, ' + str(supported_n) + ' were supported. ' + str(overstated_n + disputed_n + not_supported_n) + ' showed overstatement or dispute.'

    # --- Article tag ---
    article_tag = request.args.get('tag', '') or data.get('tag', '')
    TAG_CONFIG = {'breaking':('⚡','BREAKING','vs-tag-breaking'),'major':('🔴','MAJOR STORY','vs-tag-major'),'developing':('🔄','DEVELOPING','vs-tag-developing'),'exclusive':('★','EXCLUSIVE','vs-tag-exclusive')}
    if article_tag and article_tag.lower() in TAG_CONFIG:
        _icon,_lbl,_cls = TAG_CONFIG[article_tag.lower()]
        tag_html = '<div class="vs-tag ' + _cls + '">' + _icon + ' ' + _lbl + '</div>'
    else:
        tag_html = ''

    # --- Score ring ---
    _circ = round(2 * 3.14159 * 36, 1)
    _offset = round(_circ * (1 - score / 100), 1)
    score_ring_html = ('<svg class="vs-score-ring" viewBox="0 0 90 90">'
        '<circle cx="45" cy="45" r="36" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="5"/>'
        '<circle cx="45" cy="45" r="36" fill="none" stroke="' + score_color + '" stroke-width="5" '
        'stroke-dasharray="' + str(_circ) + '" stroke-dashoffset="' + str(_offset) + '" '
        'stroke-linecap="round" transform="rotate(-90 45 45)"/>'
        '<text x="45" y="48" text-anchor="middle" font-family="DM Serif Display,serif" font-size="22" fill="' + score_color + '">' + str(score) + '</text>'
        '<text x="45" y="62" text-anchor="middle" font-family="monospace" font-size="7" fill="rgba(255,255,255,0.3)">/100</text>'
        '</svg>')

    # --- Red flag ---
    _nflag = stats.get('disputed',0) + stats.get('not_supported',0)
    redflag_html = ('<div class="vs-redflag"><div class="vs-redflag-icon">⚠️</div>'
        '<div class="vs-redflag-text"><strong>Heads up:</strong> This article contains '
        + str(_nflag) + ' claim(s) directly contradicted by available evidence. '
        'Review the disputed verdicts carefully before sharing.</div></div>') if _nflag > 0 else ''

    # --- Story context ---
    _ctx_title = title[:80] + '...' if len(title) > 80 else title
    story_context = 'This article covers "' + _ctx_title + '". Here is what the evidence shows across ' + str(stats.get('total',0)) + ' extracted claims.'

    # --- Distribution blocks ---
    _VCOLS = [('supported','#4ade80'),('plausible','#60a5fa'),('corroborated','#34d399'),('overstated','#fb923c'),('disputed','#f87171'),('not_supported','#ef4444'),('opinion','rgba(255,255,255,0.12)'),('not_verifiable','rgba(255,255,255,0.08)')]
    dist_blocks_html = ''
    dist_legend_html = ''
    for _v,_col in _VCOLS:
        _cnt = stats.get(_v,0)
        if _cnt:
            dist_blocks_html += ('<div class="vs-dist-block" style="background:' + _col + ';"></div>') * _cnt
            dist_legend_html += '<span class="vs-leg"><span class="vs-leg-dot" style="background:' + _col + '"></span>' + str(_cnt) + ' ' + _v.replace('_',' ').title() + '</span>'

    # --- Watch for ---
    watch_for_html = ''
    if watch_for:
        watch_for_html = '<div class="vs-watchfor-label" style="margin-top:16px;">WHAT TO WATCH FOR</div><div class="vs-watchfor-list">'
        for w in watch_for:
            watch_for_html += '<div class="vs-watchfor-item"><span class="vs-watchfor-icon">→</span><span>' + w + '</span></div>'
        watch_for_html += '</div>'

    # --- Article summary html ---
    summary_html = ('<div class="vs-summary-text">' + article_summary + '</div>') if article_summary else ''

    short_url_hash = get_or_create_short_hash(art_id)
    short_url = 'verumsignal.com/r/' + short_url_hash
    # Phase 5: free vs paid template branch
    _template_name = 'report_free.html' if depth == 2 else 'report.html'
    with open(os.path.join(os.path.dirname(__file__), 'templates', _template_name), 'r') as _tf:
        html = _tf.read()
    # Free template uses the simpler claims_html_free; paid uses claims_html
    if depth == 2:
        claims_html = claims_html_free
        # Recompute total to reflect verified-only claim count for free report
        stats_total_for_free = len(verified_only)
    else:
        stats_total_for_free = None
    html = html.replace('{{source}}', str(source))
    html = html.replace('{{score}}', str(score))
    pass #removed
    pass #removed
    pass #removed
    html = html.replace('{{rating}}', str(rating))
    html = html.replace('{{as_of}}', str(as_of))
    html = html.replace('{{url}}', str(url))
    html = html.replace('{{title}}', str(title))
    html = html.replace('{{tag_html}}', str(tag_html))
    html = html.replace('{{summary_html}}', str(summary_html))
    html = html.replace('{{short_url}}', str(short_url))
    pass #removed
    html = html.replace('{{score_color}}', str(score_color))
    # Patch 3: render the article-score chip in Python so unscored articles
    # don't display a misleading '0/100 Unscored'.
    if _score_was_none:
        score_chip_html = '<span class="vs-chip-score" style="color:rgba(255,255,255,0.3)">Article: Unscored</span>'
    else:
        score_chip_html = f'<span class="vs-chip-score" style="color:{score_color}">Article: {score}/100 {rating}</span>'
    html = html.replace('{{score_chip_html}}', score_chip_html)
    # ─────────────────────────────────────────────
    # ARTICLE SCORE vs OUTLET SCORE
    # The variables score / rating from data dict are the ARTICLE's score
    # (computed from this article's own claims). We alias them as article_score
    # so the renderer can clearly distinguish from outlet aggregate score below.
    # ─────────────────────────────────────────────
    article_score = score if score is not None else None
    article_rating = rating if rating else 'Unscored'
    if article_score is None:
        article_score_color = 'rgba(255,255,255,0.3)'
    elif article_score >= 70:
        article_score_color = '#4ade80'
    elif article_score >= 40:
        article_score_color = '#fbbf24'
    else:
        article_score_color = '#f87171'
    # Inclusion tier
    try:
        _ic = get_db()
        _ic_cur = _ic.cursor()
        _ic_cur.execute("SELECT verdict FROM claims c JOIN articles a ON c.article_id = a.id WHERE a.source_name = %s AND c.verdict IS NOT NULL AND c.claim_origin = 'outlet_claim' AND a.published_at IS NOT NULL AND a.published_at < NOW() - INTERVAL '6 hours'", (source,))
        _outlet_verdicts = [r[0] for r in _ic_cur.fetchall()]
        _verdict_count = len(_outlet_verdicts)
        _ic.close()
        _scoreable_outlet = [v for v in _outlet_verdicts if v in WEIGHTS]
        _ws = sum(WEIGHTS[v] for v in _scoreable_outlet)
        outlet_score = compute_score(_ws, len(_scoreable_outlet))
        outlet_rating = compute_score_band(outlet_score)
    except:
        _verdict_count = 0
        outlet_score = None
        outlet_rating = 'Unscored' 
    inclusion_tier = compute_tier(_verdict_count)
    tier_color = {
        'Published':    '#4ade80',
        'Stabilizing':  '#60a5fa',
        'Limited Data': '#fbbf24',
        'Excluded':     '#f87171',
    }[inclusion_tier]
    html = html.replace('{{inclusion_tier}}', str(inclusion_tier))
    html = html.replace('{{tier_color}}', str(tier_color))
    html = html.replace('{{verdict_count}}', str(_verdict_count))

    # Build outlet history line for top of report
    if _verdict_count >= 20 and outlet_score is not None:
        outlet_history_line = (
            source + ' outlet history: '
            '<b>' + str(outlet_score) + '/100 ' + outlet_rating + '</b> '
            '(' + str(_verdict_count) + ' verdicts collected)'
        )
    else:
        outlet_history_line = (
            source + ' outlet history: '
            '<b>insufficient data</b> '
            '(' + str(_verdict_count) + '/20 verdicts collected, see <a href=\"/methodology\" target=\"_blank\">Methodology v1.6</a>)'
        )
    html = html.replace('{{outlet_history_line}}', outlet_history_line)
    # Excluded tier display logic
    is_excluded = _verdict_count < 20

    # ── TOP-OF-PAGE: always article score (nav chip + sidebar big number) ──
    # Per design: top of page shows the ARTICLE's score only. Outlet score
    # lives in the bottom stats row.
    # Patch 3: use _score_was_none (captured before Patch 1's coerce) instead of
    # article_score is None, since article_score is always int after the coerce.
    _article_display_score = 'Unscored' if _score_was_none else str(article_score)
    outlet_badge_html = source + ' &nbsp;&middot;&nbsp; <b>' + _article_display_score + ('</b>' if _score_was_none else '/100</b> ' + article_rating)
    if _score_was_none:
        score_block_html = ('<div class="vs-score-num" style="color:rgba(232,232,240,0.35);font-size:32px;">Unscored</div><div class="vs-score-unit">no scoreable claims</div>')
    else:
        score_block_html = ('<div class="vs-score-num" style="color:' + article_score_color + '">' + str(article_score) + '</div><div class="vs-score-unit">/100</div><div class="vs-score-tier" style="color:' + article_score_color + '">' + article_rating + '</div>')

    # No mid-page outlet inset — outlet status communicated via bottom stats row only
    excluded_inset_html = ''

    # ── BOTTOM STATS: outlet score (or 'Insufficient data' if Excluded) ──
    if is_excluded:
        outlet_score_stat = '—'
        tier_label = 'OUTLET STATUS'
        tier_stat = 'Insufficient data'
        footer_score_text = 'outlet score not yet available'
    else:
        outlet_score_stat = str(outlet_score) + '<span>/100</span>' if outlet_score is not None else '—'
        tier_label = 'OUTLET TIER'
        tier_stat = outlet_rating
        footer_score_text = ('outlet score: ' + str(outlet_score) + '/100 ' + outlet_rating) if outlet_score is not None else 'outlet score not yet available'
    no_scoreable_claims = data.get('no_scoreable_claims', False)
    if no_scoreable_claims:
        opinion_inset_html = ('<div class="vs-opinion-inset"><div class="vs-opinion-inset-label">NOT SCORED — OPINION / UNVERIFIABLE CONTENT</div><div class="vs-opinion-inset-text">This article was retrieved successfully but did not contain verifiable factual claims that can be assessed against independent sources. It may be primarily opinion, commentary, polling data, or analysis. The Verum Signal methodology only scores articles with attributable factual claims — this article is classified as unscored. No verdict is implied about its accuracy or quality.</div></div>')
    else:
        opinion_inset_html = ''
    html = html.replace('{{opinion_inset_html}}', opinion_inset_html)
    html = html.replace('{{outlet_badge_html}}', outlet_badge_html)
    html = html.replace('{{score_block_html}}', score_block_html)
    html = html.replace('{{excluded_inset_html}}', excluded_inset_html)
    html = html.replace('{{outlet_score_stat}}', outlet_score_stat)
    html = html.replace('{{tier_label}}', tier_label)
    html = html.replace('{{tier_stat}}', tier_stat)
    html = html.replace('{{footer_score_text}}', footer_score_text)
    html = html.replace('{{methodology_callout}}', str(methodology_callout))
    html = html.replace('{{redflag_html}}', str(redflag_html))
    pass #removed
    html = html.replace('{{dist_bar_html}}', str(dist_blocks_html))
    html = html.replace('{{dist_legend_html}}', str(dist_legend_html))
    html = html.replace('{{claims_html}}', str(claims_html))
    html = html.replace('{{overall_signal}}', str(overall_signal))
    html = html.replace('{{watch_for_html}}', str(watch_for_html))
    html = html.replace('{{background_signal_html}}', background_signal_html)
    html = html.replace('{{all_sources_html}}', str(all_sources_html))
    html = html.replace('{{compare_html}}', str(compare_html))
    html = html.replace('{{total}}', str(stats_total_for_free if stats_total_for_free is not None else stats.get('total',0)))
    html = html.replace('{{sc}}', str(sc))
    from flask import Response
    return Response(html, mimetype='text/html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True, use_reloader=False)

