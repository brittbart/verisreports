import os
import json
import anthropic
from dotenv import load_dotenv
from datetime import datetime
# Load API keys
if os.path.exists(".env"):
    load_dotenv(override=False)
anthropic_key = os.getenv('ANTHROPIC_API_KEY')

# Connect to Anthropic
# Patch 13: explicit timeout prevents indefinite block on wedged API connection
client = anthropic.Anthropic(api_key=anthropic_key, timeout=120.0)


def deduplicate_claims(claims, threshold=0.6):
    kept = []
    for claim in claims:
        claim_text = claim.get("claim_text", "")
        is_duplicate = False
        for i, existing in enumerate(kept):
            existing_text = existing.get("claim_text", "")
            a_words = set(claim_text.lower().split())
            b_words = set(existing_text.lower().split())
            if not a_words or not b_words:
                continue
            intersection = a_words & b_words
            union = a_words | b_words
            score = len(intersection) / len(union)
            if score >= threshold:
                is_duplicate = True
                if len(claim_text) > len(existing_text):
                    kept[i] = claim
                break
        if not is_duplicate:
            kept.append(claim)
    return kept

def extract_claims_from_article(article):
    """Send an article to Claude and extract its top checkable claims."""
    
    title = article.get('title', 'No title')
    description = article.get('description', '')
    content = article.get('content', '')
    source = article.get('source', {}).get('name', 'Unknown')
    url = article.get('url', '')
    published = article.get('publishedAt', '')
    
    # Build the article text
    article_text = f"""
Title: {title}
Source: {source}
Published: {published}
URL: {url}

Description: {description}

Content: {content}
"""
    
    # The prompt that tells Claude what to do
    prompt = f"""You are the claim detection engine for Verum Signal, an independent claim analysis platform.

Read the following article and identify up to 7 of the most check-worthy factual claims.

A check-worthy claim is:
- A specific factual assertion that could be checked against evidence
- Something that, if wrong, would meaningfully mislead the reader
- Often (not always) includes specific numbers, statistics, names, dates, or events

IMPORTANT: Articles from any genre — opinion pieces, analysis, news, blogs, columns — can contain factual claims worth checking. Do not skip an article because its overall genre is opinion or commentary. Look for the embedded factual assertions within. An opinion piece that says "Tesla's stock dropped 40% last quarter and Musk fired half the legal team" contains two extractable factual claims even though the surrounding text is opinion.

HEADLINE PRIORITY: Pay particular attention to claims made in the headline. Headlines are where factual distortion most commonly occurs. A headline claim that overstates, mischaracterizes, or contradicts the article body is highly check-worthy. If the headline makes a specific factual assertion, extract it as a claim even if the body qualifies or contradicts it — that gap is exactly what needs verification.

OVERSTATED FRAMING: A claim is check-worthy even when it is technically accurate but omits context that would materially change the reader's understanding. "Senator X voted against the crime bill" may be technically accurate but omit that the bill was a procedural vote on an unrelated amendment. Extract such claims — the verification step will assess whether the framing is overstated.

If you find fewer than 7 truly check-worthy claims, return only what you find. Do not pad. Returning 0 claims is acceptable when an article genuinely contains no concrete factual assertions (e.g. pure aesthetic review with no facts, or speculation without specifics).

For each claim return:
1. The exact claim text (quote directly from the article where possible)
2. Who made the claim (speaker/source)
3. What type of claim it is (factual, statistical, causal, legal, scientific)
4. Why it is check-worthy
5. Whether this is an outlet_claim (the outlet itself is asserting this) or attributed_claim (the outlet is reporting that someone else said this)
6. If attributed, the exact attribution context e.g. "Trump said", "according to the Pentagon", "Iran's state media reported"

CLAIM QUALITY STANDARDS — apply strictly:
A claim is only check-worthy if it contains a SPECIFIC, VERIFIABLE FACTUAL ASSERTION. All of the following must be EXCLUDED:

- BIOGRAPHICAL FACTS: Do not extract claims that merely confirm someone's role, title, or identity (e.g. "Justice Surya Kant is the Chief Justice of India", "Elon Musk is the CEO of Tesla", "Senator Warren represents Massachusetts"). These are reference facts, not claims worth verifying.
- REDUNDANT CLAIMS: Do not extract the same assertion twice. If two sentences say the same thing in different words, extract only one — the more specific version.
- OPINION OR CHARACTERIZATION: Do not extract value judgments, characterizations, or normative statements as factual claims, even when attributed to a named speaker (e.g. "X called Y a crime against human dignity" — the characterization is opinion, not a factual claim).
- VAGUE ASSERTIONS: Do not extract claims without specific figures, dates, named events, named documents, or concrete outcomes. "The economy is doing badly" is not a claim. "GDP contracted 1.2% in Q3" is a claim.
- PROCESS STATEMENTS: Do not extract claims that merely describe an article's subject or event (e.g. "The article reports on remarks made at a conference").
- PERSONAL HEALTH DISCLOSURES: Do not extract claims about a speaker's personal health, medical procedures, or physical condition (e.g. "Senator X said he underwent surgery", "X said she is recovering well").
- RECALLED OR SECONDHAND QUOTES: Do not extract claims that are one person recalling what another person told them (e.g. "Wood recalls being told by Laffer that..."). These are not directly verifiable.
- VAGUE SENTIMENT: Do not extract claims that express general sentiment without specific content (e.g. "X said there was some pretty good news", "X expressed optimism about the talks").

A strong attributed claim looks like: "Senator X said the bill would cost $2 trillion over 10 years" or "The Pentagon confirmed 3,000 troops were deployed to X on [date]." A weak attributed claim looks like: "X said Y is important" or "X described the situation as serious."

CRITICAL CONSTRAINTS:
- claim_origin MUST be exactly "outlet_claim" OR exactly "attributed_claim". Never null. Never empty. Never any other value.
- If the claim has a clear speaker other than the outlet (e.g. "Trump said", "according to the Pentagon"), use "attributed_claim".
- Otherwise use "outlet_claim".
- Returning 0 claims is correct when an article contains no specific, verifiable factual assertions meeting the above standards.

Article:
{article_text}

Respond in this exact JSON format:
{{
  "claims": [
    {{
      "claim_text": "exact claim here",
      "speaker": "who made the claim",
      "claim_type": "type of claim",
      "why_checkworthy": "brief reason",
      "claim_origin": "outlet_claim",
      "attribution_context": "if attributed, quote the exact words used to attribute it e.g. 'Trump said' or 'according to the White House' — leave blank if outlet_claim"
    }}
  ]
}}

Return only the JSON, no other text."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,  # Sized for up to 7 claims with full schema. Was 1000 (truncated mid-JSON for >3 claims).
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        from token_logging import log_usage; log_usage('extract', message)
        
        # Parse the response
        response_text = message.content[0].text.strip()
        
        # Find the JSON block even if there's text around it
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        
        if start == -1 or end == 0:
            print(f"    No JSON found in response")
            return []
            
        json_str = response_text[start:end]
        claims_data = json.loads(json_str)
        raw_claims = claims_data.get('claims', [])

        # Validate and normalize claim_origin — Sonnet sometimes returns null
        VALID_ORIGINS = {'outlet_claim', 'attributed_claim'}
        for claim in raw_claims:
            origin = claim.get('claim_origin')
            if origin not in VALID_ORIGINS:
                # Deterministic fallback: if attribution_context is non-empty, it's attributed
                attribution = (claim.get('attribution_context') or '').strip()
                if attribution:
                    claim['claim_origin'] = 'attributed_claim'
                else:
                    claim['claim_origin'] = 'outlet_claim'
                print(f"    [origin-fix] LLM returned {origin!r}, corrected to {claim['claim_origin']!r}")

        return deduplicate_claims(raw_claims)
        
    except Exception as e:
        print(f"    Error extracting claims: {str(e)}")
        return []

def process_articles(input_file, limit=50):
    """Process all articles in a JSON file and extract claims."""
    import random
    print(f"Loading articles from {input_file}...")
    with open(input_file, 'r') as f:
        articles = json.load(f)
    # Shuffle so the limit doesn't always hit the same outlets first.
    # Feed order in feeds.py groups outlets, which would otherwise bias
    # which outlets get verified each cycle.
    random.shuffle(articles)
    if limit:
        articles = articles[:limit]
    print(f"Processing {len(articles)} articles (limit={limit})...")
    results = []
    for i, article in enumerate(articles):
        title = article.get('title', 'No title')
        source = article.get('source', {}).get('name', 'Unknown')
        print(f"[{i+1}/{len(articles)}] {source}: {title[:60]}...")
        claims = extract_claims_from_article(article)
        if claims:
            print(f"    Found {len(claims)} claims")
            result = {
                "article_id": i + 1,
                "title": title,
                "source": source,
                "url": article.get('url', ''),
                "published": article.get('publishedAt', ''),
                "claims": claims
            }
            results.append(result)
        else:
            print(f"    No claims extracted")
    today = datetime.now().strftime('%Y-%m-%d')
    output_file = f"claims_{today}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n✓ Processed {len(results)} articles")
    print(f"✓ Saved to {output_file}")
    return results


def process_articles_from_db(limit=50, min_content_chars=500, days_window=30):
    """Day 24 — Patch 27v2: DB-driven extraction queue with DIRECT DB writes.

    Reads unextracted articles from the DB, extracts claims, writes claims
    DIRECTLY to the DB using the article's actual DB id. No JSON intermediate.

    This fixes the Patch 27 bug where claims were written to JSON keyed by
    enumerate index (i+1), then load_to_database.py looked them up by that
    same i+1 against a DIFFERENT article list — causing claims to be attached
    to the wrong article.
    """
    import os
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from load_to_database import add_claims_to_existing_article

    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        application_name="veris-extract-db",
    )

    select_sql = """
        SELECT id, title, source_name, url, published_at,
               description, content
        FROM articles
        WHERE content IS NOT NULL
          AND LENGTH(content) >= %s
          AND fetched_at > NOW() - (%s || ' days')::interval
          AND source_name != 'news.google.com'
          AND id NOT IN (
              SELECT DISTINCT article_id
              FROM claims
              WHERE article_id IS NOT NULL
          )
        ORDER BY fetched_at DESC
        LIMIT %s
    """

    print(f"Querying DB for unextracted articles "
          f"(limit={limit}, min_chars={min_content_chars}, "
          f"days={days_window})...")

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(select_sql, (min_content_chars, str(days_window), limit))
            rows = list(cur.fetchall())
    except Exception as e:
        conn.close()
        raise

    print(f"Pulled {len(rows)} articles from DB.")

    if not rows:
        print("No unextracted articles match the filter. Nothing to do.")
        conn.close()
        return []

    # Parallel extraction: run Anthropic API calls concurrently, write serially.
    # Each worker thread calls extract_claims_from_article() independently.
    # DB writes happen on the main thread using per-article connections to
    # avoid connection sharing across threads.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    MAX_WORKERS = 5  # Enough to cut wall time ~3-4x without hammering the API

    def _extract_one(row, idx, total):
        """Run in a thread. Returns (row, claims, idx)."""
        article_dict = {
            "title": row["title"],
            "url": row["url"],
            "description": row["description"] or "",
            "content": row["content"],
            "publishedAt": (
                row["published_at"].isoformat()
                if row["published_at"] else None
            ),
            "source": {"name": row["source_name"]},
        }
        title_preview = (row["title"] or "")[:60]
        print(f"[{idx}/{total}] {row['source_name']}: {title_preview}...")
        try:
            claims = extract_claims_from_article(article_dict)
        except Exception as e:
            print(f"    Error extracting claims: {e}")
            claims = []
        return row, claims, idx

    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_extract_one, row, i + 1, len(rows)): row
            for i, row in enumerate(rows)
        }
        for future in as_completed(futures):
            row, claims, idx = future.result()
            if claims:
                print(f"    Found {len(claims)} claims — writing to DB with article_id={row['id']}")
                # Each DB write uses the shared connection on the main thread.
                # conn is not shared across threads — only used here.
                try:
                    write_cur = conn.cursor()
                    added = add_claims_to_existing_article(
                        cursor=write_cur,
                        article_db_id=row["id"],
                        claims_list=claims,
                    )
                    conn.commit()
                    write_cur.close()
                    results.append({
                        "article_db_id": row["id"],
                        "source_name": row["source_name"],
                        "claims_added": added,
                    })
                except Exception as e:
                    print(f"    Error writing claims for article {row['id']}: {e}")
                    conn.rollback()
            else:
                print(f"    No claims extracted")

    conn.close()
    print(f"\n\u2713 Processed {len(results)} articles with claims")
    return results

# force rebuild Mon Apr 27 15:31:55 MDT 2026
