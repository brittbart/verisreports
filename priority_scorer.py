import os
import re
import psycopg2
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)

HIGH_PRIORITY_KEYWORDS = [
    'million', 'billion', 'trillion', '%', 'percent',
    'sec', 'fbi', 'doj', 'congress', 'senate', 'federal',
    'bitcoin', 'ethereum', 'binance', 'coinbase',
    'arrested', 'charged', 'filed', 'announced', 'confirmed',
    'launched', 'banned', 'approved', 'rejected', 'hacked',
    'exploited', 'drained', 'stolen', 'lost',
    'first', 'largest', 'biggest', 'record', 'never',
    'always', 'all', 'every', 'proven', 'confirmed'
]

HIGH_PRIORITY_TYPES = ['statistical', 'legal', 'scientific']
MEDIUM_PRIORITY_TYPES = ['factual', 'causal']

def get_connection():
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432')
    )

def calculate_priority(claim_text, claim_type, source_name):
    score = 0
    claim_lower = claim_text.lower()

    if claim_type in HIGH_PRIORITY_TYPES:
        score += 30
    elif claim_type in MEDIUM_PRIORITY_TYPES:
        score += 20
    else:
        score += 5

    keyword_hits = sum(
        1 for kw in HIGH_PRIORITY_KEYWORDS
        if kw in claim_lower
    )
    score += min(keyword_hits * 5, 30)

    numbers = re.findall(r'\d+', claim_text)
    if numbers:
        score += 10

    if '$' in claim_text or 'usd' in claim_lower:
        score += 10

    words = claim_text.split()
    capitalised = sum(
        1 for w in words[1:]
        if w and w[0].isupper() and len(w) > 2
    )
    score += min(capitalised * 3, 10)

    return min(score, 100)

def score_all_claims():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.id, c.claim_text, c.claim_type, a.source_name
        FROM claims c
        JOIN articles a ON c.article_id = a.id
        WHERE c.priority_score = 0;
    """)

    claims = cursor.fetchall()
    print(f"Scoring priority for {len(claims)} claims...")

    for claim_id, claim_text, claim_type, source_name in claims:
        score = calculate_priority(
            claim_text, claim_type, source_name
        )
        cursor.execute(
            "UPDATE claims SET priority_score = %s WHERE id = %s;",
            (score, claim_id)
        )

    conn.commit()

    cursor.execute("""
        SELECT
            COUNT(*) FILTER (WHERE priority_score >= 70) as high,
            COUNT(*) FILTER (WHERE priority_score >= 40
                            AND priority_score < 70) as medium,
            COUNT(*) FILTER (WHERE priority_score < 40) as low
        FROM claims;
    """)

    high, medium, low = cursor.fetchone()

    cursor.execute("""
        SELECT c.claim_text, c.claim_type,
               a.source_name, c.priority_score
        FROM claims c
        JOIN articles a ON c.article_id = a.id
        ORDER BY c.priority_score DESC
        LIMIT 5;
    """)

    top_claims = cursor.fetchall()
    cursor.close()
    conn.close()

    print(f"Priority distribution:")
    print(f"  High (70+):     {high}")
    print(f"  Medium (40-69): {medium}")
    print(f"  Low (<40):      {low}")

    print(f"Top 5 claims:")
    for i, (text, ctype, source, score) in enumerate(top_claims):
        print(f"  [{i+1}] {score}/100 | {ctype} | {source}")
        print(f"       {text[:70]}...")

if __name__ == "__main__":
    score_all_claims()