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
client = anthropic.Anthropic(api_key=anthropic_key)


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
    prompt = f"""You are the claim detection engine for Veris, an independent fact-checking tool.

Read the following article and identify the top 3 most check-worthy factual claims. 

A check-worthy claim is:
- A specific, verifiable factual statement (not an opinion)
- Something that could be true or false and can be checked against evidence
- Significant enough that if wrong, it would mislead the reader
- Includes specific numbers, statistics, names, dates, or events when possible

For each claim return:
1. The exact claim text (quote directly from the article where possible)
2. Who made the claim (speaker/source)
3. What type of claim it is (factual, statistical, causal, legal, scientific)
4. Why it is check-worthy
5. Whether this is an outlet_claim (the outlet itself is asserting this) or attributed_claim (the outlet is reporting that someone else said this)
6. If attributed, the exact attribution context e.g. "Trump said", "according to the Pentagon", "Iran's state media reported"

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
      "claim_origin": "outlet_claim or attributed_claim",
      "attribution_context": "if attributed, quote the exact words used to attribute it e.g. 'Trump said' or 'according to the White House' — leave blank if outlet_claim"
    }}
  ]
}}

Return only the JSON, no other text."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
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
        return deduplicate_claims(raw_claims)
        
    except Exception as e:
        print(f"    Error extracting claims: {str(e)}")
        return []

def process_articles(input_file, limit=50):
    """Process all articles in a JSON file and extract claims."""
    print(f"Loading articles from {input_file}...")
    with open(input_file, 'r') as f:
        articles = json.load(f)
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
