from flask import Flask, jsonify, request, redirect, send_from_directory
from flask_cors import CORS
import psycopg2
import os
from dotenv import load_dotenv
from datetime import datetime
import os

if os.path.exists('.env'):
    load_dotenv(override=False)

app = Flask(__name__)
app.config['THREADED'] = True
CORS(app)

def get_db():
    return psycopg2.connect(
        **(dict(dsn=os.environ['DATABASE_URL']) if os.environ.get('DATABASE_URL') else dict(
            dbname=os.environ.get('DB_NAME', 'railway'),
            user=os.environ.get('DB_USER', 'postgres'),
            password=os.environ.get('DB_PASSWORD', 'Kx9mPqR7nWjL2vTsYdF4bHcE6uZaGpNe'),
            host=os.environ.get('DB_HOST', 'shinkansen.proxy.rlwy.net'),
            port=os.environ.get('DB_PORT', '35370')
        ))
    )
@app.route('/api/source', methods=['GET'])
def get_source():
    domain = request.args.get('domain', '')
    if not domain:
        return jsonify({'error': 'domain required'}), 400
    core = domain.replace('www.', '')
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            SELECT COUNT(*),
            SUM(CASE WHEN verdict = 'supported' THEN 1 ELSE 0 END),
            SUM(CASE WHEN verdict = 'disputed' THEN 1 ELSE 0 END),
            SUM(CASE WHEN verdict = 'false' THEN 1 ELSE 0 END),
            SUM(CASE WHEN verdict = 'overstated' THEN 1 ELSE 0 END)
            FROM claims c
            JOIN articles a ON c.article_id = a.id
            WHERE a.source_name ILIKE %s
            AND c.verdict IS NOT NULL
            AND (c.claim_origin = 'outlet_claim' OR c.claim_origin IS NULL)
AND (c.claim_origin = 'outlet_claim' OR c.claim_origin IS NULL)
        ''', (f'%{core}%',))
        row = cur.fetchone()
        conn.close()
        if not row or row[0] == 0:
            return jsonify({'domain': domain, 'status': 'not_found'})
        total = row[0]
        supported = row[1] or 0
        disputed = row[2] or 0
        false_count = row[3] or 0
        overstated = row[4] or 0
        score = round((supported / total) * 100) if total > 0 else 0
        if score >= 70:
            rating = 'High'
        elif score >= 40:
            rating = 'Medium'
        else:
            rating = 'Low'
        return jsonify({
            'domain': domain,
            'status': 'found',
            'rating': rating,
            'score': score,
            'total_claims': total,
            'supported': supported,
            'disputed': disputed,
            'false': false_count,
            'overstated': overstated,
            'as_of': datetime.now().strftime('%B %d, %Y')
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
        # Consistent with source reliability scoring formula.
        WEIGHTS = {
            'supported':     1.0,
            'plausible':     0.5,
            'overstated':   -0.5,
            'disputed':     -1.0,
            'not_supported':-1.5,
            'corroborated':  0.5,
        }
        weighted_sum = sum(
            WEIGHTS[c[5]] for c in claims
            if c[5] in WEIGHTS
        )
        scoreable = sum(1 for c in claims if c[5] in WEIGHTS)
        if scoreable > 0:
            normalised = (weighted_sum / scoreable + 1.5) / 2.5
            score = round(min(max(normalised * 100, 0), 100))
        else:
            score = 0

        if score >= 70:
            rating = 'High'
        elif score >= 40:
            rating = 'Medium'
        else:
            rating = 'Low'

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
        return jsonify({"status":"received","dispute_id":dispute_id,"domain":domain,"submitted_at":submitted_at.isoformat(),"message":"Your dispute has been logged and will appear publicly on the Verum Signal leaderboard. All disputes are reviewed within 10 business days. If a verdict is found incorrect it will be re-verified and updated."})
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
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static/methodology'), 'index.html')


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
    <div class="tagline">METHODOLOGY v1.5 &nbsp;·&nbsp; AUTOMATED &nbsp;·&nbsp; AUDITABLE</div>
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

@app.route('/report', methods=['GET'])
def report_page():
    url = request.args.get('url', '').strip()
    if not url:
        return redirect('/')

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
    window.location.href = '/report?url=' + encodedUrl + '&_async=1';
    return;
  }}
  fetch('/api/report-status?url=' + encodedUrl)
    .then(r => r.json())
    .then(data => {{
      if (data.status === 'ready') {{
        window.location.href = '/report?url=' + encodedUrl + '&_async=1';
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
fetch('/report?url=' + encodedUrl + '&_async=1');
setTimeout(checkStatus, 3000);
</script>
</body>
</html>"""
                return loading_html, 200, {'Content-Type': 'text/html'}
            # On-demand extraction for URLs not in DB
            try:
                import requests as _req
                from extract_claims import extract_claims_from_article
                from verdict_engine import analyse_claim
                from urllib.parse import urlparse
                import anthropic as _anth
                domain = urlparse(url).netloc.replace('www.','')
                title_text = ''
                body_text = ''

                # Try direct scraping first
                try:
                    _r = _req.get(url, timeout=(8,15), headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(_r.text, 'html.parser')
                    title_tag = soup.find('title')
                    title_text = title_tag.text.strip() if title_tag else ''
                    paragraphs = soup.find_all('p')
                    body_text = ' '.join(p.get_text() for p in paragraphs)[:8000]
                except Exception as _scrape_err:
                    print(f"Direct scrape failed: {_scrape_err}")

                # If scraping got less than 200 chars, fall back to web search
                if len(body_text) < 200:
                    print(f"Scrape returned thin content ({len(body_text)} chars) — trying web search fallback")
                    try:
                        _anth_client = _anth.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
                        # Extract slug keywords for better search
                        _url_slug = url.rstrip('/').split('/')[-1]
                        _search_keywords = ' '.join(_url_slug.replace('-', ' ').split()[:8])
                        _search_query = f'Find the full text of this article: {_search_keywords}. Source: {domain}. URL: {url}. Return the article headline and full text including all factual claims, statistics, and quoted statements.'
                        _search_msg = _anth_client.messages.create(
                            model='claude-sonnet-4-6',
                            max_tokens=2000,
                            tools=[{"type": "web_search_20250305", "name": "web_search"}],
                            messages=[{'role': 'user', 'content': _search_query}]
                        )
                        _search_text = ''
                        for _block in _search_msg.content:
                            if hasattr(_block, 'text'):
                                _search_text += _block.text
                        if _search_text and len(_search_text) > 200:
                            body_text = _search_text[:8000]
                            # Extract title from web search - skip bot protection titles
                            _ws_title = ''
                            for line in _search_text.split('\n')[:10]:
                                line = line.strip()
                                if (len(line) > 20 and len(line) < 200 
                                    and not line.startswith('http')
                                    and line.lower().strip('.') not in BOT_TITLES):
                                    _ws_title = line.rstrip('.')
                                    break
                            if _ws_title and not title_text:
                                title_text = _ws_title
                            if not title_text:
                                # Use URL slug as title fallback
                                _slug = url.rstrip('/').split('/')[-1]
                                title_text = ' '.join(_slug.replace('-', ' ').split()[:10]).title()
                            print(f"Web search fallback got {len(body_text)} chars, title: {title_text[:50]}")
                        else:
                            conn.close()
                            data = {'status': 'scrape_failed'}
                    except Exception as _ws_err:
                        print(f"Web search fallback failed: {_ws_err}")
                        conn.close()
                        data = {'status': 'scrape_failed'}

                # Detect bot-protection on scrape result only - clear bad title/body
                BOT_TITLES = {'just a moment', 'just a moment...', 'checking your browser', 'access denied', 'please verify you are a human', 'ddos protection', 'attention required', 'cloudflare'}
                if title_text and title_text.lower().strip('.').strip() in BOT_TITLES:
                    print(f"Bot protection detected on scrape: '{title_text}' — clearing scraped title/body")
                    title_text = ''
                    body_text = ''

                if not body_text:
                    conn.close()
                    data = {'status': 'scrape_failed'}
                else:
                    if not title_text:
                        title_text = domain
                    article_dict = {'title': title_text, 'description': body_text[:500], 'content': body_text, 'source': {'name': domain}, 'url': url, 'publishedAt': ''}
                    claims = extract_claims_from_article(article_dict)
                    if not claims:
                        conn.close()
                        data = {'status': 'no_claims', 'title': title_text, 'source': domain}
                    else:
                        conn2 = get_db()
                        cur2 = conn2.cursor()
                        cur2.execute("INSERT INTO articles (title, source_name, url, fetched_at, claims_verified) VALUES (%s, %s, %s, NOW(), FALSE) RETURNING id", (title_text, domain, url))
                        art_id = cur2.fetchone()[0]
                        verified_claims = []
                        for c in claims:
                            result = analyse_claim(c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), title_text, domain, cursor=cur2, claim_origin=c.get('claim_origin','outlet_claim'), attribution_context=c.get('attribution_context',''))
                            cur2.execute("INSERT INTO claims (article_id, claim_text, speaker, claim_type, claim_origin, verdict, confidence_score, verdict_summary, full_analysis, sources_used, priority_score) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                                (art_id, c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), c.get('claim_origin','outlet_claim'), result.get('verdict'), result.get('confidence_score'), result.get('verdict_summary'), result.get('full_analysis'), result.get('sources_used'), 50))
                            cid = cur2.fetchone()[0]
                            verified_claims.append((cid, c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), c.get('claim_origin','outlet_claim'), result.get('verdict'), result.get('confidence_score'), result.get('verdict_summary'), result.get('full_analysis'), result.get('sources_used')))
                        conn2.commit()
                        conn2.close()
                        conn.close()
                        rows = verified_claims
                        W = {'supported':1.0,'plausible':0.5,'corroborated':0.5,'overstated':-0.5,'disputed':-1.0,'not_supported':-1.5}
                        sc = sum(1 for c in rows if c[5] in W and c[4] == 'outlet_claim')
                        ws = sum(W[c[5]] for c in rows if c[5] in W and c[4] == 'outlet_claim')
                        score = round(min(max((ws/sc+1.5)/2.5*100,0),100)) if sc else 0
                        rating = 'High' if score>=70 else ('Medium' if score>=40 else 'Low')
                        data = {'status':'found','url':url,'title':title_text,'source':domain,'score':score,'rating':rating,'as_of':dt.now().strftime('%B %d, %Y'),'methodology_callout':f"This article contained {len(rows)} claim{'s' if len(rows)!=1 else ''} assessed after extraction. {sum(1 for c in rows if c[5]=='supported')} supported, {sum(1 for c in rows if c[5] in ('overstated','disputed','not_supported'))} flagged.",'stats':{'supported':sum(1 for c in rows if c[5]=='supported'),'plausible':sum(1 for c in rows if c[5]=='plausible'),'corroborated':sum(1 for c in rows if c[5]=='corroborated'),'overstated':sum(1 for c in rows if c[5]=='overstated'),'disputed':sum(1 for c in rows if c[5]=='disputed'),'not_supported':sum(1 for c in rows if c[5]=='not_supported'),'opinion':sum(1 for c in rows if c[5]=='opinion'),'total':len(rows)},'claims':[{'id':c[0],'claim_text':c[1],'speaker':c[2],'claim_type':c[3],'claim_origin':c[4],'verdict':c[5],'confidence_score':c[6],'verdict_summary':c[7],'full_analysis':c[8],'sources_used':c[9]} for c in rows]}
            except Exception as e:
                import traceback
                print(f"On-demand extraction failed: {e}")
                print(traceback.format_exc())
                conn.close()
                data = {'status': 'not_found'}
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
                        for c in claims:
                            result = analyse_claim(c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), title_db, source_name, cursor=cur2, claim_origin=c.get('claim_origin','outlet_claim'), attribution_context=c.get('attribution_context',''))
                            cur2.execute("INSERT INTO claims (article_id, claim_text, speaker, claim_type, claim_origin, verdict, confidence_score, verdict_summary, full_analysis, sources_used, priority_score) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                                (art_id, c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), c.get('claim_origin','outlet_claim'), result.get('verdict'), result.get('confidence_score'), result.get('verdict_summary'), result.get('full_analysis'), result.get('sources_used'), 50))
                            cid = cur2.fetchone()[0]
                            verified_claims.append((cid, c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), c.get('claim_origin','outlet_claim'), result.get('verdict'), result.get('confidence_score'), result.get('verdict_summary'), result.get('full_analysis'), result.get('sources_used')))
                        cur2.execute("UPDATE articles SET claims_verified=TRUE WHERE id=%s", (art_id,))
                        conn2.commit()
                        conn2.close()
                        rows = verified_claims
                        W = {'supported':1.0,'plausible':0.5,'corroborated':0.5,'overstated':-0.5,'disputed':-1.0,'not_supported':-1.5}
                        sc = sum(1 for c in rows if c[5] in W and c[4] == 'outlet_claim')
                        ws = sum(W[c[5]] for c in rows if c[5] in W and c[4] == 'outlet_claim')
                        score = round(min(max((ws/sc+1.5)/2.5*100,0),100)) if sc else 0
                        rating = 'High' if score>=70 else ('Medium' if score>=40 else 'Low')
                        data = {'status':'found','url':art_url,'title':title_db,'source':source_name,'score':score,'rating':rating,'as_of':dt.now().strftime('%B %d, %Y'),'methodology_callout':f"This article contained {len(rows)} claim{'s' if len(rows)!=1 else ''} assessed after extraction. {sum(1 for c in rows if c[5]=='supported')} supported, {sum(1 for c in rows if c[5] in ('overstated','disputed','not_supported'))} flagged.",'stats':{'supported':sum(1 for c in rows if c[5]=='supported'),'plausible':sum(1 for c in rows if c[5]=='plausible'),'corroborated':sum(1 for c in rows if c[5]=='corroborated'),'overstated':sum(1 for c in rows if c[5]=='overstated'),'disputed':sum(1 for c in rows if c[5]=='disputed'),'not_supported':sum(1 for c in rows if c[5]=='not_supported'),'opinion':sum(1 for c in rows if c[5]=='opinion'),'total':len(rows)},'claims':[{'id':c[0],'claim_text':c[1],'speaker':c[2],'claim_type':c[3],'claim_origin':c[4],'verdict':c[5],'confidence_score':c[6],'verdict_summary':c[7],'full_analysis':c[8],'sources_used':c[9]} for c in rows]}
                except Exception as e:
                    print(f"On-demand extraction (no_claims path) failed: {e}")
                    data = {'status': 'no_claims', 'title': title_db, 'source': source_name}
            else:
                W = {'supported':1.0,'plausible':0.5,'corroborated':0.5,'overstated':-0.5,'disputed':-1.0,'not_supported':-1.5}
                sc = sum(1 for c in rows if c[5] in W)
                ws = sum(W[c[5]] for c in rows if c[5] in W)
                score = round(min(max((ws/sc+1.5)/2.5*100,0),100)) if sc else 0
                rating = 'High' if score>=70 else ('Medium' if score>=40 else 'Low')
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
                callout_text = f"This article contained {len(rows)} claim{'s' if len(rows)!=1 else ''} assessed after extraction. {sum(1 for c in rows if c[5]=='supported')} supported, {sum(1 for c in rows if c[5] in ('overstated','disputed','not_supported'))} flagged. The independence rule and wire-service exclusion were applied where relevant."
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
    rating  = data.get('rating', 'Medium')
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

    score_color = '#4ade80' if score >= 70 else ('#fbbf24' if score >= 40 else '#f87171')

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
        conf_label = {1: "Confidence: 1/3 — one source found", 2: "Confidence: 2/3 — two sources found", 3: "Confidence: 3/3 — verified by multiple independent sources"}.get(confidence, "")
        conf_html = '<div class="vs-conf" title="' + conf_label + '">' + conf_dots + '</div>'
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
            src_pills += '<span class="vs-src ' + cls + '">' + d + '</span>'
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
            '<span class="vs-toggle">▼</span>'
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
    _INDEPENDENT = {'reuters.com','apnews.com','ap.org','npr.org','bbc.com','bbc.co.uk','nytimes.com','washingtonpost.com','theguardian.com','wikipedia.org','britannica.com','brookings.edu','rand.org','cfr.org','pbs.org','cbsnews.com','nbcnews.com','abcnews.go.com','politico.com','thehill.com','axios.com','bloomberg.com','wsj.com','economist.com','ft.com','federalnewsnetwork.com','militarytimes.com'}
    _WIRE = {'reuters.com','apnews.com','ap.org','bloomberg.com','afp.com'}
    def _src_class(d):
        d = d.replace('www.','').lower()
        if d.split('.')[-1] in _GOV_TLD or 'house.gov' in d or 'senate.gov' in d or 'congress.gov' in d:
            return 'vs-src-p'  # primary/green
        if d in _WIRE:
            return 'vs-src-w'  # wire/gray
        if d in _INDEPENDENT:
            return 'vs-src-i'  # independent/blue
        return 'vs-src-c'  # interested/amber
    if clean_domains:
        all_sources_html = ''.join('<span class="vs-src ' + _src_class(d) + '">' + d + '</span>' for d in sorted(clean_domains))
    else:
        all_sources_html = '<span style="font-family:monospace;font-size:11px;color:rgba(255,255,255,0.3);">No independent sources found — see Methodology v1.5 Section 5 (independence rule)</span>'


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
        _prompt = ('You are the editorial analysis layer for Verum Signal, an independent claim analysis platform governed by Methodology v1.5.' + chr(10) +
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
            'VERIFIED CLAIMS:' + chr(10) + _claims_text + chr(10) +
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

    with open(os.path.join(os.path.dirname(__file__), 'templates', 'report.html'), 'r') as _tf:
        html = _tf.read()
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
    pass #removed
    html = html.replace('{{score_color}}', str(score_color))
    # Inclusion tier
    try:
        _ic = get_db()
        _ic_cur = _ic.cursor()
        _ic_cur.execute("SELECT COUNT(c.id) FROM claims c JOIN articles a ON c.article_id = a.id WHERE a.source_name = %s AND c.verdict IS NOT NULL AND c.claim_origin = 'outlet_claim'", (source,))
        _verdict_count = _ic_cur.fetchone()[0]
        _ic.close()
    except:
        _verdict_count = 0
    if _verdict_count >= 100:
        inclusion_tier, tier_color = 'Published', '#4ade80'
    elif _verdict_count >= 50:
        inclusion_tier, tier_color = 'Stabilizing', '#60a5fa'
    elif _verdict_count >= 20:
        inclusion_tier, tier_color = 'Limited Data', '#fbbf24'
    else:
        inclusion_tier, tier_color = 'Excluded', '#f87171'
    html = html.replace('{{inclusion_tier}}', str(inclusion_tier))
    html = html.replace('{{tier_color}}', str(tier_color))
    html = html.replace('{{verdict_count}}', str(_verdict_count))
    # Excluded tier display logic
    is_excluded = _verdict_count < 20
    if is_excluded:
        outlet_badge_html = source + ' &nbsp;&middot;&nbsp; <span style="color:rgba(255,255,255,0.45)">Insufficient data (' + str(_verdict_count) + ' verdicts)</span>'
        score_block_html = ('<div class="vs-score-block"><div class="vs-score-num" style="color:rgba(232,232,240,0.35);font-size:36px;">Insufficient</div><div class="vs-score-unit">data</div><div style="font-family:monospace;font-size:10px;color:rgba(255,255,255,0.3);margin-top:4px;">' + str(_verdict_count) + ' verdicts &middot; min. 20 required</div></div>')
        excluded_inset_html = ('<div class="vs-excluded-inset"><div class="vs-excluded-inset-label">WHY NO OUTLET SCORE</div><div class="vs-excluded-inset-text">Verum Signal does not publish outlet scores until an outlet has accumulated at least 20 verified claim verdicts. ' + source + ' currently has ' + str(_verdict_count) + ' verdict' + ('s' if _verdict_count != 1 else '') + ', which is not enough for a reliable score. The per-claim analysis below is reliable — what’s not yet reliable is an aggregate outlet rating.</div></div>')
        outlet_score_stat = '—'
        tier_label = 'STATUS'
        tier_stat = 'Insufficient data'
        footer_score_text = 'outlet score not yet available'
    else:
        outlet_badge_html = source + ' &nbsp;&middot;&nbsp; <b>' + str(score) + '/100</b> ' + rating + ' &nbsp;&middot;&nbsp; <span style="color:' + tier_color + '">' + inclusion_tier + '</span>'
        score_block_html = ('<div class="vs-score-num" style="color:' + score_color + '">' + str(score) + '</div><div class="vs-score-unit">/100</div><div class="vs-score-tier" style="color:' + score_color + '">' + rating + '</div><div class="vs-inclusion-tier" style="color:' + tier_color + '">' + inclusion_tier + ' &middot; ' + str(_verdict_count) + ' verdicts</div>')
        excluded_inset_html = ''
        outlet_score_stat = str(score) + '<span>/100</span>'
        tier_label = 'TIER'
        tier_stat = rating
        footer_score_text = 'outlet score: ' + str(score) + '/100 ' + rating
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
    html = html.replace('{{all_sources_html}}', str(all_sources_html))
    html = html.replace('{{compare_html}}', str(compare_html))
    html = html.replace('{{total}}', str(stats.get('total',0)))
    html = html.replace('{{sc}}', str(sc))
    from flask import Response
    return Response(html, mimetype='text/html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
