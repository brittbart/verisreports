
from flask import Flask, jsonify, request, redirect, send_from_directory
from flask_cors import CORS
import psycopg2
import os
import sys
from dotenv import load_dotenv
import secrets
from seo import homepage_meta, report_meta, leaderboard_meta, methodology_meta
import string
from datetime import datetime


app = Flask(__name__)

@app.before_request
def redirect_api_paths():
    """Redirect /v1/* and /openapi.yaml from main domain to api subdomain."""
    from flask import request, redirect
    host = request.host.split(':')[0]
    if host == 'verumsignal.com':
        if request.path.startswith('/v1') or request.path == '/openapi.yaml':
            new_url = f'https://api.verumsignal.com{request.path}'
            if request.query_string:
                new_url += f'?{request.query_string.decode()}'
            return redirect(new_url, code=301)

app.config['THREADED'] = True
CORS(app)

def _normalize_url(url):
    """Normalize URL for deduplication: lowercase scheme+host, strip trailing slash, force https."""
    from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl
    try:
        p = urlparse(url.strip())
        scheme = 'https'
        netloc = p.netloc.lower()
        path = p.path.rstrip('/') or '/'
        STRIP_PARAMS = {'utm_source','utm_medium','utm_campaign','utm_term',
                        'utm_content','ref','source','fbclid','gclid'}
        params = [(k,v) for k,v in parse_qsl(p.query) if k.lower() not in STRIP_PARAMS]
        query = urlencode(params)
        return urlunparse((scheme, netloc, path, p.params, query, ''))
    except Exception:
        return url

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
from api_public import api_public
register_leaderboard_routes(app, get_db)
register_outlet_routes(app, get_db)
app.register_blueprint(api_public)
from debate_routes import register_debate_routes
register_debate_routes(app, get_db)
from mobile_routes import register_mobile_routes
register_mobile_routes(app, get_db)
from admin_routes import register_admin_routes
register_admin_routes(app, get_db)
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
        conn.close()



# ---------- Phase 4: depth-aware claim verification ----------
def verify_and_insert_claims(claims, art_id, title, source_name, cursor, depth=None):
    """Verify the top `depth` claims and insert all claims into the DB.

    depth=None means verify all claims (paid behavior).
    depth=2 means verify top 2 (free behavior).

    Outlet claims are processed first (they're what scoring uses).
    Remaining claims insert with verdict=NULL, verification_depth=NULL
    so a later upgrade can verify them.

    Verification runs concurrently (ThreadPoolExecutor, max_workers=3) mirroring
    the existing debate pattern at verdict_engine.py:523. Each worker opens its
    own DB connection for the INSERT — the shared cursor is NOT used inside workers.
    Results are re-sorted by original index before the claims[:2] cap so free-report
    ordering is preserved exactly as in the serial path.
    """
    from verdict_engine import analyse_claim, _sources_to_prose
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import json as _ijson

    def _sort_key(c):
        return 0 if c.get('claim_origin', 'outlet_claim') == 'outlet_claim' else 1
    sorted_claims = sorted(claims, key=_sort_key)
    verify_count = len(sorted_claims) if depth is None else min(depth, len(sorted_claims))

    # ── Phase 1: parallel verification + insert for claims within verify_count ──
    def _verify_worker(idx_claim):
        """Worker: verify one claim and insert it. Opens its own DB connection."""
        idx, c = idx_claim
        claim_text = c.get('claim_text', '')
        speaker = c.get('speaker', '')
        claim_type = c.get('claim_type', 'factual')
        claim_origin = c.get('claim_origin', 'outlet_claim')
        attribution_context = c.get('attribution_context', '')
        try:
            result = analyse_claim(
                claim_text, speaker, claim_type, title, source_name,
                cursor=None, claim_origin=claim_origin,
                attribution_context=attribution_context,
            )
            if result is None:
                print(f"[verify] analyse_claim returned None for: {claim_text[:80]}... -- skipping")
                return idx, None
            _ins_raw = result.get('sources_used', '')
            if isinstance(_ins_raw, list):
                _ins_str = _ins_raw
            elif isinstance(_ins_raw, str):
                try:
                    _ip = _ijson.loads(_ins_raw)
                    _ins_str = _ip if isinstance(_ip, list) else []
                except Exception:
                    _ins_str = []
            else:
                _ins_str = []
            _ins_prose = _sources_to_prose(_ins_str) if _ins_str else (str(_ins_raw) if _ins_raw else '')
            # Per-worker DB connection (MITIGATION 1 — never share cursor across threads)
            _wconn = get_db()
            try:
                with _wconn.cursor() as _wcur:
                    _wcur.execute(
                        "INSERT INTO claims (article_id, claim_text, speaker, claim_type, "
                        "claim_origin, verdict, confidence_score, verdict_summary, "
                        "full_analysis, sources_used, sources_structured, priority_score, verification_depth) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                        (art_id, claim_text, speaker, claim_type, claim_origin,
                         result.get('verdict'), result.get('confidence_score'),
                         result.get('verdict_summary'), result.get('full_analysis'),
                         _ins_prose, _ijson.dumps(_ins_str),
                         50, depth or 99),
                    )
                    cid = _wcur.fetchone()[0]
                _wconn.commit()
            finally:
                _wconn.close()
            row = (cid, claim_text, speaker, claim_type, claim_origin,
                   result.get('verdict'), result.get('confidence_score'),
                   result.get('verdict_summary'), result.get('full_analysis'),
                   _ins_prose, _ijson.dumps(_ins_str))
            return idx, row
        except Exception as e:
            print(f"[verify] worker error for claim idx={idx}: {e}")
            return idx, None

    # Dispatch verified claims concurrently (max_workers=3, matching debate pattern)
    to_verify = [(i, c) for i, c in enumerate(sorted_claims) if i < verify_count]
    verified_results = {}  # idx -> row (or None on failure)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_verify_worker, ic): ic[0] for ic in to_verify}
        for future in as_completed(futures):
            try:
                idx, row = future.result()
                verified_results[idx] = row
            except Exception as e:
                print(f"[verify] future error: {e}")

    # ── Phase 2: insert unverified claims serially (NULL insert, no model call) ──
    unverified_rows = {}
    for i, c in enumerate(sorted_claims):
        if i >= verify_count:
            claim_text = c.get('claim_text', '')
            speaker = c.get('speaker', '')
            claim_type = c.get('claim_type', 'factual')
            claim_origin = c.get('claim_origin', 'outlet_claim')
            cursor.execute(
                "INSERT INTO claims (article_id, claim_text, speaker, claim_type, "
                "claim_origin, verdict, confidence_score, verdict_summary, "
                "full_analysis, sources_used, priority_score, verification_depth) "
                "VALUES (%s,%s,%s,%s,%s,NULL,NULL,NULL,NULL,NULL,%s,NULL) RETURNING id",
                (art_id, claim_text, speaker, claim_type, claim_origin, 50),
            )
            cid = cursor.fetchone()[0]
            unverified_rows[i] = (cid, claim_text, speaker, claim_type, claim_origin,
                                  None, None, None, None, None)

    # ── Phase 3: reassemble in original index order (MITIGATION 2 — preserves free top-2) ──
    out_rows = []
    for i in range(len(sorted_claims)):
        if i in verified_results and verified_results[i] is not None:
            out_rows.append(verified_results[i])
        elif i in unverified_rows:
            out_rows.append(unverified_rows[i])
        # if verified_results[i] is None (worker failed), claim is skipped

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
                    WHEN 'corroborated'  THEN  0.75
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

    # Anon ceiling on /api/report — 3/day/IP, 429 response (non-breaking shape)
    # Full tier-gating deferred to Session 6; ceiling stops credit-drain now.
    # Audit override bypasses ceiling — testers must not be blocked after 3 reports.
    import os as _apir_os
    _apir_audit_key = _apir_os.getenv('PAID_AUDIT_KEY', '')
    _apir_override = bool(_apir_audit_key and request.args.get('audit_key') == _apir_audit_key)
    if not _apir_override and not anon_ceiling_ok(get_db, request):
        return jsonify({'error': 'daily anonymous limit reached', 'limit': 3}), 429

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
                _fuzzy = cur.fetchone()
                if _fuzzy:
                    article = _fuzzy

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
                   full_analysis, sources_used, sources_structured
            FROM claims
            WHERE article_id = %s
            ORDER BY priority_score DESC, id ASC
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
        corroborated_count = sum(1 for c in claims if c[5] == 'corroborated')
        overstated_count   = sum(1 for c in claims if c[5] == 'overstated')
        disputed_count     = sum(1 for c in claims if c[5] == 'disputed')
        not_supported_count= sum(1 for c in claims if c[5] == 'not_supported')
        not_verifiable_count= sum(1 for c in claims if c[5] == 'not_verifiable')
        opinion_count      = sum(1 for c in claims if c[5] == 'opinion')
        unverified_count   = sum(1 for c in claims if c[5] is None)

        # Weighted scoring — outlet_claims only, per v1.7 methodology.
        # Attributed claims are displayed but do not affect article score.
        # Uses api_leaderboard.WEIGHTS / compute_score / compute_score_band as
        # the single source of truth.
        weighted_sum = sum(WEIGHTS[c[5]] for c in claims if c[5] in WEIGHTS and c[4] == 'outlet_claim')
        scoreable = sum(1 for c in claims if c[5] in WEIGHTS and c[4] == 'outlet_claim')
        score = compute_score(weighted_sum, scoreable)
        rating = compute_score_band(score)

        claims_data = []
        for cid, claim_text, speaker, claim_type, claim_origin, verdict, confidence, summary, analysis, sources, src_structured in claims:
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
                'sources_used': sources,
                'sources_structured': src_structured if src_structured else []
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
                'corroborated': corroborated_count,
                'overstated': overstated_count,
                'disputed': disputed_count,
                'not_supported': not_supported_count,
                'not_verifiable': not_verifiable_count,
                'opinion': opinion_count,
                'unverified': unverified_count,
                'total': len(claims)
            },
            'claims': claims_data,
            'as_of': (verified_at.strftime('%B %d, %Y') if verified_at else 'assessment date unavailable')
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Tunable staleness threshold for Pro re-check button.
# Adjust post-launch once real re-check behavior is observable.
RECHECK_STALENESS_DAYS = 14


@app.route("/api/report/recheck", methods=["POST"])
def recheck_report():
    from auth_routes import get_current_user, check_quota, increment_quota
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    user = get_current_user(get_db)
    if not user:
        return jsonify({"error": "authentication required"}), 401
    q = check_quota(get_db, user["id"], "consumer")
    if q["tier"] not in ("pro", "scale"):
        return jsonify({"error": "Pro subscription required for re-check"}), 403
    if not q["allowed"]:
        return jsonify({"error": "monthly quota exhausted",
                        "used": q["used"], "limit": q["limit"]}), 402
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, source_name, verified_at FROM articles WHERE url=%s LIMIT 1",
            (url,)
        )
        article = cur.fetchone()
        if not article:
            conn.close()
            return jsonify({"error": "article not found"}), 404
        art_id, title, source_name, verified_at = article
        cur.execute(
            "SELECT id FROM recheck_log WHERE article_id=%s AND user_id=%s"
            " AND requested_at > NOW() - INTERVAL '24 hours'",
            (art_id, user["id"])
        )
        if cur.fetchone():
            conn.close()
            return jsonify({"error": "re-check already requested for this article in the last 24h"}), 429
        cur.execute(
            "INSERT INTO recheck_log (article_id, user_id) VALUES (%s, %s)",
            (art_id, user["id"])
        )
        conn.commit()
        conn.close()
        import anthropic as _anth_rc
        from extract_claims import extract_claims_from_article
        _anth_client_rc = _anth_rc.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        fetch_result = fetch_article_content(url, _anth_client_rc)
        if not fetch_result or fetch_result.get("status") == "paywall":
            return jsonify({"error": "could not fetch article for re-check"}), 422
        body_text = fetch_result.get("body", "")
        title_text = fetch_result.get("title") or title
        article_dict = {
            "title": title_text, "description": body_text[:500],
            "content": body_text, "source": {"name": source_name},
            "url": url, "publishedAt": ""
        }
        claims = extract_claims_from_article(article_dict)
        if not claims:
            return jsonify({"error": "no claims extracted on re-check"}), 422
        conn2 = get_db()
        cur2 = conn2.cursor()
        verified_claims = verify_and_insert_claims(
            claims, art_id, title_text, source_name, cur2, depth=None
        )
        cur2.execute(
            "UPDATE articles SET verified_at=NOW(), claims_verified=TRUE WHERE id=%s",
            (art_id,)
        )
        conn2.commit()
        conn2.close()
        increment_quota(get_db, user["id"], "consumer")
        return jsonify({
            "status": "ok",
            "claims_rechecked": len(verified_claims),
            "message": "Re-check complete. Reload the report to see updated verdicts."
        })
    except Exception as e:
        import traceback
        print(f"[recheck_report] error: {e}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


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

@app.route('/robots.txt', methods=['GET'])
def robots_txt():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'robots.txt')

@app.route("/sitemap.xml", methods=["GET"])
def sitemap_xml():
    """Auto-generated XML sitemap. Invisible until robots.txt allows crawling."""
    from flask import Response
    conn = get_db()
    cur = conn.cursor()
    pages = []
    # Static pages
    for path in ["/", "/leaderboard", "/methodology", "/how-it-works", "/debates", "/pricing"]:
        pages.append(f"  <url><loc>https://verumsignal.com{path}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>")
    # Outlet pages
    cur.execute("SELECT DISTINCT domain FROM api_outlets WHERE score IS NOT NULL ORDER BY domain")
    for row in cur.fetchall():
        pages.append(f"  <url><loc>https://verumsignal.com/outlet/{row[0]}</loc><changefreq>daily</changefreq><priority>0.7</priority></url>")
    # Debate pages
    cur.execute("SELECT slug FROM events WHERE is_public = TRUE ORDER BY event_date DESC")
    for row in cur.fetchall():
        pages.append(f"  <url><loc>https://verumsignal.com/debates/{row[0]}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>")
    # Report short URLs
    cur.execute("SELECT short_hash FROM articles WHERE short_hash IS NOT NULL AND short_hash != '' ORDER BY verified_at DESC NULLS LAST LIMIT 500")
    for row in cur.fetchall():
        pages.append(f"  <url><loc>https://verumsignal.com/r/{row[0]}</loc><changefreq>monthly</changefreq><priority>0.5</priority></url>")
    cur.close()
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        + '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(pages) + "\n</urlset>")
    return Response(xml, mimetype="application/xml")

@app.route("/api/og/report", methods=["GET"])
def og_report():
    from og_images import generate_report_og
    from flask import Response
    source = request.args.get("source", "")
    score = request.args.get("score", "")
    title = request.args.get("title", "")
    score_val = int(score) if score and score.isdigit() else None
    buf = generate_report_og(source, score_val, title)
    return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "public, max-age=86400"})

@app.route("/api/og/outlet", methods=["GET"])
def og_outlet():
    from og_images import generate_outlet_og
    from flask import Response
    domain = request.args.get("domain", "")
    score = request.args.get("score", "")
    score_val = int(score) if score and score.isdigit() else None
    buf = generate_outlet_og(domain, score_val)
    return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "public, max-age=86400"})

@app.route("/api/og/debate", methods=["GET"])
def og_debate():
    from og_images import generate_debate_og
    from flask import Response
    name = request.args.get("name", "")
    claims = request.args.get("claims", "0")
    claims_val = int(claims) if claims.isdigit() else 0
    buf = generate_debate_og(name, claims_val)
    return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "public, max-age=86400"})

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



def send_beta_request_notification(request_id, name, email, org, use_case, volume):
    """Log new beta request. Replace with real SMTP when api@verumsignal.com is configured."""
    import datetime
    line = (
        f"[{datetime.datetime.utcnow().isoformat()}] "
        f"NEW BETA REQUEST #{request_id}: {name} <{email}> from {org}\n"
        f"  Volume: {volume or 'not specified'}\n"
        f"  Use case: {use_case[:200]}\n"
        '---\n'
    )
    try:
        with open('/tmp/beta_requests.log', 'a') as f: f.write(line)
    except Exception: pass


@app.route('/api', methods=['GET'])
def api_landing():
    return send_from_directory(
        os.path.join(app.root_path, 'static', 'api'),
        'index.html'
    )


@app.route('/api/beta-request', methods=['POST'])
def api_beta_request_submit():
    """Submit a beta access request from the API landing page form."""
    import re as _re
    data = request.get_json(silent=True) or request.form
    name             = (data.get('name') or '').strip()
    email            = (data.get('email') or '').strip()
    organization     = (data.get('organization') or '').strip()
    use_case         = (data.get('use_case') or '').strip()
    estimated_volume = (data.get('estimated_volume') or '').strip() or None
    if not name or not email or not organization or not use_case:
        return jsonify({'success': False, 'error': 'missing_required_fields'}), 400
    if not _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        return jsonify({'success': False, 'error': 'invalid_email'}), 400
    if len(name) > 200 or len(email) > 200 or len(organization) > 300 or len(use_case) > 5000:
        return jsonify({'success': False, 'error': 'field_too_long'}), 400
    ip         = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    user_agent = request.headers.get('User-Agent', '')[:500]
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO api_beta_requests
              (name, email, organization, use_case, estimated_volume, ip, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (name, email, organization, use_case, estimated_volume, ip, user_agent))
        request_id = cur.fetchone()[0]; conn.commit()
    except Exception:
        conn.rollback(); return jsonify({'success': False, 'error': 'server_error'}), 500
    finally:
        cur.close(); conn.close()
    try: send_beta_request_notification(request_id, name, email, organization, use_case, estimated_volume)
    except Exception: pass
    return jsonify({'success': True, 'request_id': request_id}), 200


@app.route('/terms', methods=['GET'])
def terms_clean():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'terms.html')

@app.route('/terms.html', methods=['GET'])
def terms_html():
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static'), 'terms.html')

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

# Outlets that consistently block direct scrape — skip straight to Jina/web search
_DIRECT_SCRAPE_BLOCKED = {
    'thehill.com', 'politico.com', 'bloomberg.com', 'wsj.com', 'ft.com',
    'nytimes.com', 'washingtonpost.com', 'thedailybeast.com', 'wired.com',
}

def _try_direct_scrape(url):
    from urllib.parse import urlparse as _up
    _host = _up(url).hostname or ''
    _host = _host.lower().replace('www.', '')
    if _host in _DIRECT_SCRAPE_BLOCKED:
        print(f"[direct] Skipping {_host} (known anti-bot)")
        return None
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
        from token_logging import log_usage; log_usage('api_web_search', msg)
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

def get_background_signal(article_claim_texts, anthropic_client=None, article_id=None):
    """
    Return prior verified claims related to the current article's claims.

    DB-first: queries verified claims (supported/corroborated/plausible) using
    pg_trgm similarity against each of the current article's claim texts.
    Threshold 0.30. Filters to claims whose sources_used contains at least one
    independent-tier domain.

    Optional web search fallback (gated by BACKGROUND_SIGNAL_WEB_FALLBACK=1)
    runs only if DB returns fewer than 3 facts.

    Results cached in articles.background_signal_cached for 24h to avoid
    redundant API calls on repeat report views.

    Returns a list of {fact, source, source_url, relevance_tag} dicts, max 3.
    Returns [] if nothing meets the threshold.
    """
    if not article_claim_texts:
        return []

    # Cache check: if article_id provided and cache is fresh (<24h), return cached result
    if article_id:
        try:
            import json as _cjson
            _cconn = get_db()
            _ccur = _cconn.cursor()
            _ccur.execute("""
                SELECT background_signal_cached, background_signal_cached_at
                FROM articles WHERE id = %s
            """, (article_id,))
            _crow = _ccur.fetchone()
            _cconn.close()
            if _crow and _crow[0] is not None and _crow[1] is not None:
                from datetime import timezone
                _age = datetime.utcnow() - _crow[1].replace(tzinfo=None)
                if _age.total_seconds() < 86400:
                    print(f"[bg_signal] Cache hit for article_id={article_id} (age={int(_age.total_seconds())}s)")
                    return _crow[0] if isinstance(_crow[0], list) else _cjson.loads(_crow[0])
        except Exception as _ce:
            print(f"[bg_signal] Cache read failed: {_ce}")

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

    # Store result in cache after computation (whether from DB or web search)
    def _write_cache(aid, data):
        if not aid:
            return
        try:
            import json as _wjson
            _wconn = get_db()
            _wcur = _wconn.cursor()
            _wcur.execute("""
                UPDATE articles
                SET background_signal_cached = %s::jsonb,
                    background_signal_cached_at = NOW()
                WHERE id = %s
            """, (_wjson.dumps(data), aid))
            _wconn.commit()
            _wconn.close()
        except Exception as _we:
            print(f"[bg_signal] Cache write failed: {_we}")

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
            from token_logging import log_usage; log_usage('api_background_signal', msg)
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

    _write_cache(article_id, results[:3])
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



def _build_report_data(rows, *, url, title, source, as_of, score, rating,
                       callout_text=None, extraction_method=None):
    """Canonical report data dict — single source of truth for all three /report paths.
    as_of must be caller-supplied (never dt.now() internally):
      paths A/B: dt.now() — they just verified.
      path C:    _path_c_as_of — provenance-based (Task 5).
    callout_text: path C supplies its own; A/B use the simple extraction summary.
    Stats include all 8 verdicts (fixes missing not_verifiable on A/B/C HTML surface).
    """
    if callout_text is None:
        n_sup = sum(1 for c in rows if c[5] == "supported")
        n_flag = sum(1 for c in rows if c[5] in ("overstated", "disputed", "not_supported"))
        callout_text = (
            f"This article contained {len(rows)} claim{"s" if len(rows)!=1 else ""} "
            f"assessed after extraction. {n_sup} supported, {n_flag} flagged."
        )
    d = {
        "status": "found",
        "url": url,
        "title": title,
        "source": source,
        "score": score,
        "rating": rating,
        "as_of": as_of,
        "methodology_callout": callout_text,
        "stats": {
            "supported":     sum(1 for c in rows if c[5] == "supported"),
            "plausible":     sum(1 for c in rows if c[5] == "plausible"),
            "corroborated":  sum(1 for c in rows if c[5] == "corroborated"),
            "overstated":    sum(1 for c in rows if c[5] == "overstated"),
            "disputed":      sum(1 for c in rows if c[5] == "disputed"),
            "not_supported": sum(1 for c in rows if c[5] == "not_supported"),
            "not_verifiable":sum(1 for c in rows if c[5] == "not_verifiable"),
            "opinion":       sum(1 for c in rows if c[5] == "opinion"),
            "total":         len(rows),
        },
        "claims": [
            {"id":c[0],"claim_text":c[1],"speaker":c[2],"claim_type":c[3],
             "claim_origin":c[4],"verdict":c[5],"confidence_score":c[6],
             "verdict_summary":c[7],"full_analysis":c[8],"sources_used":c[9],
             "sources_structured":c[10] if len(c)>10 and c[10] else []}
            for c in rows
        ],
    }
    if extraction_method is not None:
        d["extraction_method"] = extraction_method
    return d


def resolve_report_access(get_db):
    """Single source of truth for report tiering.
    Anonymous visitors are treated as free tier (ruling c-i).
    Returns: {
        'user': dict|None,
        'tier': 'free'|'pro'|'scale',
        'depth': 2|None,          # 2 = free cap, None = full (paid)
        'user_id': int|None,
    }
    Depth is SERVER-DERIVED. The ?depth= URL param is ignored for access control.
    """
    import os as _ra_os
    from auth_routes import get_current_user, check_quota
    # TEMPORARY audit override (Session 3.5 — REMOVE at Session 6 real auth).
    # Server-validated shared secret. PAID_AUDIT_KEY unset = override disabled.
    _audit_key = _ra_os.getenv('PAID_AUDIT_KEY', '')
    if _audit_key and request.args.get('audit_key') == _audit_key:
        try:
            print(f"[audit_override] full-depth granted via audit_key url={request.args.get('url','')[:120]}")
        except Exception:
            pass
        return {'user': None, 'tier': 'audit', 'depth': None, 'user_id': None, 'audit_override': True}
    user = get_current_user(get_db)
    if not user:
        return {'user': None, 'tier': 'free', 'depth': 2, 'user_id': None, 'audit_override': False}
    q = check_quota(get_db, user['id'], 'consumer')
    tier = q['tier']
    depth = None if tier in ('pro', 'scale') else 2
    return {'user': user, 'tier': tier, 'depth': depth, 'user_id': user['id'], 'audit_override': False}


def anon_ceiling_ok(get_db, request):
    """Check and enforce 3/day/IP ceiling for anonymous verifications.
    Returns True if within ceiling (proceed), False if exceeded (block).
    IP is SHA-256 hashed with a salt — raw IP never stored.
    Increment only on successful verification (see anon_ceiling_increment).
    """
    import hashlib, os
    from datetime import date
    salt = os.environ.get("SECRET_KEY", "vs-anon-salt")
    forwarded = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    raw_ip = forwarded.split(",")[0].strip()
    ip_hash = hashlib.sha256(f"{salt}:{raw_ip}".encode()).hexdigest()
    today = date.today()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT count FROM anon_verify_counts WHERE ip_hash=%s AND day=%s",
            (ip_hash, today)
        )
        row = cur.fetchone()
        conn.close()
        return (row[0] if row else 0) < 3
    except Exception as e:
        print(f"[anon_ceiling_ok] DB error: {e}")
        return True  # fail open


def anon_ceiling_increment(get_db, request):
    """Increment anon daily verify counter for this IP.
    Call only after successful verify_and_insert_claims.
    """
    import hashlib, os
    from datetime import date
    salt = os.environ.get("SECRET_KEY", "vs-anon-salt")
    forwarded = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    raw_ip = forwarded.split(",")[0].strip()
    ip_hash = hashlib.sha256(f"{salt}:{raw_ip}".encode()).hexdigest()
    today = date.today()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO anon_verify_counts (ip_hash, day, count) VALUES (%s, %s, 1)"
            " ON CONFLICT (ip_hash, day) DO UPDATE"
            " SET count = anon_verify_counts.count + 1",
            (ip_hash, today)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[anon_ceiling_increment] DB error: {e}")


@app.route('/report', methods=['GET'])


def report_page():
    url = request.args.get('url', '').strip()
    if not url:
        return redirect('/')
    # Phase 4: depth-aware verification. ?depth=2 (free) or ?depth=99 (paid).
    # Default = None (full verification, current behavior).
    _access = resolve_report_access(get_db)
    depth          = _access['depth']       # server-derived; ?depth= no longer trusted
    _tier          = _access['tier']
    _gate_user     = _access['user']        # used by quota gate on path A
    _audit_override = _access.get('audit_override', False)  # TEMP: Session 3.5 audit key

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
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Analyzing — Verum Signal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,wght@0,400;0,500;0,600;1,400&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
  :root{{
    --vs-bg:#0a0a0a;
    --vs-surface:#131218;
    --vs-border:#2e2c36;
    --vs-border-strong:#3a3743;
    --vs-pink:#a855f7;
    --vs-violet-light:#c084fc;
    --vs-logo-pink:#ec4899;
    --vs-green:#22c55e;
    --vs-green-15:rgba(34,197,94,0.15);
    --vs-text:#ffffff;
    --vs-text-2:#9ca3af;
    --vs-text-3:#6b7280;
    --font-display:'DM Serif Display','Times New Roman',serif;
    --font-sans:'DM Sans',system-ui,sans-serif;
    --font-mono:ui-monospace,'SF Mono',Menlo,monospace;
  }}
  *{{box-sizing:border-box;}}
  html,body{{height:100%;}}
  body{{
    margin:0;background:var(--vs-bg);color:var(--vs-text);
    font-family:var(--font-sans);font-size:15px;line-height:1.4;letter-spacing:-0.005em;
    -webkit-font-smoothing:antialiased;
    display:flex;align-items:center;justify-content:center;
    min-height:100dvh;position:relative;overflow:hidden;
  }}
  body::before{{
    content:"";position:absolute;top:30%;left:50%;
    width:560px;height:560px;transform:translate(-50%,-50%);
    background:radial-gradient(circle, rgba(168,85,247,0.14) 0%, transparent 55%);
    pointer-events:none;
  }}
  .screen{{
    position:relative;z-index:1;width:100%;max-width:440px;
    padding:60px 44px 48px;display:flex;flex-direction:column;align-items:center;
  }}
  .mark{{
    position:relative;width:184px;height:184px;
    display:flex;align-items:center;justify-content:center;margin-bottom:30px;
  }}
  .mark .ring{{
    position:absolute;inset:0;border-radius:50%;
    border:1px solid rgba(168,85,247,0.35);opacity:0;
    animation:vs-radiate 2.6s ease-out infinite;
  }}
  .mark .ring:nth-child(2){{animation-delay:0.8s;}}
  .mark .ring:nth-child(3){{animation-delay:1.6s;}}
  .mark .core{{
    position:relative;z-index:2;width:92px;height:92px;border-radius:50%;
    background:radial-gradient(circle, #1a1330 0%, #0a0a0f 80%);
    border:1px solid rgba(168,85,247,0.5);
    box-shadow:0 0 60px rgba(168,85,247,0.35), inset 0 1px 0 rgba(255,255,255,0.08);
    display:flex;align-items:center;justify-content:center;
  }}
  .mark .core .glyph-dot{{transform-origin:27.5px 14px;animation:vs-pulse 1.8s ease-in-out infinite;}}
  .wm{{display:block;height:22px;width:auto;margin-bottom:18px;}}
  .eyebrow{{
    font-family:var(--font-mono);font-size:10px;font-weight:500;
    letter-spacing:0.20em;text-transform:uppercase;color:var(--vs-text-3);
    display:flex;align-items:center;gap:8px;margin-bottom:16px;
  }}
  .eyebrow .dot{{
    width:6px;height:6px;border-radius:50%;
    background:var(--vs-green);box-shadow:0 0 8px var(--vs-green);
    animation:vs-pulse 1.4s ease-in-out infinite;
  }}
  .url-pill{{
    max-width:100%;display:inline-flex;align-items:center;gap:9px;
    padding:10px 18px;border-radius:100px;
    background:rgba(168,85,247,0.10);border:0.5px solid rgba(168,85,247,0.35);
    margin-bottom:26px;
  }}
  .url-pill .scheme{{font-family:var(--font-mono);font-size:12.5px;color:var(--vs-text-3);flex-shrink:0;}}
  .url-pill .host{{
    font-family:var(--font-mono);font-size:12.5px;letter-spacing:0.01em;color:var(--vs-text);
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  }}
  .status{{
    font-family:var(--font-display);font-weight:400;font-size:24px;line-height:1.2;
    color:var(--vs-text-2);text-align:center;max-width:300px;margin:0 0 30px;
  }}
  .status em{{font-style:italic;color:var(--vs-violet-light);}}
  .phases{{
    list-style:none;margin:0;padding:0;width:100%;max-width:330px;
    display:flex;flex-direction:column;gap:14px;
  }}
  .phase{{display:flex;align-items:center;gap:12px;opacity:0.42;transition:opacity .3s ease;}}
  .phase .circle{{
    position:relative;flex-shrink:0;width:18px;height:18px;border-radius:50%;
    border:1px solid var(--vs-border-strong);background:transparent;
    display:flex;align-items:center;justify-content:center;
    transition:border-color .3s ease,background-color .3s ease;
  }}
  .phase .circle .core2{{position:absolute;inset:3px;border-radius:50%;background:var(--vs-violet-light);opacity:0;}}
  .phase .circle .check{{width:10px;height:10px;opacity:0;}}
  .phase .label{{flex:1;font-size:13.5px;color:var(--vs-text-2);position:relative;overflow:hidden;}}
  .phase.active{{opacity:1;}}
  .phase.active .circle{{border-color:var(--vs-violet-light);}}
  .phase.active .circle .core2{{opacity:1;animation:vs-pulse 1.2s ease-in-out infinite;}}
  .phase.active .label{{color:var(--vs-text);font-weight:500;}}
  .phase.active .label::after{{
    content:"";position:absolute;inset:0;
    background:linear-gradient(90deg,transparent 0%,rgba(168,85,247,0.18) 50%,transparent 100%);
    transform:translateX(-100%);animation:vs-shimmer-row 1.6s linear infinite;
  }}
  .phase.done{{opacity:1;}}
  .phase.done .circle{{border-color:var(--vs-green);background:var(--vs-green-15);}}
  .phase.done .circle .check{{opacity:1;}}
  .phase.done .label{{color:var(--vs-text-2);}}
  .foot{{
    width:100%;display:flex;align-items:center;justify-content:space-between;gap:14px;
    margin-top:32px;padding-top:18px;border-top:1px solid var(--vs-border);
  }}
  .cancel{{display:inline-flex;align-items:center;gap:7px;font-size:13px;color:var(--vs-text-2);text-decoration:none;transition:color .2s ease;}}
  .cancel:hover,.cancel:active{{color:var(--vs-text);}}
  .cancel svg{{width:14px;height:14px;}}
  .method{{
    display:inline-flex;align-items:center;gap:7px;
    font-family:var(--font-mono);font-size:10.5px;letter-spacing:0.06em;color:var(--vs-text-3);
    padding:4px 9px;border-radius:6px;border:1px solid var(--vs-border);background:var(--vs-surface);white-space:nowrap;
  }}
  .method i{{width:4px;height:4px;border-radius:50%;background:var(--vs-pink);}}
  @keyframes vs-radiate{{0%{{transform:scale(0.4);opacity:0.9;}}70%{{opacity:0.05;}}100%{{transform:scale(1.4);opacity:0;}}}}
  @keyframes vs-pulse{{0%,100%{{opacity:1;transform:scale(1);}}50%{{opacity:0.55;transform:scale(0.82);}}}}
  @keyframes vs-shimmer-row{{0%{{transform:translateX(-100%);}}100%{{transform:translateX(100%);}}}}
  @media (max-width:440px){{.screen{{padding:44px 26px 32px;}}.method{{display:none;}}}}
  @media (prefers-reduced-motion:reduce){{
    .mark .ring,.mark .core .glyph-dot,.eyebrow .dot,
    .phase.active .circle .core2,.phase.active .label::after{{animation:none !important;}}
  }}
</style>
</head>
<body>
  <main class="screen" role="status" aria-live="polite" aria-label="Analyzing article">
    <div class="mark">
      <span class="ring"></span><span class="ring"></span><span class="ring"></span>
      <div class="core">
        <svg width="52" height="48" viewBox="0 0 30 28" fill="none" aria-hidden="true">
          <path class="wave" d="M3 14 Q7 4 11 14 Q15 24 19 14 Q23 4 26 14" stroke="#a855f7" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
          <circle class="glyph-dot" cx="27.5" cy="14" r="2.6" fill="#ec4899"/>
        </svg>
      </div>
    </div>
    <svg class="wm" viewBox="3 5 135 18" xmlns="http://www.w3.org/2000/svg" aria-label="Verum Signal">
      <path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="25" cy="14" r="2.5" fill="#ec4899"/>
      <text x="32" y="19" font-family="'Trebuchet MS',sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text>
      <text x="88" y="19" font-family="'Trebuchet MS',sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text>
    </svg>
    <div class="eyebrow"><span class="dot"></span>Received</div>
    <div class="url-pill" title="{url}">
      <span class="scheme">https://</span>
      <span class="host">{url.split('://')[-1][:60]}{'...' if len(url.split('://')[-1]) > 60 else ''}</span>
    </div>
    <p class="status">Reading the article and<br><em>weighing every claim.</em></p>
    <ul class="phases" id="pipeline">
      <li class="phase active" data-step="0">
        <span class="circle"><span class="core2"></span><svg class="check" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg></span>
        <span class="label">Retrieving article content</span>
      </li>
      <li class="phase" data-step="1">
        <span class="circle"><span class="core2"></span><svg class="check" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg></span>
        <span class="label">Extracting factual claims</span>
      </li>
      <li class="phase" data-step="2">
        <span class="circle"><span class="core2"></span><svg class="check" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg></span>
        <span class="label">Verifying claims against sources</span>
      </li>
      <li class="phase" data-step="3">
        <span class="circle"><span class="core2"></span><svg class="check" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg></span>
        <span class="label">Generating analysis</span>
      </li>
    </ul>
    <div class="foot">
      <a class="cancel" href="/">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
        Cancel
      </a>
      <span class="method"><i></i>Methodology v1.7</span>
    </div>
  </main>
<script>
let attempts = 0;
const maxAttempts = 40;
const encodedUrl = encodeURIComponent('{url}');
const depthParam = '{depth_param}';

function renderPhases(currentStage) {{
  document.querySelectorAll('#pipeline .phase').forEach(function(el, i) {{
    el.className = 'phase' + (i < currentStage ? ' done' : i === currentStage ? ' active' : '');
  }});
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
        renderPhases(4);
        window.location.href = '/report?url=' + encodedUrl + '&_async=1' + depthParam;
      }} else {{
        if (attempts === 3) renderPhases(1);
        if (attempts === 8) renderPhases(2);
        if (attempts === 15) renderPhases(3);
        setTimeout(checkStatus, 3000);
      }}
    }})
    .catch(() => setTimeout(checkStatus, 3000));
}}

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
                        cur2.execute("INSERT INTO articles (title, source_name, url, fetched_at, claims_verified) VALUES (%s, %s, %s, NOW(), FALSE) ON CONFLICT (url) DO NOTHING RETURNING id", (title_text, domain, _normalize_url(url)))
                        row2 = cur2.fetchone()
                        if row2 is None:
                            # Article already exists — fetch its id
                            cur2.execute("SELECT id FROM articles WHERE url = %s LIMIT 1", (_normalize_url(url),))
                            row2 = cur2.fetchone()
                        art_id = row2[0]

                        # ── Quota gate (logged-in) + anon ceiling ──────────
                        from auth_routes import get_current_user, check_quota, increment_quota
                        _gate_user = get_current_user(get_db)
                        if _gate_user:
                            # Logged-in: check monthly report quota
                            _quota = check_quota(get_db, _gate_user['id'], 'consumer')
                            if not _quota['allowed']:
                                conn2.close()
                                conn.close()
                                return redirect(
                                    f'/pricing.html?reason=quota_exceeded'
                                    f'&used={_quota["used"]}'
                                    f'&limit={_quota["limit"]}'
                                    f'&tier={_quota["tier"]}'
                                )
                        else:
                            # Anonymous: enforce 3/day/IP ceiling (skip for audit override)
                            if not _audit_override and not anon_ceiling_ok(get_db, request):
                                conn2.close()
                                conn.close()
                                return (lambda _url=url, _source=source: f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Daily limit reached — Verum Signal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,wght@0,400;0,500;0,600;1,400&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
  *{{box-sizing:border-box;}}
  body{{margin:0;background:#080810;color:#e8e8f0;font-family:'DM Sans',ui-sans-serif,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:32px 16px;}}
  .stage{{width:100%;max-width:480px;}}
  .top-bar{{display:flex;align-items:center;justify-content:center;padding:0 0 20px;border-bottom:0.5px solid rgba(255,255,255,0.06);margin-bottom:32px;}}
  .mark{{display:flex;align-items:center;justify-content:center;margin-bottom:28px;}}
  .mark svg{{opacity:0.9;}}
  .panel{{background:#0d0d18;border:0.5px solid rgba(255,255,255,0.07);border-radius:12px;padding:32px 28px;text-align:center;}}
  .eyebrow{{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:0.16em;text-transform:uppercase;color:rgba(232,232,240,0.35);margin-bottom:14px;}}
  h1{{font-family:'DM Serif Display',Georgia,serif;font-size:26px;font-weight:400;line-height:1.2;color:#f0f0f8;margin:0 0 12px;}}
  h1 em{{font-style:italic;color:#c084fc;}}
  .sub{{font-size:13px;line-height:1.65;color:rgba(232,232,240,0.5);margin:0 0 24px;max-width:340px;margin-left:auto;margin-right:auto;}}
  .article-pill{{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border-radius:100px;background:rgba(255,255,255,0.04);border:0.5px solid rgba(255,255,255,0.09);font-family:ui-monospace,monospace;font-size:11px;color:rgba(232,232,240,0.4);margin-bottom:28px;max-width:100%;overflow:hidden;}}
  .article-pill .host{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
  .cta-btn{{display:inline-block;padding:12px 28px;background:#7c3aed;color:#fff;border-radius:8px;font-size:14px;font-weight:500;text-decoration:none;letter-spacing:0.01em;transition:background 0.15s ease;margin-bottom:12px;}}
  .cta-btn:hover{{background:#6d28d9;}}
  .reset-note{{font-family:ui-monospace,monospace;font-size:11px;color:rgba(232,232,240,0.3);margin-bottom:24px;}}

  .count-row{{display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:20px;}}
  .count-pip{{width:8px;height:8px;border-radius:50%;background:#a855f7;}}
  .count-pip.used{{background:rgba(255,255,255,0.12);}}
  .count-label{{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:rgba(232,232,240,0.3);text-transform:uppercase;margin-left:4px;}}
</style>
</head>
<body>
  <div class="stage">
    <div class="top-bar">
      <svg viewBox="3 5 135 18" height="20" xmlns="http://www.w3.org/2000/svg" aria-label="Verum Signal">
        <path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="25" cy="14" r="2.5" fill="#ec4899"/>
        <text x="32" y="19" font-family="'Trebuchet MS',sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text>
        <text x="88" y="19" font-family="'Trebuchet MS',sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text>
      </svg>
    </div>
    <div class="panel">
      <div class="eyebrow">Daily limit reached</div>
      <h1>You've used your 3 free<br><em>reports today.</em></h1>
      <p class="sub">Free reports reset at midnight. Upgrade to Pro for unlimited reports, full claim breakdowns, and source analysis.</p>
      <div class="count-row">
        <span class="count-pip"></span>
        <span class="count-pip"></span>
        <span class="count-pip"></span>
        <span class="count-pip used"></span>
        <span class="count-label">3 / 3 used today</span>
      </div>
      <div class="article-pill">
        <span>&#128279;</span>
        <span class="host">{_url.split("://")[-1][:55]}{"..." if len(_url.split("://")[-1]) > 55 else ""}</span>
      </div>
      <br>
      <a href="/pricing.html?url={_url}" class="cta-btn">See Pro plan &rarr;</a>
      <p class="reset-note">Free reports reset daily &middot; No card required for Pro trial</p>

    </div>
  </div>
</body>
</html>""")(), 200, {'Content-Type': 'text/html'}
                        # ── End quota / ceiling gate ────────────────────────

                        verified_claims = verify_and_insert_claims(claims, art_id, title_text, domain, cur2, depth=depth)

                        # Increment quota / ceiling counter on successful verification
                        if _gate_user:
                            increment_quota(get_db, _gate_user['id'], 'consumer')
                        elif not _audit_override:
                            anon_ceiling_increment(get_db, request)
                        conn2.commit()
                        conn2.close()
                        rows = verified_claims
                        sc = sum(1 for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                        ws = sum(WEIGHTS[c[5]] for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                        score = compute_score(ws, sc)
                        rating = compute_score_band(score)
                        data = _build_report_data(
                            rows, url=url, title=title_text, source=domain,
                            as_of=dt.now().strftime('%B %d, %Y'),
                            score=score, rating=rating,
                            extraction_method=extraction_method)
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
            cur.execute("SELECT id, claim_text, speaker, claim_type, claim_origin, verdict, confidence_score, verdict_summary, full_analysis, sources_used, sources_structured FROM claims WHERE article_id = %s ORDER BY priority_score DESC, id ASC", (art_id,))
            rows = cur.fetchall()
            conn.close()
            # Treat all-NULL-verdict rows (RSS-ingested, unverified) same as no rows
            # so paid/audit requests trigger verification rather than serving NOT YET VERIFIED
            _all_unverified = rows and all(r[5] is None for r in rows)
            if not rows or _all_unverified:
                # Trigger on-demand extraction for articles in DB but not yet extracted.
                # Routed through fetch_article_content (the three-method fetcher) to handle
                # anti-bot, paywalled, and JS-rendered sites uniformly with the user-submitted path.
                try:
                    import anthropic as _anth_cached
                    from extract_claims import extract_claims_from_article
                    from verdict_engine import analyse_claim
                    _anth_client_cached = _anth_cached.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
                    fetch_result = fetch_article_content(art_url, _anth_client_cached)
                    _fetch_failed = False
                    if fetch_result is None:
                        print(f'[cached-path] All fetch methods failed for {art_url}')
                        data = {'status': 'scrape_failed', 'title': title_db, 'source': source_name, 'url': art_url}
                        claims = []
                        _fetch_failed = True
                    elif fetch_result.get('status') == 'paywall':
                        print(f'[cached-path] Paywall detected for {art_url}')
                        data = {'status': 'paywall', 'title': title_db, 'source': source_name, 'url': art_url}
                        claims = []
                        _fetch_failed = True
                    else:
                        body_text = fetch_result.get('body', '')
                        title_text = fetch_result.get('title') or title_db
                        article_dict = {'title': title_text, 'description': body_text[:500], 'content': body_text, 'source': {'name': source_name}, 'url': art_url, 'publishedAt': ''}
                        claims = extract_claims_from_article(article_dict)
                    if not _fetch_failed and not claims:
                        data = {'status': 'no_claims', 'title': title_db, 'source': source_name, 'url': art_url}
                    else:
                        conn2 = get_db()
                        cur2 = conn2.cursor()
                        # Quota gate + anon ceiling on path B (cached-empty reextract)
                        from auth_routes import check_quota, increment_quota
                        if _gate_user:
                            # Logged-in: check monthly report quota
                            _quota_b = check_quota(get_db, _gate_user["id"], "consumer")
                            if not _quota_b["allowed"]:
                                conn2.close()
                                return redirect(
                                    f'/pricing.html?reason=quota_exceeded'
                                    f'&used={_quota_b["used"]}'
                                    f'&limit={_quota_b["limit"]}'
                                    f'&tier={_quota_b["tier"]}'
                                )
                        else:
                            # Anonymous: enforce 3/day/IP ceiling (skip for audit override)
                            if not _audit_override and not anon_ceiling_ok(get_db, request):
                                conn2.close()
                                return (lambda _url=url, _source=source: f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Daily limit reached — Verum Signal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,wght@0,400;0,500;0,600;1,400&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
  *{{box-sizing:border-box;}}
  body{{margin:0;background:#080810;color:#e8e8f0;font-family:'DM Sans',ui-sans-serif,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:32px 16px;}}
  .stage{{width:100%;max-width:480px;}}
  .top-bar{{display:flex;align-items:center;justify-content:center;padding:0 0 20px;border-bottom:0.5px solid rgba(255,255,255,0.06);margin-bottom:32px;}}
  .mark{{display:flex;align-items:center;justify-content:center;margin-bottom:28px;}}
  .mark svg{{opacity:0.9;}}
  .panel{{background:#0d0d18;border:0.5px solid rgba(255,255,255,0.07);border-radius:12px;padding:32px 28px;text-align:center;}}
  .eyebrow{{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:0.16em;text-transform:uppercase;color:rgba(232,232,240,0.35);margin-bottom:14px;}}
  h1{{font-family:'DM Serif Display',Georgia,serif;font-size:26px;font-weight:400;line-height:1.2;color:#f0f0f8;margin:0 0 12px;}}
  h1 em{{font-style:italic;color:#c084fc;}}
  .sub{{font-size:13px;line-height:1.65;color:rgba(232,232,240,0.5);margin:0 0 24px;max-width:340px;margin-left:auto;margin-right:auto;}}
  .article-pill{{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border-radius:100px;background:rgba(255,255,255,0.04);border:0.5px solid rgba(255,255,255,0.09);font-family:ui-monospace,monospace;font-size:11px;color:rgba(232,232,240,0.4);margin-bottom:28px;max-width:100%;overflow:hidden;}}
  .article-pill .host{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
  .cta-btn{{display:inline-block;padding:12px 28px;background:#7c3aed;color:#fff;border-radius:8px;font-size:14px;font-weight:500;text-decoration:none;letter-spacing:0.01em;transition:background 0.15s ease;margin-bottom:12px;}}
  .cta-btn:hover{{background:#6d28d9;}}
  .reset-note{{font-family:ui-monospace,monospace;font-size:11px;color:rgba(232,232,240,0.3);margin-bottom:24px;}}

  .count-row{{display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:20px;}}
  .count-pip{{width:8px;height:8px;border-radius:50%;background:#a855f7;}}
  .count-pip.used{{background:rgba(255,255,255,0.12);}}
  .count-label{{font-family:ui-monospace,monospace;font-size:10px;letter-spacing:0.1em;color:rgba(232,232,240,0.3);text-transform:uppercase;margin-left:4px;}}
</style>
</head>
<body>
  <div class="stage">
    <div class="top-bar">
      <svg viewBox="3 5 135 18" height="20" xmlns="http://www.w3.org/2000/svg" aria-label="Verum Signal">
        <path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="25" cy="14" r="2.5" fill="#ec4899"/>
        <text x="32" y="19" font-family="'Trebuchet MS',sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text>
        <text x="88" y="19" font-family="'Trebuchet MS',sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text>
      </svg>
    </div>
    <div class="panel">
      <div class="eyebrow">Daily limit reached</div>
      <h1>You've used your 3 free<br><em>reports today.</em></h1>
      <p class="sub">Free reports reset at midnight. Upgrade to Pro for unlimited reports, full claim breakdowns, and source analysis.</p>
      <div class="count-row">
        <span class="count-pip"></span>
        <span class="count-pip"></span>
        <span class="count-pip"></span>
        <span class="count-pip used"></span>
        <span class="count-label">3 / 3 used today</span>
      </div>
      <div class="article-pill">
        <span>&#128279;</span>
        <span class="host">{_url.split("://")[-1][:55]}{"..." if len(_url.split("://")[-1]) > 55 else ""}</span>
      </div>
      <br>
      <a href="/pricing.html?url={_url}" class="cta-btn">See Pro plan &rarr;</a>
      <p class="reset-note">Free reports reset daily &middot; No card required for Pro trial</p>

    </div>
  </div>
</body>
</html>""")(), 200, {'Content-Type': 'text/html'}
                        verified_claims = []
                        verified_claims = verify_and_insert_claims(claims, art_id, title_db, source_name, cur2, depth=depth)
                        if _gate_user:
                            increment_quota(get_db, _gate_user["id"], "consumer")
                        elif not _audit_override:
                            anon_ceiling_increment(get_db, request)
                        cur2.execute("UPDATE articles SET claims_verified=TRUE WHERE id=%s", (art_id,))
                        conn2.commit()
                        conn2.close()
                        rows = verified_claims
                        sc = sum(1 for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                        ws = sum(WEIGHTS[c[5]] for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                        score = compute_score(ws, sc)
                        rating = compute_score_band(score)
                        data = _build_report_data(
                            rows, url=art_url, title=title_db, source=source_name,
                            as_of=dt.now().strftime('%B %d, %Y'),
                            score=score, rating=rating)
                except Exception as e:
                    print(f"On-demand extraction (no_claims path) failed: {e}")
                    data = {'status': 'no_claims', 'title': title_db, 'source': source_name}
            else:
                sc = sum(1 for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                ws = sum(WEIGHTS[c[5]] for c in rows if c[5] in WEIGHTS and c[4] == 'outlet_claim')
                score = compute_score(ws, sc)
                rating = compute_score_band(score)
                scoreable_labels = {'supported':'supported by independent sources','plausible':'consistent with one credible source','corroborated':'corroborated by 5+ outlets','overstated':'overstated relative to evidence','disputed':'disputed by a credible source','not_supported':'actively contradicted by evidence'}
                parts = []
                for vk, lbl in scoreable_labels.items():
                    cnt = sum(1 for c in rows if c[5] == vk)
                    if cnt:
                        parts.append(f"{cnt} {'was' if cnt == 1 else 'were'} {lbl}")
                # Task 6: count attributed_claim (score-excluded by independence rule)
                attributed_count = sum(1 for c in rows if c[4] == 'attributed_claim')
                excl = []
                if attributed_count: excl.append(f"{attributed_count} attributed claim{'s' if attributed_count>1 else ''} excluded from outlet score")
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
                callout_text += "The independence rule and attributed-claim scoring exclusion were applied where relevant."
                # Task 5a: drive as_of from real assessment provenance, not today
                if vat:
                    _path_c_as_of = vat.strftime('%B %d, %Y')
                else:
                    # Fallback: MAX(last_checked) over claims for this article
                    try:
                        _lc_conn = get_db()
                        _lc_cur = _lc_conn.cursor()
                        _lc_cur.execute(
                            "SELECT MAX(last_checked) FROM claims WHERE article_id=%s",
                            (art_id,)
                        )
                        _lc_row = _lc_cur.fetchone()
                        _lc_conn.close()
                        _path_c_as_of = (_lc_row[0].strftime('%B %d, %Y')
                            if _lc_row and _lc_row[0] else "assessment date unavailable")
                    except Exception:
                        _path_c_as_of = "assessment date unavailable"
                data = _build_report_data(
                    rows, url=art_url, title=title_db, source=source_name,
                    as_of=_path_c_as_of, score=score, rating=rating,
                    callout_text=callout_text)
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
            'all_attributed': {
                'icon': '\u2139\ufe0f',
                'heading': 'Claims are from other sources',
                'msg': 'This article reports statements made by third parties and does not contain scoreable assertions by this outlet.',
                'detail': 'Verum Signal scores factual claims made directly by the outlet. This article is composed entirely of attributed quotes and sourced reporting, which do not count toward the outlet\u2019s reliability score. This is common in wire rewrites, press conference roundups, and aggregation pieces.',
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
    # Detect all-attributed case: claims exist but none are outlet_claim
    _score_was_none = (score is None)
    _all_attributed = False
    if _score_was_none and data.get('claims'):
        _all_attributed = all(
            c.get('claim_origin') in ('attributed_claim', 'wire_reprint')
            for c in data.get('claims', [])
            if c.get('claim_origin')
        )
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
        'supported': '+1.0', 'plausible': '+0.5', 'corroborated': '+0.75',
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

    # Confidence scale — module-level so render block can build the legend from it
    CONF_EXPLAIN = {
        1: 'Plausible or difficult to independently verify — one source found, or inherently hard to confirm.',
        2: 'One credible source with original reporting confirmed this claim (or consistent non-independent reporting).',
        3: 'Two or more genuinely independent sources with original reporting confirmed this claim.',
    }

    def claim_row(c, idx):

        v = c.get('verdict') or 'pending'
        VBAR = {'supported':'#4ade80','plausible':'#60a5fa','corroborated':'#34d399','overstated':'#fb923c','disputed':'#f87171','not_supported':'#ef4444','opinion':'rgba(255,255,255,0.1)','not_verifiable':'rgba(255,255,255,0.1)','pending':'rgba(255,255,255,0.06)'}
        VPILL = {'supported':'p-sup','plausible':'p-pla','corroborated':'p-cor','overstated':'p-ove','disputed':'p-dis','not_supported':'p-nsu','opinion':'p-opi','not_verifiable':'p-nve','pending':'p-pend'}
        VLBL = {'supported':'SUPPORTED','plausible':'PLAUSIBLE','corroborated':'CORROBORATED','overstated':'OVERSTATED','disputed':'DISPUTED','not_supported':'NOT SUPPORTED','opinion':'OPINION','not_verifiable':'NOT VERIFIABLE','pending':'NOT YET VERIFIED'}
        bar_col = VBAR.get(v, 'rgba(255,255,255,0.1)')
        pill_cls = VPILL.get(v, 'p-opi')
        lbl = VLBL.get(v, v.upper())
        text = smartquotes(c.get('claim_text', ''))
        text = text[:1].upper() + text[1:] if text else text
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
        conf_label = {
            1: "Confidence: 1/3 — plausible, or difficult to independently verify",
            2: "Confidence: 2/3 — one credible source with original reporting",
            3: "Confidence: 3/3 — two or more genuinely independent sources",
        }.get(confidence, "")
        conf_num_html = '<span class="vs-conf-num">' + str(confidence) + '/3</span>'
        conf_html = '<div class="vs-conf" title="' + conf_label + '">' + conf_num_html + conf_dots + '</div>'
        # Wire tag
        wire_html = '<span class="vs-wire">WIRE REPRINT &mdash; excluded from score</span>' if is_wire else ''
        # Source pills — structured-first, prose fallback for legacy rows
        contradicts = v in ('disputed','not_supported','overstated')
        src_structured = c.get('sources_structured') or []
        src_pills = ''
        if src_structured:
            # Render structured sources as citation chains with independence badges
            for s in src_structured[:6]:
                name = s.get('name','') or ''
                indep = s.get('independent', False)
                stype = s.get('type','secondary') or 'secondary'
                note  = s.get('note','') or ''
                pill_cls = 'vs-src-c' if contradicts else ('vs-src-p' if indep else 'vs-src-s')
                indep_badge = '<span class="vs-src-indep" title="Independently confirmed">&#10003; independent</span>' if indep else ''
                src_pills += '<span class="vs-src ' + pill_cls + '" title="' + note + '">' + name + indep_badge + '</span>'
        else:
            # Prose fallback: extract domains from free-text sources_used
            valid_tlds3 = {'com','org','gov','edu','net','io','co','uk','de','fr'}
            src_domains = []
            for word in sources.replace(',',' ').split():
                w = word.strip('().-').lower()
                parts = w.split('.')
                if len(parts) >= 2 and parts[-1] in valid_tlds3 and len(parts[0]) > 1:
                    src_domains.append(w)
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
            + ('<div class="vs-contested"><span class="vs-contested-label">What’s contested</span> ' + summary + '</div>' if contradicts and summary else '') +
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


    # Phase 5: simpler claim card for free report template
    def claim_row_free(c, idx):
        v = c.get('verdict') or 'pending'
        VLBL_FREE = {'supported':'SUPPORTED','plausible':'PLAUSIBLE','corroborated':'CORROBORATED','overstated':'OVERSTATED','disputed':'DISPUTED','not_supported':'NOT SUPPORTED','opinion':'OPINION','not_verifiable':'NOT VERIFIABLE','pending':'NOT YET VERIFIED'}
        text = smartquotes(c.get('claim_text', ''))
        text = text[:1].upper() + text[1:] if text else text
        summary = c.get('verdict_summary', '') or ''
        sources = c.get('sources_used', '') or ''
        src_structured_free = c.get('sources_structured') or []
        confidence = int(c.get('confidence_score', 2) or 2)
        lbl = VLBL_FREE.get(v, v.upper())
        summary_html = ('<p class="claim-summary">' + smartquotes(summary) + '</p>') if summary else ''
        sources_html = (
            ''.join(
                '<span class="fr-src-item">' + (s.get('name') or '') +
                (' <span class="fr-src-indep">&#10003;</span>' if s.get('independent') else '') +
                '</span>' for s in src_structured_free[:4]
            ) if src_structured_free
            else '<p class="sources-list">' + sources + '</p>'
        )
        return (
            '<div class="claim-card ' + v + '">'
            '<div class="claim-header">'
            '<div class="claim-header-left">'
            '<div class="claim-num">CLAIM ' + str(idx) + '</div>'
            '<p class="claim-text">' + text + '</p>'
            '</div>'
            '<div class="claim-header-right">'
            '<span class="verdict-pill ' + v + '">' + lbl + ' &middot; ' + str(confidence) + '/3</span>'
            '<span class="claim-toggle">Show details ▾</span>'
            '</div>'
            '</div>'
            '<div class="claim-body">'
            + summary_html +
            '<div class="sources-label">SOURCES</div>'
            + sources_html +
            '</div>'
            '</div>'
        )
    # Build only the HTML for the template being served (Task 3)
    _FREE_CLAIM_CAP = 2
    free_set = claims[:_FREE_CLAIM_CAP]
    if depth == 2:
        claims_html = "".join(claim_row_free(c, i+1) for i, c in enumerate(free_set))
        claims_html_free = claims_html
    else:
        claims_html_free = ""
        claims_html = "".join(claim_row(c, i+1) for i, c in enumerate(claims))

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
    if len(claims) >= 3 and depth != 2:
        try:
            article_claim_texts = [c.get('claim_text', '') for c in claims if c.get('claim_text')]
            import anthropic as _bg_anth
            _bg_client = _bg_anth.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
            bg_facts = get_background_signal(article_claim_texts, _bg_client, article_id=art_id)
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
                        '<div class="vs-bgsig-item" style="padding:12px 14px;'
                        'background:rgba(255,255,255,0.03);'
                        'border-left:3px solid rgba(168,85,247,0.5);'
                        'border-radius:0 6px 6px 0;margin-bottom:8px;">'
                        '<div style="font-size:13px;color:rgba(232,232,240,0.9);'
                        'line-height:1.6;margin-bottom:8px;">'
                        '\u2192 ' + (f.get('fact') or '') + '</div>'
                        '<div style="font-size:10px;font-family:monospace;'
                        'color:rgba(255,255,255,0.45);display:flex;gap:8px;flex-wrap:wrap;">'
                        '<span style="color:rgba(168,85,247,0.6);">SOURCE</span> ' + _src_html + '\u00a0\u00b7\u00a0'
                        '<span style="color:rgba(168,85,247,0.6);">RELEVANCE</span> ' + (f.get('relevance_tag') or '') + '</div>'
                        '</div>'
                    )
                background_signal_html = (
                    '<div class="vs-section" style="margin-top:32px;'
                    'background:rgba(168,85,247,0.04);'
                    'border:0.5px solid rgba(168,85,247,0.15);'
                    'border-radius:8px;padding:16px 20px;">'
                    '<div style="display:flex;align-items:center;gap:8px;'
                    'font-family:monospace;font-size:11px;'
                    'letter-spacing:0.18em;color:#a855f7;'
                    'text-transform:uppercase;margin-bottom:6px;">'
                    '<span style="width:6px;height:6px;border-radius:50%;'
                    'background:#a855f7;flex-shrink:0;"></span>'
                    'Background Signal</div>'
                    '<div style="font-size:11px;color:rgba(255,255,255,0.45);'
                    'margin-bottom:14px;">'
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
        from token_logging import log_usage as _log_usage; _log_usage('api_report_summary', _msg)
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
    # claims_html already holds the right content (built conditionally above)
    if depth == 2:
        stats_total_for_free = len(free_set)
    else:
        stats_total_for_free = None
    html = html.replace('{{source}}', str(source))
    html = html.replace('{{score}}', str(score))
    pass #removed
    pass #removed
    pass #removed
    html = html.replace('{{rating}}', str(rating))
    html = html.replace('{{as_of}}', str(as_of))
    # Task 5R: compute recheck_available — Pro only, verdict older than RECHECK_STALENESS_DAYS
    _recheck_available = False
    if _tier in ('pro', 'scale') and data.get('status') == 'found':
        _vat = data.get('_verified_at_raw')  # set below if available
        # Use as_of string as proxy if raw datetime not threaded through
        try:
            from datetime import datetime as _dt, timezone as _tz
            # SESSION 6 COUPLING: staleness computed by re-parsing the as_of DISPLAY string.
            # If as_of format ever changes, staleness math breaks silently (no exception).
            # Session 6 fix: thread raw verified_at datetime through render block instead.
            _parsed = _dt.strptime(as_of, '%B %d, %Y') if as_of and as_of != 'assessment date unavailable' else None
            if _parsed:
                _age_days = (_dt.now() - _parsed).days
                _recheck_available = _age_days >= RECHECK_STALENESS_DAYS
        except Exception:
            _recheck_available = False
    if _recheck_available:
        _recheck_html = (
            '<div style="margin:12px 24px 0 24px;padding:10px 14px;'
            'background:rgba(168,85,247,0.06);border:0.5px solid rgba(168,85,247,0.2);'
            'border-radius:4px;display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            '<span style="font-size:12px;color:rgba(232,232,240,0.55);">'
            'Assessed ' + str(as_of) + ' &mdash; verdicts may be outdated.</span>'
            '<button onclick="recheckReport()" '
            'style="font-family:monospace;font-size:11px;letter-spacing:0.08em;'
            'background:rgba(168,85,247,0.15);color:#c084fc;border:0.5px solid rgba(168,85,247,0.35);'
            'border-radius:3px;padding:5px 12px;cursor:pointer;">RE-CHECK</button>'
            '</div>'
            '<script>function recheckReport(){'
            'fetch("/api/report/recheck",{method:"POST",headers:{"Content-Type":"application/json"},'
            'body:JSON.stringify({url:"' + str(url) + '"})})'
            '.then(r=>r.json()).then(d=>{if(d.status==="ok"){location.reload();}'
            'else{alert(d.error||"Re-check failed");}})'
            '.catch(()=>alert("Re-check failed — please try again."));'
            '}</script>'
        )
    else:
        _recheck_html = ''
    html = html.replace('{{recheck_block}}', _recheck_html)
    html = html.replace('{{url}}', str(url))
    html = html.replace('{{title}}', str(title))
    html = html.replace('{{tag_html}}', str(tag_html))
    html = html.replace('{{summary_html}}', str(summary_html))
    html = html.replace('{{short_url}}', str(short_url))
    pass #removed
    html = html.replace('{{score_color}}', str(score_color))
    # Patch 3: render the article-score chip in Python so unscored articles
    # don't display a misleading '0/100 Unscored'.
    if _score_was_none and _all_attributed:
        score_chip_html = '<span class="vs-chip-score" style="color:rgba(255,255,255,0.3)" title="All claims in this article are attributed to third parties">Article: Unscored — attributed sources</span>'
    elif _score_was_none:
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
    elif _all_attributed:
        opinion_inset_html = ('<div class="vs-opinion-inset"><div class="vs-opinion-inset-label">NOT SCORED — CLAIMS ATTRIBUTED TO THIRD PARTIES</div><div class="vs-opinion-inset-text">This article reports statements made by other sources and does not contain scoreable assertions by this outlet. Verum Signal scores factual claims made directly by the outlet — attributed quotes and sourced reporting do not count toward the outlet’s reliability score. The verdicts above reflect the accuracy of the underlying claims, not this outlet’s reporting.</div></div>')
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
    confidence_legend = (
        f'Confidence: 1 = {CONF_EXPLAIN[1].split(" — ")[0].lower()}'
        f' · 2 = one strong primary source'
        f' · 3 = two+ independent sources'
    )
    html = html.replace('{{confidence_legend}}', str(confidence_legend))
    html = html.replace('{{redflag_html}}', str(redflag_html))
    pass #removed
    html = html.replace('{{dist_bar_html}}', str(dist_bar_html))
    html = html.replace('{{dist_legend_html}}', str(dist_legend_html))
    html = html.replace('{{claims_html}}', str(claims_html))
    html = html.replace('{{overall_signal}}', str(overall_signal))
    html = html.replace('{{watch_for_html}}', str(watch_for_html))
    html = html.replace('{{background_signal_html}}', background_signal_html)
    html = html.replace('{{all_sources_html}}', str(all_sources_html))
    html = html.replace('{{compare_html}}', str(compare_html))
    html = html.replace('{{total}}', str(stats_total_for_free if stats_total_for_free is not None else stats.get('total',0)))
    html = html.replace('{{sc}}', str(sc))
    html = html.replace('{{not_verifiable}}', str(stats.get('not_verifiable', 0)))
    # D2: free template passive staleness notice (14-day threshold)
    _staleness_notice = ''
    if depth == 2 and as_of and as_of != 'assessment date unavailable':
        try:
            from datetime import datetime as _dt2
            _assessed_dt = _dt2.strptime(as_of, '%B %d, %Y')  # SESSION 6 COUPLING — see comment above
            _age_days = (datetime.utcnow() - _assessed_dt).days
            if _age_days >= 14:
                _staleness_notice = (
                    '<div style="font-family:ui-monospace,monospace;font-size:11px;'
                    'color:rgba(232,232,240,0.35);padding:10px 0 6px;'
                    'border-top:0.5px solid rgba(255,255,255,0.06);margin-bottom:8px;">'
                    'Assessed ' + as_of + ' &nbsp;&middot;&nbsp; '
                    '<span style="color:rgba(168,85,247,0.5);">Pro users can request a re-check</span>'
                    '</div>'
                )
        except Exception:
            pass
    html = html.replace('{{staleness_notice}}', _staleness_notice)
    html = html.replace("{{seo_meta}}", report_meta(source=str(source), title=str(title), score=score, url=str(url), short_hash=short_url_hash))
    # ClaimReview JSON-LD structured data for Google rich snippets
    from seo import claim_review_jsonld
    jsonld_blocks = []
    _review_date = as_of or ""
    for _c in (free_set if depth == 2 else claims):
        _cv = _c.get("verdict")
        _ct = _c.get("claim_text", "")
        if _cv and _ct:
            jsonld_blocks.append(claim_review_jsonld(
                claim_text=_ct, verdict=_cv, article_url=str(url),
                article_title=str(title), source_name=str(source), review_date=_review_date))
    html = html.replace("</head>", "\n".join(jsonld_blocks) + "\n</head>", 1)
    from flask import Response
    return Response(html, mimetype='text/html')



# ----------------------------------------------------------------------
# Operational dashboard (/api/job-runs JSON + /ops HTML) — Patch 19
# ----------------------------------------------------------------------
# Both routes are basic-auth protected via OPS_PASSWORD env var.
# Username is 'admin', password is OPS_PASSWORD.
# If OPS_PASSWORD is unset, both return 503 (fail closed).

def _ops_auth():
    """Returns None if request is authorized, else a Flask Response."""
    import base64
    from flask import Response

    expected_pw = os.environ.get('OPS_PASSWORD')
    if not expected_pw:
        return Response(
            'OPS_PASSWORD not configured on server',
            status=503,
            mimetype='text/plain',
        )

    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Basic '):
        return Response(
            'Authentication required',
            status=401,
            headers={'WWW-Authenticate': 'Basic realm="Veris Ops"'},
        )

    try:
        decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
        username, _, password = decoded.partition(':')
    except Exception:
        return Response(
            'Malformed Authorization header',
            status=400,
        )

    import hmac
    if username != 'admin' or not hmac.compare_digest(password, expected_pw):
        return Response(
            'Invalid credentials',
            status=401,
            headers={'WWW-Authenticate': 'Basic realm="Veris Ops"'},
        )

    return None


@app.route('/api/job-runs', methods=['GET'])
def api_job_runs():
    """Return last 24h of job_runs as JSON. Basic-auth protected."""
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err

    try:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        id, stage, started_at, finished_at, duration_ms,
                        status, items_processed, error_class, error_message, hostname
                    FROM job_runs
                    WHERE started_at >= NOW() - INTERVAL '24 hours'
                    ORDER BY started_at DESC
                """)
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    rec = dict(zip(cols, row))
                    for k in ('started_at', 'finished_at'):
                        if rec.get(k) is not None:
                            rec[k] = rec[k].isoformat()
                    rows.append(rec)
        finally:
            conn.close()

        return jsonify({'count': len(rows), 'runs': rows})
    except Exception as e:
        return jsonify({'error': type(e).__name__, 'detail': str(e)}), 500



@app.route('/api/token-usage', methods=['GET'])
def api_token_usage():
    """Return last 24h of token usage aggregated by stage. Basic-auth protected.

    Cost is calculated in SQL using Sonnet 4 pricing (May 2026).
    If pricing changes, edit the constants inline below.
    """
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err

    try:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        stage,
                        COUNT(*) AS calls,
                        SUM(input_tokens) AS input_tokens,
                        SUM(output_tokens) AS output_tokens,
                        SUM(cache_creation_input_tokens) AS cache_creation_tokens,
                        SUM(cache_read_input_tokens) AS cache_read_tokens,
                        ROUND( (
                            SUM(input_tokens) * 3.00 / 1000000 +
                            SUM(output_tokens) * 15.00 / 1000000 +
                            SUM(cache_creation_input_tokens) * 3.75 / 1000000 +
                            SUM(cache_read_input_tokens) * 0.30 / 1000000
                        )::numeric, 4) AS approx_usd,
                        MAX(timestamp) AS last_call
                    FROM token_usage
                    WHERE timestamp >= NOW() - INTERVAL '24 hours'
                    GROUP BY stage
                    ORDER BY approx_usd DESC NULLS LAST
                """)
                cols = [d[0] for d in cur.description]
                rows = []
                for row in cur.fetchall():
                    rec = dict(zip(cols, row))
                    if rec.get('last_call') is not None:
                        rec['last_call'] = rec['last_call'].isoformat()
                    if rec.get('approx_usd') is not None:
                        rec['approx_usd'] = float(rec['approx_usd'])
                    rows.append(rec)

                # Also compute a total row
                total_usd = sum(r.get('approx_usd') or 0 for r in rows)
                total_calls = sum(r.get('calls') or 0 for r in rows)
        finally:
            conn.close()

        return jsonify({
            'count': len(rows),
            'total_calls': total_calls,
            'total_usd': round(total_usd, 4),
            'window': '24h',
            'stages': rows,
        })
    except Exception as e:
        return jsonify({'error': type(e).__name__, 'detail': str(e)}), 500



@app.route('/api/corpus-totals', methods=['GET'])
def api_corpus_totals():
    """Return article + claim totals (all-time + 24h delta). Basic-auth protected."""
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err

    try:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                # Articles: all-time + last 24h via fetched_at
                cur.execute("SELECT COUNT(*) FROM articles")
                articles_total = cur.fetchone()[0]

                cur.execute("""
                    SELECT COUNT(*) FROM articles
                    WHERE fetched_at >= NOW() - INTERVAL '24 hours'
                """)
                articles_24h = cur.fetchone()[0]

                # Claims (all)
                cur.execute("SELECT COUNT(*) FROM claims")
                claims_all_total = cur.fetchone()[0]

                cur.execute("""
                    SELECT COUNT(*) FROM claims
                    WHERE first_seen >= NOW() - INTERVAL '24 hours'
                """)
                claims_all_24h = cur.fetchone()[0]

                # Claims (scoreable) per v1.6
                cur.execute("""
                    SELECT COUNT(*) FROM claims c
                    WHERE c.verdict IS NOT NULL
                      AND c.verdict NOT IN ('opinion', 'not_verifiable')
                      AND c.claim_origin = 'outlet_claim'
                """)
                claims_scoreable_total = cur.fetchone()[0]

                cur.execute("""
                    SELECT COUNT(*) FROM claims c
                    WHERE c.verdict IS NOT NULL
                      AND c.verdict NOT IN ('opinion', 'not_verifiable')
                      AND c.claim_origin = 'outlet_claim'
                      AND c.last_checked >= NOW() - INTERVAL '24 hours'
                """)
                claims_scoreable_24h = cur.fetchone()[0]
        finally:
            conn.close()

        return jsonify({
            'articles': {
                'total': articles_total,
                'delta_24h': articles_24h,
            },
            'claims_all': {
                'total': claims_all_total,
                'delta_24h': claims_all_24h,
            },
            'claims_scoreable': {
                'total': claims_scoreable_total,
                'delta_24h': claims_scoreable_24h,
            },
        })
    except Exception as e:
        return jsonify({'error': type(e).__name__, 'detail': str(e)}), 500


_OPS_CHANGELOG_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Veris Changelog</title>
<script>const OPS_AUTH = "__OPS_AUTH_PLACEHOLDER__";</script>
<style>
:root{--bg:#0a0a0a;--fg:#e8e8e8;--fg-dim:#888;--accent:#a855f7;--ok:#4ade80;--bad:#f87171;--border:#1e1e1e;--card:#111;--mono:ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}
h1{font-size:18px;margin:0 0 4px;letter-spacing:-0.01em}
.subtitle{color:var(--fg-dim);font-size:12px;margin-bottom:24px}
.nav-links{font-size:12px;margin-bottom:20px}
.nav-links a{color:var(--accent);text-decoration:none;margin-right:16px}
.nav-links a:hover{color:#c084fc}
h2{font-size:12px;text-transform:uppercase;letter-spacing:0.1em;color:var(--fg-dim);font-weight:500;margin:40px 0 16px}
h2:first-of-type{margin-top:24px}
.deploy-entry{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:18px 20px;margin-bottom:12px}
.deploy-header{display:flex;align-items:baseline;gap:12px;margin-bottom:10px}
.deploy-date{font-family:var(--mono);font-size:11px;color:var(--fg-dim)}
.deploy-label{font-size:13px;font-weight:600;color:var(--fg)}
.deploy-commit{font-family:var(--mono);font-size:11px;color:var(--accent);text-decoration:none}
.deploy-commit:hover{color:#c084fc}
.deploy-items{margin:0;padding:0 0 0 16px;color:var(--fg-dim);font-size:13px}
.deploy-items li{margin-bottom:4px}
.deploy-items li strong{color:var(--fg)}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px}
th{text-align:left;padding:6px 10px;color:var(--fg-dim);font-weight:500;border-bottom:1px solid var(--border);font-size:10px;text-transform:uppercase;letter-spacing:0.08em}
td{padding:6px 10px;border-bottom:1px solid var(--border);vertical-align:top}
td:first-child{white-space:nowrap;color:var(--fg-dim)}
td:nth-child(2){white-space:nowrap;color:var(--fg-dim)}
td:nth-child(3){color:var(--fg)}
a.commit-hash{color:var(--accent);text-decoration:none}
a.commit-hash:hover{color:#c084fc}
tr:hover td{background:rgba(168,85,247,0.04)}
.refresh-info{color:var(--fg-dim);font-size:11px;margin-top:32px}
</style>
</head>
<body>
<a href="/ops" style="text-decoration:none;display:inline-block;margin-bottom:12px"><svg width="160" height="22" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg"><path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="14" r="2.5" fill="#ec4899"/><text x="32" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text><text x="88" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text></svg></a><br><div class="nav-links"><a href="/ops">← Pipeline</a><a href="/ops/history">History</a><a href="/ops/insights">Insights</a><a href="/ops/changelog">Changelog</a><a href="/ops/outlets">Outlets</a><a href="/ops/queue">Queue</a><a href="/ops/disputes">Disputes</a><a href="/ops/api-usage">API</a><a href="/ops/test-report">Test Reports</a><a href="/ops/mobile" style="color:#a855f7;font-weight:600">Mobile</a><a href="/ops/dashboard" style="color:#22c55e;font-weight:600">Dashboard</a></div>
<h1>Changelog</h1>
<div class="subtitle">Curated deployments and full git history &nbsp;·&nbsp; ops-auth protected</div>

<h2>Deployments</h2>

<div class="deploy-entry">
  <div class="deploy-header">
    <span class="deploy-date">May 21, 2026</span>
    <span class="deploy-label">v1.7 — Methodology refinement + data quality</span>
    <a class="deploy-commit" href="https://github.com/brittbart/verisreports/commit/1fa40af" target="_blank">1fa40af</a>
  </div>
  <ul class="deploy-items">
    <li><strong>Google News purge:</strong> 787 contaminated outlet_claims removed. 8 leaderboard outlets below threshold post-purge.</li>
    <li><strong>Non-news exclusion:</strong> 29 domains excluded from leaderboard (gao.gov, .gov, .edu, PR wires, non-news entities). EXCLUDED_DOMAINS frozenset in api_leaderboard.py.</li>
    <li><strong>Content quality gate:</strong> Google News redirect URLs blocked at ingestion in fetch_articles.py.</li>
    <li><strong>methodology_version stamping:</strong> All four article verdict write locations + debate verdict path now stamp v1.7. Column added to claims table (NOT NULL, DEFAULT v1.6, backfilled).</li>
    <li><strong>verdict_status:</strong> New column on claims. Existing verdicts set to final. Debate verdicts now write provisional; auto-promote to final at 60-minute mark.</li>
    <li><strong>Auto-promotion job:</strong> promote_provisional_verdicts() added to railway_api_refresh.py cron (every 5 min).</li>
    <li><strong>Parallel verifier bugs fixed:</strong> verification_attempts filter, claim_origin passthrough, attempts increment.</li>
    <li><strong>Leaderboard post-purge:</strong> 18 clean outlets. All contaminated outlets correctly absent.</li>
  </ul>
</div>

<div class="deploy-entry">
  <div class="deploy-header">
    <span class="deploy-date">May 19, 2026</span>
    <span class="deploy-label">API v1 + Ops Insights</span>
    <a class="deploy-commit" href="https://github.com/brittbart/verisreports/commit/c8c650b" target="_blank">c8c650b</a>
  </div>
  <ul class="deploy-items">
    <li><strong>Public API launched:</strong> api.verumsignal.com — /v1/claims, /v1/outlets, /v1/debates, /v1/meta. Bearer token auth. OpenAPI spec at /docs.</li>
    <li><strong>API tables:</strong> api_claims, api_outlets, api_debate_claims, api_keys, api_usage, api_monthly_usage, api_beta_requests.</li>
    <li><strong>veris-api-refresh cron:</strong> 5-minute refresh of all API tables from source claims.</li>
    <li><strong>API landing page:</strong> verumsignal.com/api with beta request form.</li>
    <li><strong>/ops/insights:</strong> Analytical dashboard with 7 sections (corpus, verdict distribution, outlet health, cost, outliers, debates, score trajectory).</li>
    <li><strong>Terms page:</strong> verumsignal.com/terms (design partner placeholder).</li>
    <li><strong>301 redirect:</strong> verumsignal.com/v1/* → api.verumsignal.com/v1/*</li>
  </ul>
</div>

<div class="deploy-entry">
  <div class="deploy-header">
    <span class="deploy-date">May 14, 2026</span>
    <span class="deploy-label">Iowa Senate R2 debate coverage</span>
    <a class="deploy-commit" href="https://github.com/brittbart/verisreports/commit/ba2c599" target="_blank">ba2c599</a>
  </div>
  <ul class="deploy-items">
    <li><strong>Live debate coverage:</strong> Iowa Senate Dem R2 — 106 verdicts assigned in near-real-time.</li>
    <li><strong>Debate routes:</strong> /debates, /debates/&lt;slug&gt;, /debates/&lt;slug&gt;/speakers, /debates/&lt;slug&gt;/speakers/&lt;speaker&gt;.</li>
    <li><strong>Debate admin:</strong> /admin dashboard with event/speaker CRUD and stream launch.</li>
    <li><strong>veris-stream service:</strong> Always-on debate stream handler.</li>
    <li><strong>is_public gate:</strong> Events must be is_public=TRUE to appear in API and consumer surfaces.</li>
  </ul>
</div>

<div class="deploy-entry">
  <div class="deploy-header">
    <span class="deploy-date">May 5, 2026</span>
    <span class="deploy-label">v1.6 — Methodology consolidation</span>
    <a class="deploy-commit" href="https://github.com/brittbart/verisreports/commit/860bc94" target="_blank">860bc94</a>
  </div>
  <ul class="deploy-items">
    <li><strong>Single source of truth:</strong> All scoring constants consolidated into api_leaderboard.py.</li>
    <li><strong>Breaking-news gate:</strong> 6-hour gate applied uniformly across all scoring surfaces.</li>
    <li><strong>Unscored display:</strong> Articles with no scoreable claims render as Unscored instead of 0/100.</li>
    <li><strong>Extraction prompt rewritten:</strong> Opinion-genre articles now yield embedded factual claims.</li>
    <li><strong>Paid depth raised:</strong> Up to 7 claims per paid report (was 3).</li>
    <li><strong>Source attribution cleanup:</strong> 65 verdicts redistributed from news.google.com to actual outlets.</li>
    <li><strong>Ops dashboard:</strong> /ops with pipeline health, corpus stats, cost tracking, debates panel.</li>
    <li><strong>/ops/history:</strong> Ingestion, corpus, verdict, cost, and outlet charts.</li>
  </ul>
</div>

<div class="deploy-entry">
  <div class="deploy-header">
    <span class="deploy-date">April 25, 2026</span>
    <span class="deploy-label">v1.5 — Initial public methodology + leaderboard launch</span>
  </div>
  <ul class="deploy-items">
    <li><strong>Public leaderboard:</strong> verumsignal.com/leaderboard.html — outlets ranked by reliability score.</li>
    <li><strong>Eight verdict types:</strong> supported, plausible, corroborated, overstated, disputed, not_supported, not_verifiable, opinion.</li>
    <li><strong>Scoring formula:</strong> (weighted_sum / scoreable + 1.5) / 2.5 × 100, normalized 0–100.</li>
    <li><strong>Free/paid reports:</strong> Free = top 2 claims, paid = full depth. Short shareable URLs via /r/&lt;hash&gt;.</li>
    <li><strong>Chrome extension v1.3:</strong> Popup + badge, dark aesthetic.</li>
    <li><strong>Outlet detail pages:</strong> Five outlet states (Published, Stabilizing, Limited Data, Tracked, stub).</li>
  </ul>
</div>

<div class="deploy-entry">
  <div class="deploy-header">
    <span class="deploy-date">April 18, 2026</span>
    <span class="deploy-label">Initial build — core pipeline</span>
  </div>
  <ul class="deploy-items">
    <li><strong>Core pipeline:</strong> RSS ingestion (115+ feeds), claim extraction via Claude Sonnet, web-search verification, PostgreSQL scoring.</li>
    <li><strong>Railway deployment:</strong> Auto-deploy on git push to master. Postgres managed service.</li>
    <li><strong>Scheduler:</strong> veris.service — ingestion every 15 minutes, extraction hourly, verdicts every 6 hours.</li>
  </ul>
</div>

<h2>Git history</h2>
<table>
  <thead><tr><th>Commit</th><th>Date</th><th>Message</th></tr></thead>
  <tbody id="git-log">
    <tr><td colspan="3" style="color:var(--fg-dim);padding:16px 10px">Loading git log…</td></tr>
  </tbody>
</table>

<div class="refresh-info" id="refresh-info">Git log loads on page visit</div>

<script>
async function loadGitLog() {
  try {
    const res = await fetch('/api/ops/git-log', {headers: {'Authorization': 'Basic ' + OPS_AUTH}});
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    const tbody = document.getElementById('git-log');
    if (!data.commits || !data.commits.length) {
      tbody.innerHTML = '<tr><td colspan="3" style="color:var(--fg-dim)">No commits found</td></tr>';
      return;
    }
    tbody.innerHTML = data.commits.map(c => `
      <tr>
        <td><a class="commit-hash" href="https://github.com/brittbart/verisreports/commit/${c.hash}" target="_blank">${c.short}</a></td>
        <td>${c.date}</td>
        <td>${c.message}</td>
      </tr>
    `).join('');
    document.getElementById('refresh-info').textContent = `${data.commits.length} commits loaded · ${new Date().toLocaleTimeString()}`;
  } catch(e) {
    document.getElementById('git-log').innerHTML = '<tr><td colspan="3" style="color:var(--bad)">Failed to load git log</td></tr>';
  }
}
loadGitLog();
</script>
</body>
</html>
"""

_OPS_HISTORY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veris Pipeline History</title>
<script>const OPS_AUTH = "__OPS_AUTH_PLACEHOLDER__";</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0a0a;--fg:#e8e8e8;--fg-dim:#888;--accent:#a855f7;--ok:#4ade80;--bad:#f87171;--yellow:#fbbf24;--blue:#60a5fa;--border:#1e1e1e;--card:#111;--mono:ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}
h1{font-size:18px;margin:0 0 4px;letter-spacing:-0.01em}.subtitle{color:var(--fg-dim);font-size:12px;margin-bottom:32px}
.nav-back{font-family:var(--mono);font-size:11px;color:var(--accent);text-decoration:none;display:inline-flex;align-items:center;gap:6px;margin-bottom:20px}
.nav-back:hover{color:#c084fc}h2{font-size:12px;text-transform:uppercase;letter-spacing:0.1em;color:var(--fg-dim);font-weight:500;margin:40px 0 16px}
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.chart-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px}
.chart-card.full{grid-column:1/-1}.chart-title{font-family:var(--mono);font-size:10px;letter-spacing:0.1em;text-transform:uppercase;color:var(--fg-dim);margin-bottom:16px}
.chart-wrap{position:relative;height:220px}.chart-wrap.tall{height:280px}
.stat-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:32px}
.stat-box{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px}
.stat-num{font-family:var(--mono);font-size:28px;font-weight:600;line-height:1;margin-bottom:6px}
.stat-label{font-size:11px;color:var(--fg-dim);text-transform:uppercase;letter-spacing:0.1em}
.refresh-info{color:#333;font-size:11px;font-family:var(--mono);margin-top:24px}
@media(max-width:700px){.chart-grid{grid-template-columns:1fr}.stat-row{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<a href="/ops" style="text-decoration:none;display:inline-block;margin-bottom:12px"><svg width="160" height="22" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg"><path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="14" r="2.5" fill="#ec4899"/><text x="32" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text><text x="88" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text></svg></a><br><div class="nav-links"><a href="/ops">← Pipeline</a><a href="/ops/history">History</a><a href="/ops/insights">Insights</a><a href="/ops/changelog">Changelog</a><a href="/ops/outlets">Outlets</a><a href="/ops/queue">Queue</a><a href="/ops/disputes">Disputes</a><a href="/ops/api-usage">API</a><a href="/ops/test-report">Test Reports</a><a href="/ops/mobile" style="color:#a855f7;font-weight:600">Mobile</a><a href="/ops/dashboard" style="color:#22c55e;font-weight:600">Dashboard</a></div>
<h1>Veris pipeline — history</h1>
<div class="subtitle" id="subtitle">loading…</div>
<div class="stat-row">
<div class="stat-box"><div class="stat-num" id="s-articles" style="color:var(--accent)">—</div><div class="stat-label">Total articles</div></div>
<div class="stat-box"><div class="stat-num" id="s-verdicts" style="color:var(--ok)">—</div><div class="stat-label">Total verdicts</div></div>
<div class="stat-box"><div class="stat-num" id="s-outlets" style="color:var(--blue)">—</div><div class="stat-label">Outlets scored</div></div>
<div class="stat-box"><div class="stat-num" id="s-cost" style="color:var(--yellow)">—</div><div class="stat-label">Cost last 7d</div></div>
</div>
<h2>Article ingestion — last 30 days</h2>
<div class="chart-grid"><div class="chart-card full"><div class="chart-title">Daily new articles ingested</div><div class="chart-wrap tall"><canvas id="c-ing"></canvas></div></div></div>
<h2>Corpus growth — last 30 days</h2>
<div class="chart-grid"><div class="chart-card full"><div class="chart-title">Cumulative articles in database</div><div class="chart-wrap"><canvas id="c-corp"></canvas></div></div></div>
<h2>Verdict volume — last 30 days</h2>
<div class="chart-grid"><div class="chart-card full"><div class="chart-title">Daily verdicts by type</div><div class="chart-wrap tall"><canvas id="c-verd"></canvas></div></div></div>
<h2>Cost — last 14 days</h2>
<div class="chart-grid"><div class="chart-card full"><div class="chart-title">Daily API spend (USD)</div><div class="chart-wrap"><canvas id="c-cost"></canvas></div></div></div>
<h2>Outlet verdict counts</h2>
<div class="chart-grid"><div class="chart-card full"><div class="chart-title">Scoreable verdicts per outlet</div><div class="chart-wrap tall"><canvas id="c-out"></canvas></div></div></div>
<div class="refresh-info">Data loads on page visit · verumsignal.com/ops/history</div>
<script>
const D={responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{backgroundColor:'#1a1a1a',borderColor:'#333',borderWidth:1,titleColor:'#e8e8e8',bodyColor:'#aaa',padding:10}},scales:{x:{grid:{color:'rgba(255,255,255,0.04)'},ticks:{color:'#555',font:{family:'ui-monospace',size:10},maxRotation:45}},y:{grid:{color:'rgba(255,255,255,0.04)'},ticks:{color:'#555',font:{family:'ui-monospace',size:10}}}}};
function fd(s){const d=new Date(s+'T00:00:00');return d.toLocaleDateString(undefined,{month:'short',day:'numeric'});}
function fn(n){return n===null||n===undefined?'—':Number(n).toLocaleString();}
async function load(){
const res=await fetch('/api/pipeline-history',{headers:{'Authorization':'Basic '+OPS_AUTH}});
if(!res.ok){document.getElementById('subtitle').textContent='error';return;}
const d=await res.json();
document.getElementById('subtitle').textContent='Loaded '+new Date().toLocaleTimeString();
const ta=d.corpus.length?d.corpus[d.corpus.length-1].cumulative:0;
const tv=d.verdicts.reduce((s,r)=>s+r.total,0);
const c7=d.costs.slice(-7).reduce((s,r)=>s+r.usd,0);
document.getElementById('s-articles').textContent=fn(ta);
document.getElementById('s-verdicts').textContent=fn(tv);
document.getElementById('s-outlets').textContent=fn(d.outlets.length);
document.getElementById('s-cost').textContent='$'+c7.toFixed(2);
new Chart(document.getElementById('c-ing'),{type:'bar',data:{labels:d.ingestion.map(r=>fd(r.day)),datasets:[{data:d.ingestion.map(r=>r.articles),backgroundColor:'rgba(168,85,247,0.6)',borderColor:'rgba(168,85,247,0.9)',borderWidth:1,borderRadius:2}]},options:D});
new Chart(document.getElementById('c-corp'),{type:'line',data:{labels:d.corpus.map(r=>fd(r.day)),datasets:[{data:d.corpus.map(r=>r.cumulative),borderColor:'#a855f7',backgroundColor:'rgba(168,85,247,0.08)',fill:true,tension:0.3,pointRadius:2}]},options:D});
const vs=JSON.parse(JSON.stringify(D));vs.scales.x.stacked=true;vs.scales.y.stacked=true;vs.plugins.legend={display:true,labels:{color:'#888',font:{size:11},boxWidth:12}};
new Chart(document.getElementById('c-verd'),{type:'bar',data:{labels:d.verdicts.map(r=>fd(r.day)),datasets:[{label:'Supported',data:d.verdicts.map(r=>r.supported),backgroundColor:'rgba(74,222,128,0.7)',borderRadius:2},{label:'Plausible',data:d.verdicts.map(r=>r.plausible),backgroundColor:'rgba(251,191,36,0.7)',borderRadius:2},{label:'Overstated',data:d.verdicts.map(r=>r.overstated),backgroundColor:'rgba(251,191,36,0.3)',borderRadius:2},{label:'Disputed',data:d.verdicts.map(r=>r.disputed),backgroundColor:'rgba(248,113,113,0.7)',borderRadius:2},{label:'Not supported',data:d.verdicts.map(r=>r.not_supported),backgroundColor:'rgba(239,68,68,0.7)',borderRadius:2}]},options:vs});
const co=JSON.parse(JSON.stringify(D));co.scales.y.ticks.callback=v=>'$'+v.toFixed(2);
new Chart(document.getElementById('c-cost'),{type:'bar',data:{labels:d.costs.map(r=>fd(r.day)),datasets:[{data:d.costs.map(r=>r.usd),backgroundColor:'rgba(251,191,36,0.6)',borderColor:'rgba(251,191,36,0.9)',borderWidth:1,borderRadius:2}]},options:co});
const oo=JSON.parse(JSON.stringify(D));oo.indexAxis='y';oo.scales.x.stacked=true;oo.scales.y.stacked=true;oo.plugins.legend={display:true,labels:{color:'#888',font:{size:11},boxWidth:12}};
new Chart(document.getElementById('c-out'),{type:'bar',data:{labels:d.outlets.map(r=>r.name),datasets:[{label:'Supported',data:d.outlets.map(r=>r.supported),backgroundColor:'rgba(74,222,128,0.7)',borderRadius:2},{label:'Negative',data:d.outlets.map(r=>r.negative),backgroundColor:'rgba(248,113,113,0.7)',borderRadius:2},{label:'Other',data:d.outlets.map(r=>r.scoreable-r.supported-r.negative),backgroundColor:'rgba(255,255,255,0.08)',borderRadius:2}]},options:oo});
}
load();

async function loadStreamHealth() {
  try {
    const res = await fetch('/api/ops/stream-health', {headers: {'Authorization': 'Basic ' + OPS_AUTH}});
    const d = await res.json();
    let card = document.getElementById('stream-health-card');
    if (!card) {
      card = document.createElement('div');
      card.id = 'stream-health-card';
      card.style.cssText = 'background:var(--card);border:1px solid var(--border);border-radius:6px;padding:14px;margin-bottom:24px;';
      const h2 = document.querySelector('h2');
      if (h2) h2.parentNode.insertBefore(card, h2);
    }
    const age = d.age_seconds !== null && d.age_seconds !== undefined
      ? (d.age_seconds < 60 ? d.age_seconds + 's ago' : Math.floor(d.age_seconds / 60) + 'm ago')
      : '\u2014';
    const stale = d.stale || d.status === 'unknown';
    const dot = stale ? '<span style="color:#f87171;">&#9679;</span>' : '<span style="color:#4ade80;">&#9679;</span>';
    const statusColor = d.status === 'running' ? '#4ade80' : d.status === 'unknown' ? '#888' : '#f87171';
    card.innerHTML = `<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:var(--fg-dim);font-weight:500;margin-bottom:8px;">Stream heartbeat</div>
      <div style="font-family:var(--mono);font-size:13px;line-height:1.8;">
        <div>${dot} <span style="color:${statusColor};">${d.status || 'unknown'}</span></div>
        <div style="color:var(--fg-dim);font-size:11px;">Last beat: ${age}</div>
        ${d.event_id ? '<div style="color:var(--fg-dim);font-size:11px;">Event: ' + d.event_id + '</div>' : ''}
        ${stale && d.status !== 'unknown' ? '<div style="color:#f87171;font-size:11px;margin-top:4px;">\u26a0 stale \u2014 no heartbeat in 5m</div>' : ''}
        ${d.error ? '<div style="color:#f87171;font-size:11px;margin-top:4px;">' + d.error + '</div>' : ''}
      </div>`;
  } catch(e) { console.error('stream health error', e); }
}
loadStreamHealth();
setInterval(loadStreamHealth, 30000);
</script>
</body>
</html>"""

_OPS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Veris Ops</title>
<script>const OPS_AUTH = "__OPS_AUTH_PLACEHOLDER__";</script>
<style>
  :root {
    --bg: #0a0a0a;
    --fg: #e8e8e8;
    --fg-dim: #888;
    --accent: #a855f7;
    --ok: #4ade80;
    --bad: #f87171;
    --running: #fbbf24;
    --border: #222;
    --card: #111;
    --mono: ui-monospace, "SF Mono", Menlo, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px;
    background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px; line-height: 1.5;
  }
  h1 { font-size: 18px; margin: 0 0 4px; letter-spacing: -0.01em; }
  h2 {
    font-size: 14px; margin: 32px 0 12px;
    color: var(--fg-dim); text-transform: uppercase;
    letter-spacing: 0.08em; font-weight: 500;
  }
  h2:first-of-type { margin-top: 24px; }
  .subtitle { color: var(--fg-dim); font-size: 12px; margin-bottom: 24px; }
  .grid {
    display: grid; gap: 12px;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    margin-bottom: 12px;
  }
  .corpus-grid {
    display: grid; gap: 12px;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    margin-bottom: 12px;
  }
  .corpus-card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 6px; padding: 14px 16px;
  }
  .corpus-card .label {
    font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.1em; color: var(--fg-dim);
    margin-bottom: 6px; font-weight: 500;
  }
  .corpus-card .total {
    font-family: var(--mono); font-size: 22px;
    font-weight: 600; color: var(--fg); line-height: 1;
  }
  .corpus-card .delta {
    font-family: var(--mono); font-size: 11px;
    color: var(--accent); margin-top: 4px;
  }
  .corpus-card .delta.zero { color: var(--fg-dim); }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 6px; padding: 14px;
  }
  .card h3 {
    margin: 0 0 8px; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--fg-dim); font-weight: 500;
  }
  .stage-stats { font-family: var(--mono); font-size: 12px; }
  .stage-stats > div { display: flex; justify-content: space-between; padding: 2px 0; }
  .stage-stats .label { color: var(--fg-dim); }
  table {
    width: 100%; border-collapse: collapse;
    font-family: var(--mono); font-size: 12px;
  }
  th, td {
    text-align: left; padding: 8px 10px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  th {
    color: var(--fg-dim); font-weight: 500;
    text-transform: uppercase; font-size: 10px;
    letter-spacing: 0.08em;
  }
  td.error { white-space: normal; max-width: 400px; word-break: break-word; }
  td.num { text-align: right; }
  th.num { text-align: right; }
  .status-ok { color: var(--ok); }
  .status-failed { color: var(--bad); font-weight: 600; }
  .status-running { color: var(--running); }
  .stage-tag {
    display: inline-block; padding: 2px 6px; border-radius: 3px;
    background: var(--border); font-size: 11px;
  }
  .total-row {
    background: var(--card);
    font-weight: 600;
    border-top: 2px solid var(--border);
  }
  .total-row td { color: var(--fg); }
  .refresh-info {
    color: var(--fg-dim); font-size: 11px;
    font-family: var(--mono); margin-top: 24px;
  }
  .empty { color: var(--fg-dim); padding: 32px; text-align: center; font-style: italic; }
  .cost-headline {
    color: var(--accent);
    font-family: var(--mono);
    font-size: 11px;
    margin-left: 12px;
    font-weight: normal;
    text-transform: none;
    letter-spacing: normal;
  }
</style>
</head>
<body>
<a href="/ops" style="text-decoration:none;display:inline-block;margin-bottom:12px"><svg width="160" height="22" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg"><path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="14" r="2.5" fill="#ec4899"/><text x="32" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text><text x="88" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text></svg></a>
<h1>Veris pipeline — last 24h</h1>
<div class="subtitle" id="subtitle">loading…</div>
<div style="margin-bottom:12px;font-size:12px;"><a href="/ops/history" style="color:#a855f7;text-decoration:none;margin-right:16px">History →</a><a href="/ops/insights" style="color:#a855f7;text-decoration:none;margin-right:16px">Insights →</a><a href="/ops/changelog" style="color:#a855f7;text-decoration:none;margin-right:16px">Changelog →</a><a href="/ops/outlets" style="color:#a855f7;text-decoration:none;margin-right:16px">Outlets →</a><a href="/ops/queue" style="color:#a855f7;text-decoration:none;margin-right:16px">Queue →</a><a href="/ops/disputes" style="color:#a855f7;text-decoration:none;margin-right:16px">Disputes →</a><a href="/ops/api-usage" style="color:#a855f7;text-decoration:none;margin-right:16px">API →</a><a href="/ops/test-report" style="color:#a855f7;text-decoration:none;margin-right:16px">Test Reports →</a><a href="/ops/mobile" style="color:#a855f7;text-decoration:none">Mobile →</a><a href="/ops/dashboard" style="color:#22c55e;text-decoration:none;margin-left:16px">Dashboard →</a></div>

<h2>Corpus</h2>
<div id="corpus" class="corpus-grid">
  <div class="corpus-card"><div class="label">Articles</div><div class="total">\u2014</div><div class="delta">&nbsp;</div></div>
  <div class="corpus-card"><div class="label">Claims (scoreable)</div><div class="total">\u2014</div><div class="delta">&nbsp;</div></div>
  <div class="corpus-card"><div class="label">Claims (all)</div><div class="total">\u2014</div><div class="delta">&nbsp;</div></div>
</div>

<h2>Pipeline health <span id="pipeline-status-badge" style="font-size:11px;font-weight:400;text-transform:none;letter-spacing:normal;margin-left:8px;"></span></h2>
<div id="schedule-grid" class="grid" style="margin-bottom:20px;"></div>
<div id="summary" class="grid"></div>

<h2>Recent runs</h2>
<table id="runs-table">
  <thead><tr>
    <th>id</th><th>stage</th><th>started</th><th>duration</th>
    <th>status</th><th class="num">items</th><th>error</th>
  </tr></thead>
  <tbody><tr><td colspan="7" class="empty">loading…</td></tr></tbody>
</table>

<h2>Costs (24h)<span class="cost-headline" id="cost-headline"></span></h2>
<table id="cost-table">
  <thead><tr>
    <th>stage</th>
    <th class="num">calls</th>
    <th class="num">in tokens</th>
    <th class="num">out tokens</th>
    <th class="num">cache in</th>
    <th class="num">cache read</th>
    <th class="num">approx USD</th>
    <th>last call</th>
  </tr></thead>
  <tbody><tr><td colspan="8" class="empty">loading…</td></tr></tbody>
</table>

<h2>Debates <span id="surge-badge" style="font-size:11px;font-weight:400;text-transform:none;letter-spacing:normal;margin-left:8px;"></span></h2>
<div id="debates-grid" class="grid" style="margin-bottom:20px;"></div>
<table id="debates-table" style="margin-bottom:24px;">
  <thead><tr>
    <th>Event</th><th>Date</th><th class="num">Utterances</th>
    <th class="num">Claims</th><th class="num">Verified</th><th class="num">Pending</th>
  </tr></thead>
  <tbody><tr><td colspan="6" class="empty">loading…</td></tr></tbody>
</table>
<div class="refresh-info" id="refresh-info">auto-refresh every 30s · sonnet 4 pricing</div>

<script>
function fmtTime(iso) {
  if (!iso) return '\u2014';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}
function fmtDuration(ms) {
  if (ms === null || ms === undefined) return '\u2014';
  if (ms < 1000) return ms + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  const m = Math.floor(ms / 60000);
  const s = ((ms % 60000) / 1000).toFixed(0);
  return m + 'm ' + s + 's';
}
function fmtNum(n) {
  if (n === null || n === undefined) return '\u2014';
  return Number(n).toLocaleString();
}
function fmtUsd(n) {
  if (n === null || n === undefined) return '\u2014';
  if (n === 0) return '$0.00';
  if (n < 0.01) return '<$0.01';
  return '$' + Number(n).toFixed(2);
}
function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/\'/g, '&#039;');
}
function renderCorpus(data) {
  const cards = document.querySelectorAll('#corpus .corpus-card');
  if (!data || cards.length < 3) return;
  const items = [
    { node: cards[0], total: data.articles.total, delta: data.articles.delta_24h },
    { node: cards[1], total: data.claims_scoreable.total, delta: data.claims_scoreable.delta_24h },
    { node: cards[2], total: data.claims_all.total, delta: data.claims_all.delta_24h },
  ];
  items.forEach(({ node, total, delta }) => {
    node.querySelector('.total').textContent = fmtNum(total);
    const deltaEl = node.querySelector('.delta');
    if (delta && delta > 0) {
      deltaEl.textContent = '+' + fmtNum(delta) + ' last 24h';
      deltaEl.classList.remove('zero');
    } else {
      deltaEl.textContent = 'no change last 24h';
      deltaEl.classList.add('zero');
    }
  });
}


function renderCosts(data) {
  const stages = data.stages || [];
  const tbody = document.querySelector('#cost-table tbody');

  if (stages.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">no API calls in the last 24h</td></tr>';
    document.getElementById('cost-headline').textContent = '';
    return;
  }

  const rows = stages.map(s => {
    return '<tr>'
      + '<td><span class="stage-tag">' + escapeHtml(s.stage) + '</span></td>'
      + '<td class="num">' + fmtNum(s.calls) + '</td>'
      + '<td class="num">' + fmtNum(s.input_tokens) + '</td>'
      + '<td class="num">' + fmtNum(s.output_tokens) + '</td>'
      + '<td class="num">' + fmtNum(s.cache_creation_tokens) + '</td>'
      + '<td class="num">' + fmtNum(s.cache_read_tokens) + '</td>'
      + '<td class="num">' + fmtUsd(s.approx_usd) + '</td>'
      + '<td>' + fmtTime(s.last_call) + '</td>'
      + '</tr>';
  });
  const total = '<tr class="total-row">'
    + '<td>TOTAL</td>'
    + '<td class="num">' + fmtNum(data.total_calls) + '</td>'
    + '<td class="num" colspan="4"></td>'
    + '<td class="num">' + fmtUsd(data.total_usd) + '</td>'
    + '<td></td>'
    + '</tr>';
  tbody.innerHTML = rows.join('') + total;

  document.getElementById('cost-headline').textContent =
    fmtUsd(data.total_usd) + ' across ' + fmtNum(data.total_calls) + ' API calls';
}

async function loadData() {
  // Corpus totals
  try {
    const res = await fetch('/api/corpus-totals', { headers: { 'Authorization': 'Basic ' + OPS_AUTH } });
    if (res.ok) {
      renderCorpus(await res.json());
    }
  } catch (err) {
    console.error('corpus-totals fetch error:', err);
  }

  // Job runs
  try {
    const res = await fetch('/api/job-runs', { headers: { 'Authorization': 'Basic ' + OPS_AUTH } });
    if (!res.ok) {
      document.getElementById('subtitle').textContent = 'error: ' + res.status + ' ' + res.statusText;
    } else {
      const data = await res.json();
      renderRuns(data);
    }
  } catch (err) {
    document.getElementById('subtitle').textContent = 'fetch error: ' + err.message;
  }

  // Token usage / costs
  try {
    const res = await fetch('/api/token-usage', { headers: { 'Authorization': 'Basic ' + OPS_AUTH } });
    if (res.ok) {
      const data = await res.json();
      renderCosts(data);
    }
  } catch (err) {
    console.error('token-usage fetch error:', err);
  }
}


// Schedule config: cron expressions and friendly names
const SCHEDULES = [
  { stage: 'fetch',    label: 'Fetch',    intervalMins: 180, icon: '⬇' },
  { stage: 'extract',  label: 'Extract',  intervalMins: 60,  icon: '🔍' },
  { stage: 'verdicts', label: 'Verdicts', intervalMins: 360, icon: '⚖' },
];

function renderSchedule(runs) {
  const byStage = {};
  for (const r of runs) {
    if (!byStage[r.stage] || new Date(r.started_at) > new Date(byStage[r.stage].started_at)) {
      byStage[r.stage] = r;
    }
  }

  const grid = document.getElementById('schedule-grid');
  let anyBad = false;

  grid.innerHTML = SCHEDULES.map(({ stage, label, intervalMins, icon }) => {
    const last = byStage[stage];
    const isRunning = last && last.status === 'running';
    const lastTime = last ? new Date(last.started_at) : null;
    const now = new Date();
    const elapsedMins = lastTime ? (now - lastTime) / 60000 : null;
    const nextMins = elapsedMins !== null ? Math.max(0, intervalMins - elapsedMins) : null;

    let statusColor = 'var(--ok)';
    let statusText = '';
    let nextText = '';

    if (isRunning) {
      statusColor = 'var(--running)';
      statusText = 'RUNNING';
      nextText = 'in progress';
    } else if (elapsedMins === null) {
      statusColor = 'var(--fg-dim)';
      statusText = 'NO DATA';
      nextText = 'unknown';
    } else if (elapsedMins > intervalMins * 1.5) {
      statusColor = 'var(--bad)';
      statusText = 'OVERDUE';
      nextText = 'overdue by ' + Math.round(elapsedMins - intervalMins) + 'm';
      anyBad = true;
    } else if (elapsedMins > intervalMins * 1.1) {
      statusColor = 'var(--running)';
      statusText = 'LATE';
      nextText = 'due now';
    } else {
      statusText = 'OK';
      const h = Math.floor(nextMins / 60);
      const m = Math.round(nextMins % 60);
      nextText = h > 0 ? 'in ' + h + 'h ' + m + 'm' : 'in ' + Math.round(nextMins) + 'm';
    }

    const lastStr = lastTime ? fmtTime(lastTime.toISOString()) : '—';

    return '<div class="card">'
      + '<h3>' + icon + ' ' + label + ' <span style="color:' + statusColor + ';font-size:10px;">' + statusText + '</span></h3>'
      + '<div class="stage-stats">'
      + '<div><span class="label">next run</span><span style="color:' + statusColor + ';">' + nextText + '</span></div>'
      + '<div><span class="label">last run</span><span>' + lastStr + '</span></div>'
      + (last && last.items_processed !== null ? '<div><span class="label">last ingested</span><span>' + fmtNum(last.items_processed) + '</span></div>' : '')
      + '<div><span class="label">cadence</span><span>' + (intervalMins >= 60 ? intervalMins/60 + 'h' : intervalMins + 'm') + '</span></div>'
      + '</div></div>';
  }).join('');

  const badge = document.getElementById('pipeline-status-badge');
  if (anyBad) {
    badge.textContent = '⚠ overdue';
    badge.style.color = 'var(--bad)';
  } else {
    badge.textContent = '✓ healthy';
    badge.style.color = 'var(--ok)';
  }
}

function renderRuns(data) {
  const runs = data.runs || [];
  renderSchedule(runs);

  const byStage = {};
  for (const r of runs) {
    if (!byStage[r.stage]) byStage[r.stage] = { runs: [], ok: 0, failed: 0, running: 0 };
    byStage[r.stage].runs.push(r);
    if (r.status === 'ok') byStage[r.stage].ok++;
    else if (r.status === 'failed') byStage[r.stage].failed++;
    else if (r.status === 'running') byStage[r.stage].running++;
  }

  const orderedStages = ['fetch', 'extract', 'load', 'priority', 'preverify', 'backfill', 'verdicts'];
  const allStages = orderedStages.filter(s => byStage[s]).concat(
    Object.keys(byStage).filter(s => !orderedStages.includes(s))
  );

  const summary = document.getElementById('summary');
  summary.innerHTML = allStages.map(stage => {
    const s = byStage[stage];
    const okDurations = s.runs.filter(r => r.status === 'ok' && r.duration_ms).map(r => r.duration_ms);
    const avgMs = okDurations.length ? Math.round(okDurations.reduce((a,b) => a+b, 0) / okDurations.length) : null;
    const lastRun = s.runs[0];
    return '<div class="card">'
      + '<h3>' + escapeHtml(stage) + '</h3>'
      + '<div class="stage-stats">'
      + '<div><span class="label">runs</span><span>' + s.runs.length + '</span></div>'
      + '<div><span class="label">ok</span><span class="status-ok">' + s.ok + '</span></div>'
      + (s.failed ? '<div><span class="label">failed</span><span class="status-failed">' + s.failed + '</span></div>' : '')
      + (s.running ? '<div><span class="label">running</span><span class="status-running">' + s.running + '</span></div>' : '')
      + '<div><span class="label">avg</span><span>' + fmtDuration(avgMs) + '</span></div>'
      + '<div><span class="label">last</span><span>' + fmtTime(lastRun ? lastRun.started_at : null) + '</span></div>'
      + '</div></div>';
  }).join('');

  const tbody = document.querySelector('#runs-table tbody');
  if (runs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">no runs in the last 24h</td></tr>';
  } else {
    tbody.innerHTML = runs.map(r => {
      const statusClass = 'status-' + r.status;
      const errCell = r.error_class
        ? '<td class="error"><strong>' + escapeHtml(r.error_class) + '</strong>: ' + escapeHtml(r.error_message || '') + '</td>'
        : '<td>\u2014</td>';
      return '<tr>'
        + '<td>' + r.id + '</td>'
        + '<td><span class="stage-tag">' + escapeHtml(r.stage) + '</span></td>'
        + '<td>' + fmtTime(r.started_at) + '</td>'
        + '<td>' + fmtDuration(r.duration_ms) + '</td>'
        + '<td class="' + statusClass + '">' + escapeHtml(r.status) + '</td>'
        + '<td class="num">' + (r.items_processed !== null ? r.items_processed : '\u2014') + '</td>'
        + errCell
        + '</tr>';
    }).join('');
  }

  document.getElementById('subtitle').textContent = data.count + ' runs · refreshed ' + new Date().toLocaleTimeString();
}

loadData();
setInterval(loadData, 30000);

async function loadDebates() {
  try {
    const res = await fetch('/api/ops/debates', {headers: {'Authorization': 'Basic ' + OPS_AUTH}});
    const d = await res.json();
    const badge = document.getElementById('surge-badge');
    if (badge) {
      if (d.surge_active) {
        badge.innerHTML = '<span style="color:#4ade80;background:rgba(74,222,128,0.1);padding:3px 10px;border-radius:100px;border:0.5px solid rgba(74,222,128,0.3);">&#9679; SURGE ACTIVE &mdash; ' + (d.live_event ? d.live_event.event_name : '') + ' &middot; ' + d.pending_surge + ' pending</span>';
      } else {
        badge.innerHTML = '<span style="color:#888;background:rgba(255,255,255,0.04);padding:3px 10px;border-radius:100px;border:0.5px solid rgba(255,255,255,0.1);">&#9679; Normal mode</span>';
      }
    }
    const tbody = document.querySelector('#debates-table tbody');
    if (tbody && d.events && d.events.length) {
      tbody.innerHTML = d.events.map(e =>
        '<tr>'
        + '<td><a href="/debates/' + e.slug + '" target="_blank" style="color:#a855f7;">' + e.event_name + '</a></td>'
        + '<td>' + e.event_date + '</td>'
        + '<td class="num">' + e.utterances + '</td>'
        + '<td class="num">' + e.claims_total + '</td>'
        + '<td class="num" style="color:#4ade80;">' + e.claims_verified + '</td>'
        + '<td class="num" style="color:' + (e.claims_pending > 0 ? '#fbbf24' : '#888') + ';">' + e.claims_pending + '</td>'
        + '</tr>'
      ).join('');
    } else if (tbody) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No public events yet</td></tr>';
    }
  } catch(e) { console.error('debates load error', e); }
}
loadDebates();
setInterval(loadDebates, 30000);

async function loadStreamHealth() {
  try {
    const res = await fetch('/api/ops/stream-health', {headers: {'Authorization': 'Basic ' + OPS_AUTH}});
    const d = await res.json();
    const grid = document.getElementById('debates-grid');
    if (!grid) return;
    let existing = document.getElementById('stream-health-card');
    if (!existing) {
      existing = document.createElement('div');
      existing.id = 'stream-health-card';
      existing.className = 'card';
      grid.prepend(existing);
    }
    const age = d.age_seconds !== null && d.age_seconds !== undefined
      ? (d.age_seconds < 60 ? d.age_seconds + 's ago' : Math.floor(d.age_seconds / 60) + 'm ago')
      : '—';
    const stale = d.stale || d.status === 'unknown';
    const dot = stale ? '<span style="color:#f87171;">&#9679;</span>' : '<span style="color:#4ade80;">&#9679;</span>';
    const statusColor = d.status === 'running' ? '#4ade80' : d.status === 'unknown' ? '#888' : '#f87171';
    existing.innerHTML = `
      <h3>Stream heartbeat</h3>
      <div style="font-family:var(--mono);font-size:13px;line-height:1.8;">
        <div>${dot} <span style="color:${statusColor};">${d.status || 'unknown'}</span></div>
        <div style="color:var(--fg-dim);font-size:11px;">Last beat: ${age}</div>
        ${d.event_id ? '<div style="color:var(--fg-dim);font-size:11px;">Event: ' + d.event_id + '</div>' : ''}
        ${stale && d.status !== 'unknown' ? '<div style="color:#f87171;font-size:11px;margin-top:4px;">⚠ stale &mdash; no heartbeat in 5m</div>' : ''}
        ${d.error ? '<div style="color:#f87171;font-size:11px;margin-top:4px;">' + d.error + '</div>' : ''}
      </div>
    `;
  } catch(e) { console.error('stream health error', e); }
}
loadStreamHealth();
setInterval(loadStreamHealth, 30000);
</script>
</body>
</html>"""



@app.route('/api/pipeline-history', methods=['GET'])
def api_pipeline_history():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT DATE(fetched_at) as day, COUNT(*) as articles
            FROM articles
            WHERE fetched_at > NOW() - INTERVAL '30 days'
            GROUP BY day ORDER BY day ASC
        """)
        ingestion = [{"day": str(r[0]), "articles": r[1]} for r in cur.fetchall()]
        cur.execute("""
            SELECT DATE(last_checked) as day,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE verdict = 'supported') as supported,
                COUNT(*) FILTER (WHERE verdict = 'plausible') as plausible,
                COUNT(*) FILTER (WHERE verdict = 'corroborated') as corroborated,
                COUNT(*) FILTER (WHERE verdict = 'overstated') as overstated,
                COUNT(*) FILTER (WHERE verdict = 'disputed') as disputed,
                COUNT(*) FILTER (WHERE verdict = 'not_supported') as not_supported
            FROM claims
            WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '30 days'
            GROUP BY day ORDER BY day ASC
        """)
        verdicts = [{"day": str(r[0]), "total": r[1], "supported": r[2],
                     "plausible": r[3], "corroborated": r[4], "overstated": r[5],
                     "disputed": r[6], "not_supported": r[7]} for r in cur.fetchall()]
        cur.execute("""
            SELECT DATE(fetched_at) as day, COUNT(*) as new_articles,
                SUM(COUNT(*)) OVER (ORDER BY DATE(fetched_at)) as cumulative
            FROM articles
            WHERE fetched_at > NOW() - INTERVAL '30 days'
            GROUP BY day ORDER BY day ASC
        """)
        corpus = [{"day": str(r[0]), "new": r[1], "cumulative": r[2]} for r in cur.fetchall()]
        cur.execute("""
            SELECT DATE(timestamp) as day,
                ROUND(SUM(
                    (input_tokens * 3.0 + output_tokens * 15.0 +
                     cache_creation_input_tokens * 3.75 +
                     cache_read_input_tokens * 0.30) / 1000000.0
                )::numeric, 2) as usd,
                COUNT(*) as calls
            FROM token_usage
            WHERE timestamp > NOW() - INTERVAL '14 days'
            GROUP BY day ORDER BY day ASC
        """)
        costs = [{"day": str(r[0]), "usd": float(r[1]), "calls": r[2]} for r in cur.fetchall()]
        cur.execute("""
            SELECT a.source_name,
                COUNT(*) FILTER (WHERE c.verdict IS NOT NULL AND c.claim_origin = 'outlet_claim') as scoreable,
                COUNT(*) FILTER (WHERE c.verdict = 'supported') as supported,
                COUNT(*) FILTER (WHERE c.verdict IN ('disputed','not_supported','overstated')) as negative
            FROM claims c JOIN articles a ON c.article_id = a.id
            WHERE c.verdict IS NOT NULL AND c.claim_origin = 'outlet_claim'
            GROUP BY a.source_name
            HAVING COUNT(*) FILTER (WHERE c.verdict IS NOT NULL AND c.claim_origin = 'outlet_claim') >= 20
            ORDER BY scoreable DESC LIMIT 15
        """)
        outlets = [{"name": r[0], "scoreable": r[1], "supported": r[2], "negative": r[3]}
                   for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({"ingestion": ingestion, "verdicts": verdicts,
                        "corpus": corpus, "costs": costs, "outlets": outlets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ============================================================
# /ops/insights — analytical dashboard
# ============================================================

_INSIGHTS_CACHE = {'data': None, 'expires_at': 0}


def _compute_snapshot(cur):
    try:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE verdict IS NOT NULL) AS scored_claims,
                COUNT(*) AS total_claims
            FROM claims
        """)
        r = cur.fetchone()
        scored, total_claims = r

        cur.execute("SELECT COUNT(*) FROM articles")
        total_articles = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT LOWER(source_name)) FROM articles")
        outlets_tracked = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(DISTINCT LOWER(a.source_name))
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim' AND c.verdict IS NOT NULL
            GROUP BY LOWER(a.source_name)
            HAVING COUNT(*) >= 20
        """)
        outlets_scored = cur.rowcount
        # Re-query for count
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT LOWER(a.source_name)
                FROM claims c JOIN articles a ON a.id = c.article_id
                WHERE c.claim_origin = 'outlet_claim' AND c.verdict IS NOT NULL
                GROUP BY LOWER(a.source_name)
                HAVING COUNT(*) >= 20
            ) t
        """)
        outlets_scored = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM claims
            WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '7 days'
        """)
        claims_7d = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM claims
            WHERE verdict IS NOT NULL
              AND last_checked BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days'
        """)
        claims_prev_7d = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM articles WHERE fetched_at > NOW() - INTERVAL '24 hours'
        """)
        articles_24h = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM articles
            WHERE fetched_at BETWEEN NOW() - INTERVAL '48 hours' AND NOW() - INTERVAL '24 hours'
        """)
        articles_prev_24h = cur.fetchone()[0]

        avg_verdicts_day = round(claims_7d / 7, 1) if claims_7d else 0

        def delta(now, prev):
            if not prev: return None
            return round((now - prev) / prev * 100, 1)

        return {
            'scored_claims': scored,
            'total_articles': total_articles,
            'outlets_tracked': outlets_tracked,
            'outlets_scored': outlets_scored,
            'claims_7d': claims_7d,
            'claims_7d_delta': delta(claims_7d, claims_prev_7d),
            'articles_24h': articles_24h,
            'articles_24h_delta': delta(articles_24h, articles_prev_24h),
            'avg_verdicts_day': avg_verdicts_day,
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_verdict_distribution(cur):
    try:
        cur.execute("""
            SELECT verdict, COUNT(*) as cnt
            FROM claims WHERE verdict IS NOT NULL
            GROUP BY verdict ORDER BY cnt DESC
        """)
        lifetime = {r[0]: r[1] for r in cur.fetchall()}
        total_lt = sum(lifetime.values())

        cur.execute("""
            SELECT verdict, COUNT(*) as cnt
            FROM claims
            WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '7 days'
            GROUP BY verdict
        """)
        last7 = {r[0]: r[1] for r in cur.fetchall()}
        total_7d = sum(last7.values())

        cur.execute("""
            SELECT verdict, COUNT(*) as cnt
            FROM claims
            WHERE verdict IS NOT NULL
              AND last_checked BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days'
            GROUP BY verdict
        """)
        prev7 = {r[0]: r[1] for r in cur.fetchall()}
        total_prev7 = sum(prev7.values())

        verdicts = ['supported','plausible','corroborated','overstated',
                    'disputed','not_supported','not_verifiable','opinion']
        rows = []
        for v in verdicts:
            lt_cnt = lifetime.get(v, 0)
            l7_cnt = last7.get(v, 0)
            p7_cnt = prev7.get(v, 0)
            lt_pct = round(lt_cnt / total_lt * 100, 1) if total_lt else 0
            l7_pct = round(l7_cnt / total_7d * 100, 1) if total_7d else 0
            delta = None
            if p7_cnt:
                delta = round((l7_cnt - p7_cnt) / p7_cnt * 100, 1)
            rows.append({
                'verdict': v,
                'lifetime_count': lt_cnt,
                'lifetime_pct': lt_pct,
                'last7_count': l7_cnt,
                'last7_pct': l7_pct,
                'delta': delta,
            })

        # 30-day daily trend
        cur.execute("""
            SELECT
                DATE_TRUNC('day', last_checked) AS day,
                verdict,
                COUNT(*) AS cnt
            FROM claims
            WHERE verdict IS NOT NULL
              AND last_checked > NOW() - INTERVAL '30 days'
            GROUP BY 1, 2 ORDER BY 1
        """)
        trend_raw = cur.fetchall()
        trend = {}
        for row in trend_raw:
            day = row[0].strftime('%Y-%m-%d')
            if day not in trend:
                trend[day] = {}
            trend[day][row[1]] = row[2]

        return {
            'lifetime': lifetime,
            'total_lt': total_lt,
            'rows': rows,
            'trend': trend,
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_outlet_table(cur):
    try:
        cur.execute("""
            SELECT
                LOWER(a.source_name) AS outlet_id,
                a.source_name AS outlet_name,
                COUNT(*) FILTER (WHERE c.verdict='supported') AS supported,
                COUNT(*) FILTER (WHERE c.verdict='plausible') AS plausible,
                COUNT(*) FILTER (WHERE c.verdict='corroborated') AS corroborated,
                COUNT(*) FILTER (WHERE c.verdict='overstated') AS overstated,
                COUNT(*) FILTER (WHERE c.verdict='disputed') AS disputed,
                COUNT(*) FILTER (WHERE c.verdict='not_supported') AS not_supported,
                COUNT(*) FILTER (WHERE c.verdict='not_verifiable') AS not_verifiable,
                COUNT(*) FILTER (WHERE c.verdict IS NOT NULL) AS total,
                MAX(c.last_checked) AS last_verdict
            FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim' AND c.verdict IS NOT NULL
            GROUP BY LOWER(a.source_name), a.source_name
        """)
        rows = cur.fetchall()

        # Velocity: claims last 7d per outlet
        cur.execute("""
            SELECT LOWER(a.source_name), COUNT(*)
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim'
              AND c.verdict IS NOT NULL
              AND c.last_checked > NOW() - INTERVAL '7 days'
            GROUP BY LOWER(a.source_name)
        """)
        velocity = {r[0]: r[1] for r in cur.fetchall()}

        # Score 30d ago: recompute from claims older than 30d
        cur.execute("""
            SELECT
                LOWER(a.source_name) AS outlet_id,
                COUNT(*) FILTER (WHERE c.verdict='supported') AS supported,
                COUNT(*) FILTER (WHERE c.verdict='plausible') AS plausible,
                COUNT(*) FILTER (WHERE c.verdict='corroborated') AS corroborated,
                COUNT(*) FILTER (WHERE c.verdict='overstated') AS overstated,
                COUNT(*) FILTER (WHERE c.verdict='disputed') AS disputed,
                COUNT(*) FILTER (WHERE c.verdict='not_supported') AS not_supported
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim'
              AND c.verdict IS NOT NULL
              AND c.last_checked < NOW() - INTERVAL '30 days'
            GROUP BY LOWER(a.source_name)
        """)
        old_scores = {}
        for r in cur.fetchall():
            oid, sup, pla, cor, ove, dis, ns = r
            sc = sup + pla + cor + ove + dis + ns
            if sc > 0:
                w = sup*1.0 + pla*0.5 + cor*0.5 + ove*-0.5 + dis*-1.0 + ns*-1.5
                old_scores[oid] = round((w/sc + 1.5)/2.5*100, 1)

        def score(sup, pla, cor, ove, dis, ns):
            sc = sup + pla + cor + ove + dis + ns
            if not sc: return None
            w = sup*1.0 + pla*0.5 + cor*0.5 + ove*-0.5 + dis*-1.0 + ns*-1.5
            return round((w/sc + 1.5)/2.5*100, 1)

        def tier(total):
            if total >= 100: return 'published'
            if total >= 50: return 'stabilizing'
            if total >= 20: return 'limited_data'
            return 'tracked'

        outlets = []
        for r in rows:
            (oid, oname, sup, pla, cor, ove, dis, ns, nv, total, last_v) = r
            sc = score(sup, pla, cor, ove, dis, ns)
            scoreable = sup + pla + cor + ove + dis + ns
            old_sc = old_scores.get(oid)
            traj = round(sc - old_sc, 1) if (sc and old_sc) else None
            outlets.append({
                'outlet_id': oid,
                'outlet_name': oname,
                'score': sc,
                'tier': tier(scoreable),
                'total': total,
                'scoreable': scoreable,
                'supported_pct': round(sup/scoreable*100, 1) if scoreable else 0,
                'overstated_pct': round(ove/scoreable*100, 1) if scoreable else 0,
                'disputed_pct': round(dis/scoreable*100, 1) if scoreable else 0,
                'not_supported_pct': round(ns/scoreable*100, 1) if scoreable else 0,
                'velocity': velocity.get(oid, 0),
                'trajectory': traj,
                'last_verdict': last_v.strftime('%Y-%m-%d %H:%M') if last_v else '—',
            })

        outlets.sort(key=lambda x: (x['trajectory'] is None, -(x['trajectory'] or 0)))

        # Tier transition indicators
        tier_thresholds = [20, 50, 100]
        for o in outlets:
            o['approaching_tier'] = None
            for t in tier_thresholds:
                if t - 5 <= o['scoreable'] < t:
                    names = {20: 'limited_data', 50: 'stabilizing', 100: 'published'}
                    o['approaching_tier'] = names[t]

        return {'outlets': outlets}
    except Exception as e:
        return {'error': str(e)}


def _compute_outliers(cur):
    try:
        cur.execute("""
            SELECT
                LOWER(a.source_name) AS outlet_id,
                a.source_name AS outlet_name,
                COUNT(*) FILTER (WHERE c.verdict='overstated') AS overstated,
                COUNT(*) FILTER (WHERE c.verdict='disputed') AS disputed,
                COUNT(*) FILTER (WHERE c.verdict IS NOT NULL
                    AND c.verdict NOT IN ('not_verifiable','opinion')) AS scoreable
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin = 'outlet_claim' AND c.verdict IS NOT NULL
            GROUP BY LOWER(a.source_name), a.source_name
            HAVING COUNT(*) FILTER (WHERE c.verdict IS NOT NULL
                AND c.verdict NOT IN ('not_verifiable','opinion')) >= 5
        """)
        rows = cur.fetchall()

        # Corpus averages
        total_sc = sum(r[4] for r in rows)
        total_ov = sum(r[2] for r in rows)
        total_di = sum(r[3] for r in rows)
        avg_ov = total_ov / total_sc if total_sc else 0
        avg_di = total_di / total_sc if total_sc else 0

        # Velocity comparison
        cur.execute("""
            SELECT LOWER(a.source_name), COUNT(*) FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin='outlet_claim' AND c.verdict IS NOT NULL
              AND c.last_checked > NOW() - INTERVAL '7 days'
            GROUP BY LOWER(a.source_name)
        """)
        vel_now = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("""
            SELECT LOWER(a.source_name), COUNT(*) FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE c.claim_origin='outlet_claim' AND c.verdict IS NOT NULL
              AND c.last_checked BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days'
            GROUP BY LOWER(a.source_name)
        """)
        vel_prev = {r[0]: r[1] for r in cur.fetchall()}

        outliers = []
        for (oid, oname, ov, di, sc) in rows:
            ov_rate = ov/sc if sc else 0
            di_rate = di/sc if sc else 0
            if avg_ov and ov_rate > avg_ov * 2:
                outliers.append({
                    'outlet': oname, 'outlet_id': oid,
                    'type': 'high_overstated',
                    'msg': f'Overstated rate {ov_rate:.1%} vs corpus avg {avg_ov:.1%} (2x+)',
                })
            if avg_di and di_rate > avg_di * 3:
                outliers.append({
                    'outlet': oname, 'outlet_id': oid,
                    'type': 'high_disputed',
                    'msg': f'Disputed rate {di_rate:.1%} vs corpus avg {avg_di:.1%} (3x+)',
                })
            vn = vel_now.get(oid, 0)
            vp = vel_prev.get(oid, 0)
            if vp >= 5 and vn < vp * 0.5:
                outliers.append({
                    'outlet': oname, 'outlet_id': oid,
                    'type': 'volume_drop',
                    'msg': f'Volume dropped: {vn} claims last 7d vs {vp} prior 7d',
                })
            if vp >= 2 and vn > vp * 2:
                outliers.append({
                    'outlet': oname, 'outlet_id': oid,
                    'type': 'volume_spike',
                    'msg': f'Volume spiked: {vn} claims last 7d vs {vp} prior 7d',
                })

        return {
            'outliers': outliers,
            'avg_overstated_pct': round(avg_ov * 100, 1),
            'avg_disputed_pct': round(avg_di * 100, 1),
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_corpus_growth(cur):
    try:
        cur.execute("""
            SELECT DATE_TRUNC('day', fetched_at) AS day, COUNT(*) AS articles
            FROM articles WHERE fetched_at > NOW() - INTERVAL '30 days'
            GROUP BY 1 ORDER BY 1
        """)
        ingestion = [{'day': r[0].strftime('%Y-%m-%d'), 'articles': r[1]} for r in cur.fetchall()]

        cur.execute("""
            SELECT DATE_TRUNC('day', last_checked) AS day, COUNT(*) AS verdicts
            FROM claims WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '30 days'
            GROUP BY 1 ORDER BY 1
        """)
        throughput = [{'day': r[0].strftime('%Y-%m-%d'), 'verdicts': r[1]} for r in cur.fetchall()]

        # Backlog
        cur.execute("""
            SELECT COUNT(*) FROM articles WHERE processed = FALSE OR processed IS NULL
        """)
        unextracted = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM claims WHERE verdict IS NULL AND claim_origin = 'outlet_claim'
        """)
        unverified = cur.fetchone()[0]

        # Rates
        cur.execute("""
            SELECT COUNT(*) FROM articles WHERE fetched_at > NOW() - INTERVAL '24 hours'
        """)
        ing_24h = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM claims WHERE verdict IS NOT NULL AND last_checked > NOW() - INTERVAL '24 hours'
        """)
        ver_24h = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM claims WHERE last_checked > NOW() - INTERVAL '24 hours'
        """)
        ext_24h = cur.fetchone()[0]

        extraction_rate = round(ext_24h / ing_24h, 2) if ing_24h else None
        verdict_rate = round(ver_24h / ext_24h, 2) if ext_24h else None

        return {
            'ingestion': ingestion,
            'throughput': throughput,
            'unextracted': unextracted,
            'unverified': unverified,
            'extraction_rate': extraction_rate,
            'verdict_rate': verdict_rate,
            'ing_24h': ing_24h,
            'ver_24h': ver_24h,
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_debates(cur):
    try:
        cur.execute("""
            SELECT
                e.id, e.event_name, e.event_date, e.is_public,
                COUNT(c.id) AS total,
                COUNT(c.id) FILTER (WHERE c.verdict IS NOT NULL) AS verified
            FROM events e
            LEFT JOIN claims c ON c.event_id = e.id AND c.claim_origin = 'debate_claim'
            GROUP BY e.id, e.event_name, e.event_date, e.is_public
            ORDER BY e.event_date DESC
        """)
        debates = []
        for r in cur.fetchall():
            eid, name, date, is_public, total, verified = r
            pct = round(verified/total*100) if total else None
            debates.append({
                'id': eid, 'name': name,
                'date': date.strftime('%Y-%m-%d') if date else '—',
                'is_public': is_public,
                'total': total, 'verified': verified,
                'unverified': total - verified,
                'pct': pct,
                'api_visible': is_public and verified > 0,
            })
        return {'debates': debates}
    except Exception as e:
        return {'error': str(e)}


def _compute_cost_and_usage(cur):
    try:
        cur.execute("""
            SELECT
                ROUND((
                    SUM(input_tokens) * 3.00 / 1000000 +
                    SUM(output_tokens) * 15.00 / 1000000 +
                    COALESCE(SUM(cache_creation_input_tokens), 0) * 3.75 / 1000000 +
                    COALESCE(SUM(cache_read_input_tokens), 0) * 0.30 / 1000000
                )::numeric, 2) AS cost_30d
            FROM job_runs
            WHERE started_at > NOW() - INTERVAL '30 days'
        """ if False else "SELECT NULL")
        # job_runs doesn't have token columns — use the existing cost endpoint pattern
        cur.execute("""
            SELECT
                ROUND(SUM(
                    input_tokens * 3.00 / 1000000.0 +
                    output_tokens * 15.00 / 1000000.0
                )::numeric, 2) AS cost
            FROM job_runs
            WHERE started_at > NOW() - INTERVAL '30 days'
              AND input_tokens IS NOT NULL
        """)
        cost_30d = cur.fetchone()[0]
    except Exception:
        cost_30d = None

    try:
        cur.execute("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL")
        active_keys = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM api_usage WHERE created_at > NOW() - INTERVAL '7 days'")
        api_calls_7d = cur.fetchone()[0]
        cur.execute("""
            SELECT endpoint, COUNT(*) as cnt FROM api_usage
            WHERE created_at > NOW() - INTERVAL '7 days'
            GROUP BY endpoint ORDER BY cnt DESC LIMIT 1
        """)
        top_endpoint = cur.fetchone()
        return {
            'cost_30d': float(cost_30d) if cost_30d else None,
            'active_keys': active_keys,
            'api_calls_7d': api_calls_7d,
            'top_endpoint': top_endpoint[0] if top_endpoint else None,
        }
    except Exception as e:
        return {'error': str(e)}


def _compute_insights_context():
    from time import time as _time
    import datetime
    conn = get_db()
    cur = conn.cursor()
    ctx = {'generated_at': datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
    try:
        sections = [
            ('snapshot', _compute_snapshot),
            ('verdict', _compute_verdict_distribution),
            ('outlets', _compute_outlet_table),
            ('outliers', _compute_outliers),
            ('corpus', _compute_corpus_growth),
            ('debates', _compute_debates),
            ('cost', _compute_cost_and_usage),
        ]
        for key, fn in sections:
            try:
                t0 = _time()
                ctx[key] = fn(cur)
                elapsed = _time() - t0
                if elapsed > 5:
                    app.logger.warning(f'insights section {key} took {elapsed:.1f}s')
            except Exception as e:
                ctx[key] = {'error': str(e)}
                app.logger.error(f'insights section {key} failed: {e}')
    finally:
        cur.close()
        conn.close()
    return ctx


def build_insights_context():
    from time import time as _time
    if _INSIGHTS_CACHE['data'] and _time() < _INSIGHTS_CACHE['expires_at']:
        return _INSIGHTS_CACHE['data']
    data = _compute_insights_context()
    _INSIGHTS_CACHE['data'] = data
    _INSIGHTS_CACHE['expires_at'] = _time() + 600
    return data


_OPS_INSIGHTS_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ops Insights — Verum Signal</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0a0f;--surface:#12121a;--border:rgba(255,255,255,0.08);--text:rgba(255,255,255,0.9);--text2:rgba(255,255,255,0.55);--text3:rgba(255,255,255,0.3);--violet:#a855f7;--green:#22c55e;--amber:#f59e0b;--red:#dc2626;--red-light:#f87171;--mono:'JetBrains Mono',ui-monospace,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}
a{color:var(--violet);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1400px;margin:0 auto;padding:0 24px 80px}
.top-bar{display:flex;align-items:center;justify-content:space-between;padding:20px 0 32px;border-bottom:0.5px solid var(--border);margin-bottom:32px}
.top-bar h1{font-size:20px;font-weight:600;letter-spacing:-0.3px}
.top-bar .links{display:flex;gap:16px;font-size:13px}
.refresh-note{font-size:12px;color:var(--text3);margin-top:4px}
.section{margin-bottom:48px}
.section-head{margin-bottom:20px}
.section-head h2{font-size:16px;font-weight:600;letter-spacing:-0.2px;margin-bottom:4px}
.section-head p{font-size:12px;color:var(--text2)}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px}
.card{background:var(--surface);border:0.5px solid var(--border);border-radius:10px;padding:16px 20px;min-width:160px;flex:1}
.card-num{font-size:28px;font-weight:700;font-family:var(--mono);letter-spacing:-1px;color:var(--text)}
.card-label{font-size:11px;color:var(--text2);margin-top:4px;text-transform:uppercase;letter-spacing:.06em}
.card-delta{font-size:11px;margin-top:6px}
.delta-up{color:var(--green)}
.delta-dn{color:var(--red-light)}
.pill{display:inline-block;padding:3px 10px;border-radius:100px;font-size:11px;font-family:var(--mono);border:0.5px solid var(--border);color:var(--violet)}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.chart-card{background:var(--surface);border:0.5px solid var(--border);border-radius:10px;padding:20px}
.chart-card h3{font-size:13px;font-weight:600;margin-bottom:16px;color:var(--text2)}
.chart-wrap{position:relative;height:220px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:11px;font-weight:500;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;padding:8px 10px;border-bottom:0.5px solid var(--border);cursor:pointer;white-space:nowrap}
th:hover{color:var(--text)}
td{padding:8px 10px;border-bottom:0.5px solid rgba(255,255,255,0.04);vertical-align:middle}
tr:last-child td{border-bottom:0}
.tbl-wrap{background:var(--surface);border:0.5px solid var(--border);border-radius:10px;overflow:auto;max-height:520px}
.tbl-wrap table{min-width:900px}
.traj-pos{color:var(--green);font-family:var(--mono)}
.traj-neg{color:var(--red-light);font-family:var(--mono)}
.traj-neu{color:var(--text3);font-family:var(--mono)}
.cell-amber{background:rgba(245,158,11,0.12)}
.cell-red{background:rgba(220,38,38,0.10)}
.tier-badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-family:var(--mono)}
.tier-published{background:rgba(34,197,94,0.15);color:var(--green)}
.tier-stabilizing{background:rgba(168,85,247,0.15);color:var(--violet)}
.tier-limited_data{background:rgba(245,158,11,0.12);color:var(--amber)}
.tier-tracked{background:rgba(255,255,255,0.06);color:var(--text3)}
.approaching{font-size:10px;color:var(--amber);margin-left:6px}
.callouts{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:16px}
.callout{background:var(--surface);border:0.5px solid var(--border);border-radius:10px;padding:14px 16px}
.callout h4{font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}
.callout-item{font-size:12px;padding:4px 0;border-bottom:0.5px solid rgba(255,255,255,0.04)}
.callout-item:last-child{border-bottom:0}
.outlier-list{display:flex;flex-direction:column;gap:8px}
.outlier-item{background:var(--surface);border:0.5px solid var(--border);border-radius:8px;padding:12px 16px;display:flex;gap:12px;align-items:flex-start}
.outlier-badge{font-size:10px;font-family:var(--mono);padding:2px 8px;border-radius:4px;white-space:nowrap;margin-top:2px}
.badge-ov{background:rgba(245,158,11,0.15);color:var(--amber)}
.badge-di{background:rgba(220,38,38,0.15);color:var(--red-light)}
.badge-vol{background:rgba(168,85,247,0.15);color:var(--violet)}
.outlier-msg{font-size:13px;color:var(--text)}
.outlier-outlet{font-size:11px;color:var(--text2);margin-top:2px}
.no-outliers{color:var(--text3);font-size:13px;padding:20px 0}
.err{color:var(--red-light);font-size:12px;padding:12px;background:rgba(220,38,38,0.08);border-radius:6px}
.debate-table td:nth-child(4){text-align:center}
.dot-green{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green)}
.dot-gray{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--text3)}
.rate-card{font-family:var(--mono);font-size:22px}
.rate-ok{color:var(--green)}
.rate-warn{color:var(--amber)}
.rate-bad{color:var(--red-light)}
@media(max-width:900px){.two-col,.callouts{grid-template-columns:1fr}.cards{flex-direction:column}}
</style>
</head>
<body>
<div class="wrap">
<div class="top-bar">
  <div>
    <h1>Ops Insights</h1>
    <div class="refresh-note">Data as of {{ generated_at }}  ·  <a href="/ops/insights?refresh=1">Refresh now</a>  ·  <a href="/ops">← Pipeline</a>  ·  <a href="/ops/history">History</a>  ·  <a href="/ops/changelog">Changelog</a>  ·  <a href="/ops/outlets">Outlets</a>  ·  <a href="/ops/queue">Queue</a>  ·  <a href="/ops/disputes">Disputes</a>  ·  <a href="/ops/api-usage">API</a><a href="/ops/test-report">Test Reports</a><a href="/ops/mobile" style="color:#a855f7;font-weight:600">Mobile</a><a href="/ops/dashboard" style="color:#22c55e;font-weight:600">Dashboard</a></div>
  </div>
</div>

<!-- ============ SECTION 1: SNAPSHOT ============ -->
<div class="section">
  <div class="section-head"><h2>Snapshot</h2><p>Key corpus metrics at a glance.</p></div>
  {% set s = snapshot %}
  {% if s.get('error') %}<div class="err">{{ s.error }}</div>{% else %}
  <div class="cards">
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.scored_claims) }}</div>
      <div class="card-label">Scored claims</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.total_articles) }}</div>
      <div class="card-label">Total articles</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.outlets_tracked) }}</div>
      <div class="card-label">Outlets tracked</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.outlets_scored) }}</div>
      <div class="card-label">Outlets scored (≥20)</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.claims_7d) }}</div>
      <div class="card-label">Claims scored (7d)</div>
      {% if s.claims_7d_delta is not none %}
        <div class="card-delta {{ 'delta-up' if s.claims_7d_delta >= 0 else 'delta-dn' }}">
          {{ '↑' if s.claims_7d_delta >= 0 else '↓' }} {{ s.claims_7d_delta|abs }}% vs prior 7d
        </div>
      {% endif %}
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(s.articles_24h) }}</div>
      <div class="card-label">Articles ingested (24h)</div>
      {% if s.articles_24h_delta is not none %}
        <div class="card-delta {{ 'delta-up' if s.articles_24h_delta >= 0 else 'delta-dn' }}">
          {{ '↑' if s.articles_24h_delta >= 0 else '↓' }} {{ s.articles_24h_delta|abs }}% vs prior 24h
        </div>
      {% endif %}
    </div>
    <div class="card">
      <div class="card-num">{{ s.avg_verdicts_day }}</div>
      <div class="card-label">Avg verdicts/day (7d)</div>
    </div>
    <div class="card">
      <div class="card-num"><span class="pill">v1.6</span></div>
      <div class="card-label">Methodology version</div>
    </div>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 2: VERDICT DISTRIBUTION ============ -->
<div class="section">
  <div class="section-head"><h2>Verdict Distribution</h2><p>What the methodology is producing — lifetime and 30-day trend. Scoring robustness changes deployed May 12 should show overstated rising from ~18.8%.</p></div>
  {% set v = verdict %}
  {% if v.get('error') %}<div class="err">{{ v.error }}</div>{% else %}
  <div class="two-col" style="margin-bottom:20px">
    <div class="chart-card">
      <h3>Lifetime verdict mix</h3>
      <div class="chart-wrap"><canvas id="c-donut"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>30-day daily trend</h3>
      <div class="chart-wrap"><canvas id="c-trend"></canvas></div>
    </div>
  </div>
  <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th>Verdict</th>
        <th>Lifetime</th>
        <th>Lifetime %</th>
        <th>Last 7d</th>
        <th>Last 7d %</th>
        <th>7d vs prior 7d</th>
      </tr></thead>
      <tbody>
        {% for r in v.rows %}
        <tr>
          <td>{{ r.verdict }}</td>
          <td>{{ "{:,}".format(r.lifetime_count) }}</td>
          <td>{{ r.lifetime_pct }}%</td>
          <td>{{ "{:,}".format(r.last7_count) }}</td>
          <td>{{ r.last7_pct }}%</td>
          <td>{% if r.delta is not none %}<span class="{{ 'traj-pos' if r.delta >= 0 else 'traj-neg' }}">{{ '+' if r.delta >= 0 else '' }}{{ r.delta }}%</span>{% else %}<span class="traj-neu">—</span>{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 3: OUTLET TABLE ============ -->
<div class="section">
  <div class="section-head"><h2>Outlet Detail</h2><p>Every tracked outlet with trajectory, velocity, and verdict breakdown. Default: most improved first. Click headers to sort.</p></div>
  {% set o = outlets %}
  {% if o.get('error') %}<div class="err">{{ o.error }}</div>{% else %}
  <div class="tbl-wrap">
    <table id="outlet-table">
      <thead><tr>
        <th onclick="sortTable(0)">Outlet</th>
        <th onclick="sortTable(1)">Score</th>
        <th onclick="sortTable(2)">Tier</th>
        <th onclick="sortTable(3)">Claims</th>
        <th onclick="sortTable(4)">Supported%</th>
        <th onclick="sortTable(5)">Overstated%</th>
        <th onclick="sortTable(6)">Disputed%</th>
        <th onclick="sortTable(7)">Not-supp%</th>
        <th onclick="sortTable(8)">Velocity 7d</th>
        <th onclick="sortTable(9)">Trajectory 30d</th>
        <th onclick="sortTable(10)">Last verdict</th>
      </tr></thead>
      <tbody>
        {% for outlet in o.outlets %}
        <tr>
          <td><a href="/outlet/{{ outlet.outlet_id }}">{{ outlet.outlet_name }}</a>{% if outlet.approaching_tier %}<span class="approaching">→ {{ outlet.approaching_tier }}</span>{% endif %}</td>
          <td>{{ outlet.score if outlet.score is not none else '—' }}</td>
          <td><span class="tier-badge tier-{{ outlet.tier }}">{{ outlet.tier }}</span></td>
          <td>{{ outlet.total }}</td>
          <td>{{ outlet.supported_pct }}%</td>
          <td class="{{ 'cell-amber' if outlet.overstated_pct > 30 else '' }}">{{ outlet.overstated_pct }}%</td>
          <td class="{{ 'cell-red' if outlet.disputed_pct > 5 else '' }}">{{ outlet.disputed_pct }}%</td>
          <td>{{ outlet.not_supported_pct }}%</td>
          <td>{{ outlet.velocity }}</td>
          <td>{% if outlet.trajectory is not none %}<span class="{{ 'traj-pos' if outlet.trajectory >= 0 else 'traj-neg' }}">{{ '+' if outlet.trajectory >= 0 else '' }}{{ outlet.trajectory }}</span>{% else %}<span class="traj-neu">—</span>{% endif %}</td>
          <td style="font-size:11px;color:var(--text2)">{{ outlet.last_verdict }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="callouts">
    <div class="callout">
      <h4>Most improved (30d)</h4>
      {% for outlet in (o.outlets | selectattr('trajectory', '!=', none) | sort(attribute='trajectory', reverse=True) | list)[:3] %}
      <div class="callout-item"><a href="/outlet/{{ outlet.outlet_id }}">{{ outlet.outlet_name }}</a> <span class="traj-pos">+{{ outlet.trajectory }}</span></div>
      {% else %}<div class="callout-item" style="color:var(--text3)">No data</div>
      {% endfor %}
    </div>
    <div class="callout">
      <h4>Most declined (30d)</h4>
      {% for outlet in (o.outlets | selectattr('trajectory', '!=', none) | sort(attribute='trajectory') | list)[:3] %}
      {% if outlet.trajectory < 0 %}
      <div class="callout-item"><a href="/outlet/{{ outlet.outlet_id }}">{{ outlet.outlet_name }}</a> <span class="traj-neg">{{ outlet.trajectory }}</span></div>
      {% endif %}
      {% else %}<div class="callout-item" style="color:var(--text3)">No data</div>
      {% endfor %}
    </div>
    <div class="callout">
      <h4>Approaching tier change</h4>
      {% set approachers = o.outlets | selectattr('approaching_tier', '!=', none) | list %}
      {% for outlet in approachers %}
      <div class="callout-item"><a href="/outlet/{{ outlet.outlet_id }}">{{ outlet.outlet_name }}</a> → <span style="color:var(--amber)">{{ outlet.approaching_tier }}</span></div>
      {% else %}<div class="callout-item" style="color:var(--text3)">None within 5 claims</div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 4: OUTLIERS ============ -->
<div class="section">
  <div class="section-head"><h2>Outlier Detection</h2><p>Outlets with statistically unusual behavior. Corpus averages: overstated {{ outliers.get('avg_overstated_pct', '—') }}% · disputed {{ outliers.get('avg_disputed_pct', '—') }}%.</p></div>
  {% set ol = outliers %}
  {% if ol.get('error') %}<div class="err">{{ ol.error }}</div>
  {% elif ol.outliers %}
  <div class="outlier-list">
    {% for item in ol.outliers %}
    <div class="outlier-item">
      <span class="outlier-badge {{ 'badge-ov' if 'overstated' in item.type else ('badge-di' if 'disputed' in item.type else 'badge-vol') }}">{{ item.type.replace('_', ' ') }}</span>
      <div>
        <div class="outlier-msg">{{ item.msg }}</div>
        <div class="outlier-outlet"><a href="/outlet/{{ item.outlet_id }}">{{ item.outlet }}</a></div>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="no-outliers">✓ No outliers detected against current corpus averages.</div>
  {% endif %}
</div>

<!-- ============ SECTION 5: CORPUS GROWTH ============ -->
<div class="section">
  <div class="section-head"><h2>Corpus Growth &amp; Pipeline Health</h2><p>30-day ingestion and verdict throughput. Rates near 1.0 = healthy pipeline.</p></div>
  {% set c = corpus %}
  {% if c.get('error') %}<div class="err">{{ c.error }}</div>{% else %}
  <div class="two-col" style="margin-bottom:20px">
    <div class="chart-card">
      <h3>Daily ingestion (articles)</h3>
      <div class="chart-wrap"><canvas id="c-ing"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Daily verdict throughput</h3>
      <div class="chart-wrap"><canvas id="c-verd"></canvas></div>
    </div>
  </div>
  <div class="cards">
    <div class="card">
      <div class="card-num">{{ "{:,}".format(c.unextracted) }}</div>
      <div class="card-label">Unextracted backlog</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(c.unverified) }}</div>
      <div class="card-label">Unverified backlog</div>
    </div>
    <div class="card">
      {% set er = c.extraction_rate %}
      <div class="card-num rate-card {{ 'rate-ok' if er and er >= 0.8 else ('rate-warn' if er and er >= 0.5 else 'rate-bad') }}">{{ er if er is not none else '—' }}</div>
      <div class="card-label">Extraction rate (24h)</div>
    </div>
    <div class="card">
      {% set vr = c.verdict_rate %}
      <div class="card-num rate-card {{ 'rate-ok' if vr and vr >= 0.8 else ('rate-warn' if vr and vr >= 0.5 else 'rate-bad') }}">{{ vr if vr is not none else '—' }}</div>
      <div class="card-label">Verdict rate (24h)</div>
    </div>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 6: DEBATES ============ -->
<div class="section">
  <div class="section-head"><h2>Debate Pipeline</h2><p>All debate events and their verification status.</p></div>
  {% set db = debates %}
  {% if db.get('error') %}<div class="err">{{ db.error }}</div>{% else %}
  <div class="tbl-wrap">
    <table class="debate-table">
      <thead><tr>
        <th>Event</th><th>Date</th><th>Public</th><th>Total claims</th><th>Verified</th><th>Pending</th><th>Verified%</th><th>API visible</th>
      </tr></thead>
      <tbody>
        {% for d in db.debates %}
        <tr>
          <td>{{ d.name }}</td>
          <td style="font-family:var(--mono);font-size:12px">{{ d.date }}</td>
          <td>{% if d.is_public %}<span class="dot-green"></span>{% else %}<span class="dot-gray"></span>{% endif %}</td>
          <td>{{ d.total }}</td>
          <td>{{ d.verified }}</td>
          <td>{{ d.unverified }}</td>
          <td>{{ d.pct|string + '%' if d.pct is not none else '—' }}</td>
          <td>{% if d.api_visible %}✓{% elif d.total == 0 %}<span style="color:var(--text3)">upcoming</span>{% else %}<span style="color:var(--text3)">gated</span>{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>

<!-- ============ SECTION 7: COST & API USAGE ============ -->
<div class="section">
  <div class="section-head"><h2>Cost &amp; API Usage</h2><p>Anthropic API spend and public API customer activity.</p></div>
  {% set cu = cost %}
  {% if cu.get('error') %}<div class="err">{{ cu.error }}</div>{% else %}
  <div class="cards">
    <div class="card">
      <div class="card-num">{{ '$' + '%.2f'|format(cu.cost_30d) if cu.cost_30d else '—' }}</div>
      <div class="card-label">Anthropic cost (30d)</div>
    </div>
    <div class="card">
      <div class="card-num">{{ cu.active_keys }}</div>
      <div class="card-label">Active API keys</div>
    </div>
    <div class="card">
      <div class="card-num">{{ "{:,}".format(cu.api_calls_7d) }}</div>
      <div class="card-label">API calls (7d)</div>
    </div>
    <div class="card">
      <div class="card-num" style="font-size:14px;padding-top:6px">{{ cu.top_endpoint or '—' }}</div>
      <div class="card-label">Top endpoint (7d)</div>
    </div>
  </div>
  {% if cu.api_calls_7d == 0 %}<p style="color:var(--text3);font-size:12px;margin-top:8px">No active API customers yet.</p>{% endif %}
  {% endif %}
</div>

</div><!-- /wrap -->

<script>
// Chart colors
const VC = {
  supported:     '#22c55e',
  plausible:     '#16a34a',
  corroborated:  '#15803d',
  overstated:    '#f59e0b',
  disputed:      '#f87171',
  not_supported: '#dc2626',
  not_verifiable:'#6b7280',
  opinion:       '#4b5563',
};
const DARK = {plugins:{legend:{labels:{color:'rgba(255,255,255,0.55)',font:{size:11}}}},scales:{x:{ticks:{color:'rgba(255,255,255,0.4)',font:{size:10}},grid:{color:'rgba(255,255,255,0.05)'}},y:{ticks:{color:'rgba(255,255,255,0.4)',font:{size:10}},grid:{color:'rgba(255,255,255,0.05)'}}}};

// Section 2: donut
{% if not verdict.get('error') %}
(function(){
  const lt = {{ verdict.lifetime | tojson }};
  const labels = Object.keys(lt);
  const data = Object.values(lt);
  const colors = labels.map(l => VC[l] || '#888');
  new Chart(document.getElementById('c-donut'), {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 1, borderColor: '#12121a' }] },
    options: { plugins: { legend: { position: 'right', labels: { color: 'rgba(255,255,255,0.55)', font: { size: 11 }, boxWidth: 12 } } }, cutout: '60%' }
  });

  // Trend chart
  const trend = {{ verdict.trend | tojson }};
  const days = Object.keys(trend).sort();
  const verdicts = ['supported','plausible','corroborated','overstated','disputed','not_supported','not_verifiable'];
  const datasets = verdicts.map(v => ({
    label: v, data: days.map(d => trend[d]?.[v] || 0),
    backgroundColor: VC[v] + 'cc', borderColor: VC[v], borderWidth: 0, fill: true
  }));
  new Chart(document.getElementById('c-trend'), {
    type: 'bar',
    data: { labels: days, datasets },
    options: { ...DARK, plugins: { ...DARK.plugins, legend: { ...DARK.plugins.legend, display: false } }, scales: { x: { ...DARK.scales.x, stacked: true }, y: { ...DARK.scales.y, stacked: true } } }
  });
})();
{% endif %}

// Section 5: ingestion + throughput
{% if not corpus.get('error') %}
(function(){
  const ing = {{ corpus.ingestion | tojson }};
  const thr = {{ corpus.throughput | tojson }};
  new Chart(document.getElementById('c-ing'), {
    type: 'bar',
    data: { labels: ing.map(r=>r.day), datasets: [{ data: ing.map(r=>r.articles), backgroundColor: 'rgba(168,85,247,0.6)', borderRadius: 2 }] },
    options: { ...DARK, plugins: { legend: { display: false } } }
  });
  new Chart(document.getElementById('c-verd'), {
    type: 'bar',
    data: { labels: thr.map(r=>r.day), datasets: [{ data: thr.map(r=>r.verdicts), backgroundColor: 'rgba(34,197,94,0.6)', borderRadius: 2 }] },
    options: { ...DARK, plugins: { legend: { display: false } } }
  });
})();
{% endif %}

// Sortable outlet table
var sortDir = {};
function sortTable(col) {
  var table = document.getElementById('outlet-table');
  var tbody = table.tBodies[0];
  var rows = Array.from(tbody.rows);
  var asc = !sortDir[col];
  sortDir = {};
  sortDir[col] = asc;
  rows.sort(function(a, b) {
    var av = a.cells[col].textContent.trim();
    var bv = b.cells[col].textContent.trim();
    var an = parseFloat(av.replace(/[^0-9.\-]/g, ''));
    var bn = parseFloat(bv.replace(/[^0-9.\-]/g, ''));
    if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(function(r) { tbody.appendChild(r); });
}
</script>
</body>
</html>"""


@app.route('/ops/insights', methods=['GET'])
def ops_insights():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    if request.args.get('refresh') == '1':
        _INSIGHTS_CACHE['expires_at'] = 0
    ctx = build_insights_context()
    from flask import render_template_string
    return render_template_string(_OPS_INSIGHTS_HTML, **ctx)


@app.route('/ops/changelog', methods=['GET'])
def ops_changelog():
    """Render the ops changelog. Basic-auth protected."""
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    import base64 as _b64
    ops_pw = os.environ.get('OPS_PASSWORD', '')
    ops_auth_b64 = _b64.b64encode(f'admin:{ops_pw}'.encode()).decode()
    html = _OPS_CHANGELOG_HTML.replace('__OPS_AUTH_PLACEHOLDER__', ops_auth_b64)
    from flask import Response
    return Response(html, mimetype='text/html')


@app.route('/api/ops/git-log', methods=['GET'])
def api_ops_git_log():
    """Return recent git commits as JSON. Basic-auth protected. Reads from git_log DB table."""
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    from flask import jsonify
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT hash, short_hash, date, message
            FROM git_log
            ORDER BY date DESC, id ASC
            LIMIT 50
        """)
        commits = [
            {'hash': r[0], 'short': r[1], 'date': r[2], 'message': r[3]}
            for r in cur.fetchall()
        ]
        cur.close()
        db.close()
        return jsonify({'commits': commits})
    except Exception as e:
        return jsonify({'commits': [], 'note': str(e)})


# ── /ops/outlets ──────────────────────────────────────────────────────────────

@app.route('/ops/outlets', methods=['GET'])
def ops_outlets():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    from flask import render_template_string
    import json
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT outlet_id, outlet_name, score, tier, total_scoreable_claims,
                   total_evaluated_claims, verdict_counts, last_evaluated_at, updated_at
            FROM api_outlets
            ORDER BY COALESCE(score, -1) DESC
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        # Convert decimals and datetimes for JSON
        for r in rows:
            r['score'] = float(r['score']) if r['score'] is not None else None
            r['last_evaluated_at'] = r['last_evaluated_at'].isoformat() if r['last_evaluated_at'] else None
            r['updated_at'] = r['updated_at'].isoformat() if r['updated_at'] else None
        cur.execute("SELECT COUNT(*) FROM api_outlets WHERE score IS NOT NULL")
        scored_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM api_outlets")
        total_count = cur.fetchone()[0]
        # Tracked outlets (below threshold, in api_outlets)
        cur.execute("""
            SELECT outlet_id, outlet_name, total_scoreable_claims, last_evaluated_at
            FROM api_outlets WHERE score IS NULL
            ORDER BY total_scoreable_claims DESC LIMIT 40
        """)
        tracked_rows = [{"outlet_id": r[0], "outlet_name": r[1],
                          "claims": r[2], "needed": max(0, 20 - r[2]),
                          "last_evaluated_at": r[3].isoformat() if r[3] else None}
                         for r in cur.fetchall()]
        # Outlets with articles in DB but not in api_outlets
        EXCLUDED = ["gao.gov","justice.gov","war.gov","whitehouse.gov","federalreserve.gov",
                    "sec.gov","tools.cdc.gov","fda.gov","blogs.loc.gov",
                    "health.harvard.edu","news.mit.edu","prnewswire.com"]
        cur.execute("""
            SELECT a.source_name,
                   COUNT(*) FILTER (WHERE c.verdict IS NOT NULL AND c.claim_origin = 'outlet_claim') AS verdicted,
                   COUNT(*) FILTER (WHERE c.claim_origin = 'outlet_claim') AS total_claims,
                   COUNT(DISTINCT a.id) AS articles
            FROM articles a
            LEFT JOIN claims c ON c.article_id = a.id
            WHERE a.source_name NOT IN (SELECT outlet_id FROM api_outlets)
              AND a.source_name NOT LIKE '%%news.google.com%%'
              AND a.source_name NOT IN (SELECT unnest(%s::text[]))
            GROUP BY a.source_name
            HAVING COUNT(*) FILTER (WHERE c.claim_origin = 'outlet_claim') > 0
            ORDER BY verdicted DESC
            LIMIT 30
        """, (EXCLUDED,))
        absent_rows = [{"outlet": r[0], "verdicted": r[1],
                         "total_claims": r[2], "articles": r[3],
                         "needed": max(0, 20 - r[1])}
                        for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()
    _OPS_OUTLETS_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Ops — Outlets</title>
<style>
:root{--bg:#0a0a0a;--fg:#e8e8e8;--fg-dim:#888;--accent:#a855f7;--ok:#4ade80;--bad:#f87171;--yellow:#fbbf24;--border:#1e1e1e;--card:#111;--mono:ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}
h1{font-size:18px;margin:0 0 4px}.subtitle{color:var(--fg-dim);font-size:12px;margin-bottom:24px}
.nav-links{font-size:12px;margin-bottom:20px}.nav-links a{color:var(--accent);text-decoration:none;margin-right:16px}.nav-links a:hover{color:#c084fc}
h2{font-size:12px;text-transform:uppercase;letter-spacing:0.1em;color:var(--fg-dim);font-weight:500;margin:32px 0 12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:7px 10px;color:var(--fg-dim);font-weight:500;border-bottom:1px solid var(--border);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:hover td{background:rgba(168,85,247,0.04)}
.score{font-weight:600;font-family:var(--mono)}
.score.high{color:var(--ok)}.score.mid{color:var(--yellow)}.score.low{color:var(--bad)}.score.none{color:var(--fg-dim)}
.tier{font-size:11px;padding:2px 7px;border-radius:3px;font-family:var(--mono)}
.tier.published{background:rgba(74,222,128,0.12);color:var(--ok)}
.tier.stabilizing{background:rgba(96,165,250,0.12);color:#60a5fa}
.tier.limited_data{background:rgba(251,191,36,0.12);color:var(--yellow)}
.tier.tracked{background:rgba(136,136,136,0.12);color:var(--fg-dim)}
.mono{font-family:var(--mono);font-size:12px}
.bar-wrap{display:flex;gap:2px;height:8px;border-radius:2px;overflow:hidden;min-width:80px}
.bar-seg{height:100%}
input#search{background:var(--card);border:1px solid var(--border);color:var(--fg);padding:6px 10px;border-radius:4px;font-size:12px;width:220px;margin-bottom:16px}
</style></head><body>
<a href="/ops" style="text-decoration:none;display:inline-block;margin-bottom:12px"><svg width="160" height="22" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg"><path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="14" r="2.5" fill="#ec4899"/><text x="32" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text><text x="88" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text></svg></a><br><div class="nav-links"><a href="/ops">← Pipeline</a><a href="/ops/history">History</a><a href="/ops/insights">Insights</a><a href="/ops/changelog">Changelog</a><a href="/ops/outlets">Outlets</a><a href="/ops/queue">Queue</a><a href="/ops/disputes">Disputes</a><a href="/ops/api-usage">API</a><a href="/ops/test-report">Test Reports</a><a href="/ops/mobile" style="color:#a855f7;font-weight:600">Mobile</a><a href="/ops/dashboard" style="color:#22c55e;font-weight:600">Dashboard</a></div>
<h1>Outlets</h1>
<div class="subtitle">{{ scored_count }} scored · {{ total_count }} total tracked</div>
<input id="search" placeholder="Filter outlets…" oninput="filterTable(this.value)">
<table id="outlet-table">
<thead><tr>
  <th>Outlet</th><th>Score</th><th>Tier</th><th>Claims</th><th>Verdicts</th><th>Last evaluated</th>
</tr></thead>
<tbody>
{% for r in outlets %}
<tr>
  <td><a href="{{ r.leaderboard_url or '#' }}" style="color:var(--accent);text-decoration:none" target="_blank">{{ r.outlet_name or r.outlet_id }}</a></td>
  <td class="score {% if r.score is none %}none{% elif r.score >= 85 %}high{% elif r.score >= 70 %}mid{% else %}low{% endif %}">
    {{ '%.1f'|format(r.score) if r.score is not none else '—' }}
  </td>
  <td><span class="tier {{ r.tier }}">{{ r.tier }}</span></td>
  <td class="mono">{{ r.total_scoreable_claims }}</td>
  <td>
    {% if r.verdict_counts %}
    <div class="bar-wrap" title="{{ r.verdict_counts }}">
      {% set vc = r.verdict_counts %}
      {% set total = (vc.get('supported',0) + vc.get('plausible',0) + vc.get('corroborated',0) + vc.get('overstated',0) + vc.get('disputed',0) + vc.get('not_supported',0)) %}
      {% if total > 0 %}
        <div class="bar-seg" style="width:{{ (vc.get('supported',0)+vc.get('plausible',0)+vc.get('corroborated',0))/total*100 }}%;background:var(--ok)"></div>
        <div class="bar-seg" style="width:{{ vc.get('overstated',0)/total*100 }}%;background:var(--yellow)"></div>
        <div class="bar-seg" style="width:{{ (vc.get('disputed',0)+vc.get('not_supported',0))/total*100 }}%;background:var(--bad)"></div>
      {% endif %}
    </div>
    {% endif %}
  </td>
  <td class="mono" style="color:var(--fg-dim);font-size:11px">{{ r.last_evaluated_at[:10] if r.last_evaluated_at else '—' }}</td>
</tr>
{% endfor %}
</tbody></table>

<h2>Tracked — approaching threshold</h2>
{% if tracked %}
<table>
<thead><tr><th>Outlet</th><th>Verdicted claims</th><th>Needed to score</th><th>Last evaluated</th></tr></thead>
<tbody>
{% for r in tracked %}
<tr>
  <td>{{ r.outlet_name or r.outlet_id }}</td>
  <td class="mono">{{ r.claims }}</td>
  <td class="mono" style="color:{% if r.needed == 0 %}var(--ok){% elif r.needed <= 3 %}var(--yellow){% else %}var(--fg-dim){% endif %}">{{ r.needed }} more</td>
  <td class="mono" style="color:var(--fg-dim);font-size:11px">{{ r.last_evaluated_at[:10] if r.last_evaluated_at else '—' }}</td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<div style="color:var(--fg-dim)">No tracked outlets.</div>{% endif %}

<h2>In pipeline — not yet scored</h2>
<div style="color:var(--fg-dim);font-size:12px;margin-bottom:12px">Outlets with articles and claims in the DB but not yet in the leaderboard. Includes outlets removed in v1.7 purge.</div>
{% if absent %}
<table>
<thead><tr><th>Outlet</th><th>Verdicted</th><th>Outlet claims</th><th>Articles</th><th>Needed</th></tr></thead>
<tbody>
{% for r in absent %}
<tr>
  <td>{{ r.outlet }}</td>
  <td class="mono">{{ r.verdicted }}</td>
  <td class="mono" style="color:var(--fg-dim)">{{ r.total_claims }}</td>
  <td class="mono" style="color:var(--fg-dim)">{{ r.articles }}</td>
  <td class="mono" style="color:{% if r.needed <= 5 %}var(--yellow){% else %}var(--fg-dim){% endif %}">{{ r.needed }} more</td>
</tr>
{% endfor %}
</tbody></table>
{% else %}<div style="color:var(--fg-dim)">No absent outlets found.</div>{% endif %}

<script>
function filterTable(q) {
  q = q.toLowerCase();
  document.querySelectorAll('#outlet-table tbody tr').forEach(tr => {
    tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
</script>
</body></html>"""
    return render_template_string(_OPS_OUTLETS_HTML,
        outlets=rows, scored_count=scored_count, total_count=total_count,
        tracked=tracked_rows, absent=absent_rows)


# ── /ops/queue ────────────────────────────────────────────────────────────────

@app.route('/ops/queue', methods=['GET'])
def ops_queue():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    from flask import render_template_string
    conn = get_db()
    cur = conn.cursor()
    try:
        # Overall queue stats
        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE verdict IS NULL AND priority_score >= 30 AND COALESCE(verification_attempts,0) < 3) AS eligible,
              COUNT(*) FILTER (WHERE verdict IS NULL AND priority_score < 30) AS below_threshold,
              COUNT(*) FILTER (WHERE verdict IS NULL AND COALESCE(verification_attempts,0) >= 3) AS capped,
              COUNT(*) FILTER (WHERE verdict IS NOT NULL) AS verdicted,
              COUNT(*) AS total
            FROM claims
        """)
        q = cur.fetchone()
        queue_stats = dict(eligible=q[0], below_threshold=q[1], capped=q[2], verdicted=q[3], total=q[4])

        # Eligible claims by outlet (top 20)
        cur.execute("""
            SELECT a.source_name, COUNT(*) AS n,
                   AVG(c.priority_score)::int AS avg_priority,
                   MAX(c.first_seen) AS newest
            FROM claims c
            JOIN articles a ON c.article_id = a.id
            WHERE c.verdict IS NULL
              AND c.priority_score >= 30
              AND COALESCE(c.verification_attempts, 0) < 3
            GROUP BY a.source_name
            ORDER BY n DESC
            LIMIT 20
        """)
        by_outlet = [{'outlet': r[0], 'count': r[1], 'avg_priority': r[2],
                      'newest': r[3].strftime('%Y-%m-%d %H:%M') if r[3] else None}
                     for r in cur.fetchall()]

        # Priority distribution of eligible claims
        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE priority_score >= 70) AS high,
              COUNT(*) FILTER (WHERE priority_score >= 50 AND priority_score < 70) AS mid,
              COUNT(*) FILTER (WHERE priority_score >= 30 AND priority_score < 50) AS low
            FROM claims
            WHERE verdict IS NULL AND priority_score >= 30 AND COALESCE(verification_attempts,0) < 3
        """)
        pq = cur.fetchone()
        priority_dist = dict(high=pq[0], mid=pq[1], low=pq[2])

        # Oldest unverified eligible claim
        cur.execute("""
            SELECT MIN(first_seen) FROM claims
            WHERE verdict IS NULL AND priority_score >= 30 AND COALESCE(verification_attempts,0) < 3
        """)
        oldest = cur.fetchone()[0]

    finally:
        cur.close()
        conn.close()

    _OPS_QUEUE_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Ops — Queue</title>
<style>
:root{--bg:#0a0a0a;--fg:#e8e8e8;--fg-dim:#888;--accent:#a855f7;--ok:#4ade80;--bad:#f87171;--yellow:#fbbf24;--border:#1e1e1e;--card:#111;--mono:ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}
h1{font-size:18px;margin:0 0 4px}.subtitle{color:var(--fg-dim);font-size:12px;margin-bottom:24px}
.nav-links{font-size:12px;margin-bottom:20px}.nav-links a{color:var(--accent);text-decoration:none;margin-right:16px}.nav-links a:hover{color:#c084fc}
h2{font-size:12px;text-transform:uppercase;letter-spacing:0.1em;color:var(--fg-dim);font-weight:500;margin:32px 0 12px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:8px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px 18px}
.stat-label{font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:var(--fg-dim);margin-bottom:6px}
.stat-value{font-family:var(--mono);font-size:24px;font-weight:600;line-height:1}
.stat-value.ok{color:var(--ok)}.stat-value.warn{color:var(--yellow)}.stat-value.bad{color:var(--bad)}.stat-value.dim{color:var(--fg-dim)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:7px 10px;color:var(--fg-dim);font-weight:500;border-bottom:1px solid var(--border);font-size:10px;text-transform:uppercase;letter-spacing:0.08em}
td{padding:7px 10px;border-bottom:1px solid var(--border)}
tr:hover td{background:rgba(168,85,247,0.04)}
.mono{font-family:var(--mono);font-size:12px}
</style></head><body>
<a href="/ops" style="text-decoration:none;display:inline-block;margin-bottom:12px"><svg width="160" height="22" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg"><path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="14" r="2.5" fill="#ec4899"/><text x="32" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text><text x="88" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text></svg></a><br><div class="nav-links"><a href="/ops">← Pipeline</a><a href="/ops/history">History</a><a href="/ops/insights">Insights</a><a href="/ops/changelog">Changelog</a><a href="/ops/outlets">Outlets</a><a href="/ops/queue">Queue</a><a href="/ops/disputes">Disputes</a><a href="/ops/api-usage">API</a><a href="/ops/test-report">Test Reports</a><a href="/ops/mobile" style="color:#a855f7;font-weight:600">Mobile</a><a href="/ops/dashboard" style="color:#22c55e;font-weight:600">Dashboard</a></div>
<h1>Verdict Queue</h1>
<div class="subtitle">Claims awaiting verdict assignment</div>

<h2>Overview</h2>
<div class="stat-grid">
  <div class="stat-card"><div class="stat-label">Eligible for verdict</div><div class="stat-value {% if queue_stats.eligible > 500 %}warn{% elif queue_stats.eligible > 0 %}ok{% else %}dim{% endif %}">{{ queue_stats.eligible }}</div></div>
  <div class="stat-card"><div class="stat-label">Below priority threshold</div><div class="stat-value dim">{{ queue_stats.below_threshold }}</div></div>
  <div class="stat-card"><div class="stat-label">Capped (3 attempts)</div><div class="stat-value {% if queue_stats.capped > 0 %}warn{% else %}dim{% endif %}">{{ queue_stats.capped }}</div></div>
  <div class="stat-card"><div class="stat-label">Verdicted (total)</div><div class="stat-value ok">{{ queue_stats.verdicted }}</div></div>
</div>
{% if oldest %}<div style="font-size:12px;color:var(--fg-dim);margin-top:8px">Oldest eligible claim: {{ oldest.strftime('%Y-%m-%d %H:%M') }}</div>{% endif %}

<h2>Priority distribution (eligible claims)</h2>
<div class="stat-grid">
  <div class="stat-card"><div class="stat-label">High priority (70+)</div><div class="stat-value ok">{{ priority_dist.high }}</div></div>
  <div class="stat-card"><div class="stat-label">Mid priority (50–69)</div><div class="stat-value">{{ priority_dist.mid }}</div></div>
  <div class="stat-card"><div class="stat-label">Low priority (30–49)</div><div class="stat-value dim">{{ priority_dist.low }}</div></div>
</div>

<h2>By outlet (top 20 eligible)</h2>
{% if by_outlet %}
<table>
<thead><tr><th>Outlet</th><th>Eligible claims</th><th>Avg priority</th><th>Newest claim</th></tr></thead>
<tbody>
{% for r in by_outlet %}
<tr>
  <td>{{ r.outlet }}</td>
  <td class="mono">{{ r.count }}</td>
  <td class="mono">{{ r.avg_priority }}</td>
  <td class="mono" style="color:var(--fg-dim);font-size:11px">{{ r.newest or '—' }}</td>
</tr>
{% endfor %}
</tbody></table>
{% else %}
<div style="color:var(--fg-dim);padding:16px 0">Queue is empty — all eligible claims have been processed.</div>
{% endif %}
</body></html>"""
    return render_template_string(_OPS_QUEUE_HTML,
        queue_stats=queue_stats, by_outlet=by_outlet,
        priority_dist=priority_dist, oldest=oldest)


# ── /ops/disputes ─────────────────────────────────────────────────────────────

@app.route('/ops/disputes', methods=['GET'])
def ops_disputes():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    from flask import render_template_string
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, domain, claim_id, contact_email, dispute_text,
                   outlet_response, status, submitted_at, reviewed_at, resolution
            FROM outlet_disputes
            ORDER BY submitted_at DESC
        """)
        cols = [d[0] for d in cur.description]
        disputes = [dict(zip(cols, r)) for r in cur.fetchall()]
        for d in disputes:
            d['submitted_at'] = d['submitted_at'].strftime('%Y-%m-%d %H:%M') if d['submitted_at'] else None
            d['reviewed_at'] = d['reviewed_at'].strftime('%Y-%m-%d %H:%M') if d['reviewed_at'] else None
        cur.execute("SELECT status, COUNT(*) FROM outlet_disputes GROUP BY status")
        status_counts = dict(cur.fetchall())
    finally:
        cur.close()
        conn.close()

    _OPS_DISPUTES_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Ops — Disputes</title>
<style>
:root{--bg:#0a0a0a;--fg:#e8e8e8;--fg-dim:#888;--accent:#a855f7;--ok:#4ade80;--bad:#f87171;--yellow:#fbbf24;--border:#1e1e1e;--card:#111;--mono:ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}
h1{font-size:18px;margin:0 0 4px}.subtitle{color:var(--fg-dim);font-size:12px;margin-bottom:24px}
.nav-links{font-size:12px;margin-bottom:20px}.nav-links a{color:var(--accent);text-decoration:none;margin-right:16px}.nav-links a:hover{color:#c084fc}
h2{font-size:12px;text-transform:uppercase;letter-spacing:0.1em;color:var(--fg-dim);font-weight:500;margin:32px 0 12px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
.stat-label{font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:var(--fg-dim);margin-bottom:6px}
.stat-value{font-family:var(--mono);font-size:22px;font-weight:600}
.dispute-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:18px 20px;margin-bottom:12px}
.dispute-header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.dispute-domain{font-weight:600;font-size:14px}
.dispute-meta{font-size:11px;color:var(--fg-dim);font-family:var(--mono)}
.status-badge{font-size:11px;padding:2px 8px;border-radius:3px;font-family:var(--mono)}
.status-badge.pending{background:rgba(251,191,36,0.15);color:var(--yellow)}
.status-badge.resolved{background:rgba(74,222,128,0.12);color:var(--ok)}
.status-badge.rejected{background:rgba(248,113,113,0.12);color:var(--bad)}
.dispute-text{color:var(--fg-dim);font-size:13px;margin-top:8px;line-height:1.6}
.empty{color:var(--fg-dim);padding:32px 0;text-align:center}
</style></head><body>
<a href="/ops" style="text-decoration:none;display:inline-block;margin-bottom:12px"><svg width="160" height="22" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg"><path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="14" r="2.5" fill="#ec4899"/><text x="32" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text><text x="88" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text></svg></a><br><div class="nav-links"><a href="/ops">← Pipeline</a><a href="/ops/history">History</a><a href="/ops/insights">Insights</a><a href="/ops/changelog">Changelog</a><a href="/ops/outlets">Outlets</a><a href="/ops/queue">Queue</a><a href="/ops/disputes">Disputes</a><a href="/ops/api-usage">API</a><a href="/ops/test-report">Test Reports</a><a href="/ops/mobile" style="color:#a855f7;font-weight:600">Mobile</a><a href="/ops/dashboard" style="color:#22c55e;font-weight:600">Dashboard</a></div>
<h1>Verdict Disputes</h1>
<div class="subtitle">Submitted via report page correction links</div>

<div class="stat-grid">
  <div class="stat-card"><div class="stat-label">Total</div><div class="stat-value">{{ disputes|length }}</div></div>
  <div class="stat-card"><div class="stat-label">Pending</div><div class="stat-value" style="color:var(--yellow)">{{ status_counts.get('pending', 0) }}</div></div>
  <div class="stat-card"><div class="stat-label">Resolved</div><div class="stat-value" style="color:var(--ok)">{{ status_counts.get('resolved', 0) }}</div></div>
  <div class="stat-card"><div class="stat-label">Rejected</div><div class="stat-value" style="color:var(--bad)">{{ status_counts.get('rejected', 0) }}</div></div>
</div>

{% if disputes %}
{% for d in disputes %}
<div class="dispute-card">
  <div class="dispute-header">
    <div>
      <span class="dispute-domain">{{ d.domain }}</span>
      {% if d.claim_id %}<span class="dispute-meta" style="margin-left:10px">claim #{{ d.claim_id }}</span>{% endif %}
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <span class="dispute-meta">{{ d.submitted_at }}</span>
      <span class="status-badge {{ d.status or 'pending' }}">{{ d.status or 'pending' }}</span>
    </div>
  </div>
  {% if d.contact_email %}<div class="dispute-meta" style="margin-bottom:6px">From: {{ d.contact_email }}</div>{% endif %}
  <div class="dispute-text">{{ d.dispute_text }}</div>
  {% if d.resolution %}<div class="dispute-text" style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px"><strong style="color:var(--fg)">Resolution:</strong> {{ d.resolution }}</div>{% endif %}
</div>
{% endfor %}
{% else %}
<div class="empty">No disputes submitted yet.</div>
{% endif %}
</body></html>"""
    return render_template_string(_OPS_DISPUTES_HTML,
        disputes=disputes, status_counts=status_counts)


# ── /ops/api-usage ────────────────────────────────────────────────────────────


# ════════════════════════════════════════════════════════════════
# Page view tracking + Ops Dashboard
# ════════════════════════════════════════════════════════════════

@app.route('/api/pv', methods=['POST'])
def api_page_view():
    """Record a page view. No auth — lightweight, public."""
    try:
        data = request.get_json(silent=True) or {}
        path = (data.get('p') or '/')[:500]
        referrer = (data.get('r') or '')[:500] or None
        session_id = (data.get('s') or '')[:64] or None
        screen_width = data.get('w')
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip:
            ip = ip.split(',')[0].strip()
        ua = (request.headers.get('User-Agent') or '')[:500]
        is_mobile_app = 'verum-signal-app' in ua.lower() or data.get('app') is True

        # Skip bots and ops pages
        if any(skip in path for skip in ['/ops/', '/api/pv', '/static/', '/favicon']):
            return jsonify({'ok': True, 'id': None})

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO page_views (path, referrer, user_agent, session_id, ip, screen_width, is_mobile_app)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (path, referrer, ua, session_id, ip, screen_width, is_mobile_app))
        pv_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'ok': True, 'id': pv_id})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/pv/duration', methods=['POST'])
def api_page_view_duration():
    """Update duration for an existing page view (beacon on page leave)."""
    try:
        data = request.get_json(silent=True) or {}
        pv_id = data.get('id')
        duration = data.get('d')
        if not pv_id or not duration:
            return jsonify({'ok': False}), 400
        duration = min(int(duration), 3600000)  # cap at 1 hour
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE page_views SET duration_ms = %s WHERE id = %s", (duration, pv_id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'ok': True})
    except Exception:
        return jsonify({'ok': False}), 500


_OPS_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard — Verum Signal Ops</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#fff;font-family:'DM Sans',system-ui,sans-serif;padding:24px 32px}
h1{font-family:'DM Serif Display',Georgia,serif;font-size:28px;margin-bottom:4px;letter-spacing:-0.5px}
.sub{color:#6b7280;font-size:12px;margin-bottom:28px;font-family:monospace}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:32px}
.card{background:#131218;border:0.5px solid #3a3743;border-radius:14px;padding:18px 20px}
.card .label{font-size:10px;letter-spacing:1.6px;text-transform:uppercase;color:#6b7280;margin-bottom:8px;font-family:monospace}
.card .value{font-family:'DM Serif Display',Georgia,serif;font-size:32px;letter-spacing:-1px;line-height:1}
.card .delta{font-size:11px;color:#22c55e;margin-top:4px;font-family:monospace}
.card .delta.down{color:#ef4444}
h2{font-size:14px;letter-spacing:1.8px;text-transform:uppercase;color:#9ca3af;margin-bottom:14px;font-family:monospace;
   display:flex;align-items:center;gap:10px}
h2::after{content:'';flex:1;height:0.5px;background:rgba(255,255,255,0.07)}
table{width:100%;border-collapse:collapse;margin-bottom:32px}
th{text-align:left;font-size:10px;letter-spacing:1.2px;text-transform:uppercase;color:#6b7280;padding:8px 12px;
   border-bottom:0.5px solid #3a3743;font-family:monospace;font-weight:600}
td{padding:10px 12px;border-bottom:0.5px solid rgba(255,255,255,0.04);font-size:13px;color:#9ca3af}
td:first-child{color:#fff}
tr:hover td{background:rgba(168,85,247,0.03)}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:600;font-family:monospace}
.badge-live{background:rgba(34,197,94,0.15);color:#22c55e}
.badge-app{background:rgba(168,85,247,0.1);color:#a855f7}
.pct-bar{height:4px;border-radius:99px;background:#2e2c36;margin-top:4px;overflow:hidden}
.pct-fill{height:100%;border-radius:99px;background:#a855f7}
.refresh{color:#6b7280;text-decoration:none;font-size:11px;font-family:monospace;float:right}
.refresh:hover{color:#a855f7}
.live-dot{width:6px;height:6px;border-radius:3px;background:#22c55e;display:inline-block;margin-right:4px;
          animation:pulse 1.4s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:800px){.two-col{grid-template-columns:1fr}}
</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
</head>
<body>
<a href="?refresh=1" class="refresh">↻ Refresh</a>
<h1>Dashboard</h1>
<p class="sub">VERUM SIGNAL OPS · {{ now }}</p>

<!-- Summary cards -->
<div class="grid">
  <div class="card">
    <div class="label">Visitors today</div>
    <div class="value">{{ today.visitors }}</div>
  </div>
  <div class="card">
    <div class="label">Page views today</div>
    <div class="value">{{ today.views }}</div>
  </div>
  <div class="card">
    <div class="label">Avg duration</div>
    <div class="value">{{ today.avg_duration }}</div>
  </div>
  <div class="card">
    <div class="label">Live now (5m)</div>
    <div class="value"><span class="live-dot"></span>{{ live_count }}</div>
  </div>
</div>

<div class="grid">
  <div class="card">
    <div class="label">7-day visitors</div>
    <div class="value">{{ week.visitors }}</div>
  </div>
  <div class="card">
    <div class="label">7-day page views</div>
    <div class="value">{{ week.views }}</div>
  </div>
  <div class="card">
    <div class="label">30-day visitors</div>
    <div class="value">{{ month.visitors }}</div>
  </div>
  <div class="card">
    <div class="label">30-day page views</div>
    <div class="value">{{ month.views }}</div>
  </div>
</div>

<div class="two-col">
<div>
<!-- Top pages -->
<h2>Top pages (7 days)</h2>
<table>
<tr><th>Page</th><th>Views</th><th>Avg time</th></tr>
{% for p in top_pages %}
<tr>
  <td>{{ p.path }}</td>
  <td>{{ p.views }}</td>
  <td style="color:#6b7280">{{ p.avg_dur }}</td>
</tr>
{% endfor %}
{% if not top_pages %}<tr><td colspan="3" style="color:#6b7280">No data yet</td></tr>{% endif %}
</table>
</div>

<div>
<!-- Mobile app activity -->
<h2>App API calls (7 days)</h2>
<table>
<tr><th>Endpoint</th><th>Calls</th></tr>
{% for a in app_api %}
<tr>
  <td>{{ a.endpoint }}</td>
  <td>{{ a.calls }}</td>
</tr>
{% endfor %}
{% if not app_api %}<tr><td colspan="2" style="color:#6b7280">No API calls</td></tr>{% endif %}
</table>
</div>
</div>

<!-- Daily breakdown -->
<h2>Daily views (last 14 days)</h2>
<table>
<tr><th>Date</th><th>Visitors</th><th>Views</th><th>Avg duration</th><th>App calls</th></tr>
{% for d in daily %}
<tr>
  <td>{{ d.date }}</td>
  <td>{{ d.visitors }}</td>
  <td>{{ d.views }}</td>
  <td style="color:#6b7280">{{ d.avg_dur }}</td>
  <td style="color:#6b7280">{{ d.api_calls }}</td>
</tr>
{% endfor %}
{% if not daily %}<tr><td colspan="5" style="color:#6b7280">No data yet</td></tr>{% endif %}
</table>

<!-- Recent visitors -->
<h2>Recent visitors</h2>
<table>
<tr><th>Time</th><th>Page</th><th>Duration</th><th>Referrer</th><th>Session</th></tr>
{% for v in recent %}
<tr>
  <td style="font-family:monospace;font-size:11px">{{ v.time }}</td>
  <td>{{ v.path }}</td>
  <td style="color:#6b7280">{{ v.duration }}</td>
  <td style="color:#6b7280;font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ v.referrer or '—' }}</td>
  <td style="font-family:monospace;font-size:10px;color:#6b7280">{{ v.sid }}</td>
</tr>
{% endfor %}
{% if not recent %}<tr><td colspan="5" style="color:#6b7280">No visitors yet</td></tr>{% endif %}
</table>

<!-- Users -->
<h2>User registrations</h2>
<table>
<tr><th>Period</th><th>Count</th></tr>
<tr><td>This week</td><td>{{ users_week }}</td></tr>
<tr><td>This month</td><td>{{ users_month }}</td></tr>
<tr><td>Total</td><td>{{ users_total }}</td></tr>
</table>

<p style="color:#3a3743;font-size:10px;margin-top:40px;font-family:monospace">Verum Signal Ops · Confidential</p>
</body></html>"""


@app.route('/ops/dashboard', methods=['GET'])
def ops_main():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    from flask import render_template_string
    from datetime import datetime, timedelta

    conn = get_db()
    cur = conn.cursor()

    def _fmt_dur(ms):
        if ms is None or ms == 0:
            return '—'
        s = int(ms / 1000)
        if s < 60:
            return f'{s}s'
        return f'{s // 60}m {s % 60}s'

    def _period_stats(days):
        cur.execute("""
            SELECT
                COUNT(DISTINCT session_id) AS visitors,
                COUNT(*) AS views,
                ROUND(AVG(duration_ms) FILTER (WHERE duration_ms > 0))::int AS avg_dur
            FROM page_views
            WHERE created_at > NOW() - INTERVAL '%s days'
        """ % days)
        r = cur.fetchone()
        return {'visitors': r[0] or 0, 'views': r[1] or 0, 'avg_duration': _fmt_dur(r[2])}

    today = _period_stats(1)
    week = _period_stats(7)
    month = _period_stats(30)

    # Live (last 5 min)
    cur.execute("SELECT COUNT(DISTINCT session_id) FROM page_views WHERE created_at > NOW() - INTERVAL '5 minutes'")
    live_count = cur.fetchone()[0] or 0

    # Top pages (7 days)
    cur.execute("""
        SELECT path, COUNT(*) AS views, ROUND(AVG(duration_ms) FILTER (WHERE duration_ms > 0))::int AS avg_dur
        FROM page_views
        WHERE created_at > NOW() - INTERVAL '7 days'
        GROUP BY path ORDER BY views DESC LIMIT 15
    """)
    top_pages = [{'path': r[0], 'views': r[1], 'avg_dur': _fmt_dur(r[2])} for r in cur.fetchall()]

    # App API calls (7 days)
    try:
        cur.execute("""
            SELECT endpoint, COUNT(*) AS calls
            FROM api_usage
            WHERE created_at > NOW() - INTERVAL '7 days'
            GROUP BY endpoint ORDER BY calls DESC LIMIT 10
        """)
        app_api = [{'endpoint': r[0], 'calls': r[1]} for r in cur.fetchall()]
    except Exception:
        app_api = []

    # Daily breakdown (14 days)
    cur.execute("""
        SELECT
            created_at::date AS day,
            COUNT(DISTINCT session_id) AS visitors,
            COUNT(*) AS views,
            ROUND(AVG(duration_ms) FILTER (WHERE duration_ms > 0))::int AS avg_dur
        FROM page_views
        WHERE created_at > NOW() - INTERVAL '14 days'
        GROUP BY day ORDER BY day DESC
    """)
    pv_daily = {str(r[0]): {'date': str(r[0]), 'visitors': r[1], 'views': r[2], 'avg_dur': _fmt_dur(r[3])} for r in cur.fetchall()}

    # API calls per day
    try:
        cur.execute("""
            SELECT created_at::date AS day, COUNT(*) AS calls
            FROM api_usage
            WHERE created_at > NOW() - INTERVAL '14 days'
            GROUP BY day ORDER BY day DESC
        """)
        api_daily = {str(r[0]): r[1] for r in cur.fetchall()}
    except Exception:
        api_daily = {}

    daily = []
    for i in range(14):
        d = str((datetime.now() - timedelta(days=i)).date())
        row = pv_daily.get(d, {'date': d, 'visitors': 0, 'views': 0, 'avg_dur': '—'})
        row['api_calls'] = api_daily.get(d, 0)
        daily.append(row)

    # Recent visitors
    cur.execute("""
        SELECT created_at, path, duration_ms, referrer, session_id
        FROM page_views ORDER BY created_at DESC LIMIT 20
    """)
    recent = [{'time': r[0].strftime('%H:%M:%S'), 'path': r[1], 'duration': _fmt_dur(r[2]),
               'referrer': (r[3] or '')[:60], 'sid': (r[4] or '')[:8]} for r in cur.fetchall()]

    # User registrations
    try:
        cur.execute("SELECT COUNT(*) FROM users WHERE created_at > NOW() - INTERVAL '7 days'")
        users_week = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM users WHERE created_at > NOW() - INTERVAL '30 days'")
        users_month = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM users")
        users_total = cur.fetchone()[0] or 0
    except Exception:
        users_week = users_month = users_total = 0

    cur.close()
    conn.close()

    return render_template_string(_OPS_DASHBOARD_HTML,
        now=datetime.now().strftime('%B %-d, %Y · %-I:%M %p'),
        today=today, week=week, month=month,
        live_count=live_count,
        top_pages=top_pages, app_api=app_api,
        daily=daily, recent=recent,
        users_week=users_week, users_month=users_month, users_total=users_total,
    )


@app.route('/ops/api-usage', methods=['GET'])
def ops_api_usage():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    from flask import render_template_string
    conn = get_db()
    cur = conn.cursor()
    try:
        # Keys with usage
        cur.execute("""
            SELECT k.id, k.key_prefix, k.tier, k.monthly_quota,
                   k.created_at, k.last_used_at, k.revoked_at,
                   COALESCE(m.call_count, 0) AS calls_this_month
            FROM api_keys k
            LEFT JOIN api_monthly_usage m
              ON m.key_id = k.id AND m.year_month = TO_CHAR(NOW(), 'YYYY-MM')
            ORDER BY k.id
        """)
        cols = [d[0] for d in cur.description]
        keys = [dict(zip(cols, r)) for r in cur.fetchall()]
        for k in keys:
            k['created_at'] = k['created_at'].strftime('%Y-%m-%d') if k['created_at'] else None
            k['last_used_at'] = k['last_used_at'].strftime('%Y-%m-%d %H:%M') if k['last_used_at'] else None
            k['revoked_at'] = k['revoked_at'].strftime('%Y-%m-%d') if k['revoked_at'] else None
            k['active'] = k['revoked_at'] is None
            k['pct'] = round(k['calls_this_month'] / k['monthly_quota'] * 100, 1) if k['monthly_quota'] else 0

        # Recent calls
        cur.execute("""
            SELECT u.created_at, k.key_prefix, u.endpoint, u.status_code, u.response_time_ms, u.ip
            FROM api_usage u
            JOIN api_keys k ON k.id = u.key_id
            ORDER BY u.created_at DESC
            LIMIT 50
        """)
        cols = [d[0] for d in cur.description]
        calls = [dict(zip(cols, r)) for r in cur.fetchall()]
        for c in calls:
            c['created_at'] = c['created_at'].strftime('%Y-%m-%d %H:%M:%S') if c['created_at'] else None

        # Endpoint breakdown (last 30 days)
        cur.execute("""
            SELECT endpoint, COUNT(*) AS n,
                   ROUND(AVG(response_time_ms)) AS avg_ms,
                   COUNT(*) FILTER (WHERE status_code >= 400) AS errors
            FROM api_usage
            WHERE created_at > NOW() - INTERVAL '30 days'
            GROUP BY endpoint ORDER BY n DESC
        """)
        endpoints = [{'endpoint': r[0], 'calls': r[1], 'avg_ms': r[2], 'errors': r[3]}
                     for r in cur.fetchall()]

        # Total calls this month
        cur.execute("""
            SELECT COALESCE(SUM(call_count), 0) FROM api_monthly_usage
            WHERE year_month = TO_CHAR(NOW(), 'YYYY-MM')
        """)
        total_this_month = cur.fetchone()[0]

    finally:
        cur.close()
        conn.close()

    _OPS_API_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Ops — API Usage</title>
<style>
:root{--bg:#0a0a0a;--fg:#e8e8e8;--fg-dim:#888;--accent:#a855f7;--ok:#4ade80;--bad:#f87171;--yellow:#fbbf24;--border:#1e1e1e;--card:#111;--mono:ui-monospace,'SF Mono',Menlo,monospace}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.5}
h1{font-size:18px;margin:0 0 4px}.subtitle{color:var(--fg-dim);font-size:12px;margin-bottom:24px}
.nav-links{font-size:12px;margin-bottom:20px}.nav-links a{color:var(--accent);text-decoration:none;margin-right:16px}.nav-links a:hover{color:#c084fc}
h2{font-size:12px;text-transform:uppercase;letter-spacing:0.1em;color:var(--fg-dim);font-weight:500;margin:32px 0 12px}
.key-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px 18px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
.key-prefix{font-family:var(--mono);font-size:13px;font-weight:600}
.key-meta{font-size:11px;color:var(--fg-dim)}
.key-status{font-size:11px;padding:2px 8px;border-radius:3px;font-family:var(--mono)}
.key-status.active{background:rgba(74,222,128,0.12);color:var(--ok)}
.key-status.revoked{background:rgba(248,113,113,0.12);color:var(--bad)}
.quota-bar{height:6px;border-radius:3px;background:var(--border);width:120px;overflow:hidden}
.quota-fill{height:100%;border-radius:3px;background:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:6px 10px;color:var(--fg-dim);font-weight:500;border-bottom:1px solid var(--border);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid var(--border);font-family:var(--mono)}
tr:hover td{background:rgba(168,85,247,0.04)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.dim{color:var(--fg-dim)}
</style></head><body>
<a href="/ops" style="text-decoration:none;display:inline-block;margin-bottom:12px"><svg width="160" height="22" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg"><path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="14" r="2.5" fill="#ec4899"/><text x="32" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text><text x="88" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text></svg></a><br><div class="nav-links"><a href="/ops">← Pipeline</a><a href="/ops/history">History</a><a href="/ops/insights">Insights</a><a href="/ops/changelog">Changelog</a><a href="/ops/outlets">Outlets</a><a href="/ops/queue">Queue</a><a href="/ops/disputes">Disputes</a><a href="/ops/api-usage">API</a><a href="/ops/test-report">Test Reports</a><a href="/ops/mobile" style="color:#a855f7;font-weight:600">Mobile</a><a href="/ops/dashboard" style="color:#22c55e;font-weight:600">Dashboard</a></div>
<h1>API Usage</h1>
<div class="subtitle">{{ total_this_month }} calls this month across all keys</div>

<h2>Keys</h2>
{% for k in keys %}
<div class="key-card">
  <div>
    <div class="key-prefix">{{ k.key_prefix }}… <span class="key-status {{ 'active' if k.active else 'revoked' }}">{{ 'active' if k.active else 'revoked' }}</span></div>
    <div class="key-meta">{{ k.tier }} · created {{ k.created_at }} · last used {{ k.last_used_at or 'never' }}</div>
  </div>
  <div style="text-align:right">
    <div style="font-family:var(--mono);font-size:13px">{{ k.calls_this_month }} / {{ k.monthly_quota }} this month</div>
    <div class="quota-bar" style="margin-top:6px;margin-left:auto"><div class="quota-fill" style="width:{{ [k.pct, 100]|min }}%"></div></div>
    <div class="key-meta">{{ k.pct }}% of quota</div>
  </div>
</div>
{% endfor %}

<h2>Endpoints (last 30 days)</h2>
<table>
<thead><tr><th>Endpoint</th><th>Calls</th><th>Avg ms</th><th>Errors</th></tr></thead>
<tbody>
{% for e in endpoints %}
<tr>
  <td style="color:var(--fg)">{{ e.endpoint }}</td>
  <td>{{ e.calls }}</td>
  <td class="{{ 'bad' if e.avg_ms and e.avg_ms > 2000 else '' }}">{{ e.avg_ms or '—' }}</td>
  <td class="{{ 'bad' if e.errors > 0 else 'dim' }}">{{ e.errors }}</td>
</tr>
{% endfor %}
</tbody></table>

<h2>Recent calls</h2>
<table>
<thead><tr><th>Time</th><th>Key</th><th>Endpoint</th><th>Status</th><th>ms</th><th>IP</th></tr></thead>
<tbody>
{% for c in calls %}
<tr>
  <td class="dim" style="font-size:11px">{{ c.created_at }}</td>
  <td>{{ c.key_prefix }}…</td>
  <td style="color:var(--fg)">{{ c.endpoint }}</td>
  <td class="{{ 'bad' if c.status_code >= 400 else 'ok' }}">{{ c.status_code }}</td>
  <td>{{ c.response_time_ms }}</td>
  <td class="dim" style="font-size:11px">{{ c.ip }}</td>
</tr>
{% endfor %}
</tbody></table>
</body></html>"""
    return render_template_string(_OPS_API_HTML,
        keys=keys, calls=calls, endpoints=endpoints, total_this_month=total_this_month)


@app.route('/ops/history', methods=['GET'])
def ops_history():
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    import base64 as _b64
    ops_pw = os.environ.get('OPS_PASSWORD', '')
    ops_auth_b64 = _b64.b64encode(f'admin:{ops_pw}'.encode()).decode()
    html = _OPS_HISTORY_HTML.replace('__OPS_AUTH_PLACEHOLDER__', ops_auth_b64)
    from flask import Response
    return Response(html, mimetype='text/html')


@app.route('/api/ops/stream-health', methods=['GET'])
def api_ops_stream_health():
    """Return veris-stream heartbeat status. Basic-auth protected."""
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT status, started_at, items_processed, error_message
            FROM job_runs
            WHERE stage = 'stream_heartbeat'
            ORDER BY started_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            return jsonify({'status': 'unknown', 'last_heartbeat': None, 'event_id': None, 'error': None})
        status, started_at, event_id, error_msg = row
        age_seconds = (datetime.utcnow() - started_at.replace(tzinfo=None)).total_seconds()
        return jsonify({
            'status': status,
            'last_heartbeat': started_at.isoformat(),
            'age_seconds': int(age_seconds),
            'event_id': event_id if event_id else None,
            'error': error_msg or None,
            'stale': age_seconds > 300,  # no heartbeat in 5 min = stale
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/health/debate', methods=['GET'])
def api_health_debate():
    """Public endpoint for monitoring debate claim promotion.
    Returns count of provisional claims older than 70 minutes.
    Designed for uptime monitors (UptimeRobot, Better Stack).
    Returns 200 if healthy, 503 if overdue provisionals exist."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM claims
            WHERE claim_origin = 'debate_claim'
              AND verdict_status = 'provisional'
              AND verdict IS NOT NULL
              AND first_seen < NOW() - INTERVAL '70 minutes'
        """)
        overdue = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM claims
            WHERE claim_origin = 'debate_claim'
              AND verdict_status = 'provisional'
              AND verdict IS NOT NULL
        """)
        total_provisional = cur.fetchone()[0]
        cur.close()
        status_code = 200 if overdue == 0 else 503
        return jsonify({
            'status': 'healthy' if overdue == 0 else 'overdue',
            'overdue_provisionals': overdue,
            'total_provisionals': total_provisional,
        }), status_code
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/ops/debates', methods=['GET'])
def api_ops_debates():
    """Debate pipeline stats for ops dashboard. Basic-auth protected."""
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    try:
        conn = get_db()
        cur = conn.cursor()

        # Live event check
        cur.execute("""
            SELECT id, slug, event_name, event_date, start_time, timezone
            FROM events
            WHERE is_public = TRUE AND event_date = CURRENT_DATE
            AND (
                start_time IS NULL
                OR (
                    NOW() AT TIME ZONE COALESCE(timezone, 'UTC') >=
                        (start_time::interval - INTERVAL '5 minutes')
                    AND NOW() AT TIME ZONE COALESCE(timezone, 'UTC') <=
                        (start_time::interval + INTERVAL '3 hours')
                )
            )
            LIMIT 1
        """)
        live_row = cur.fetchone()
        live_event = None
        if live_row:
            live_event = {
                'id': live_row[0],
                'slug': live_row[1],
                'event_name': live_row[2],
                'event_date': str(live_row[3]),
                'start_time': str(live_row[4]) if live_row[4] else None,
                'timezone': live_row[5],
            }

        # Per-event stats
        cur.execute("""
            SELECT
                e.id, e.slug, e.event_name, e.event_date, e.is_public,
                COUNT(DISTINCT su.id) AS utterances,
                COUNT(DISTINCT c.id) AS claims_total,
                COUNT(DISTINCT c.id) FILTER (WHERE c.verdict IS NOT NULL) AS claims_verified,
                COUNT(DISTINCT c.id) FILTER (WHERE c.verdict IS NULL) AS claims_pending
            FROM events e
            LEFT JOIN speaker_utterances su ON su.event_id = e.id
            LEFT JOIN claims c ON c.event_id = e.id AND c.claim_origin = 'debate_claim'
            WHERE e.is_public = TRUE
            GROUP BY e.id, e.slug, e.event_name, e.event_date, e.is_public
            ORDER BY e.event_date DESC
        """)
        events = []
        for row in cur.fetchall():
            events.append({
                'id': row[0],
                'slug': row[1],
                'event_name': row[2],
                'event_date': str(row[3]),
                'is_public': row[4],
                'utterances': row[5] or 0,
                'claims_total': row[6] or 0,
                'claims_verified': row[7] or 0,
                'claims_pending': row[8] or 0,
            })

        # Surge mode pending claims
        pending_surge = 0
        if live_event:
            cur.execute("""
                SELECT COUNT(*) FROM claims
                WHERE event_id = %s
                  AND claim_origin = 'debate_claim'
                  AND verdict IS NULL
            """, (live_event['id'],))
            pending_surge = cur.fetchone()[0] or 0

        cur.close()
        conn.close()

        return jsonify({
            'live_event': live_event,
            'surge_active': live_event is not None,
            'pending_surge': pending_surge,
            'events': events,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/disputes', methods=['GET', 'POST'])
def public_disputes():
    from flask import request as _req
    submitted = False
    error = None
    if _req.method == 'POST':
        try:
            dtype = _req.form.get('dispute_type','').strip()
            article_url = _req.form.get('article_url','').strip()
            claim_text = _req.form.get('claim_text','').strip()
            dispute_text = _req.form.get('dispute_text','').strip()
            contact_email = _req.form.get('contact_email','').strip()
            if not article_url or not dispute_text or not contact_email:
                error = 'Please fill in all required fields.'
            else:
                from urllib.parse import urlparse as _up
                domain = (_up(article_url).hostname or '').replace('www.','')
                conn = get_db()
                cur = conn.cursor()
                full_text = f"[{dtype.upper()}] Article: {article_url}\n\nClaim: {claim_text}\n\nDispute: {dispute_text}"
                cur.execute(
                    "INSERT INTO outlet_disputes (domain, claim_id, contact_email, dispute_text, outlet_response, status, submitted_at) VALUES (%s, NULL, %s, %s, %s, 'pending', NOW())",
                    (domain, contact_email, full_text, dtype))
                conn.commit(); cur.close(); conn.close()
                submitted = True
        except Exception as e:
            error = 'Submission failed — please email disputes@verumsignal.com directly.'

    CSS = """:root{--bg:#0a0a0f;--fg:#e8e8f0;--dim:#888;--accent:#a855f7;--card:#111118;--border:#1e1e2e;--red:#f87171;--green:#4ade80}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;font-size:15px;line-height:1.6}
.wrap{max-width:680px;margin:0 auto;padding:48px 24px}
.logo{margin-bottom:40px}.logo a{color:var(--fg);text-decoration:none;font-weight:700;font-size:14px;letter-spacing:1.5px}
.logo em{color:var(--accent);font-style:italic}
h1{font-size:26px;font-weight:700;margin-bottom:8px}
.sub{color:var(--dim);font-size:14px;margin-bottom:32px;max-width:540px;line-height:1.6}
.tier{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin-bottom:12px;display:block;cursor:pointer;transition:border-color 0.2s}
.tier:hover{border-color:var(--accent)}
.tier h3{font-size:14px;font-weight:600;margin-bottom:2px}
.tier p{font-size:13px;color:var(--dim)}
.tier input[type=radio]{margin-right:10px;accent-color:var(--accent)}
.form-section{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:28px;margin-top:24px}
.field{margin-bottom:20px}
.field label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:var(--dim);margin-bottom:6px}
.field input,.field textarea{width:100%;background:#0a0a0f;border:1px solid var(--border);border-radius:6px;padding:10px 12px;color:var(--fg);font-size:14px;font-family:inherit;outline:none;transition:border-color 0.2s}
.field input:focus,.field textarea:focus{border-color:var(--accent)}
.field textarea{resize:vertical;min-height:120px}
.field .hint{font-size:12px;color:var(--dim);margin-top:4px}
.btn{display:inline-flex;align-items:center;padding:10px 24px;background:var(--accent);color:#fff;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer}
.btn:hover{opacity:0.85}
.success{background:rgba(74,222,128,0.08);border:1px solid rgba(74,222,128,0.3);border-radius:10px;padding:28px;text-align:center}
.success h2{color:var(--green);margin-bottom:8px}
.error-msg{background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.3);border-radius:6px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:var(--red)}
.policy{font-size:12px;color:var(--dim);margin-top:24px;line-height:1.6}
footer{margin-top:48px;font-size:12px;color:var(--dim);display:flex;gap:24px}
footer a{color:var(--dim);text-decoration:none}footer a:hover{color:var(--fg)}"""

    SUCCESS = """<div class="success">
      <h2>Dispute received</h2>
      <p style="color:#888;margin-top:8px">We will acknowledge within 48 hours and complete review within 14 days.<br>
      Questions? Email <a href="mailto:disputes@verumsignal.com" style="color:#a855f7">disputes@verumsignal.com</a></p>
    </div>"""

    ERR = f'<div class="error-msg">{error}</div>' if error else ''

    FORM = """
    <p style="font-size:13px;color:#888;margin-bottom:16px">Select dispute type:</p>
    <form method="POST">
      <label class="tier">
        <input type="radio" name="dispute_type" value="reader" required>
        <h3>Reader correction</h3>
        <p>I found a factual error. I am not affiliated with the outlet.</p>
      </label>
      <label class="tier">
        <input type="radio" name="dispute_type" value="outlet_reply">
        <h3>Outlet right-of-reply</h3>
        <p>I represent the outlet whose content was scored and wish to formally challenge a verdict.</p>
      </label>
      <div class="form-section">
        <div class="field">
          <label>Article URL *</label>
          <input type="url" name="article_url" placeholder="https://example.com/article" required>
        </div>
        <div class="field">
          <label>Claim being disputed (optional but helpful)</label>
          <textarea name="claim_text" placeholder="Paste the specific claim text you are disputing..." rows="3"></textarea>
        </div>
        <div class="field">
          <label>Your dispute *</label>
          <textarea name="dispute_text" placeholder="Explain what is incorrect and provide counter-evidence or sources..." required></textarea>
          <div class="hint">Be specific. Include links to authoritative sources where possible.</div>
        </div>
        <div class="field">
          <label>Your email address *</label>
          <input type="email" name="contact_email" placeholder="you@example.com" required>
          <div class="hint">We will respond to this address. Not published.</div>
        </div>
        <button type="submit" class="btn">Submit dispute</button>
      </div>
    </form>
    <p class="policy">
      <strong>Our process:</strong> All disputes are acknowledged within 48 hours and reviewed within 14 days.
      If a verdict is incorrect, it will be updated with a public audit trail entry.
      Outlet right-of-reply responses are published alongside the disputed verdict.
      Disputes are never silently dismissed.
      For urgent matters, email <a href="mailto:disputes@verumsignal.com" style="color:var(--accent)">disputes@verumsignal.com</a>.
    </p>"""

    body = SUCCESS if submitted else (ERR + FORM)

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dispute a Verdict — Verum Signal</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<div class="logo"><a href="/">VERUM <em>SIGNAL</em></a></div>
<h1>Dispute a verdict</h1>
<p class="sub">If you believe a verdict is factually incorrect, submit a dispute below. Reader corrections and outlet right-of-reply challenges are both accepted. Every dispute is reviewed — nothing is silently dismissed.</p>
{body}
<footer><a href="/">Home</a><a href="/methodology">Methodology</a><a href="/leaderboard">Leaderboard</a><a href="/status">Status</a></footer>
</div></body></html>"""
    return html, 200, {"Content-Type": "text/html"}

@app.route('/status')
def status_page():
    """Public status page — no auth required."""
    import datetime as _dt
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM articles")
        articles = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM claims WHERE verdict IS NOT NULL")
        claims = cur.fetchone()[0]
        cur.execute("""
            SELECT stage, MAX(finished_at) as last_ok, MAX(items_processed) as last_count
            FROM job_runs WHERE status = 'ok'
              AND started_at > NOW() - INTERVAL '48 hours'
            GROUP BY stage ORDER BY stage
        """)
        stages = {row[0]: {"last_ok": row[1], "last_count": row[2]} for row in cur.fetchall()}
        cur.execute("""
            SELECT COUNT(*) FROM job_runs
            WHERE status = 'failed' AND started_at > NOW() - INTERVAL '24 hours'
        """)
        error_count = cur.fetchone()[0]
        cur.close(); conn.close()

        now = _dt.datetime.now(_dt.timezone.utc)

        def age_str(dt):
            if not dt: return "never"
            if dt.tzinfo is None: dt = dt.replace(tzinfo=_dt.timezone.utc)
            mins = int((now - dt).total_seconds() / 60)
            if mins < 2: return "just now"
            if mins < 60: return f"{mins}m ago"
            hrs = mins // 60
            if hrs < 24: return f"{hrs}h ago"
            return f"{hrs//24}d ago"

        def stage_status(name, threshold_hours=4):
            s = stages.get(name)
            if not s or not s["last_ok"]: return "unknown", "#888"
            dt = s["last_ok"]
            if dt.tzinfo is None: dt = dt.replace(tzinfo=_dt.timezone.utc)
            hrs = (now - dt).total_seconds() / 3600
            if hrs < threshold_hours: return "operational", "#4ade80"
            if hrs < threshold_hours * 2: return "degraded", "#fbbf24"
            return "outage", "#f87171"

        fs, fc = stage_status("fetch", 4)
        es, ec = stage_status("extract", 2)
        vs, vc = stage_status("verdicts", 8)
        overall_ok = all(s == "operational" for s in [fs, es, vs])
        oc = "#4ade80" if overall_ok else "#fbbf24"
        ol = "All systems operational" if overall_ok else "Partial degradation"
        fi = stages.get("fetch", {})
        ei = stages.get("extract", {})
        vi = stages.get("verdicts", {})
        incident_msg = "No incidents in the last 24 hours." if not error_count else f"{error_count} pipeline errors in the last 24h."

        html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Verum Signal Status</title>
<style>
:root{{--bg:#0a0a0f;--fg:#e8e8f0;--dim:#888;--accent:#a855f7;--card:#111118;--border:#1e1e2e;--mono:ui-monospace,SF Mono,Menlo,monospace}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;font-size:14px;line-height:1.6}}
.wrap{{max-width:720px;margin:0 auto;padding:48px 24px}}
.logo{{margin-bottom:40px}}.logo a{{color:var(--fg);text-decoration:none;font-weight:700;font-size:14px;letter-spacing:1.5px}}
.logo em{{color:var(--accent);font-style:italic}}
.overall{{display:flex;align-items:center;gap:14px;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px 24px;margin-bottom:32px}}
.od{{width:14px;height:14px;border-radius:50%;background:{oc};flex-shrink:0}}
.ol{{font-size:18px;font-weight:600}}.ot{{margin-left:auto;font-size:12px;color:var(--dim)}}
h2{{font-size:11px;text-transform:uppercase;letter-spacing:0.1em;color:var(--dim);margin:28px 0 12px;font-weight:500}}
.comp{{display:flex;align-items:center;padding:14px 0;border-bottom:1px solid var(--border)}}.comp:last-child{{border-bottom:none}}
.cn{{font-weight:500}}.cd{{font-size:12px;color:var(--dim);margin-top:2px}}
.cs{{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:12px}}
.dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.stat-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:32px}}
.sc{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px}}
.sl{{font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:var(--dim);margin-bottom:4px}}
.sv{{font-family:var(--mono);font-size:22px;font-weight:600}}
.inc{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;font-size:13px;color:var(--dim)}}
footer{{margin-top:48px;font-size:12px;color:var(--dim);display:flex;gap:24px}}
footer a{{color:var(--dim);text-decoration:none}}footer a:hover{{color:var(--fg)}}
</style></head><body>
<div class="wrap">
<div class="logo"><a href="/">VERUM <em>SIGNAL</em></a></div>
<div class="overall"><div class="od"></div><div class="ol">{ol}</div><div class="ot">Updated {now.strftime("%H:%M UTC")}</div></div>
<h2>Corpus</h2>
<div class="stat-grid">
<div class="sc"><div class="sl">Articles</div><div class="sv">{articles:,}</div></div>
<div class="sc"><div class="sl">Verified claims</div><div class="sv">{claims:,}</div></div>
<div class="sc"><div class="sl">Last ingested</div><div class="sv" style="font-size:14px;padding-top:4px">{age_str(fi.get("last_ok"))}</div></div>
</div>
<h2>Pipeline</h2>
<div style="background:var(--card);border:1px solid var(--border);border-radius:8px;padding:0 16px">
<div class="comp"><div><div class="cn">Article ingestion</div><div class="cd">Last run: {age_str(fi.get("last_ok"))} &middot; {fi.get("last_count") or 0} articles</div></div><div class="cs"><div class="dot" style="background:{fc}"></div>{fs}</div></div>
<div class="comp"><div><div class="cn">Claim extraction</div><div class="cd">Last run: {age_str(ei.get("last_ok"))} &middot; {ei.get("last_count") or 0} claims</div></div><div class="cs"><div class="dot" style="background:{ec}"></div>{es}</div></div>
<div class="comp"><div><div class="cn">Claim verification</div><div class="cd">Last run: {age_str(vi.get("last_ok"))} &middot; {vi.get("last_count") or 0} verdicts</div></div><div class="cs"><div class="dot" style="background:{vc}"></div>{vs}</div></div>
<div class="comp"><div><div class="cn">Public API</div><div class="cd">api.verumsignal.com/v1/*</div></div><div class="cs"><div class="dot" style="background:#4ade80"></div>operational</div></div>
</div>
<h2>Incidents</h2>
<div class="inc">{incident_msg}</div>
<footer><a href="/">Home</a><a href="/developers">API</a><a href="/methodology">Methodology</a><span>Auto-refreshes every 60s</span></footer>
</div></body></html>"""
        return html, 200, {"Content-Type": "text/html"}
    except Exception as e:
        return f"<h1>Status unavailable</h1><p>{e}</p>", 500, {"Content-Type": "text/html"}

@app.route('/ops/test-report', methods=['GET'])
def ops_test_report():
    """Internal paid-report test page. OPS_PASSWORD protected."""
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err
    audit_key = os.environ.get('PAID_AUDIT_KEY', '')
    html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>Verum Signal — Test Report Generator</title>
<style>
body{background:#080810;color:#e8e8f0;font-family:'DM Sans',sans-serif;
     min-height:100vh;display:flex;align-items:center;justify-content:center;margin:0}
.w{width:100%;max-width:640px;padding:40px 24px}
.logo{color:#a855f7;font-size:13px;letter-spacing:.12em;text-transform:uppercase;
      font-family:Georgia,serif;font-style:italic;margin-bottom:32px}
h1{font-size:22px;font-weight:600;margin:0 0 8px}
p{color:rgba(232,232,240,.5);font-size:14px;margin:0 0 28px;line-height:1.6}
input{width:100%;background:#12121e;border:1px solid rgba(168,85,247,.25);
      border-radius:8px;padding:12px 16px;color:#f0f0f8;font-size:14px;
      box-sizing:border-box;margin-bottom:16px;outline:none}
input:focus{border-color:#a855f7}
button{background:#7c3aed;border:none;border-radius:8px;padding:12px 28px;
       color:#fff;font-size:14px;cursor:pointer;font-weight:500}
button:hover{background:#6d28d9}
.note{margin-top:20px;font-size:12px;color:rgba(232,232,240,.3);line-height:1.6}
.key-status{font-size:12px;margin-bottom:20px;padding:8px 12px;border-radius:6px;
            border:1px solid rgba(168,85,247,.2);background:rgba(168,85,247,.06)}
</style>
</head>
<body><div class="w">
<div class="logo">Verum Signal</div>
<h1>Test Report Generator</h1>
<p>Generates a full paid report via the audit override. Ops-protected — not publicly accessible.</p>
""" + (
    '<div class="key-status" style="color:#4ade80;">&#10003; PAID_AUDIT_KEY active — reports will resolve at paid tier</div>'
    if audit_key else
    '<div class="key-status" style="color:#f87171;">&#9888; PAID_AUDIT_KEY not set — reports will resolve at free tier</div>'
) + """
<input id="url-input" type="url" placeholder="https://..." autofocus>
<br>
<div style="display:flex;gap:10px;margin-top:4px;">
  <button onclick="goPaid()" style="background:#7c3aed;">Paid report &rarr;</button>
  <button onclick="goFree()" style="background:#374151;">Free report &rarr;</button>
</div>
<div class="note">
  Both open in a new tab. Paid uses the audit key; free uses no key (anon path).<br>
  Anon ceiling clear: <code>DELETE FROM anon_verify_counts WHERE day = CURRENT_DATE;</code>
</div>
</div>
<script>
const AUDIT_KEY = """ + repr(audit_key) + """;
function goPaid() {
  const v = document.getElementById('url-input').value.trim();
  if (!v.startsWith('http')) { alert('Paste a full article URL starting with https://'); return; }
  const keyParam = AUDIT_KEY ? '&audit_key=' + encodeURIComponent(AUDIT_KEY) : '';
  window.open('/report?url=' + encodeURIComponent(v) + keyParam, '_blank');
}
function goFree() {
  const v = document.getElementById('url-input').value.trim();
  if (!v.startsWith('http')) { alert('Paste a full article URL starting with https://'); return; }
  window.open('/report?url=' + encodeURIComponent(v), '_blank');
}
document.getElementById('url-input').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') goPaid();
});
</script>
</body></html>"""
    return html, 200, {'Content-Type': 'text/html'}


@app.route('/ops', methods=['GET'])
def ops_dashboard():
    """Render the ops dashboard HTML. Basic-auth protected."""
    auth_err = _ops_auth()
    if auth_err is not None:
        return auth_err

    from flask import Response
    import base64 as _b64
    ops_pw = os.environ.get('OPS_PASSWORD', '')
    ops_auth_b64 = _b64.b64encode(f'admin:{ops_pw}'.encode()).decode()
    ops_html = _OPS_HTML.replace('__OPS_AUTH_PLACEHOLDER__', ops_auth_b64)
    return Response(ops_html, mimetype='text/html')



# ──────────────────────────────────────────────────────────────────────────────
# /ops/mobile — Mobile app operational dashboard
# Paste this block into api.py after the last @app.route('/ops/...') block.
# Add 'Mobile' link to NAV_LINKS in all existing ops pages.
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/ops/mobile', methods=['GET'])
def ops_mobile():
    auth_err = _ops_auth()
    if auth_err:
        return auth_err
    import datetime as _dt
    generated_at = _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # Pull live data from DB
    db = get_db()
    cur = db.cursor()

    # Mobile API endpoint health checks
    endpoint_checks = []
    endpoints = [
        ('GET', '/mobile/v1/articles', 'Article list'),
        ('GET', '/mobile/v1/articles/<id>/report', 'Article report'),
        ('GET', '/mobile/v1/outlets/leaderboard', 'Leaderboard'),
        ('GET', '/mobile/v1/outlets/<domain>', 'Outlet detail'),
        ('GET', '/mobile/v1/debates', 'Debates list'),
        ('GET', '/mobile/v1/debates/<slug>', 'Debate detail'),
        ('GET', '/mobile/v1/debates/<slug>/stream', 'SSE stream'),
        ('GET', '/mobile/v1/methodology', 'Methodology'),
    ]

    # vs_summary stats
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE vs_summary IS NOT NULL) as with_summary,
            COUNT(*) FILTER (WHERE vs_summary IS NULL) as without_summary,
            MAX(vs_summary_generated_at) as last_generated
        FROM articles
        WHERE claims_verified = true OR id IN (SELECT DISTINCT article_id FROM claims)
    """)
    vs_row = cur.fetchone()
    vs_with = vs_row[0] if vs_row else 0
    vs_without = vs_row[1] if vs_row else 0
    vs_last = vs_row[2].strftime('%Y-%m-%d %H:%M UTC') if vs_row and vs_row[2] else 'Never'

    # Mobile tables row counts
    mobile_tables = ['users', 'user_devices', 'user_follows_outlet', 'user_follows_event',
                     'user_follows_speaker', 'user_saved_reports', 'user_notification_prefs', 'notification_log']
    table_counts = {}
    for t in mobile_tables:
        try:
            cur.execute(f'SELECT COUNT(*) FROM {t}')
            table_counts[t] = cur.fetchone()[0]
        except Exception:
            table_counts[t] = 'N/A'

    # Outlet tier distribution
    cur.execute("SELECT tier, COUNT(*) FROM api_outlets GROUP BY tier ORDER BY count DESC")
    tier_rows = cur.fetchall()
    scored_count = sum(c for t, c in tier_rows if t != 'tracked')
    tracked_count = sum(c for t, c in tier_rows if t == 'tracked')

    # May 26 debate status
    cur.execute("""
        SELECT e.id, e.event_name, e.event_date, e.start_time, e.stream_url,
               COUNT(c.id) as claim_count,
               COUNT(c.id) FILTER (WHERE c.verdict_status = 'provisional') as provisional_count,
               COUNT(c.id) FILTER (WHERE c.verdict_status = 'final') as final_count
        FROM events e
        LEFT JOIN claims c ON c.event_id = e.id
        WHERE e.id = 9
        GROUP BY e.id, e.event_name, e.event_date, e.start_time, e.stream_url
    """)
    may26 = cur.fetchone()

    # Speaker config for May 26
    cur.execute("""
        SELECT s.id, s.name, es.speaker_order, s.role
        FROM speakers s
        JOIN event_speakers es ON es.speaker_id = s.id AND es.event_id = 9
        WHERE s.id IN (187, 188, 189, 3)
        ORDER BY es.speaker_order
    """)
    speakers = cur.fetchall()

    cur.close()
    db.close()

    NAV = '<a href="/ops">Pipeline</a><a href="/ops/history">History</a><a href="/ops/insights">Insights</a><a href="/ops/changelog">Changelog</a><a href="/ops/outlets">Outlets</a><a href="/ops/queue">Queue</a><a href="/ops/disputes">Disputes</a><a href="/ops/api-usage">API</a><a href="/ops/test-report">Test Reports</a><a href="/ops/mobile" style="color:#a855f7;font-weight:600">Mobile</a>'

    tier_html = ''.join(f'<tr><td>{t}</td><td>{c}</td><td>{"Scored" if t != "tracked" else "Ingesting only"}</td></tr>' for t, c in tier_rows)
    table_counts_html = ''.join(f'<tr><td>{t}</td><td>{table_counts.get(t, "N/A")}</td></tr>' for t in mobile_tables)
    speakers_html = ''.join(f'<tr><td>{s[0]}</td><td>{s[1]}</td><td>{s[3] or "—"}</td><td>{s[2]}</td></tr>' for s in speakers) if speakers else '<tr><td colspan="4">No speakers found</td></tr>'

    may26_html = ''
    if may26:
        may26_html = f'''
        <div class="stat-row"><span class="stat-label">Event ID</span><span class="stat-val">{may26[0]}</span></div>
        <div class="stat-row"><span class="stat-label">Title</span><span class="stat-val">{may26[1]}</span></div>
        <div class="stat-row"><span class="stat-label">Date</span><span class="stat-val">{may26[2]}</span></div>
        <div class="stat-row"><span class="stat-label">Start time</span><span class="stat-val">{may26[3]} MT</span></div>
        <div class="stat-row"><span class="stat-label">Stream URL</span><span class="stat-val"><a href="{may26[4]}" target="_blank">{may26[4]}</a></span></div>
        <div class="stat-row"><span class="stat-label">Claims so far</span><span class="stat-val">{may26[5]} total · {may26[6]} provisional · {may26[7]} final</span></div>
        '''

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Mobile Ops — Verum Signal</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0a0a0a; color: #e5e5e5; font-size: 14px; line-height: 1.5; }}
  .nav {{ padding: 14px 24px; border-bottom: 1px solid #1f1f1f; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }}
  .nav a {{ color: #6b7280; text-decoration: none; font-size: 13px; }}
  .nav a:hover {{ color: #e5e5e5; }}
  .wordmark {{ font-size: 13px; font-weight: 700; color: #fff; letter-spacing: 1px; margin-right: 8px; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 28px 24px 60px; }}
  h1 {{ font-size: 22px; font-weight: 600; color: #fff; margin-bottom: 4px; }}
  .subtitle {{ font-size: 12px; color: #6b7280; margin-bottom: 28px; }}
  h2 {{ font-size: 13px; font-weight: 600; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.1em; margin: 28px 0 12px; padding-bottom: 8px; border-bottom: 1px solid #1f1f1f; }}
  .tabs {{ display: flex; gap: 0; border-bottom: 1px solid #1f1f1f; margin-bottom: 24px; }}
  .tab {{ padding: 10px 18px; font-size: 13px; color: #6b7280; cursor: pointer; border-bottom: 2px solid transparent; transition: color .15s; }}
  .tab:hover {{ color: #e5e5e5; }}
  .tab.active {{ color: #a855f7; border-bottom-color: #a855f7; font-weight: 500; }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 20px; }}
  .metric {{ background: #111; border: 1px solid #1f1f1f; border-radius: 8px; padding: 14px 16px; }}
  .metric-label {{ font-size: 11px; color: #6b7280; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.06em; }}
  .metric-value {{ font-size: 24px; font-weight: 600; color: #fff; line-height: 1.1; }}
  .metric-sub {{ font-size: 11px; color: #4b5563; margin-top: 4px; }}
  .card {{ background: #111; border: 1px solid #1f1f1f; border-radius: 10px; padding: 16px 20px; margin-bottom: 14px; }}
  .card h3 {{ font-size: 13px; font-weight: 600; color: #e5e5e5; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ text-align: left; color: #6b7280; font-weight: 500; padding: 6px 10px; border-bottom: 1px solid #1f1f1f; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #1a1a1a; color: #d1d5db; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  td:first-child {{ font-family: monospace; font-size: 11px; color: #9ca3af; }}
  .badge {{ font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px; white-space: nowrap; }}
  .badge-green {{ background: rgba(34,197,94,.15); color: #22c55e; }}
  .badge-amber {{ background: rgba(245,158,11,.15); color: #f59e0b; }}
  .badge-red {{ background: rgba(239,68,68,.15); color: #ef4444; }}
  .badge-gray {{ background: rgba(107,114,128,.15); color: #9ca3af; }}
  .badge-violet {{ background: rgba(168,85,247,.15); color: #a855f7; }}
  .stat-row {{ display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px solid #1a1a1a; font-size: 12px; }}
  .stat-row:last-child {{ border-bottom: none; }}
  .stat-label {{ color: #6b7280; }}
  .stat-val {{ color: #d1d5db; font-family: monospace; font-size: 11px; text-align: right; }}
  .method {{ font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 3px; margin-right: 6px; }}
  .get {{ background: rgba(34,197,94,.15); color: #22c55e; }}
  .post {{ background: rgba(59,130,246,.15); color: #60a5fa; }}
  code {{ font-family: monospace; font-size: 11px; background: #1a1a1a; padding: 1px 5px; border-radius: 3px; color: #d1d5db; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  .endpoint-row {{ padding: 10px 0; border-bottom: 1px solid #1a1a1a; display: flex; align-items: center; gap: 10px; font-size: 12px; }}
  .endpoint-row:last-child {{ border-bottom: none; }}
  .endpoint-path {{ font-family: monospace; color: #d1d5db; flex: 1; }}
  .refresh-note {{ font-size: 11px; color: #4b5563; margin-top: 32px; text-align: center; }}
  .schema-note {{ font-size: 11px; color: #f59e0b; background: rgba(245,158,11,.08); border: 1px solid rgba(245,158,11,.2); border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; }}
  @media (max-width: 640px) {{ .two-col {{ grid-template-columns: 1fr; }} .grid {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>
<div class="nav">
  <a href="/ops" style="text-decoration:none;display:inline-block"><svg width="160" height="22" viewBox="0 0 185 28" xmlns="http://www.w3.org/2000/svg"><path d="M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14" fill="none" stroke="#a855f7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="25" cy="14" r="2.5" fill="#ec4899"/><text x="32" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="700" fill="#ffffff" letter-spacing="1.5">VERUM</text><text x="88" y="19" font-family="Trebuchet MS,sans-serif" font-size="13" font-weight="400" font-style="italic" fill="#c084fc" letter-spacing="1.5" transform="skewX(-6)">SIGNAL</text></svg></a>
  {NAV}
</div>
<div class="container">
  <h1>Mobile Ops</h1>
  <div class="subtitle">verumsignal.com/ops/mobile &nbsp;·&nbsp; Verum Signal iOS + Android app &nbsp;·&nbsp; {generated_at}</div>

  <div class="tabs">
    <div class="tab active" onclick="showTab(this,'overview')">Overview</div>
    <div class="tab" onclick="showTab(this,'api')">API Reference</div>
    <div class="tab" onclick="showTab(this,'schema')">Schema</div>
    <div class="tab" onclick="showTab(this,'sse')">SSE Protocol</div>
    <div class="tab" onclick="showTab(this,'builds')">Build History</div>
  </div>

  <!-- OVERVIEW TAB -->
  <div id="tab-overview" class="tab-content active">
    <div class="grid">
      <div class="metric">
        <div class="metric-label">App version</div>
        <div class="metric-value">1.0.3</div>
        <div class="metric-sub">versionCode 4 · preview APK</div>
      </div>
      <div class="metric">
        <div class="metric-label">expo-updates</div>
        <div class="metric-value" style="font-size:16px;padding-top:6px;color:#22c55e">Removed</div>
        <div class="metric-sub">Re-add in v1.1</div>
      </div>
      <div class="metric">
        <div class="metric-label">vs_summary cached</div>
        <div class="metric-value">{vs_with}</div>
        <div class="metric-sub">{vs_without} articles pending · last {vs_last}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Scored outlets</div>
        <div class="metric-value">{scored_count}</div>
        <div class="metric-sub">{tracked_count} tracked · not yet scored</div>
      </div>
      <div class="metric">
        <div class="metric-label">Auth</div>
        <div class="metric-value" style="font-size:16px;padding-top:6px;color:#f59e0b">Stubbed</div>
        <div class="metric-sub">Clerk signup pending</div>
      </div>
      <div class="metric">
        <div class="metric-label">Push notifications</div>
        <div class="metric-value" style="font-size:16px;padding-top:6px;color:#f59e0b">Stubbed</div>
        <div class="metric-sub">Expo Push API pending</div>
      </div>
    </div>

    <h2>Mobile table row counts</h2>
    <div class="card">
      <table>
        <tr><th>Table</th><th>Rows</th></tr>
        {table_counts_html}
      </table>
    </div>

    <h2>Outlet tier distribution</h2>
    <div class="card">
      <table>
        <tr><th>Tier</th><th>Count</th><th>Status</th></tr>
        {tier_html}
      </table>
    </div>

    <h2>Screen inventory</h2>
    <div class="two-col">
      <div class="card">
        <h3>Built · new design system</h3>
        <table>
          <tr><th>Screen</th><th>Status</th></tr>
          <tr><td>Articles tab</td><td><span class="badge badge-green">Ready</span></td></tr>
          <tr><td>Article report</td><td><span class="badge badge-green">Ready</span></td></tr>
          <tr><td>Leaderboard tab</td><td><span class="badge badge-green">Ready</span></td></tr>
          <tr><td>Debates tab</td><td><span class="badge badge-green">Ready</span></td></tr>
          <tr><td>Profile tab</td><td><span class="badge badge-green">Ready</span></td></tr>
        </table>
      </div>
      <div class="card">
        <h3>Built · needs design update</h3>
        <table>
          <tr><th>Screen</th><th>Status</th></tr>
          <tr><td>Outlet detail</td><td><span class="badge badge-amber">Old design</span></td></tr>
          <tr><td>Debate detail</td><td><span class="badge badge-amber">Old design</span></td></tr>
          <tr><td>Onboarding (3)</td><td><span class="badge badge-gray">Not built</span></td></tr>
        </table>
      </div>
    </div>

    <h2>Signup blockers</h2>
    <div class="card">
      <div class="endpoint-row"><span class="endpoint-path">Clerk (auth)</span><span class="badge badge-red">Blocked</span><span style="color:#6b7280;font-size:11px">Unblocks all 401 auth endpoints</span></div>
      <div class="endpoint-row"><span class="endpoint-path">Expo account</span><span class="badge badge-green">Done · brittbarton</span><span style="color:#6b7280;font-size:11px">EAS builds working</span></div>
      <div class="endpoint-row"><span class="endpoint-path">Apple Developer</span><span class="badge badge-red">Not started</span><span style="color:#6b7280;font-size:11px">Verum Signal LLC · DUNS required · 2–8 weeks</span></div>
      <div class="endpoint-row"><span class="endpoint-path">Google Play</span><span class="badge badge-red">Not started</span><span style="color:#6b7280;font-size:11px">$25 one-time · 1–3 days</span></div>
    </div>
  </div>

  <!-- API TAB -->
  <div id="tab-api" class="tab-content">
    <h2>Public endpoints</h2>
    <div class="card">
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/articles</span><span class="badge badge-green">Working</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/articles/&lt;id&gt;/report</span><span class="badge badge-green">Fixed</span><span style="color:#22c55e;font-size:11px;margin-left:8px">circular import resolved</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/outlets/leaderboard</span><span class="badge badge-green">Working</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/outlets/&lt;domain&gt;</span><span class="badge badge-green">Working</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/debates</span><span class="badge badge-green">Working</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/debates/&lt;slug&gt;</span><span class="badge badge-amber">Not tested in app</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/debates/&lt;slug&gt;/stream</span><span class="badge badge-amber">SSE · load tested</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/methodology</span><span class="badge badge-green">Working</span><span style="color:#6b7280;font-size:11px;margin-left:8px">reads PUBLIC_METHODOLOGY_VERSIONS</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/health</span><span class="badge badge-green">Working</span></div>
    </div>

    <h2>Auth-required endpoints (stubbed)</h2>
    <div class="card">
      <div class="endpoint-row"><span class="method post">POST</span><span class="endpoint-path">/mobile/v1/auth/sync</span><span class="badge badge-gray">501</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/me</span><span class="badge badge-gray">501</span></div>
      <div class="endpoint-row"><span class="method post">POST</span><span class="endpoint-path">/mobile/v1/devices/register</span><span class="badge badge-gray">501</span></div>
      <div class="endpoint-row"><span class="method post">POST</span><span class="endpoint-path">/mobile/v1/articles/&lt;id&gt;/save</span><span class="badge badge-gray">401</span></div>
      <div class="endpoint-row"><span class="method post">POST</span><span class="endpoint-path">/mobile/v1/outlets/&lt;domain&gt;/follow</span><span class="badge badge-gray">401</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/me/saved</span><span class="badge badge-gray">401</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/me/follows</span><span class="badge badge-gray">401</span></div>
      <div class="endpoint-row"><span class="method get">GET</span><span class="endpoint-path">/mobile/v1/me/notifications</span><span class="badge badge-gray">401</span></div>
    </div>

    <h2>Known data gaps</h2>
    <div class="card">
      <div class="schema-note">These are production data issues, not API bugs. Logged for v1.1.</div>
      <table>
        <tr><th>#</th><th>Field</th><th>Issue</th><th>Priority</th></tr>
        <tr><td>1</td><td><code>outlet.name</code></td><td>Stores domain string ("cbc.ca") not display name ("CBC News"). No display names in DB.</td><td><span class="badge badge-amber">v1.1</span></td></tr>
        <tr><td>2</td><td><code>article.published_at</code></td><td>Null for many articles. time_ago falls back to ingestion time.</td><td><span class="badge badge-gray">Known</span></td></tr>
        <tr><td>3</td><td><code>outlet.tier</code> "tracked"</td><td>149 outlets with no verdicts show tier="tracked" in article cards. Excluded from leaderboard.</td><td><span class="badge badge-green">Handled</span></td></tr>
        <tr><td>4</td><td><code>speaker.sub</code></td><td>Design shows "D · Iowa" under speaker names. party/title not returned in mobile API.</td><td><span class="badge badge-amber">v1.1</span></td></tr>
        <tr><td>5</td><td><code>report.article_score</code></td><td>Returns outlet score as proxy. Per-article score requires compute_article_score().</td><td><span class="badge badge-amber">v1.1</span></td></tr>
      </table>
    </div>
  </div>

  <!-- SCHEMA TAB -->
  <div id="tab-schema" class="tab-content">
    <div class="schema-note">These are production schema corrections. MOBILE_BACKEND_GAPS.md originally had wrong column names — these are the actual values confirmed during build.</div>

    <h2>Production schema corrections</h2>
    <div class="card">
      <table>
        <tr><th>Table</th><th>Doc assumed</th><th>Actual</th></tr>
        <tr><td>api_outlets</td><td><code>domain, name, claim_count</code></td><td><code>outlet_id, outlet_name, total_scoreable_claims</code></td></tr>
        <tr><td>api_outlets</td><td><code>category column</code></td><td>Does not exist</td></tr>
        <tr><td>articles</td><td><code>source_domain</code></td><td><code>source_name</code></td></tr>
        <tr><td>articles</td><td><code>byline, lead_image_url</code></td><td>Do not exist</td></tr>
        <tr><td>events</td><td><code>title, description, start_time (datetime), location, end_time, status</code></td><td><code>event_name, event_subtitle, event_date (DATE), venue</code> — no end_time or status</td></tr>
        <tr><td>claims → debates</td><td><code>debate_id</code></td><td><code>event_id</code></td></tr>
        <tr><td>articles.id, events.id, speakers.id</td><td>BIGINT</td><td>INTEGER</td></tr>
        <tr><td>api_outlets.tier</td><td>3 values</td><td>4 values: published(3) · stabilizing(7) · limited_data(8) · tracked(149)</td></tr>
      </table>
    </div>

    <h2>New mobile tables (8 added May 23, 2026)</h2>
    <div class="two-col">
      <div>
        <div class="card">
          <h3>users</h3>
          <table>
            <tr><th>Column</th><th>Type</th><th>Notes</th></tr>
            <tr><td>id</td><td>BIGSERIAL PK</td><td>—</td></tr>
            <tr><td>external_id</td><td>TEXT UNIQUE</td><td>Clerk user ID</td></tr>
            <tr><td>email</td><td>TEXT</td><td>—</td></tr>
            <tr><td>tier</td><td>TEXT</td><td>free|pro_monthly|pro_annual|complimentary</td></tr>
            <tr><td>created_at</td><td>TIMESTAMPTZ</td><td>—</td></tr>
            <tr><td>deleted_at</td><td>TIMESTAMPTZ</td><td>Soft delete</td></tr>
          </table>
        </div>
        <div class="card">
          <h3>user_devices</h3>
          <table>
            <tr><th>Column</th><th>Type</th><th>Notes</th></tr>
            <tr><td>id</td><td>BIGSERIAL PK</td><td>—</td></tr>
            <tr><td>user_id</td><td>BIGINT FK</td><td>→ users.id</td></tr>
            <tr><td>push_token</td><td>TEXT</td><td>Expo push token</td></tr>
            <tr><td>platform</td><td>TEXT</td><td>ios|android</td></tr>
            <tr><td>push_enabled</td><td>BOOL</td><td>—</td></tr>
            <tr><td>last_seen_at</td><td>TIMESTAMPTZ</td><td>—</td></tr>
          </table>
        </div>
        <div class="card">
          <h3>user_notification_prefs</h3>
          <table>
            <tr><th>Column</th><th>Type</th><th>Notes</th></tr>
            <tr><td>id</td><td>BIGSERIAL PK</td><td>—</td></tr>
            <tr><td>user_id</td><td>BIGINT FK UNIQUE</td><td>—</td></tr>
            <tr><td>debate_alerts</td><td>BOOL</td><td>Default true</td></tr>
            <tr><td>verdict_alerts</td><td>BOOL</td><td>Default true</td></tr>
            <tr><td>daily_digest</td><td>BOOL</td><td>Default false</td></tr>
            <tr><td>quiet_hours_start</td><td>TIME</td><td>—</td></tr>
            <tr><td>quiet_hours_end</td><td>TIME</td><td>—</td></tr>
            <tr><td>timezone</td><td>TEXT</td><td>IANA tz string</td></tr>
          </table>
        </div>
        <div class="card">
          <h3>articles (new columns)</h3>
          <table>
            <tr><th>Column</th><th>Type</th><th>Notes</th></tr>
            <tr><td>vs_summary</td><td>TEXT</td><td>Lazy generated by claude-sonnet-4-6</td></tr>
            <tr><td>vs_summary_generated_at</td><td>TIMESTAMPTZ</td><td>—</td></tr>
          </table>
        </div>
      </div>
      <div>
        <div class="card">
          <h3>user_follows_outlet</h3>
          <table>
            <tr><th>Column</th><th>Type</th></tr>
            <tr><td>id</td><td>BIGSERIAL PK</td></tr>
            <tr><td>user_id</td><td>BIGINT FK</td></tr>
            <tr><td>outlet_domain</td><td>TEXT</td></tr>
            <tr><td>created_at</td><td>TIMESTAMPTZ</td></tr>
          </table>
          <div style="font-size:10px;color:#4b5563;margin-top:8px">UNIQUE(user_id, outlet_domain)</div>
        </div>
        <div class="card">
          <h3>user_follows_event</h3>
          <table>
            <tr><th>Column</th><th>Type</th></tr>
            <tr><td>id</td><td>BIGSERIAL PK</td></tr>
            <tr><td>user_id</td><td>BIGINT FK</td></tr>
            <tr><td>event_id</td><td>INT FK → events.id</td></tr>
            <tr><td>created_at</td><td>TIMESTAMPTZ</td></tr>
          </table>
          <div style="font-size:10px;color:#4b5563;margin-top:8px">UNIQUE(user_id, event_id)</div>
        </div>
        <div class="card">
          <h3>user_saved_reports</h3>
          <table>
            <tr><th>Column</th><th>Type</th></tr>
            <tr><td>id</td><td>BIGSERIAL PK</td></tr>
            <tr><td>user_id</td><td>BIGINT FK</td></tr>
            <tr><td>article_id</td><td>INT FK → articles.id</td></tr>
            <tr><td>created_at</td><td>TIMESTAMPTZ</td></tr>
          </table>
          <div style="font-size:10px;color:#4b5563;margin-top:8px">UNIQUE(user_id, article_id)</div>
        </div>
        <div class="card">
          <h3>notification_log</h3>
          <table>
            <tr><th>Column</th><th>Type</th></tr>
            <tr><td>id</td><td>BIGSERIAL PK</td></tr>
            <tr><td>user_id</td><td>BIGINT FK</td></tr>
            <tr><td>notification_type</td><td>TEXT</td></tr>
            <tr><td>related_event_id</td><td>INT</td></tr>
            <tr><td>related_article_id</td><td>INT</td></tr>
            <tr><td>sent_at</td><td>TIMESTAMPTZ</td></tr>
            <tr><td>push_token</td><td>TEXT</td></tr>
          </table>
          <div style="font-size:10px;color:#4b5563;margin-top:8px">Dedup cap: 5 verdict alerts per user per debate</div>
        </div>
      </div>
    </div>
  </div>

  <!-- SSE TAB -->
  <div id="tab-sse" class="tab-content">
    <h2>Configuration</h2>
    <div class="card">
      <div class="stat-row"><span class="stat-label">Endpoint</span><span class="stat-val">GET /mobile/v1/debates/&lt;slug&gt;/stream</span></div>
      <div class="stat-row"><span class="stat-label">Poll interval</span><span class="stat-val">5 seconds</span></div>
      <div class="stat-row"><span class="stat-label">Heartbeat interval</span><span class="stat-val">15 seconds</span></div>
      <div class="stat-row"><span class="stat-label">Client heartbeat timeout</span><span class="stat-val">30 seconds → reconnect</span></div>
      <div class="stat-row"><span class="stat-label">Max reconnect attempts</span><span class="stat-val">10 (exponential backoff, 5s base)</span></div>
      <div class="stat-row"><span class="stat-label">Reconnection param</span><span class="stat-val">?since_id=N (avoids re-sending existing claims)</span></div>
      <div class="stat-row"><span class="stat-label">Infrastructure</span><span class="stat-val">Sync Gunicorn — 1 worker per SSE connection</span></div>
      <div class="stat-row"><span class="stat-label">Nginx buffering</span><span class="stat-val">X-Accel-Buffering: no header set</span></div>
    </div>

    <h2>Load test results (k6 v2.0.0 · May 23, 2026)</h2>
    <div class="card">
      <div class="stat-row"><span class="stat-label">Concurrent SSE connections</span><span class="stat-val">100 (ramped 0→10→50→100→50→0)</span></div>
      <div class="stat-row"><span class="stat-label">Concurrent REST users</span><span class="stat-val">30 (20 articles + 10 leaderboard)</span></div>
      <div class="stat-row"><span class="stat-label">Articles p95 latency</span><span class="stat-val" style="color:#22c55e">188ms (target: &lt;500ms)</span></div>
      <div class="stat-row"><span class="stat-label">Leaderboard p95 latency</span><span class="stat-val" style="color:#22c55e">101ms (target: &lt;500ms)</span></div>
      <div class="stat-row"><span class="stat-label">SSE first event p95</span><span class="stat-val" style="color:#22c55e">140ms (target: &lt;3000ms)</span></div>
      <div class="stat-row"><span class="stat-label">API error rate</span><span class="stat-val" style="color:#22c55e">0.00% (target: &lt;0.1%)</span></div>
      <div class="stat-row"><span class="stat-label">Total checks passed</span><span class="stat-val" style="color:#22c55e">33,661 / 33,661 (100%)</span></div>
      <div class="stat-row"><span class="stat-label">SSE events delivered</span><span class="stat-val">11,126</span></div>
      <div class="stat-row"><span class="stat-label">Decision</span><span class="stat-val" style="color:#22c55e">Ship as-is. No gevent or dedicated service needed for May 26.</span></div>
    </div>

    <h2>SSE event types</h2>
    <div class="card">
      <table>
        <tr><th>Event</th><th>When</th><th>Key fields</th></tr>
        <tr><td>connected</td><td>Immediately on connection</td><td>existing_claims[], is_ended, claim_count</td></tr>
        <tr><td>claim</td><td>New verified claim</td><td>id, claim_text, verdict, confidence_score, verdict_summary, speaker_id, speaker_name, is_provisional, first_seen, methodology_version</td></tr>
        <tr><td>heartbeat</td><td>Every 15s</td><td>ts (unix timestamp)</td></tr>
        <tr><td>debate_ended</td><td>event_date passes</td><td>slug, ended_at</td></tr>
        <tr><td>error</td><td>Server error</td><td>message, code (NOT_FOUND stops reconnect)</td></tr>
      </table>
    </div>

    <h2>Client hook: useDebateStream</h2>
    <div class="card">
      <div class="stat-row"><span class="stat-label">Implementation</span><span class="stat-val">fetch() + ReadableStream (no native EventSource in RN)</span></div>
      <div class="stat-row"><span class="stat-label">Deduplication</span><span class="stat-val">By claim ID — Set of existing IDs</span></div>
      <div class="stat-row"><span class="stat-label">Status values</span><span class="stat-val">connecting | connected | reconnecting | ended | error | closed</span></div>
      <div class="stat-row"><span class="stat-label">Only enabled when</span><span class="stat-val">debate.is_live === true</span></div>
      <div class="stat-row"><span class="stat-label">Past debates</span><span class="stat-val">Falls back to REST API claim list</span></div>
    </div>
  </div>

  <!-- BUILD HISTORY TAB -->
  <div id="tab-builds" class="tab-content">
    <h2>All EAS builds</h2>
    <div class="card">
      <table>
        <tr><th>Build ID</th><th>Profile</th><th>Version</th><th>Runtime</th><th>Fingerprint</th><th>Status</th><th>Failure reason</th></tr>
        <tr><td>b08f7319</td><td>preview</td><td>1.0.0</td><td>1.0.0</td><td style="font-size:9px">57f9e84b</td><td><span class="badge badge-red">Failed</span></td><td>Pre-design code snapshotted</td></tr>
        <tr><td>1109e9b5</td><td>preview</td><td>1.0.0</td><td>1.0.0</td><td style="font-size:9px">57f9e84b ⚠</td><td><span class="badge badge-red">Failed</span></td><td>EAS cached native bundle — same fingerprint as build 1</td></tr>
        <tr><td>59b217d9</td><td>preview</td><td>1.0.0</td><td>1.0.0</td><td style="font-size:9px">57f9e84b ⚠</td><td><span class="badge badge-red">Failed</span></td><td>EAS cached native bundle — same fingerprint again</td></tr>
        <tr><td>82485029</td><td>preview</td><td>1.0.0</td><td>1.0.0</td><td style="font-size:9px">0bcfc025</td><td><span class="badge badge-red">Failed</span></td><td>OTA runtime version mismatch (1.0.0 vs exposdk:56.0.0)</td></tr>
        <tr><td>dfed8b16</td><td>development</td><td>1.0.0</td><td>1.0.0</td><td style="font-size:9px">ff6310bc</td><td><span class="badge badge-red">Failed</span></td><td>Font loading black screen + WSL2 tunnel unreachable</td></tr>
        <tr><td>00e658e0</td><td>development</td><td>1.0.2</td><td>exposdk:56.0.0</td><td style="font-size:9px">b33e88d1</td><td><span class="badge badge-red">Failed</span></td><td>expo-updates checked development OTA channel — download failed</td></tr>
        <tr><td>d325ddcd</td><td>preview</td><td>1.0.3</td><td>exposdk:56.0.0</td><td style="font-size:9px">88133b16</td><td><span class="badge badge-red">Failed</span></td><td>Crashed on launch — useCallback in JSX (hooks violation)</td></tr>
        <tr><td>7e903cdf</td><td>preview</td><td>1.0.3</td><td>exposdk:56.0.0</td><td style="font-size:9px">88133b16</td><td><span class="badge badge-red">Failed</span></td><td>Error boundary build — confirmed hooks violation in leaderboard + debates</td></tr>
        <tr><td>current</td><td>preview</td><td>1.0.3</td><td>—</td><td>—</td><td><span class="badge badge-violet">Building</span></td><td>useCallback extracted from JSX in leaderboard.tsx + debates.tsx</td></tr>
      </table>
    </div>

    <h2>Root cause log</h2>
    <div class="card">
      <table>
        <tr><th>#</th><th>Root cause</th><th>Resolution</th></tr>
        <tr><td>1</td><td>EAS native bundle caching — pure JS changes don't change fingerprint</td><td>Adding expo-dev-client later changed fingerprint</td></tr>
        <tr><td>2</td><td>OTA runtime version mismatch — appVersion vs sdkVersion policy</td><td>Set runtimeVersion: sdkVersion in app.json</td></tr>
        <tr><td>3</td><td>expo-updates active in dev build — conflicts with Metro tunnel</td><td>Removed expo-updates entirely for v1</td></tr>
        <tr><td>4</td><td>WSL2 Metro unreachable — 172.21.176.1:8081 blocked from phone</td><td>Unresolved — use preview APK instead of dev tunnel</td></tr>
        <tr><td>5</td><td>Font loading black screen — return null/View when fonts pending</td><td>3-second timeout fallback via expo-splash-screen</td></tr>
        <tr><td>6</td><td>useCallback in JSX — hooks rules violation in index.tsx</td><td>Extracted to const onRefresh before return</td></tr>
        <tr><td>7</td><td>Circular import api.py ↔ mobile_routes.py + missing ast/os imports</td><td>Inlined get_or_create_short_hash + global imports</td></tr>
        <tr><td>8</td><td>useCallback called inline in JSX props (leaderboard.tsx, debates.tsx) — hooks rules violation</td><td>Extracted to const onRefresh before return statement</td></tr>
        <tr><td>9</td><td>ops_mobile route defined after app.run() — never registered under python api.py</td><td>Moved route before if __name__ == '__main__' block</td></tr>
      </table>
    </div>
  </div>

  <!-- MAY 26 TAB -->
  <div id="tab-may26" class="tab-content">
    <h2>Event configuration (event_id=9)</h2>
    <div class="card">
      {may26_html if may26_html else '<div style="color:#6b7280;font-size:12px">Event not found in DB</div>'}
    </div>

    <h2>Speaker configuration</h2>
    <div class="card">
      <table>
        <tr><th>ID</th><th>Name</th><th>Role</th><th>Speaker order</th></tr>
        {speakers_html}
      </table>
    </div>

    <h2>Readiness checklist</h2>
    <div class="card">
      <div class="endpoint-row"><span class="endpoint-path">Event in DB · is_public=True · correct timezone (MT)</span><span class="badge badge-green">✓ Done</span></div>
      <div class="endpoint-row"><span class="endpoint-path">Speakers configured with speaker_order</span><span class="badge badge-green">✓ Done</span></div>
      <div class="endpoint-row"><span class="endpoint-path">verdict_status='provisional' stamping in extract_debate_claims.py</span><span class="badge badge-green">✓ Patched</span></div>
      <div class="endpoint-row"><span class="endpoint-path">promote_provisional_verdicts() running every 5 minutes</span><span class="badge badge-green">✓ Active</span></div>
      <div class="endpoint-row"><span class="endpoint-path">SSE load test passed (100 concurrent · 0% errors)</span><span class="badge badge-green">✓ Passed</span></div>
      <div class="endpoint-row"><span class="endpoint-path">Mobile SSE stream endpoint live</span><span class="badge badge-green">✓ Live</span></div>
      <div class="endpoint-row"><span class="endpoint-path">Rev AI connection dry run</span><span class="badge badge-green">✓ Passed</span></div>
      <div class="endpoint-row"><span class="endpoint-path">YouTube live VIDEO_ID for CBS Colorado</span><span class="badge badge-amber">⚠ Day-of action (30 min before 7 PM MT)</span></div>
      <div class="endpoint-row"><span class="endpoint-path">DB password rotation</span><span class="badge badge-amber">⚠ Deferred (pre-launch · no real users)</span></div>
    </div>

    <h2>Live command (run day-of — replace VIDEO_ID)</h2>
    <div class="card" style="font-family:monospace;font-size:12px;color:#9ca3af;line-height:1.8">
      cd ~/projects/veris && source venv/bin/activate<br>
      python3 debate_stream.py \\<br>
      &nbsp;&nbsp;--mode live \\<br>
      &nbsp;&nbsp;--url "https://www.youtube.com/watch?v=VIDEO_ID_HERE" \\<br>
      &nbsp;&nbsp;--event-slug colorado-governor-republican-primary-debate-round-2 \\<br>
      &nbsp;&nbsp;--speakers "SCOTT BOTTOMS:187,BARBARA KIRKMEYER:188" \\<br>
      &nbsp;&nbsp;--speaker-order "187,188"
    </div>
  </div>

  <div class="refresh-note">{generated_at} · <a href="/ops/mobile" style="color:#a855f7">Refresh</a> · <a href="/ops" style="color:#6b7280">← Pipeline</a></div>
</div>

<script>
function showTab(el, name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
}}
</script>
</body>
</html>'''

    return html

# ── /account ──────────────────────────────────────────────────────────────────
@app.route('/account', methods=['GET'])
def account_page():
    from auth_routes import get_current_user, get_subscription, QUOTA_LIMITS
    from flask import session
    import os

    user = get_current_user(get_db)
    if not user:
        return redirect('/pricing.html?reason=login_required')

    # Consumer subscription
    consumer_sub = get_subscription(get_db, user['id'], 'consumer')
    consumer_tier = consumer_sub['tier'] if consumer_sub else 'free'
    consumer_used = consumer_sub['quota_used_this_month'] if consumer_sub else 0
    consumer_limit = QUOTA_LIMITS['consumer'][consumer_tier]
    consumer_reset = consumer_sub['quota_reset_at'] if consumer_sub else None

    # API subscription
    api_sub = get_subscription(get_db, user['id'], 'api')
    api_tier = api_sub['tier'] if api_sub else None
    api_used = api_sub['quota_used_this_month'] if api_sub else 0
    api_limit = QUOTA_LIMITS['api'][api_tier] if api_tier else None
    api_reset = api_sub['quota_reset_at'] if api_sub else None

    def quota_bar(used, limit, tier):
        if limit == 0:
            pct = 100
        else:
            pct = min(100, round(used / limit * 100))
        cls = 'full' if pct >= 100 else ('warn' if pct >= 80 else '')
        return f'''
        <div class="quota-label">
          Reports used this month
          <span>{used} / {limit}</span>
        </div>
        <div class="quota-track">
          <div class="quota-fill {cls}" style="width:{pct}%"></div>
        </div>'''

    def reset_line(reset_at):
        if not reset_at:
            return ''
        try:
            from datetime import timezone
            if hasattr(reset_at, 'strftime'):
                return f'<div class="quota-reset">Resets {reset_at.strftime("%B 1")}</div>'
        except Exception:
            pass
        return ''

    def tier_badge(tier):
        return f'<span class="tier-badge {tier}">{tier.upper()}</span>'

    # ── Consumer section ───────────────────────────────────────────────────────
    consumer_html = f'''
    <div class="section">
      <div class="section-label">Consumer · Reports</div>
      <div class="tier-row">
        <div style="font-size:13px;color:rgba(255,255,255,0.55);font-weight:300;">
          Full claim-by-claim analysis
        </div>
        {tier_badge(consumer_tier)}
      </div>
      {quota_bar(consumer_used, consumer_limit, consumer_tier)}
      {reset_line(consumer_reset)}
      {upgrade_consumer_cta(consumer_tier)}
    </div>'''

    # ── API section (only if they have an API subscription) ───────────────────
    api_html = ''
    if api_sub:
        api_html = f'''
    <div class="section">
      <div class="section-label">API · Calls</div>
      <div class="tier-row">
        <div style="font-size:13px;color:rgba(255,255,255,0.55);font-weight:300;">
          Programmatic access
        </div>
        {tier_badge(api_tier)}
      </div>
      {quota_bar(api_used, api_limit, api_tier)}
      {reset_line(api_reset)}
      {upgrade_api_cta(api_tier)}
    </div>'''

    content = f'''
    <div class="panel">
      <div class="panel-header">
        <div class="eyebrow">Account</div>
        <div class="email-line">{user["email"]}</div>
      </div>
      {consumer_html}
      {api_html}
      <div class="signout-section">
        <span class="signout-meta">verumsignal.com</span>
        <button class="btn-signout" id="signout-btn">Sign out</button>
      </div>
    </div>'''

    with open(os.path.join(os.path.dirname(__file__), 'templates', 'account.html'), 'r') as f:
        template = f.read()

    html = template.replace('{{content}}', content)
    from flask import Response
    return Response(html, mimetype='text/html')


def upgrade_consumer_cta(tier):
    if tier in ('pro', 'scale'):
        return ''
    return '''
    <div class="upgrade-block">
      <p>Upgrade to Pro for 50 on-demand reports per month, full claim analysis,
         and all sources weighed and cited.</p>
      <a href="/pricing.html" class="btn-upgrade">Upgrade to Pro &rarr;</a>
    </div>'''


def upgrade_api_cta(tier):
    if tier in ('pro', 'scale'):
        return ''
    return '''
    <div class="upgrade-block" style="margin-top:12px;">
      <p>Upgrade to API Pro for 1,000 calls/month, or Scale for 25,000 calls/month.</p>
      <a href="/api#pricing" class="btn-upgrade">Upgrade API access &rarr;</a>
    </div>'''


# ── /auth/error ────────────────────────────────────────────────────────────────
@app.route('/auth/error', methods=['GET'])
def auth_error_page():
    import os
    reason = request.args.get('reason', 'unknown')

    messages = {
        'missing_token':     ('Link missing', 'The sign-in link is incomplete. Please request a new one.'),
        'invalid_token':     ('Link not found', 'This sign-in link is invalid or has already been used. Each link works once.'),
        'token_already_used':('Link already used', 'This sign-in link has already been used. Please request a new one.'),
        'token_expired':     ('Link expired', 'This sign-in link expired after 15 minutes. Please request a new one.'),
        'server_error':      ('Something went wrong', 'An unexpected error occurred. Please try again.'),
    }

    title, message = messages.get(reason, ('Sign-in failed', 'Please request a new sign-in link.'))

    content = f'''
    <div class="panel">
      <div class="error-panel">
        <div class="error-icon">&#10007;</div>
        <h2>{title}</h2>
        <p>{message}</p>
        <a href="/" class="btn-ghost">&larr; Back to Verum Signal</a>
      </div>
    </div>'''

    with open(os.path.join(os.path.dirname(__file__), 'templates', 'account.html'), 'r') as f:
        template = f.read()

    html = template.replace('{{content}}', content)
    from flask import Response
    return Response(html, mimetype='text/html')



@app.route('/ops/sse-test', methods=['GET'])
def ops_sse_test():
    auth_err = _ops_auth()
    if auth_err:
        return auth_err
    from flask import Response
    # Get all public events for selector
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, slug, event_name FROM events WHERE is_public = TRUE ORDER BY event_date DESC")
    events = [{'id': r[0], 'slug': r[1], 'name': r[2]} for r in cur.fetchall()]
    cur.close()
    db.close()
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SSE Test — Verum Signal Ops</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0a0a0f; color: #e2e8f0; font-family: 'Inter', system-ui, sans-serif; padding: 24px; }
  h1 { font-size: 16px; font-weight: 600; color: #a855f7; margin-bottom: 4px; }
  .sub { font-size: 12px; color: #64748b; margin-bottom: 24px; }
  .controls { display: flex; gap: 12px; align-items: flex-end; margin-bottom: 24px; flex-wrap: wrap; }
  label { font-size: 11px; color: #94a3b8; display: block; margin-bottom: 4px; letter-spacing: 0.05em; text-transform: uppercase; }
  select, input, button { background: #1e1e2e; border: 1px solid #2d2d3f; color: #e2e8f0; border-radius: 6px; padding: 8px 12px; font-size: 13px; }
  button { cursor: pointer; background: #a855f7; border-color: #a855f7; color: #fff; font-weight: 500; }
  button:hover { background: #9333ea; }
  button.danger { background: #ef4444; border-color: #ef4444; }
  button.secondary { background: #1e1e2e; border-color: #374151; color: #94a3b8; }
  button.secondary:hover { border-color: #a855f7; color: #a855f7; }
  .status-bar { display: flex; gap: 16px; align-items: center; padding: 10px 16px; background: #1e1e2e; border: 1px solid #2d2d3f; border-radius: 8px; margin-bottom: 20px; font-size: 12px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #374151; display: inline-block; margin-right: 6px; }
  .dot.connected { background: #4ade80; box-shadow: 0 0 6px rgba(74,222,128,0.5); }
  .dot.error { background: #ef4444; }
  .layout { display: grid; grid-template-columns: 1fr 320px; gap: 20px; }
  .feed { display: flex; flex-direction: column; gap: 12px; }
  .card { background: #1e1e2e; border: 1px solid #2d2d3f; border-radius: 10px; padding: 16px; border-left: 3px solid #a855f7; transition: border-color 0.3s; }
  .card.provisional { border-left-color: #4ade80; opacity: 0.85; }
  .card.updated { animation: flash 0.6s ease; }
  @keyframes flash { 0%,100% { background: #1e1e2e; } 50% { background: #1a1a2e; border-color: #a855f7; } }
  .card-speaker { font-size: 11px; font-weight: 600; color: #a855f7; letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 6px; display: flex; justify-content: space-between; }
  .card-text { font-size: 13px; color: #e2e8f0; line-height: 1.5; margin-bottom: 10px; font-style: italic; }
  .card-footer { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .pill { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; padding: 3px 8px; border-radius: 4px; text-transform: uppercase; }
  .pill-verifying { background: rgba(74,222,128,0.12); border: 1px solid rgba(74,222,128,0.3); color: #4ade80; display: flex; align-items: center; gap: 5px; }
  .prov-dot { width: 5px; height: 5px; border-radius: 50%; background: #4ade80; animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }
  .pill-supported { background: rgba(74,222,128,0.15); color: #4ade80; }
  .pill-plausible { background: rgba(251,191,36,0.15); color: #fbbf24; }
  .pill-corroborated { background: rgba(139,92,246,0.15); color: #8b5cf6; }
  .pill-overstated { background: rgba(251,146,60,0.15); color: #fb923c; }
  .pill-disputed { background: rgba(239,68,68,0.15); color: #ef4444; }
  .pill-not-supported { background: rgba(239,68,68,0.15); color: #ef4444; }
  .pill-not-verifiable { background: rgba(100,116,139,0.15); color: #64748b; }
  .pill-opinion { background: rgba(100,116,139,0.15); color: #64748b; }
  .pill-provisional-badge { background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.25); color: #4ade80; font-size: 10px; padding: 2px 7px; border-radius: 4px; }
  .log { background: #0f0f1a; border: 1px solid #2d2d3f; border-radius: 8px; padding: 12px; font-family: monospace; font-size: 11px; color: #64748b; height: 500px; overflow-y: auto; }
  .log-entry { padding: 3px 0; border-bottom: 1px solid #1e1e2e; }
  .log-entry.ev-connected { color: #4ade80; }
  .log-entry.ev-claim { color: #a855f7; }
  .log-entry.ev-claim_provisional { color: #4ade80; }
  .log-entry.ev-claim_update { color: #fbbf24; }
  .log-entry.ev-heartbeat { color: #334155; }
  .log-entry.ev-error { color: #ef4444; }
  .inject-form { background: #1e1e2e; border: 1px solid #2d2d3f; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .inject-form h3 { font-size: 12px; color: #94a3b8; margin-bottom: 12px; letter-spacing: 0.05em; text-transform: uppercase; }
  .inject-form textarea, .inject-form input { width: 100%; margin-bottom: 8px; padding: 8px; font-size: 12px; background: #0f0f1a; border: 1px solid #2d2d3f; color: #e2e8f0; border-radius: 4px; }
  .inject-form textarea { height: 60px; resize: vertical; font-family: inherit; }
  .inject-form .row { display: flex; gap: 8px; }
  .empty { text-align: center; color: #334155; padding: 40px; font-size: 13px; }
  .ts { font-size: 10px; color: #475569; }
</style>
</head>
<body>
<h1>SSE Stream Test</h1>
<div class="sub">Ops-only staging tool — test live claim feed without affecting production debate pages</div>

<div class="controls">
  <div>
    <label>Event</label>
    <select id="event-select">""" +     ''.join(f'<option value="{e["slug"]}">{e["name"]}</option>' for e in events) +     """</select>
  </div>
  <div>
    <button onclick="connectSSE()">Connect</button>
    <button class="danger" onclick="disconnectSSE()" style="margin-left:6px">Disconnect</button>
    <button class="secondary" onclick="clearFeed()" style="margin-left:6px">Clear feed</button>
  </div>
</div>

<div class="status-bar">
  <span><span class="dot" id="conn-dot"></span><span id="conn-status">Disconnected</span></span>
  <span id="event-label" style="color:#64748b">—</span>
  <span id="claim-count" style="color:#64748b">0 claims</span>
  <span id="last-event" style="color:#475569">—</span>
</div>

<div class="layout">
  <div>
    <div class="inject-form">
      <h3>Inject test claim</h3>
      <textarea id="inject-text" placeholder="Claim text...">Colorado has the highest property tax rate in the Mountain West.</textarea>
      <input id="inject-speaker" placeholder="Speaker name" value="Test Speaker">
      <div class="row">
        <button onclick="injectProvisional()" style="flex:1">Inject provisional</button>
        <button onclick="injectVerified()" style="flex:1;background:#4ade80;border-color:#4ade80;color:#000">Inject verified</button>
      </div>
    </div>
    <div class="feed" id="claim-feed">
      <div class="empty" id="feed-empty">No claims yet — connect to an event and inject test claims</div>
    </div>
  </div>
  <div>
    <label style="margin-bottom:8px;display:block">Event log</label>
    <div class="log" id="event-log"></div>
  </div>
</div>

<script>
var es = null;
var claimCount = 0;

function log(type, msg) {
  var el = document.getElementById('event-log');
  var entry = document.createElement('div');
  entry.className = 'log-entry ev-' + type;
  var ts = new Date().toLocaleTimeString();
  entry.textContent = '[' + ts + '] ' + type.toUpperCase() + ': ' + msg;
  el.insertBefore(entry, el.firstChild);
  document.getElementById('last-event').textContent = type + ' · ' + ts;
}

function setStatus(state) {
  var dot = document.getElementById('conn-dot');
  var status = document.getElementById('conn-status');
  dot.className = 'dot ' + (state === 'connected' ? 'connected' : state === 'error' ? 'error' : '');
  status.textContent = state.charAt(0).toUpperCase() + state.slice(1);
}

function clearFeed() {
  var feed = document.getElementById('claim-feed');
  feed.innerHTML = '<div class="empty" id="feed-empty">Feed cleared</div>';
  claimCount = 0;
  document.getElementById('claim-count').textContent = '0 claims';
}

function disconnectSSE() {
  if (es) { es.close(); es = null; }
  setStatus('disconnected');
  log('system', 'Disconnected');
}

function connectSSE() {
  if (es) { es.close(); }
  var slug = document.getElementById('event-select').value;
  document.getElementById('event-label').textContent = slug;
  setStatus('connecting');
  log('system', 'Connecting to ' + slug + '...');

  es = new EventSource('/mobile/v1/debates/' + slug + '/stream?since_id=0');

  es.addEventListener('connected', function(e) {
    setStatus('connected');
    var data = JSON.parse(e.data);
    log('connected', 'event_id=' + data.event_id + ', existing=' + (data.existing_claims||[]).length + ', provisional=' + (data.existing_provisional||[]).length);
    (data.existing_claims || []).forEach(function(c) { renderClaim(c, false); });
    (data.existing_provisional || []).forEach(function(c) { renderClaim(c, true); });
  });

  es.addEventListener('claim', function(e) {
    var c = JSON.parse(e.data);
    log('claim', 'id=' + c.id + ' verdict=' + c.verdict + ' speaker=' + c.speaker_name);
    renderClaim(c, false);
  });

  es.addEventListener('claim_provisional', function(e) {
    var c = JSON.parse(e.data);
    log('claim_provisional', 'id=' + c.id + ' speaker=' + c.speaker_name);
    renderClaim(c, true);
  });

  es.addEventListener('claim_update', function(e) {
    var c = JSON.parse(e.data);
    log('claim_update', 'id=' + c.id + ' verdict=' + c.verdict);
    updateClaim(c);
  });

  es.addEventListener('heartbeat', function(e) {
    log('heartbeat', JSON.parse(e.data).ts);
  });

  es.addEventListener('error', function(e) {
    setStatus('error');
    try { log('error', JSON.stringify(JSON.parse(e.data))); }
    catch(ex) { log('error', 'SSE connection error'); }
  });

  es.addEventListener('debate_ended', function() {
    log('system', 'Debate ended — stream closed');
    setStatus('disconnected');
    es.close();
  });
}

var VERDICT_LABELS = {
  'supported':'SUPPORTED','plausible':'PLAUSIBLE','corroborated':'CORROBORATED',
  'overstated':'OVERSTATED','disputed':'DISPUTED','not_supported':'NOT SUPPORTED',
  'not_verifiable':'NOT VERIFIABLE','opinion':'OPINION'
};

function renderClaim(c, isProvisional) {
  var feed = document.getElementById('claim-feed');
  var empty = document.getElementById('feed-empty');
  if (empty) empty.remove();

  var verdictKey = (c.verdict || '').replace(/ /g,'_');
  var verdictClass = 'pill-' + verdictKey.replace(/_/g,'-');
  var verdictLabel = VERDICT_LABELS[verdictKey] || verdictKey;

  var pillHtml = isProvisional
    ? '<span class="pill pill-verifying"><span class="prov-dot"></span>VERIFYING</span>'
    : '<span class="pill ' + verdictClass + '">' + verdictLabel + '</span>' +
      (c.is_provisional ? '<span class="pill-provisional-badge">provisional</span>' : '');

  var card = document.createElement('div');
  card.className = 'card' + (isProvisional ? ' provisional' : '');
  card.id = 'claim-' + c.id;
  card.innerHTML =
    '<div class="card-speaker">' +
      '<span>' + (c.speaker_name || 'Unknown') + '</span>' +
      '<span class="ts">id:' + c.id + (c.timestamp_seconds ? ' · ' + Math.floor(c.timestamp_seconds/60) + ':' + String(c.timestamp_seconds%60).padStart(2,'0') : '') + '</span>' +
    '</div>' +
    '<div class="card-text">“' + (c.claim_text||'') + '”</div>' +
    '<div class="card-footer" id="footer-' + c.id + '">' + pillHtml + '</div>' +
    (c.verdict_summary ? '<div style="font-size:11px;color:#64748b;margin-top:8px;line-height:1.5">' + c.verdict_summary + '</div>' : '');

  feed.insertBefore(card, feed.firstChild);
  claimCount++;
  document.getElementById('claim-count').textContent = claimCount + ' claim' + (claimCount !== 1 ? 's' : '');
}

function updateClaim(c) {
  var card = document.getElementById('claim-' + c.id);
  if (!card) { renderClaim(c, false); return; }
  card.classList.remove('provisional');
  card.classList.add('updated');
  setTimeout(function() { card.classList.remove('updated'); }, 700);
  var verdictKey = (c.verdict || '').replace(/ /g,'_');
  var verdictClass = 'pill-' + verdictKey.replace(/_/g,'-');
  var verdictLabel = VERDICT_LABELS[verdictKey] || verdictKey;
  var footer = document.getElementById('footer-' + c.id);
  if (footer) {
    footer.innerHTML = '<span class="pill ' + verdictClass + '">' + verdictLabel + '</span>' +
      (c.is_provisional ? '<span class="pill-provisional-badge">provisional</span>' : '');
  }
  if (c.verdict_summary) {
    var existing = card.querySelector('[style*="font-size:11px"]');
    if (existing) existing.textContent = c.verdict_summary;
    else card.insertAdjacentHTML('beforeend', '<div style="font-size:11px;color:#64748b;margin-top:8px;line-height:1.5">' + c.verdict_summary + '</div>');
  }
}

function injectProvisional() {
  var text = document.getElementById('inject-text').value.trim();
  var speaker = document.getElementById('inject-speaker').value.trim();
  if (!text) return;
  var slug = document.getElementById('event-select').value;
  fetch('/api/ops/sse-test/inject', {
    method: 'POST',
    headers: {'Content-Type':'application/json', 'Authorization': 'Basic ' + btoa('admin:' + prompt('OPS_PASSWORD:'))},
    body: JSON.stringify({slug: slug, claim_text: text, speaker_name: speaker, provisional: true})
  }).then(function(r) { return r.json(); }).then(function(d) {
    log('system', 'Injected provisional claim id=' + d.claim_id);
  }).catch(function(e) { log('error', 'Inject failed: ' + e); });
}

function injectVerified() {
  var text = document.getElementById('inject-text').value.trim();
  var speaker = document.getElementById('inject-speaker').value.trim();
  if (!text) return;
  var slug = document.getElementById('event-select').value;
  fetch('/api/ops/sse-test/inject', {
    method: 'POST',
    headers: {'Content-Type':'application/json', 'Authorization': 'Basic ' + btoa('admin:' + prompt('OPS_PASSWORD:'))},
    body: JSON.stringify({slug: slug, claim_text: text, speaker_name: speaker, provisional: false})
  }).then(function(r) { return r.json(); }).then(function(d) {
    log('system', 'Injected verified claim id=' + d.claim_id);
  }).catch(function(e) { log('error', 'Inject failed: ' + e); });
}
</script>
</body>
</html>"""
    from flask import Response
    return Response(html, mimetype='text/html')


@app.route('/api/ops/sse-test/inject', methods=['POST'])
def api_ops_sse_test_inject():
    """Inject a test claim for SSE staging. Ops-auth protected."""
    auth_err = _ops_auth()
    if auth_err:
        return auth_err
    from flask import jsonify, request as req
    data = req.get_json()
    slug = data.get('slug')
    claim_text = data.get('claim_text', '').strip()
    speaker_name = data.get('speaker_name', 'Test Speaker').strip()
    is_provisional = data.get('provisional', True)

    if not slug or not claim_text:
        return jsonify({'error': 'slug and claim_text required'}), 400

    db = get_db()
    cur = db.cursor()
    try:
        # Get event_id
        cur.execute("SELECT id FROM events WHERE slug = %s AND is_public = TRUE", (slug,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Event not found'}), 404
        event_id = row[0]

        # Get or create speaker
        cur.execute("SELECT id FROM speakers WHERE name = %s LIMIT 1", (speaker_name,))
        spk = cur.fetchone()
        if spk:
            speaker_id = spk[0]
        else:
            cur.execute(
                "INSERT INTO speakers (name, normalized_name, speaker_type) VALUES (%s, %s, 'politician') RETURNING id",
                (speaker_name, speaker_name.lower())
            )
            speaker_id = cur.fetchone()[0]

        # Insert claim
        verdict = None if is_provisional else 'supported'
        verdict_summary = None if is_provisional else 'Test verdict — injected via SSE staging tool.'
        verdict_status = 'provisional'
        cur.execute("""
            INSERT INTO claims
                (claim_text, claim_origin, verdict, verdict_summary, verdict_status,
                 methodology_version, speaker_id, event_id, first_seen, last_checked,
                 priority_score, verification_method)
            VALUES
                (%s, 'debate_claim', %s, %s, %s, 'v1.7', %s, %s,
                 NOW(), %s, 50, 'fresh')
            RETURNING id
        """, (claim_text, verdict, verdict_summary, verdict_status,
              speaker_id, event_id, None if is_provisional else __import__('datetime').datetime.now(__import__('datetime').timezone.utc)))
        claim_id = cur.fetchone()[0]
        db.commit()
        return jsonify({'claim_id': claim_id, 'event_id': event_id, 'provisional': is_provisional})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        db.close()

from auth_routes import register_auth_routes
register_auth_routes(app, get_db)

# ── Attribution review ops page ─────────────────────────────────────────────
from ops_attribution import bp as ops_attribution_bp
app.register_blueprint(ops_attribution_bp)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True, use_reloader=False)


