import os
import json
import psycopg2
import anthropic
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)
client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))


OPINION_SIGNALS = [
    "price prediction",
    "price target",
    "resistance level",
    "support level",
    "breakout from its",
    "stiff overhead resistance",
    "macro downtrend",
    "signaling a potential",
    "could re-accelerate",
    "mock draft",
    "is expected to",
    "is predicted to",
    "is projected to",
    "will likely",
    "should be",
    "must be",
    "deserves to",
]

def pre_filter_claim(claim_text):
    text = claim_text.lower()
    for signal in OPINION_SIGNALS:
        if signal in text:
            return "opinion"
    return "send_to_api"

def get_connection():
    return psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432')
    )

def check_database_first(cursor, claim_text):
    try:
        cursor.execute('''
            SELECT verdict, confidence_score, verdict_summary
            FROM claims
            WHERE verdict IS NOT NULL
            AND similarity(claim_text, %s) > 0.85
            AND last_checked > NOW() - INTERVAL '24 hours'
            ORDER BY confidence_score DESC
            LIMIT 1
        ''', (claim_text,))
        result = cursor.fetchone()
        if result:
            print(f"  -> Cache hit (24hr window)")
            return result
        cursor.execute('''
            SELECT verdict, confidence_score, verdict_summary
            FROM claims
            WHERE verdict IS NOT NULL
            AND similarity(claim_text, %s) > 0.6
            ORDER BY confidence_score DESC
            LIMIT 1
        ''', (claim_text,))
        return cursor.fetchone()
    except Exception:
        return None


def check_source_consensus(cursor, claim_text):
    try:
        cursor.execute('''
            SELECT verdict, COUNT(*) as count
            FROM claims
            WHERE verdict IS NOT NULL
            AND similarity(claim_text, %s) > 0.5
            GROUP BY verdict
            ORDER BY count DESC
            LIMIT 1
        ''', (claim_text,))
        return cursor.fetchone()
    except Exception:
        return None
    
def analyse_claim(claim_text, speaker, claim_type,
                  article_title, source_name, cursor=None):

    if cursor:
        db_result = check_database_first(cursor, claim_text)
        if db_result:
            verdict, confidence, summary = db_result
            print(f"  -> Database match: {verdict}")
            return {
                "verdict": verdict,
                "confidence_score": confidence,
                "verdict_summary": summary,
                "full_analysis": "Matched verified claim in Veris database.",
                "sources_used": "Veris internal database"
            }

        consensus = check_source_consensus(cursor, claim_text)
        if consensus and consensus[1] >= 5:
            verdict, count = consensus
            print(f"  -> Consensus: {verdict} ({count} sources)")
            return {
                "verdict": verdict,
                "confidence_score": 2,
                "verdict_summary": f"Corroborated across {count} sources.",
                "full_analysis": f"{count} sources agree on this verdict.",
                "sources_used": "Veris source consensus"
            }

    if pre_filter_claim(claim_text) == "opinion":
        print(f"  -> Pre-filter: opinion")
        return {"verdict":"opinion","confidence_score":1,"verdict_summary":"Prediction or editorial opinion.","full_analysis":"Pre-classified by local filter.","sources_used":"Local filter"}
    print(f"  -> Web search verification...")
    prompt = f"""You are the Veris fact-checking engine. Your job is to verify claims with rigorous, defensible standards. Use web search to find primary sources.

CLAIM: {claim_text}
SPEAKER: {speaker}
TYPE: {claim_type}
ARTICLE: {article_title}
SOURCE: {source_name}

VERIFICATION STANDARDS — read carefully before assigning a verdict:


INDEPENDENCE RULE:
Two sources are only independent if they obtained the information through different means. Multiple outlets repeating the same wire report = ONE source, not multiple. To call something verified you must find sources that independently confirmed the fact through different channels.

CONSENSUS EXCEPTION:
If 5 or more outlets are consistently reporting the same claim without contradiction, assign verified at confidence 2/3 even if you cannot confirm each outlet independently sourced the information. Widespread consistent reporting across multiple outlets is a strong signal of accuracy. If any credible outlet contradicts the claim, use disputed instead regardless of how many outlets agree.

VERDICT DEFINITIONS — apply strictly:

- verified: Confirmed by at least TWO genuinely independent sources from Tier 1 or above, each having obtained the information through different means. If you cannot find this, do NOT use verified.

- plausible: Consistent with available evidence but only confirmed by one credible source, or by multiple sources all citing the same original report. Use this when the claim seems likely true but true independence cannot be established.

- disputed: ANY credible source contradicts the claim, OR evidence is genuinely mixed. Do not default to plausible when evidence is mixed — use disputed. This verdict is underused and should be applied whenever you find meaningful contradiction.

- overstated: The core fact is real but the specific figure, scale, or characterisation is exaggerated or imprecise. Use when a claim is directionally correct but materially misleading in its specifics.

- not_supported: Positive evidence contradicts the claim, OR the claim makes specific assertions that authoritative sources explicitly refute.

- not_verifiable: The claim cannot be confirmed or denied because primary sources are unavailable, access is restricted, or the event is too recent. Use sparingly — exhaust search options first.

- opinion: A value judgement, prediction, or normative claim that cannot be true or false. Also use for analyst conclusions presented as facts.

CONFIDENCE SCORE — assign based on source quality, not just number of sources:
- 3: Verified by two or more genuinely independent sources with original reporting
- 2: Verified by one credible source with original reporting, or plausible based on consistent but non-independent reporting
- 1: Plausible or disputed, or claim is inherently difficult to verify

SEARCH INSTRUCTIONS:
1. Search for the specific claim first
2. Find the original source — who first reported this?
3. Find a second source that independently verified it (not just repeated it)
4. If you find any contradiction from a credible source, assign disputed
5. Note the quality of sources in your analysis

Return ONLY this JSON:
{{
  "verdict": "verdict here",
  "confidence_score": 1,
  "verdict_summary": "one sentence plain-language explanation",
  "full_analysis": "2-3 sentences explaining what you found, what sources you used, and why you assigned this verdict",
  "sources_used": "specific named sources and whether each independently confirmed the fact"
}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search"
                }
            ],
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        response_text = ""
        for block in message.content:
            if hasattr(block, "text"):
                response_text += block.text
        response_text = response_text.strip()
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        if start == -1 or end == 0:
            return None
        return json.loads(response_text[start:end])
    except Exception as e:
        print(f"    Error: {str(e)}")
        return None
    
def update_source_profile(cursor, source_name, verdict):
    if verdict == 'opinion':
        return
    field_map = {
        'verified':      'verified_count',
        'plausible':     'plausible_count',
        'disputed':      'disputed_count',
        'not_supported': 'disputed_count',
        'overstated':    'overstated_count',
        'not_verifiable':'not_verifiable_count',
    }
    field = field_map.get(verdict, 'not_verifiable_count')
    try:
        cursor.execute(f"""
            UPDATE sources
            SET total_claims_checked = total_claims_checked + 1,
                {field} = {field} + 1,
                last_analysed = NOW()
            WHERE name = %s;
        """, (source_name,))
    except Exception:
        pass


def calculate_reliability_score(cursor, source_name, trigger_claim_id=None):
    try:
        cursor.execute("""
            SELECT reliability_score FROM sources WHERE name = %s;
        """, (source_name,))
        existing = cursor.fetchone()
        old_score = existing[0] if existing else None

        cursor.execute("""
            SELECT
                SUM(CASE WHEN verdict = 'verified'   THEN 1.0
                         WHEN verdict = 'plausible'  THEN 0.5
                         WHEN verdict = 'overstated' THEN -0.5
                         WHEN verdict IN ('disputed','not_supported') THEN -1.0
                         ELSE 0 END) as weighted,
                COUNT(*) FILTER (
                    WHERE verdict NOT IN ('opinion','not_verifiable')
                ) as scoreable
            FROM claims c
            JOIN articles a ON c.article_id = a.id
            WHERE a.source_name = %s
            AND c.verdict IS NOT NULL
            AND (c.claim_origin = 'outlet_claim' OR c.claim_origin IS NULL)
            AND a.published_at IS NOT NULL
            AND a.published_at < NOW() - INTERVAL '6 hours';
        """, (source_name,))
        result = cursor.fetchone()
        if not result or not result[1] or result[1] == 0:
            return
        weighted = float(result[0] or 0)
        scoreable = result[1]
        normalised = (weighted / scoreable + 1.5) / 2.5
        numeric_score = round(min(max(normalised * 100, 0), 100))
        new_score = "High" if numeric_score >= 70 else "Medium" if numeric_score >= 40 else "Low"

        cursor.execute("""
            INSERT INTO sources (name, reliability_score, last_analysed)
            VALUES (%s, %s, NOW())
            ON CONFLICT (name) DO UPDATE
            SET reliability_score = %s, last_analysed = NOW();
        """, (source_name, new_score, new_score))

        if old_score != new_score:
            cursor.execute("""
                INSERT INTO score_history
                (source_name, old_score, new_score, new_verified, new_total, trigger_claim_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (source_name, old_score, new_score, verified, total, trigger_claim_id))

    except Exception as e:
        print(f"    Score update error: {str(e)}")

def run_verdict_engine(limit=10):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.claim_text, c.speaker, c.claim_type,
               a.title, a.source_name, c.priority_score
        FROM claims c
        JOIN articles a ON c.article_id = a.id
        WHERE c.verdict IS NULL
        AND c.priority_score >= 30
        ORDER BY c.priority_score DESC
        LIMIT %s;
    """, (limit,))
    claims = cursor.fetchall()
    if not claims:
        print("No high priority unverified claims found.")
        return
    print(f"Found {len(claims)} claims to analyse")
    verdicts_assigned = 0
    for i, (claim_id, claim_text, speaker, claim_type,
            article_title, source_name,
            priority_score) in enumerate(claims):
        print(f"[{i+1}/{len(claims)}] Priority: {priority_score}/100")
        print(f"  {claim_text[:70]}...")
        print(f"  Source: {source_name}")
        result = analyse_claim(
            claim_text, speaker, claim_type,
            article_title, source_name, cursor
        )
        if result:
            verdict = result.get('verdict', 'not_verifiable')
            confidence = min(result.get('confidence_score', 1), 3)
            summary = result.get('verdict_summary', '')
            analysis = result.get('full_analysis', '')
            sources = result.get('sources_used', '')
            cursor.execute("""
                UPDATE claims
                SET verdict = %s,
                    confidence_score = %s,
                    verdict_summary = %s,
                    full_analysis = %s,
                    sources_used = %s,
                    last_checked = NOW()
                WHERE id = %s;
            """, (verdict, confidence, summary,
                  analysis, sources, claim_id))
            update_source_profile(cursor, source_name, verdict)
            calculate_reliability_score(cursor, source_name)
            conn.commit()
            verdicts_assigned += 1
            print(f"  v {verdict.upper()} (confidence: {confidence}/3)")
            print(f"  {summary}\n")
        else:
            print(f"  x Skipping\n")
    cursor.close()
    conn.close()
    print(f"Verdicts assigned: {verdicts_assigned}")


if __name__ == "__main__":
    run_verdict_engine(limit=10)

def run_batch_verdict_engine(limit=500):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.claim_text, c.speaker, c.claim_type,
               a.title, a.source_name, c.priority_score
        FROM claims c
        JOIN articles a ON c.article_id = a.id
        WHERE c.verdict IS NULL
        AND c.priority_score >= 30
        ORDER BY c.priority_score DESC
        LIMIT %s;
    """, (limit,))
    claims = cursor.fetchall()
    if not claims:
        print("No claims to process.")
        cursor.close()
        conn.close()
        return
    print(f"Preparing batch of {len(claims)} claims...")
    requests = []
    claim_ids = []
    for claim_id, claim_text, speaker, claim_type, article_title, source_name, priority_score in claims:
        if cursor:
            db_result = check_database_first(cursor, claim_text)
            if db_result:
                verdict, confidence, summary = db_result
                cursor.execute("""
                    UPDATE claims SET verdict=%s, confidence_score=%s,
                    verdict_summary=%s, full_analysis=%s, sources_used=%s,
                    last_checked=NOW() WHERE id=%s
                """, (verdict, confidence, summary,
                      "Matched verified claim in Veris database.",
                      "Veris internal database", claim_id))
                update_source_profile(cursor, source_name, verdict)
                calculate_reliability_score(cursor, source_name, claim_id)
                conn.commit()
                print(f"  -> Database match: {verdict} (claim {claim_id})")
                continue
            consensus = check_source_consensus(cursor, claim_text)
            if consensus and consensus[1] >= 5:
                verdict, count = consensus
                cursor.execute("""
                    UPDATE claims SET verdict=%s, confidence_score=%s,
                    verdict_summary=%s, full_analysis=%s, sources_used=%s,
                    last_checked=NOW() WHERE id=%s
                """, (verdict, 2, f"Consensus from {count} sources.",
                      f"{count} sources agree on this verdict.",
                      "Veris source consensus", claim_id))
                update_source_profile(cursor, source_name, verdict)
                calculate_reliability_score(cursor, source_name, claim_id)
                conn.commit()
                print(f"  -> Consensus: {verdict} (claim {claim_id})")
                continue
        prompt = build_prompt(claim_text, speaker, claim_type, article_title, source_name)
        requests.append({
            "custom_id": str(claim_id),
            "params": {
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}]
            }
        })
        claim_ids.append(claim_id)
    cursor.close()
    conn.close()
    if not requests:
        print("All claims resolved via database/consensus — no batch needed.")
        return
    print(f"Submitting batch of {len(requests)} claims to Anthropic...")
    batch = client.beta.messages.batches.create(requests=requests)
    print(f"Batch submitted. ID: {batch.id}")
    print(f"Save this ID — run process_batch_results('{batch.id}') when complete.")
    with open('pending_batch.txt', 'w') as f:
        f.write(batch.id)
    return batch.id
def build_prompt(claim_text, speaker, claim_type, article_title, source_name):
    return f"""You are the Veris fact-checking engine. Verify this claim using web search.

CLAIM: {claim_text}
SPEAKER: {speaker}
TYPE: {claim_type}
ARTICLE: {article_title}
SOURCE: {source_name}

INDEPENDENCE RULE: Two sources are only independent if they obtained information through different means. Multiple outlets repeating the same wire = ONE source.

CONSENSUS EXCEPTION: If 5+ outlets consistently report the same claim without contradiction, assign verified at confidence 2/3.

VERDICT DEFINITIONS:
- verified: TWO genuinely independent sources confirm
- plausible: Consistent but only one credible source
- disputed: ANY credible source contradicts
- overstated: Core fact real but exaggerated
- not_supported: Evidence contradicts the claim
- not_verifiable: Cannot confirm - sources unavailable
- opinion: Value judgement or prediction

CONFIDENCE: 3=two+ independent sources, 2=one credible source, 1=plausible/disputed

Return ONLY this JSON:
{{
  "verdict": "verdict here",
  "confidence_score": 1,
  "verdict_summary": "one sentence explanation",
  "full_analysis": "2-3 sentences of reasoning",
  "sources_used": "named sources and whether each independently confirmed"
}}"""


def run_batch_verdict_engine(limit=500):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.claim_text, c.speaker, c.claim_type,
               a.title, a.source_name, c.priority_score
        FROM claims c
        JOIN articles a ON c.article_id = a.id
        WHERE c.verdict IS NULL
        AND c.priority_score >= 30
        ORDER BY c.priority_score DESC
        LIMIT %s;
    """, (limit,))
    claims = cursor.fetchall()
    if not claims:
        print("No claims to process.")
        cursor.close()
        conn.close()
        return
    print(f"Preparing batch of {len(claims)} claims...")
    requests = []
    for claim_id, claim_text, speaker, claim_type, article_title, source_name, priority_score in claims:
        db_result = check_database_first(cursor, claim_text)
        if db_result:
            verdict, confidence, summary = db_result
            cursor.execute("""UPDATE claims SET verdict=%s, confidence_score=%s,
                verdict_summary=%s, full_analysis=%s, sources_used=%s,
                last_checked=NOW() WHERE id=%s""",
                (verdict, confidence, summary, "Matched in Veris database.",
                "Veris internal database", claim_id))
            update_source_profile(cursor, source_name, verdict)
            calculate_reliability_score(cursor, source_name, claim_id)
            conn.commit()
            print(f"  -> Database match: {verdict} (claim {claim_id})")
            continue
        consensus = check_source_consensus(cursor, claim_text)
        if consensus and consensus[1] >= 5:
            verdict, count = consensus
            cursor.execute("""UPDATE claims SET verdict=%s, confidence_score=%s,
                verdict_summary=%s, full_analysis=%s, sources_used=%s,
                last_checked=NOW() WHERE id=%s""",
                (verdict, 2, f"Consensus from {count} sources.",
                f"{count} sources agree.", "Veris source consensus", claim_id))
            update_source_profile(cursor, source_name, verdict)
            calculate_reliability_score(cursor, source_name, claim_id)
            conn.commit()
            print(f"  -> Consensus: {verdict} (claim {claim_id})")
            continue
        prompt = build_prompt(claim_text, speaker, claim_type, article_title, source_name)
        requests.append({
            "custom_id": str(claim_id),
            "params": {
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}]
            }
        })
    cursor.close()
    conn.close()
    if not requests:
        print("All claims resolved via database/consensus.")
        return
    print(f"Submitting batch of {len(requests)} claims...")
    batch = client.beta.messages.batches.create(requests=requests)
    print(f"Batch ID: {batch.id}")
    with open("pending_batch.txt", "w") as f:
        f.write(batch.id)
    print("Batch ID saved to pending_batch.txt")
    print("Results ready in up to 24 hours. Run process_batch_results() to collect.")
    return batch.id


def process_batch_results(batch_id=None):
    if not batch_id:
        try:
            batch_id = open("pending_batch.txt").read().strip()
        except:
            print("No batch ID found.")
            return
    print(f"Checking batch {batch_id}...")
    batch = client.beta.messages.batches.retrieve(batch_id)
    print(f"Status: {batch.processing_status}")
    if batch.processing_status != "ended":
        print(f"Not ready yet. Counts: {batch.request_counts}")
        return
    conn = get_connection()
    cursor = conn.cursor()
    saved = 0
    for result in client.beta.messages.batches.results(batch_id):
        claim_id = int(result.custom_id)
        if result.result.type == "succeeded":
            response_text = ""
            for block in result.result.message.content:
                if hasattr(block, "text"):
                    response_text += block.text
            response_text = response_text.strip()
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start == -1 or end == 0:
                continue
            try:
                data = json.loads(response_text[start:end])
                verdict = data.get("verdict", "not_verifiable")
                confidence = min(data.get("confidence_score", 1), 3)
                summary = data.get("verdict_summary", "")
                analysis = data.get("full_analysis", "")
                sources = data.get("sources_used", "")
                cursor.execute("""SELECT a.source_name FROM claims c
                    JOIN articles a ON c.article_id = a.id WHERE c.id = %s""",
                    (claim_id,))
                row = cursor.fetchone()
                source_name = row[0] if row else "unknown"
                cursor.execute("""UPDATE claims SET verdict=%s, confidence_score=%s,
                    verdict_summary=%s, full_analysis=%s, sources_used=%s,
                    last_checked=NOW() WHERE id=%s""",
                    (verdict, confidence, summary, analysis, sources, claim_id))
                update_source_profile(cursor, source_name, verdict)
                calculate_reliability_score(cursor, source_name, claim_id)
                conn.commit()
                saved += 1
                print(f"  Saved claim {claim_id}: {verdict} ({confidence}/3)")
            except Exception as e:
                print(f"  Error claim {claim_id}: {str(e)}")
        else:
            print(f"  Claim {claim_id} failed: {result.result.type}")
    cursor.close()
    conn.close()
    print(f"Done. {saved} verdicts saved.")
    import os
    if os.path.exists("pending_batch.txt"):
        os.remove("pending_batch.txt")
