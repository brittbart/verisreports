from flask import Flask, jsonify, request
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
            SUM(CASE WHEN verdict = 'verified' THEN 1 ELSE 0 END),
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
        verified = row[1] or 0
        disputed = row[2] or 0
        false_count = row[3] or 0
        overstated = row[4] or 0
        score = round((verified / total) * 100) if total > 0 else 0
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
            'verified': verified,
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
        verified_count     = sum(1 for c in claims if c[5] == 'verified')
        plausible_count    = sum(1 for c in claims if c[5] == 'plausible')
        overstated_count   = sum(1 for c in claims if c[5] == 'overstated')
        disputed_count     = sum(1 for c in claims if c[5] == 'disputed')
        not_supported_count= sum(1 for c in claims if c[5] == 'not_supported')
        opinion_count      = sum(1 for c in claims if c[5] == 'opinion')
        unverified_count   = sum(1 for c in claims if c[5] is None)

        # Weighted scoring — opinion, not_verifiable, and unverified excluded.
        # Consistent with source reliability scoring formula.
        WEIGHTS = {
            'verified':      1.0,
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
            normalised = (weighted_sum / scoreable + 1.5) / 3.0
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
                'verified': verified_count,
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
        return jsonify({"status":"received","dispute_id":dispute_id,"domain":domain,"submitted_at":submitted_at.isoformat(),"message":"Your dispute has been logged and will appear publicly on the Verum Signal leaderboard. All disputes are reviewed within 14 days. If a verdict is found incorrect it will be re-verified and updated."})
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
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)# Railway deployment Mon Apr 20 15:45:41 MDT 2026
