import os
import json
import psycopg2
from dotenv import load_dotenv
from datetime import datetime

if os.path.exists(".env"):
    load_dotenv(override=False)

def get_connection():
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432')
    )

def load_articles(articles_file, claims_file):
    """Load articles and claims from JSON files into the database."""
    
    print(f"Loading from {articles_file} and {claims_file}...")
    
    # Load the JSON files
    with open(articles_file, 'r') as f:
        articles = json.load(f)
    
    with open(claims_file, 'r') as f:
        claims_data = json.load(f)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    articles_added = 0
    claims_added = 0
    sources_added = 0
    
    # Build a lookup of claims by article index
    claims_by_index = {}
    for item in claims_data:
        idx = item.get('article_id', 0)
        claims_by_index[idx] = item.get('claims', [])
    
    print(f"Processing {len(articles)} articles...")
    
    for i, article in enumerate(articles):
        title = article.get('title', 'No title')
        source_name = article.get('source', {}).get('name', 'Unknown')
        url = article.get('url', '')
        if 'news.google.com' in url:
            try:
                import requests as _r
                resp = _r.get(url, timeout=5, allow_redirects=True,
                              headers={'User-Agent':'Mozilla/5.0'})
                url = resp.url
            except Exception:
                pass
        published = article.get('publishedAt')
        description = article.get('description', '')
        content = article.get('content', '')
        
        # Insert or update source profile
        cursor.execute("""
            INSERT INTO sources (name, last_analysed)
            VALUES (%s, NOW())
            ON CONFLICT (name) DO UPDATE
            SET last_analysed = NOW()
            RETURNING id;
        """, (source_name,))
        
        # Insert article
        try:
            cursor.execute("""
                INSERT INTO articles 
                    (title, source_name, url, published_at, 
                     description, content, processed)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                RETURNING id;
            """, (title, source_name, url, published,
                  description, content, True))
            
            result = cursor.fetchone()
            
            if result:
                article_db_id = result[0]
                articles_added += 1
                
                # Insert claims for this article
                article_claims = claims_by_index.get(i + 1, [])
                
                for claim in article_claims:
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
                    
        except Exception as e:
            print(f"  Error on article {i+1}: {str(e)}")
            conn.rollback()
            continue
    
    conn.commit()
    cursor.close()
    conn.close()
    
    print(f"\n✓ Articles added to database: {articles_added}")
    print(f"✓ Claims added to database: {claims_added}")
    print(f"✓ Sources tracked: {len(set(a.get('source', {}).get('name', '') for a in articles))}")

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