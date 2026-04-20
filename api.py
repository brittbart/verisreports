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
    host = os.environ.get('DB_HOST') or 'shinkansen.proxy.rlwy.net'
    port = os.environ.get('DB_PORT') or '35370'
    name = os.environ.get('DB_NAME') or 'railway'
    user = os.environ.get('DB_USER') or 'postgres'
    password = os.environ.get('DB_PASSWORD') or 'ymBrWvBvPDNRHDkojqPwhPvzLZTRRacw'
    print(f"DEBUG: connecting to {host}:{port}")
    return psycopg2.connect(
        dbname=name,
        user=user,
        password=password,
        host=host,
        port=port
    )
@app.route('/api/source', methods=['GET'])
def get_source():
    domain = request.args.get('domain', '')
    if not domain:
        return jsonify({'error': 'domain required'}), 400
    core = domain.replace('www.', '').split('.')[0]
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
    return jsonify({
        'status': 'ok',
        'version': '2.0',
        'db_host': os.environ.get('DB_HOST', 'NOT SET'),
        'db_port': os.environ.get('DB_PORT', 'NOT SET')
    })

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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)# Railway deployment Mon Apr 20 15:45:41 MDT 2026
