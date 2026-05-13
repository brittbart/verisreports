import os
import json
import re
import psycopg2
import anthropic
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)
# Patch 13: explicit timeout prevents indefinite block on wedged API connection
client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'), timeout=120.0)


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
    # Patch 13: timeouts + keepalives + statement_timeout (180s)
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
        application_name='veris-verdict',
    )
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = 180000")
    conn.commit()
    return conn

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
    
def strip_attribution(claim_text, speaker):
    """Extract core factual assertion from attributed claim text."""
    if not claim_text:
        return claim_text
    text = claim_text.strip()
    patterns = [
        r'^[^,\.]{3,60}?\s+(?:said|claimed|stated|argued|warned|noted|announced|confirmed|told\s+\w+|declared|alleged|asserted|insisted|acknowledged|admitted|revealed|suggested|indicated)\s+(?:that\s+)?(.+)$',
        r'^[Aa]ccording\s+to\s+[^,]{3,60},\s*(.+)$',
    ]
    for pattern in patterns:
        m = re.match(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            core = m.group(1).strip()
            if len(core) >= 30:
                return core[0].upper() + core[1:]
    return claim_text


def build_attributed_prompt(core_claim, original_claim, speaker, claim_type, article_title, source_name):
    """Build verification prompt for attributed claims — verifies underlying fact, not attribution."""
    return f"""You are the Verum Signal verification engine. Your job is to verify the FACTUAL CONTENT of a claim, not whether someone said it.

CONTEXT: {speaker} made the following claim, as reported by {source_name}:
ORIGINAL: "{original_claim}"

WHAT TO VERIFY — the underlying factual assertion:
CORE CLAIM: {core_claim}

CRITICAL INSTRUCTION: Do NOT verify whether {speaker} said this. Assume the attribution is correct. Your job is to verify whether the FACTUAL CONTENT of the claim is accurate. Search for evidence that confirms or contradicts the specific facts asserted.

Examples:
- "Trump said gas prices are up 5% from 2011" -> verify whether gas prices are actually up 5% from 2011
- "Harris said the bill would cost $2 trillion" -> verify whether the bill actually costs $2 trillion

TYPE: {claim_type}
ARTICLE: {article_title}

VERIFICATION STANDARDS:

INDEPENDENCE RULE: Two sources are only independent if they obtained the information through different means. Multiple outlets repeating the same wire = ONE source.

CONSENSUS EXCEPTION: If 5+ outlets consistently report the same factual content without contradiction, assign corroborated at confidence 2/3.

VERDICT DEFINITIONS:
- supported: Underlying fact confirmed by TWO genuinely independent primary sources
- plausible: Consistent with evidence but only one credible source found
- disputed: ANY credible source contradicts the factual assertion
- overstated: Core fact is real but figure or scale is exaggerated
- not_supported: Evidence actively contradicts the factual assertion
- not_verifiable: Cannot confirm or deny — primary sources unavailable
- corroborated: 5+ outlets consistently report same factual content
- opinion: Value judgement or prediction that cannot be empirically true or false

CONFIDENCE SCORE:
- 3: Two or more genuinely independent sources with original reporting
- 2: One credible source, or plausible based on consistent reporting
- 1: Plausible or disputed

SEARCH INSTRUCTIONS:
1. Search for the specific factual assertion — NOT the quote or the speaker
2. Find primary sources (government data, official reports, direct documentation)
3. Find a second independent source
4. If any credible source contradicts, assign disputed

CRITICAL CONSTRAINTS:
- verdict MUST be exactly one of: supported, plausible, corroborated, overstated, disputed, not_supported, not_verifiable, opinion
- If cannot determine, use not_verifiable

Return ONLY this JSON:
{{
  "verdict": "supported",
  "confidence_score": 1,
  "verdict_summary": "one sentence explaining whether the underlying fact holds up",
  "full_analysis": "2-3 sentences on what you found, sources used, and why this verdict",
  "sources_used": "specific named sources and whether each independently confirmed the underlying fact"
}}"""


def analyse_claim(claim_text, speaker, claim_type,
                  article_title, source_name, cursor=None, **kwargs):

    if cursor:
        db_result = check_database_first(cursor, claim_text)
        if db_result:
            verdict, confidence, summary = db_result
            print(f"  -> Database match: {verdict}")
            return {
                "verdict": verdict,
                "confidence_score": confidence,
                "verdict_summary": summary,
                "full_analysis": "Matched supported claim in Veris database.",
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

    claim_origin = kwargs.get('claim_origin', 'outlet_claim')
    if claim_origin == 'attributed_claim':
        core_claim = strip_attribution(claim_text, speaker or '')
        print(f"  -> Attributed claim verification (core: {core_claim[:60]}...)")
        prompt = build_attributed_prompt(
            core_claim, claim_text, speaker, claim_type, article_title, source_name
        )
    else:
        print(f"  -> Web search verification...")
        prompt = f"""You are the Verum Signal verification engine. Your job is to verify claims with rigorous, defensible standards. Use web search to find primary sources.

CLAIM: {claim_text}
SPEAKER: {speaker}
TYPE: {claim_type}
ARTICLE: {article_title}
SOURCE: {source_name}

VERIFICATION STANDARDS — read carefully before assigning a verdict:


INDEPENDENCE RULE:
Two sources are only independent if they obtained the information through different means. Multiple outlets repeating the same wire report = ONE source, not multiple. To call something verified you must find sources that independently confirmed the fact through different channels.

CONSENSUS EXCEPTION:
If 5 or more outlets are consistently reporting the same claim without contradiction, assign supported at confidence 2/3 even if you cannot confirm each outlet independently sourced the information. Widespread consistent reporting across multiple outlets is a strong signal of accuracy. If any credible outlet contradicts the claim, use disputed instead regardless of how many outlets agree.

VERDICT DEFINITIONS — apply strictly:

- supported: Confirmed by at least TWO genuinely independent sources from Tier 1 or above, each having obtained the information through different means. If you cannot find this, do NOT use supported.

- plausible: Consistent with available evidence but only confirmed by one credible source, or by multiple sources all citing the same original report. Use this when the claim seems likely true but true independence cannot be established.

- disputed: ANY credible source contradicts the claim, OR evidence is genuinely mixed. Do not default to plausible when evidence is mixed — use disputed. This verdict is underused and should be applied whenever you find meaningful contradiction.

- overstated: The core fact is real but the specific figure, scale, or characterisation is exaggerated or imprecise. Use when a claim is directionally correct but materially misleading in its specifics.

- not_supported: Positive evidence contradicts the claim, OR the claim makes specific assertions that authoritative sources explicitly refute.

- not_verifiable: The claim cannot be confirmed or denied because primary sources are unavailable, access is restricted, or the event is too recent. Use sparingly — exhaust search options first.

- corroborated: 5 or more outlets are consistently reporting the same claim without contradiction, but full independence cannot be established. Use this when the consensus exception applies. Counts as +0.5 (weaker than supported).

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

CRITICAL CONSTRAINTS:
- The "verdict" field MUST be EXACTLY one of these 8 lowercase strings, with no variations: supported, plausible, corroborated, overstated, disputed, not_supported, not_verifiable, opinion.
- Do NOT use "verified", "true", "false", "confirmed", or any other value. Only the 8 listed above.
- If you cannot determine the verdict, use "not_verifiable".

Return ONLY this JSON:
{{
  "verdict": "supported",
  "confidence_score": 1,
  "verdict_summary": "one sentence plain-language explanation",
  "full_analysis": "2-3 sentences explaining what you found, what sources you used, and why you assigned this verdict",
  "sources_used": "specific named sources and whether each independently confirmed the fact"
}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search"
                }
            ],
            system=[{"type": "text", "text": "You are the Verum Signal verification engine. Return only valid JSON.", "cache_control": {"type": "ephemeral"}}],
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        from token_logging import log_usage; log_usage('verdicts', message)
        response_text = ""
        for block in message.content:
            if hasattr(block, "text"):
                response_text += block.text
        response_text = response_text.strip()
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        if start == -1 or end == 0:
            return None
        result = json.loads(response_text[start:end])

        # Validate verdict — Sonnet sometimes returns 'verified' or other invalid values
        VALID_VERDICTS = {'supported', 'plausible', 'corroborated', 'overstated',
                          'disputed', 'not_supported', 'not_verifiable', 'opinion'}
        verdict = result.get('verdict', '')
        if verdict not in VALID_VERDICTS:
            # Common LLM mistakes mapped to correct values
            corrections = {
                'verified': 'supported',
                'true': 'supported',
                'confirmed': 'supported',
                'false': 'not_supported',
                'misleading': 'overstated',
                'unverified': 'not_verifiable',
                'unknown': 'not_verifiable',
            }
            corrected = corrections.get(str(verdict).lower().strip(), 'not_verifiable')
            print(f"    [verdict-fix] LLM returned {verdict!r}, corrected to {corrected!r}")
            result['verdict'] = corrected

        return result
    except Exception as e:
        print(f"    Error: {str(e)}")
        return None
    
def update_source_profile(cursor, source_name, verdict):
    if verdict == 'opinion':
        return
    field_map = {
        'supported':     'verified_count',
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

        # Verdict weights below: see api_leaderboard.WEIGHTS for the canonical
        # methodology source of truth. SQL kept inline here as a single tested
        # site to avoid query-plan churn.
        cursor.execute("""
            SELECT
                SUM(CASE WHEN verdict = 'supported'     THEN 1.0
                         WHEN verdict = 'plausible'    THEN 0.5
                         WHEN verdict = 'corroborated' THEN 0.5
                         WHEN verdict = 'overstated'   THEN -0.5
                         WHEN verdict = 'disputed'     THEN -1.0
                         WHEN verdict = 'not_supported' THEN -1.5
                         ELSE 0 END) as weighted,
                COUNT(*) FILTER (
                    WHERE verdict NOT IN ('opinion','not_verifiable')
                ) as scoreable
            FROM claims c
            JOIN articles a ON c.article_id = a.id
            WHERE a.source_name = %s
            AND c.verdict IS NOT NULL
            AND c.claim_origin = 'outlet_claim'
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
            """, (source_name, old_score, new_score, round(weighted), scoreable, trigger_claim_id))

    except Exception as e:
        print(f"    Score update error: {str(e)}")
        try:
            cursor.connection.rollback()
        except:
            pass
        try:
            cursor.connection.rollback()
        except:
            pass

def run_verdict_engine(limit=10, depth=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.claim_text, c.speaker, c.claim_type,
               a.title, a.source_name, c.priority_score,
               c.claim_origin, COALESCE(c.attribution_context, '')
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
            priority_score, claim_origin, attribution_context) in enumerate(claims):
        print(f"[{i+1}/{len(claims)}] Priority: {priority_score}/100")
        print(f"  {claim_text[:70]}...")
        print(f"  Source: {source_name} [{claim_origin}]")
        result = analyse_claim(
            claim_text, speaker, claim_type,
            article_title, source_name, cursor,
            claim_origin=claim_origin,
            attribution_context=attribution_context,
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
                    verification_depth = COALESCE(%s, verification_depth),
                    last_checked = NOW()
                WHERE id = %s;
            """, (verdict, confidence, summary,
                  analysis, sources, depth or 99, claim_id))
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

def build_prompt(claim_text, speaker, claim_type, article_title, source_name):
    return f"""You are the Verum Signal verification engine. Verify this claim using web search.

CLAIM: {claim_text}
SPEAKER: {speaker}
TYPE: {claim_type}
ARTICLE: {article_title}
SOURCE: {source_name}

INDEPENDENCE RULE: Two sources are only independent if they obtained information through different means. Multiple outlets repeating the same wire = ONE source.

CONSENSUS EXCEPTION: If 5+ outlets consistently report the same claim without contradiction, assign supported at confidence 2/3.

VERDICT DEFINITIONS:
- supported: TWO genuinely independent sources confirm
- plausible: Consistent but only one credible source
- disputed: ANY credible source contradicts
- overstated: Core fact real but exaggerated
- not_supported: Evidence contradicts the claim
- not_verifiable: Cannot confirm - sources unavailable
- opinion: Value judgement or prediction

CONFIDENCE: 3=two+ independent sources, 2=one credible source, 1=plausible/disputed

CRITICAL CONSTRAINTS:
- The "verdict" field MUST be EXACTLY one of these 8 lowercase strings, with no variations: supported, plausible, corroborated, overstated, disputed, not_supported, not_verifiable, opinion.
- Do NOT use "verified", "true", "false", "confirmed", or any other value. Only the 8 listed above.
- If you cannot determine the verdict, use "not_verifiable".

Return ONLY this JSON:
{{
  "verdict": "supported",
  "confidence_score": 1,
  "verdict_summary": "one sentence explanation",
  "full_analysis": "2-3 sentences of reasoning",
  "sources_used": "named sources and whether each independently confirmed"
}}"""


def run_batch_verdict_engine(limit=500, depth=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.claim_text, c.speaker, c.claim_type,
               a.title, a.source_name, c.priority_score,
               c.claim_origin, COALESCE(c.attribution_context, '')
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
                verification_depth=COALESCE(%s, verification_depth),
                last_checked=NOW() WHERE id=%s""",
                (verdict, confidence, summary, "Matched in Veris database.",
                "Veris internal database", depth or 99, claim_id))
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
                verification_depth=COALESCE(%s, verification_depth),
                last_checked=NOW() WHERE id=%s""",
                (verdict, 2, f"Consensus from {count} sources.",
                f"{count} sources agree.", "Veris source consensus", depth or 99, claim_id))
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
                "max_tokens": 600,
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
                    verification_depth=COALESCE(%s, verification_depth),
                    last_checked=NOW() WHERE id=%s""",
                    (verdict, confidence, summary, analysis, sources, 99, claim_id))
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
