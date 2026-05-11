import os, psycopg2, time
from dotenv import load_dotenv
if os.path.exists(".env"):
    load_dotenv(override=False)

TOP_OUTLETS = [
    "bbc.com", "bbc.co.uk", "npr.org", "reuters.com", "apnews.com",
    "nytimes.com", "washingtonpost.com", "theguardian.com", "foxnews.com",
    "cnn.com", "aljazeera.com", "bloomberg.com", "wsj.com", "axios.com",
    "thehill.com", "politico.com", "cbsnews.com", "nbcnews.com",
    "abcnews.go.com", "msnbc.com"
]

def get_connection():
    return psycopg2.connect(
        dbname=os.environ.get("DB_NAME", "railway"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD"),
        host=os.environ.get("DB_HOST", "shinkansen.proxy.rlwy.net"),
        port=os.environ.get("DB_PORT", "35370")
    )

def is_top_outlet(source_name):
    if not source_name:
        return False
    source_lower = source_name.lower()
    for outlet in TOP_OUTLETS:
        if outlet in source_lower:
            return True
    return False

def pre_verify_articles(limit=50):
    """
    Find recently fetched articles from top outlets that have
    claims extracted but not yet verified. Verify their claims
    using the standard verdict engine so reports load instantly
    when users paste URLs.
    """
    print(f"Pre-verification: checking for unverified top-outlet articles...")
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT a.id, a.title, a.source_name, a.url
        FROM articles a
        WHERE a.claims_verified = FALSE
        AND a.fetched_at > NOW() - INTERVAL '24 hours'
        AND EXISTS (
            SELECT 1 FROM claims c
            WHERE c.article_id = a.id
            AND c.verdict IS NULL
        )
        ORDER BY a.fetched_at DESC
        LIMIT %s
    """, (limit,))

    articles = cur.fetchall()
    top_articles = [(aid, title, source, url) for aid, title, source, url in articles if is_top_outlet(source)]
    print(f"Found {len(articles)} unverified articles, {len(top_articles)} from top outlets")

    if not top_articles:
        print("No top outlet articles need pre-verification")
        cur.close()
        conn.close()
        return 0

    from verdict_engine import analyse_claim, update_source_profile, calculate_reliability_score, get_connection as ve_conn

    verified_count = 0
    for art_id, title, source_name, url in top_articles:
        try:
            cur.execute("""
                SELECT id, claim_text, speaker, claim_type
                FROM claims
                WHERE article_id = %s
                AND verdict IS NULL
            """, (art_id,))
            claims = cur.fetchall()

            if not claims:
                cur.execute("UPDATE articles SET claims_verified=TRUE, verified_at=NOW() WHERE id=%s", (art_id,))
                conn.commit()
                continue

            print(f"Pre-verifying {len(claims)} claims from {source_name}: {title[:50]}...")
            article_done = True

            for claim_id, claim_text, speaker, claim_type in claims:
                result = analyse_claim(claim_text, speaker, claim_type, title, source_name, cur)
                if result:
                    verdict = result.get("verdict", "not_verifiable")
                    confidence = min(result.get("confidence_score", 1), 3)
                    summary = result.get("verdict_summary", "")
                    analysis = result.get("full_analysis", "")
                    sources = result.get("sources_used", "")
                    cur.execute("""
                        UPDATE claims SET verdict=%s, confidence_score=%s,
                        verdict_summary=%s, full_analysis=%s, sources_used=%s,
                        last_checked=NOW() WHERE id=%s
                    """, (verdict, confidence, summary, analysis, sources, claim_id))
                    update_source_profile(cur, source_name, verdict)
                    calculate_reliability_score(cur, source_name, claim_id)
                    conn.commit()
                    verified_count += 1
                    time.sleep(0.5)
                else:
                    article_done = False

            if article_done:
                cur.execute("UPDATE articles SET claims_verified=TRUE, verified_at=NOW() WHERE id=%s", (art_id,))
                conn.commit()
                print(f"  Article pre-verified: {source_name}")

        except Exception as e:
            print(f"  Error pre-verifying article {art_id}: {str(e)[:60]}")
            conn.rollback()
            continue

    cur.close()
    conn.close()
    print(f"Pre-verification complete. {verified_count} claims verified.")
    return verified_count

if __name__ == "__main__":
    pre_verify_articles(limit=50)
