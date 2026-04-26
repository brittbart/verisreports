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
            # On-demand extraction for URLs not in DB
            try:
                import requests as _req
                from extract_claims import extract_claims_from_article
                from verdict_engine import analyse_claim
                from urllib.parse import urlparse
                _r = _req.get(url, timeout=(8,15), headers={'User-Agent': 'Mozilla/5.0'})
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(_r.text, 'html.parser')
                title_tag = soup.find('title')
                title_text = title_tag.text.strip() if title_tag else url
                paragraphs = soup.find_all('p')
                body_text = ' '.join(p.get_text() for p in paragraphs)[:8000]
                domain = urlparse(url).netloc.replace('www.','')
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
                        result = analyse_claim(c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), title_text, domain, cursor=cur2)
                        cur2.execute("INSERT INTO claims (article_id, claim_text, speaker, claim_type, claim_origin, verdict, confidence_score, verdict_summary, full_analysis, sources_used, priority_score) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            (art_id, c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), c.get('claim_origin','outlet_claim'), result.get('verdict'), result.get('confidence_score'), result.get('verdict_summary'), result.get('full_analysis'), result.get('sources_used'), 50))
                        cid = cur2.fetchone()[0]
                        verified_claims.append((cid, c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), c.get('claim_origin','outlet_claim'), result.get('verdict'), result.get('confidence_score'), result.get('verdict_summary'), result.get('full_analysis'), result.get('sources_used')))
                    conn2.commit()
                    conn2.close()
                    conn.close()
                    rows = verified_claims
                    W = {'supported':1.0,'plausible':0.5,'corroborated':0.5,'overstated':-0.5,'disputed':-1.0,'not_supported':-1.5}
                    sc = sum(1 for c in rows if c[5] in W)
                    ws = sum(W[c[5]] for c in rows if c[5] in W)
                    score = round(min(max((ws/sc+1.5)/2.5*100,0),100)) if sc else 0
                    rating = 'High' if score>=70 else ('Medium' if score>=40 else 'Low')
                    data = {'status':'found','url':url,'title':title_text,'source':domain,'score':score,'rating':rating,'as_of':dt.now().strftime('%B %d, %Y'),'methodology_callout':f"This article contained {sc} scoreable factual claims after extraction.",'stats':{'supported':sum(1 for c in rows if c[5]=='supported'),'plausible':sum(1 for c in rows if c[5]=='plausible'),'corroborated':sum(1 for c in rows if c[5]=='corroborated'),'overstated':sum(1 for c in rows if c[5]=='overstated'),'disputed':sum(1 for c in rows if c[5]=='disputed'),'not_supported':sum(1 for c in rows if c[5]=='not_supported'),'opinion':sum(1 for c in rows if c[5]=='opinion'),'total':len(rows)},'claims':[{'id':c[0],'claim_text':c[1],'speaker':c[2],'claim_type':c[3],'claim_origin':c[4],'verdict':c[5],'confidence_score':c[6],'verdict_summary':c[7],'full_analysis':c[8],'sources_used':c[9]} for c in rows]}
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
                            result = analyse_claim(c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), title_db, source_name, cursor=cur2)
                            cur2.execute("INSERT INTO claims (article_id, claim_text, speaker, claim_type, claim_origin, verdict, confidence_score, verdict_summary, full_analysis, sources_used, priority_score) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                                (art_id, c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), c.get('claim_origin','outlet_claim'), result.get('verdict'), result.get('confidence_score'), result.get('verdict_summary'), result.get('full_analysis'), result.get('sources_used'), 50))
                            cid = cur2.fetchone()[0]
                            verified_claims.append((cid, c.get('claim_text',''), c.get('speaker',''), c.get('claim_type','factual'), c.get('claim_origin','outlet_claim'), result.get('verdict'), result.get('confidence_score'), result.get('verdict_summary'), result.get('full_analysis'), result.get('sources_used')))
                        cur2.execute("UPDATE articles SET claims_verified=TRUE WHERE id=%s", (art_id,))
                        conn2.commit()
                        conn2.close()
                        rows = verified_claims
                        W = {'supported':1.0,'plausible':0.5,'corroborated':0.5,'overstated':-0.5,'disputed':-1.0,'not_supported':-1.5}
                        sc = sum(1 for c in rows if c[5] in W)
                        ws = sum(W[c[5]] for c in rows if c[5] in W)
                        score = round(min(max((ws/sc+1.5)/2.5*100,0),100)) if sc else 0
                        rating = 'High' if score>=70 else ('Medium' if score>=40 else 'Low')
                        data = {'status':'found','url':art_url,'title':title_db,'source':source_name,'score':score,'rating':rating,'as_of':dt.now().strftime('%B %d, %Y'),'methodology_callout':f"This article contained {sc} scoreable factual claims after extraction.",'stats':{'supported':sum(1 for c in rows if c[5]=='supported'),'plausible':sum(1 for c in rows if c[5]=='plausible'),'corroborated':sum(1 for c in rows if c[5]=='corroborated'),'overstated':sum(1 for c in rows if c[5]=='overstated'),'disputed':sum(1 for c in rows if c[5]=='disputed'),'not_supported':sum(1 for c in rows if c[5]=='not_supported'),'opinion':sum(1 for c in rows if c[5]=='opinion'),'total':len(rows)},'claims':[{'id':c[0],'claim_text':c[1],'speaker':c[2],'claim_type':c[3],'claim_origin':c[4],'verdict':c[5],'confidence_score':c[6],'verdict_summary':c[7],'full_analysis':c[8],'sources_used':c[9]} for c in rows]}
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
                callout_text = f"This article contained {sc} scoreable factual claim{'s' if sc!=1 else ''} after extraction. "
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
    if status in ('not_found', 'no_claims', 'error'):
        msg = {
            'not_found': 'This article isn\u2019t in our database yet. Our scheduler runs every 3 hours \u2014 try again soon, or paste a URL from a tracked outlet.',
            'no_claims': 'We found this article but couldn\u2019t extract scoreable factual claims from it.',
            'error': data.get('message', 'Something went wrong. Please try again.')
        }.get(status, 'Unknown error.')
        return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verum Signal \u2014 Report</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#080810;color:#f0f0f8;font-family:'DM Sans',system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}}.wrap{{max-width:520px;padding:48px 32px;text-align:center}}.logo{{font-family:Georgia,serif;font-size:22px;color:#a855f7;margin-bottom:32px}}h2{{font-size:20px;margin-bottom:16px}}p{{font-size:15px;color:rgba(240,240,248,.55);line-height:1.65;margin-bottom:28px}}a{{color:#a855f7;text-decoration:none}}</style>
</head><body><div class="wrap">
<div class="logo">Verum Signal</div>
<h2>Report unavailable</h2>
<p>{msg}</p>
<a href="/">\u2190 Back to search</a>
</div></body></html>""", 404 if status == 'not_found' else 200, {'Content-Type': 'text/html'}

    # ── Build verdict pills ──
    stats   = data.get('stats', {})
    claims  = data.get('claims', [])
    title   = data.get('title', 'Article Report')
    source  = data.get('source', '')
    score   = data.get('score', 0)
    rating  = data.get('rating', 'Medium')
    as_of   = data.get('as_of', '')

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

    def claim_row(c):
        v = c.get('verdict') or 'not_verifiable'
        col = VERDICT_COLOR.get(v, ('#aaa','rgba(170,170,170,0.1)','rgba(170,170,170,0.3)'))
        lbl = VERDICT_LABEL.get(v, v)
        wt  = VERDICT_WEIGHT.get(v, '')
        text = c.get('claim_text', '')
        summary = c.get('verdict_summary', '') or ''
        sources = c.get('sources_used', '') or ''
        pipeline = c.get('claim_type', 'web search') or 'web search'
        cid = c.get('id', '')
        attr = c.get('claim_origin', 'outlet_claim') or 'outlet_claim'
        attr_label = 'Original reporting \u2014 counts toward outlet score' if attr == 'outlet_claim' else 'Wire reprint \u2014 excluded from outlet score'
        breaking = c.get('breaking', False)
        breaking_pill = ('\u26a1 Breaking \u2014 excluded from outlet score until gate passes' if breaking else '')

        return f"""
<div class="claim-row" onclick="this.classList.toggle('open')">
  <div class="claim-header">
    <span class="verdict-badge" style="background:{col[1]};border-color:{col[2]};color:{col[0]};">{lbl}</span>
    <span class="claim-text">{text}</span>
    <span class="pipeline-tag">{pipeline}</span>
    <svg class="chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 6l4 4 4-4"/></svg>
  </div>
  <div class="claim-body">
    <div class="claim-grid">
      <div><div class="detail-label">Verdict reasoning</div><div class="detail-val">{summary}</div></div>
      <div><div class="detail-label">Sources consulted</div><div class="detail-val">{sources}</div></div>
    </div>
    <div style="margin-top:8px;">
      <span class="attr-tag">{attr_label}</span>
      {f'<span class="break-tag">{breaking_pill}</span>' if breaking else ''}
    </div>
    <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;">
      {"".join(f'<span style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid rgba(168,85,247,0.3);color:rgba(168,85,247,0.8);background:rgba(168,85,247,0.06)">{d.strip()}</span>' for d in sources.replace(","," ").split() if "." in d and len(d)>3)[:5]}
    </div>
    <div class="detail-label" style="margin-top:8px;">Weight: {wt}</div>
  </div>
</div>"""

    claims_html = ''.join(claim_row(c) for c in claims)

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

    # Build all sources list
    all_domains = set()
    for c in claims:
        src = c.get('sources_used','') or ''
        for word in src.replace(',',' ').split():
            if '.' in word and len(word) > 3:
                all_domains.add(word.strip('().-').lower())
    all_sources_html = ''.join(f'<span style="font-size:11px;padding:4px 12px;border-radius:4px;border:1px solid rgba(168,85,247,0.3);color:rgba(168,85,247,0.8);background:rgba(168,85,247,0.06)">{d}</span>' for d in sorted(all_domains) if len(d)>3)

    # Build overall signal narrative
    supported_n = stats.get("supported",0)
    overstated_n = stats.get("overstated",0)
    disputed_n = stats.get("disputed",0)
    not_supported_n = stats.get("not_supported",0)
    total_n = stats.get("total",0)
    if rating == "High":
        overall_signal = f"This article scores in the High tier. The factual claims assessed were well-sourced and confirmed by independent reporting. Verdicts reflect the evidence available at time of analysis."
    elif rating == "Medium":
        overall_signal = f"This article scores in the Medium tier. Of {total_n} claims assessed, {supported_n} were supported by independent sources. {overstated_n + disputed_n + not_supported_n} claim(s) showed evidence of overstatement or factual dispute. Verdicts reflect the evidence available at time of analysis."
    else:
        overall_signal = f"This article scores in the Low tier. Multiple claims showed signs of overstatement or direct contradiction by independent sources. Readers should consult additional sources before drawing conclusions. Verdicts reflect the evidence available at time of analysis."

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verum Signal \u2014 {source}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#080810;--bg2:#0e0e1a;--bg3:#13131f;--border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.12);--text:#f0f0f8;--muted:rgba(240,240,248,0.72);--dim:rgba(240,240,248,0.45);--violet:#a855f7;--violet-dim:rgba(168,85,247,0.15);--violet-border:rgba(168,85,247,0.3);--sidebar-w:280px;--font-serif:'DM Serif Display',Georgia,serif;--font-sans:'DM Sans',system-ui,sans-serif}}
html{{font-size:16px;scroll-behavior:smooth}}
body{{background:var(--bg);color:var(--text);font-family:var(--font-sans);line-height:1.6;min-height:100vh}}
.layout{{display:grid;grid-template-columns:1fr var(--sidebar-w);max-width:1200px;margin:0 auto;min-height:100vh;border:1px solid var(--violet-border);border-radius:12px;overflow:hidden;margin-top:1.5rem;margin-bottom:3rem;box-shadow:0 0 0 1px rgba(168,85,247,0.08),0 24px 80px rgba(0,0,0,0.6)}}
.main-col{{grid-column:1;border-right:1px solid var(--border);padding-bottom:5rem}}
.sidebar{{grid-column:2;position:sticky;top:0;height:100vh;overflow-y:auto;padding:2rem 1.5rem;display:flex;flex-direction:column;gap:1.5rem}}
.top-bar{{padding:12px 2.5rem;border-bottom:1px solid rgba(168,85,247,0.15);display:flex;align-items:center;gap:12px}}
.logo-mark{{display:flex;align-items:center;gap:8px;font-family:var(--font-serif);font-size:15px;color:var(--text)}}
.logo-dot{{width:8px;height:8px;border-radius:50%;background:#e879f9}}
.tagline{{font-size:11px;color:var(--dim);font-style:italic;margin-left:4px}}
.article-header{{padding:2rem 2.5rem 0}}
.tag-row{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:1rem}}
.tag{{font-size:11px;font-weight:500;letter-spacing:0.06em;text-transform:uppercase;padding:3px 10px;border-radius:100px;border:1px solid var(--border2);color:var(--muted)}}
.tag.outlet{{border-color:var(--violet-border);color:var(--violet);background:var(--violet-dim)}}
.article-title{{font-family:var(--font-serif);font-size:1.8rem;line-height:1.2;color:var(--text);margin-bottom:0.75rem;max-width:680px}}
.meta-row{{display:flex;align-items:center;gap:1rem;font-size:12px;color:var(--dim);padding-bottom:2rem;border-bottom:1px solid var(--border);flex-wrap:wrap}}
.section{{padding:2rem 2.5rem;border-bottom:1px solid var(--border)}}
.section-label{{font-size:10px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:var(--dim);margin-bottom:0.75rem}}
.section-label-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:0.75rem}}
.expand-all{{font-size:11px;color:rgba(168,85,247,0.7);background:none;border:none;cursor:pointer;font-family:var(--font-sans)}}
.callout{{border-left:3px solid;padding:1rem 1.25rem;border-radius:0 8px 8px 0;background:var(--bg2)}}
.callout.violet{{border-color:var(--violet)}}
.callout p{{font-size:0.9rem;color:var(--muted);line-height:1.65}}
.pills-grid{{display:flex;flex-wrap:wrap;gap:8px}}
.claim-row{{border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:8px;transition:border-color .15s}}
.claim-row:hover{{border-color:var(--border2)}}
.claim-header{{display:flex;flex-direction:column;padding:1rem 1.25rem;cursor:pointer;background:var(--bg2);gap:0.5rem}}
.claim-header-top{{display:flex;align-items:center;justify-content:space-between}}
.verdict-badge{{font-size:10px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;padding:3px 10px;border-radius:100px;border:1px solid;white-space:nowrap}}
.pipeline-tag{{font-size:10px;color:var(--dim);white-space:nowrap}}
.claim-text{{font-size:0.9rem;color:var(--text);line-height:1.5}}
.chevron{{width:16px;height:16px;color:var(--dim);transition:transform .2s;flex-shrink:0}}
.claim-row.open .chevron{{transform:rotate(180deg)}}
.claim-body{{display:none;padding:1rem 1.25rem 1.25rem;background:var(--bg3);border-top:1px solid var(--border)}}
.claim-row.open .claim-body{{display:block}}
.claim-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:.75rem}}
.detail-label{{font-size:10px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:var(--dim);margin-bottom:.35rem}}
.detail-val{{font-size:.85rem;color:var(--muted);line-height:1.6}}
.attr-tag{{font-size:10px;font-weight:500;letter-spacing:0.06em;text-transform:uppercase;padding:3px 10px;border-radius:100px;border:1px solid var(--border2);color:var(--dim)}}
.break-tag{{font-size:10px;color:#eab308;border:1px solid rgba(234,179,8,0.3);background:rgba(234,179,8,0.08);padding:3px 10px;border-radius:100px;margin-left:6px}}
.summary-text{{font-family:var(--font-serif);font-size:1.05rem;line-height:1.75;color:var(--muted);font-style:italic;max-width:640px}}
.stat-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border);border:1px solid var(--border);border-radius:8px;overflow:hidden}}
.stat-cell{{background:var(--bg2);padding:1.25rem;text-align:center}}
.stat-label{{font-size:10px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:var(--dim);margin-bottom:.5rem}}
.stat-value{{font-family:var(--font-serif);font-size:1.5rem;color:var(--text)}}
.stat-sub{{font-size:11px;color:var(--dim);margin-top:2px}}
.footer-badge{{margin:2rem 2.5rem 0;padding:1.25rem 1.5rem;background:var(--bg2);border:1px solid var(--border);border-radius:12px;display:flex;align-items:center;justify-content:space-between}}
.footer-logo{{display:flex;align-items:center;gap:.5rem;font-family:var(--font-serif);font-size:1rem;color:var(--text)}}
.footer-logo-dot{{width:8px;height:8px;border-radius:50%;background:#e879f9}}
.share-btn{{font-size:11px;color:var(--dim);cursor:pointer;padding:5px 12px;border:1px solid var(--border);border-radius:100px;background:none;font-family:var(--font-sans);transition:border-color .15s}}
.verdict-panel{{background:var(--bg2);border:1px solid var(--border);border-radius:10px;overflow:hidden}}
.vp-header{{padding:1rem 1.25rem .75rem;border-bottom:1px solid var(--border)}}
.vp-label{{font-size:10px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:var(--dim);margin-bottom:.35rem}}
.vp-nums{{font-family:var(--font-serif);font-size:1rem;color:var(--muted);margin-bottom:.5rem}}
.vbar{{height:4px;border-radius:2px;overflow:hidden;background:var(--border);display:flex;margin-top:.75rem}}
.score-box{{padding:1rem 1.25rem}}
.score-label{{font-size:10px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:var(--dim);margin-bottom:.35rem}}
.score-val{{font-family:var(--font-serif);font-size:2rem}}
.score-bar{{height:3px;background:var(--border);border-radius:2px;overflow:hidden;margin-top:.75rem}}
.legend-item{{display:flex;align-items:center;justify-content:space-between;padding:.5rem 1.25rem;font-size:12px;border-bottom:1px solid var(--border)}}
.legend-left{{display:flex;align-items:center;gap:8px}}
.legend-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.legend-name{{color:var(--muted)}}
.legend-wt{{color:var(--dim);font-size:11px}}
.vn{{font-size:11px;color:var(--dim);line-height:1.5;padding:.75rem 1rem;background:var(--bg2);border:1px solid var(--border);border-radius:8px}}
@media(max-width:768px){{.layout{{grid-template-columns:1fr;border-radius:0;margin:0}}.main-col{{border-right:none;border-bottom:1px solid var(--border)}}.sidebar{{position:static;height:auto}}.claim-grid{{grid-template-columns:1fr}}.stat-row{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body>
<div class="layout">
<main class="main-col">
  <div class="top-bar">
    <div class="logo-mark"><div class="logo-dot"></div>VERUM<em style="font-style:italic;color:#c084fc;margin-left:3px">SIGNAL</em></div>
    <span class="tagline">We provide the signals. You decide.</span>
  </div>

  <header class="article-header">
    <div class="tag-row">
      <span class="tag outlet">{source}</span>
      <span class="tag">News</span>
    </div>
    <h1 class="article-title">{title}</h1>
    <div class="meta-row">
      <span>{as_of}</span>
      <span>&middot;</span>
      <span>{stats.get('total',0)} claims analysed</span>
      <span>&middot;</span>
      <span>Methodology v1.5</span>
      <span>&middot;</span>
      <a href="{url}" target="_blank" style="color:rgba(168,85,247,0.7);text-decoration:none;">Source \u2197</a>
    </div>
  </header>

  <section class="section">
    <div class="section-label">Methodology applied</div>
    <div class="callout violet">
      <p>Each factual claim passes through a three-step pipeline: cache check (free), internal consensus check (free), then web search. Claims attributed to wire services or accurately quoting named speakers are excluded from the outlet\u2019s reliability score. Breaking news claims published within 6 hours are excluded until the gate passes. Methodology v1.5.</p>
    </div>
  </section>

  <section class="section">
    <div class="section-label">Claim summary</div>
    <div class="pills-grid">{total_pill}{pills_html}</div>
  </section>

  <section class="section">
    <div class="section-label-row">
      <span class="section-label" style="margin-bottom:0">Claim analysis</span>
      <button class="expand-all" onclick="toggleAll(this)">Expand all</button>
    </div>
    <div id="claims-list">{claims_html}</div>
  </section>

  <section class="section">
    <div class="section-label">Factual core</div>
    <p class="summary-text">This report shows {stats.get('total',0)} claims extracted from the article, assessed against independent sources using the Verum Signal methodology. Verdicts reflect the evidence available at time of analysis.</p>
  </section>

  <section class="section">
    <div class="stat-row">
      <div class="stat-cell"><div class="stat-label">Claims</div><div class="stat-value">{stats.get('total',0)}</div><div class="stat-sub">analysed</div></div>
      <div class="stat-cell"><div class="stat-label">Supported</div><div class="stat-value" style="color:#4ade80">{stats.get('supported',0)}</div><div class="stat-sub">of {stats.get('total',0)}</div></div>
      <div class="stat-cell"><div class="stat-label">Contested</div><div class="stat-value" style="color:#f87171">{stats.get('disputed',0) + stats.get('not_supported',0)}</div><div class="stat-sub">disputed + not supported</div></div>
      <div class="stat-cell"><div class="stat-label">Outlet score</div><div class="stat-value" style="color:{score_color}">{score}</div><div class="stat-sub">{rating}</div></div>
    </div>
  </section>

  <section class="section">
    <div class="section-label">Things to hold in mind</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:20px;">
        <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:8px;">Why might this outlet score differently on other articles?</div>
        <div style="font-size:12px;color:var(--dim);line-height:1.6;">Scores reflect the specific claims in this article, not a blanket rating. An outlet can score high on one story and lower on another.</div>
      </div>
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:20px;">
        <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:8px;">What does Corroborated mean vs Supported?</div>
        <div style="font-size:12px;color:var(--dim);line-height:1.6;">Supported means two independent sources confirmed the claim. Corroborated means 5+ outlets reported consistently — no external verification was needed.</div>
      </div>
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:20px;">
        <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:8px;">Why is the Opinion claim excluded from scoring?</div>
        <div style="font-size:12px;color:var(--dim);line-height:1.6;">Opinion content is not a factual signal. Outlets are not penalized for editorial framing — only for factual inaccuracies in their own reporting.</div>
      </div>
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:20px;">
        <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:8px;">Can a verdict change after it's assigned?</div>
        <div style="font-size:12px;color:var(--dim);line-height:1.6;">Yes. Verdicts are reviewed when new evidence emerges or when a dispute is submitted. Submit a correction below if you believe a verdict is wrong.</div>
      </div>
    </div>
  </section>

  <section class="section">
    <div class="section-label">Sources consulted</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;">{all_sources_html}</div>
  </section>

  <section class="section">
    <div class="section-label">Overall signal</div>
    <div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:24px;">
      <div style="font-family:var(--font-serif);font-size:1rem;line-height:1.75;color:var(--muted);font-style:italic;">{overall_signal}</div>
    </div>
  </section>

  <div class="footer-badge">
    <div>
      <div class="footer-logo"><div class="footer-logo-dot"></div>Verum Signal</div>
      <div style="font-size:11px;color:var(--dim);margin-top:4px">We provide the signals. You decide.</div>
      <div style="font-size:11px;color:var(--dim);margin-top:2px">Methodology v1.5 &nbsp;&middot;&nbsp; Automated &nbsp;&middot;&nbsp; Auditable</div>
    </div>
    <button class="share-btn" onclick="copyUrl()">Share report \u2197</button>
  </div>
</main>

<aside class="sidebar">
  <div class="verdict-panel">
    <div class="vp-header">
      <div class="vp-label">Article verdict distribution</div>
      <div class="vp-nums">{stats.get('supported',0)} supported &middot; {stats.get('disputed',0) + stats.get('not_supported',0)} contested</div>
      <div class="vbar">{verdict_bar_segs}</div>
    </div>
    <div class="score-box">
      <div class="score-label">Outlet reliability score</div>
      <div class="score-val" style="color:{score_color}">{score}</div>
      <div style="font-size:11px;color:{score_color};opacity:.7">{rating} reliability</div>
      <div class="score-bar"><div style="width:{score_bar_pct}%;height:100%;background:{score_color};border-radius:2px"></div></div>
      <div style="font-size:11px;color:var(--dim);margin-top:.4rem">Based on {stats.get('total',0)} scored claims</div>
    </div>
    <div style="border-top:1px solid var(--border)">
      <div class="legend-item"><div class="legend-left"><div class="legend-dot" style="background:#4ade80"></div><span class="legend-name">Supported</span></div><span class="legend-wt">+1.0</span></div>
      <div class="legend-item"><div class="legend-left"><div class="legend-dot" style="background:#fbbf24"></div><span class="legend-name">Plausible</span></div><span class="legend-wt">+0.5</span></div>
      <div class="legend-item"><div class="legend-left"><div class="legend-dot" style="background:#60a5fa"></div><span class="legend-name">Corroborated</span></div><span class="legend-wt">+0.5</span></div>
      <div class="legend-item"><div class="legend-left"><div class="legend-dot" style="background:#fb923c"></div><span class="legend-name">Overstated</span></div><span class="legend-wt">\u22120.5</span></div>
      <div class="legend-item"><div class="legend-left"><div class="legend-dot" style="background:#f43f5e"></div><span class="legend-name">Disputed</span></div><span class="legend-wt">\u22121.0</span></div>
      <div class="legend-item"><div class="legend-left"><div class="legend-dot" style="background:#f87171"></div><span class="legend-name">Not supported</span></div><span class="legend-wt">\u22121.5</span></div>
      <div class="legend-item"><div class="legend-left"><div class="legend-dot" style="background:var(--dim)"></div><span class="legend-name">Not verifiable</span></div><span class="legend-wt">excluded</span></div>
      <div class="legend-item" style="border-bottom:none"><div class="legend-left"><div class="legend-dot" style="background:var(--dim)"></div><span class="legend-name">Opinion</span></div><span class="legend-wt">excluded</span></div>
    </div>
  </div>
  <div class="vn">Verdicts from two genuinely independent sources satisfy the independence rule. Wire service reprints and accurately reported quotes from named speakers are excluded from outlet scoring.</div>
  <div style="font-size:11px;color:var(--dim);line-height:1.5;text-align:center">Methodology v1.5 &nbsp;&middot;&nbsp; Automated &nbsp;&middot;&nbsp; Auditable<br><span style="color:var(--violet);opacity:.7">verumsignal.com/methodology</span></div>
</aside>
</div>

<script>
function toggleAll(btn) {{
  const rows = document.querySelectorAll('.claim-row');
  const anyOpen = [...rows].some(r => r.classList.contains('open'));
  rows.forEach(r => anyOpen ? r.classList.remove('open') : r.classList.add('open'));
  btn.textContent = anyOpen ? 'Expand all' : 'Collapse all';
}}
function copyUrl() {{
  navigator.clipboard.writeText(window.location.href).then(() => {{
    const btn = document.querySelector('.share-btn');
    btn.textContent = 'Copied \u2713';
    btn.style.color = '#4ade80';
    setTimeout(() => {{ btn.textContent = 'Share report \u2197'; btn.style.color = ''; }}, 2000);
  }});
}}
</script>
</body>
</html>"""

    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    app.run(host='0.0.0.0', port=port, debug=False)
