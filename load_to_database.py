import os
import json
import psycopg2
from dotenv import load_dotenv
from datetime import datetime
from google_news_parser import resolve_publisher
if os.path.exists(".env"):
    load_dotenv(override=False)

def get_connection():
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'),
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        application_name='veris-load',
    )
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 180000")
    conn.commit()
    return conn


def _normalize_url(url):
    """Normalize URL for deduplication: lowercase scheme+host, strip trailing slash,
    strip common tracking params, force https."""
    from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl
    try:
        p = urlparse(url.strip())
        scheme = 'https'
        netloc = p.netloc.lower()
        path = p.path.rstrip('/')  or '/'
        # Strip common tracking params
        STRIP_PARAMS = {'utm_source','utm_medium','utm_campaign','utm_term',
                        'utm_content','ref','source','fbclid','gclid'}
        params = [(k,v) for k,v in parse_qsl(p.query) if k.lower() not in STRIP_PARAMS]
        query = urlencode(params)
        return urlunparse((scheme, netloc, path, p.params, query, ''))
    except Exception:
        return url

def load_single_article_with_claims(cursor, article, claims_list):
    """Day 24 refactor: shared per-article insert logic."""
    title = article.get('title', 'No title')
    raw_source = article.get('source', {}).get('name', 'Unknown')
    url = _normalize_url(article.get('url', ''))
    from urllib.parse import urlparse as _up
    _h = _up(url).hostname or ''
    _h = _h.replace('www.', '').replace('feeds.', '').replace('rss.', '')
    _AGGREGATORS = {'news.google.com', 'news.yahoo.com', 'msn.com'}
    if _h in _AGGREGATORS and raw_source and raw_source != 'Unknown' and raw_source not in _AGGREGATORS:
        source_name = raw_source
    else:
        source_name = _h if _h else raw_source
    if source_name in _AGGREGATORS:
        _recovered = resolve_publisher(url, title)
        if _recovered:
            source_name = _recovered
    _raw_pub = article.get('publishedAt')
    published = _raw_pub if (_raw_pub and str(_raw_pub).strip()) else None
    description = article.get('description', '')
    content = article.get('content', '')
    cursor.execute("""
        INSERT INTO sources (name, last_analysed)
        VALUES (%s, NOW())
        ON CONFLICT (name) DO UPDATE
        SET last_analysed = NOW()
        RETURNING id;
    """, (source_name,))
    cursor.execute("SAVEPOINT article_sp;")
    article_db_id = None
    claims_added = 0
    inserted = False
    try:
        cursor.execute("""
            INSERT INTO articles
                (title, source_name, url, published_at,
                 description, content, processed)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
            RETURNING id;
        """, (title, source_name, url, published, description, content, True))
        result = cursor.fetchone()
        if result:
            article_db_id = result[0]
            inserted = True
            for claim in claims_list:
                cursor.execute("""
                    INSERT INTO claims
                        (article_id, claim_text, speaker,
                         claim_type, why_checkworthy,
                         claim_origin, attribution_context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, (
                    article_db_id,
                    claim.get('claim_text', ''),
                    claim.get('speaker', ''),
                    claim.get('claim_type', ''),
                    claim.get('why_checkworthy', ''),
                    claim.get('claim_origin', 'outlet_claim'),
                    claim.get('attribution_context', '')
                ))
                claims_added += 1
        cursor.execute("RELEASE SAVEPOINT article_sp;")
    except Exception as e:
        print(f"  Error inserting article {title[:50]}: {str(e)}")
        cursor.execute("ROLLBACK TO SAVEPOINT article_sp;")
        return {'inserted': False, 'article_db_id': None, 'claims_added': 0, 'source_name': source_name}
    return {'inserted': inserted, 'article_db_id': article_db_id, 'claims_added': claims_added, 'source_name': source_name}


def add_claims_to_existing_article(cursor, article_db_id, claims_list):
    """Day 24: direct-DB extract path — article already exists, just add claims."""
    claims_added = 0
    for claim in claims_list:
        cursor.execute("""
            INSERT INTO claims
                (article_id, claim_text, speaker,
                 claim_type, why_checkworthy,
                 claim_origin, attribution_context)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
        """, (
            article_db_id,
            claim.get('claim_text', ''),
            claim.get('speaker', ''),
            claim.get('claim_type', ''),
            claim.get('why_checkworthy', ''),
            claim.get('claim_origin', 'outlet_claim'),
            claim.get('attribution_context', '')
        ))
        claims_added += 1
    return claims_added


def load_articles(articles_file, claims_file):
    """Load articles and claims from JSON files into the database."""
    print(f"Loading from {articles_file} and {claims_file}...")
    with open(articles_file, 'r') as f:
        articles = json.load(f)
    with open(claims_file, 'r') as f:
        claims_data = json.load(f)
    conn = get_connection()
    cursor = conn.cursor()
    articles_added = 0
    claims_added = 0
    claims_by_index = {}
    for item in claims_data:
        idx = item.get('article_id', 0)
        claims_by_index[idx] = item.get('claims', [])
    print(f"Processing {len(articles)} articles...")
    for i, article in enumerate(articles):
        article_claims = claims_by_index.get(i + 1, [])
        result = load_single_article_with_claims(cursor=cursor, article=article, claims_list=article_claims)
        if result['inserted']:
            articles_added += 1
            claims_added += result['claims_added']
        if (i + 1) % 25 == 0:
            conn.commit()
            print(f"  [commit] {i+1} articles persisted", flush=True)
    conn.commit()
    cursor.close()
    conn.close()
    print(f"\n\u2713 Articles added to database: {articles_added}")
    print(f"\u2713 Claims added to database: {claims_added}")
    print(f"\u2713 Sources tracked: {len(set(a.get('source', {}).get('name', '') for a in articles))}")


def show_database_summary():
    """Show a quick summary of what's in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM articles;")
    article_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM claims;")
    claim_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM sources;")
    source_count = cursor.fetchone()[0]
    cursor.execute("""
        SELECT source_name, COUNT(*) as article_count
        FROM articles
        GROUP BY source_name
        ORDER BY article_count DESC
        LIMIT 5;
    """)
    top_sources = cursor.fetchall()
    cursor.execute("""
        SELECT claim_type, COUNT(*) as count
        FROM claims
        GROUP BY claim_type
        ORDER BY count DESC;
    """)
    claim_types = cursor.fetchall()
    cursor.close()
    conn.close()
    print("\n========== VERIS DATABASE SUMMARY ==========")
    print(f"Total articles:  {article_count}")
    print(f"Total claims:    {claim_count}")
    print(f"Total sources:   {source_count}")
    print("\nTop sources by article count:")
    for source, count in top_sources:
        print(f"  {source}: {count} articles")
    print("\nClaims by type:")
    for claim_type, count in claim_types:
        print(f"  {claim_type}: {count} claims")
    print("============================================")


if __name__ == "__main__":
    today = datetime.now().strftime('%Y-%m-%d')
    articles_file = f"articles_{today}.json"
    claims_file = f"claims_{today}.json"
    if not os.path.exists(articles_file):
        print(f"Articles file not found: {articles_file}")
    elif not os.path.exists(claims_file):
        print(f"Claims file not found: {claims_file}")
    else:
        load_articles(articles_file, claims_file)
        show_database_summary()
