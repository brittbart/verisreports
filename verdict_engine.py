import os
import json
import re
import psycopg2
import anthropic
from verdict_prompts import VERDICT_SYSTEM_PROMPT
from dotenv import load_dotenv
from api_leaderboard import METHODOLOGY_VERSION

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
    # Hardcoded fallbacks: Railway Runtime V2 strips env vars in subprocesses
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME', 'railway'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', 'PXLJKUdf14OB8bq4dWgF2P0gCs4FjVP'),
        host=os.getenv('DB_HOST', 'shinkansen.proxy.rlwy.net'),
        port=os.getenv('DB_PORT', '35370'),
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
    return f"""CONTEXT: {speaker} made the following claim, as reported by {source_name}:
ORIGINAL: "{original_claim}"

WHAT TO VERIFY — the underlying factual assertion:
CORE CLAIM: {core_claim}

CRITICAL INSTRUCTION: Do NOT verify whether {speaker} said this. Assume the attribution is correct. Your job is to verify whether the FACTUAL CONTENT of the claim is accurate. Search for evidence that confirms or contradicts the specific facts asserted.

Examples:
- "Trump said gas prices are up 5% from 2011" -> verify whether gas prices are actually up 5% from 2011
- "Harris said the bill would cost $2 trillion" -> verify whether the bill actually costs $2 trillion

TYPE: {claim_type}
ARTICLE: {article_title}

Try at least 2-3 distinct search queries before concluding not_verifiable."""



def _sources_to_prose(structured):
    """Convert structured sources array to prose fallback for sources_used TEXT.
    Returns empty string if empty/None.
    """
    if not structured:
        return ""
    parts = []
    for s in structured:
        name = s.get("name", "") or ""
        note = s.get("note", "") or ""
        indep = s.get("independent", False)
        part = name
        if note: part += f" — {note}"
        if indep: part += " (independently confirmed)"
        parts.append(part)
    return "; ".join(parts)

def analyse_claim(claim_text, speaker, claim_type,
                  article_title, source_name, cursor=None, stage='verdicts', **kwargs):

    # Cache and consensus short-circuits removed (Option B, 2026-05-28):
    # every scoreable verdict is its own fresh, evidence-based verification.
    # No verdict is ever copied from a similar prior claim. See DECISIONS.md D-016.

    if pre_filter_claim(claim_text) == "opinion":
        print(f"  -> Pre-filter: opinion")
        return {"verdict":"opinion","confidence_score":1,"verdict_summary":"Prediction or editorial opinion.","full_analysis":"Pre-classified by local filter.","sources_used":"Local filter","sources_structured":[]}

    claim_origin = kwargs.get('claim_origin', 'outlet_claim')
    if claim_origin == 'attributed_claim':
        core_claim = strip_attribution(claim_text, speaker or '')
        print(f"  -> Attributed claim verification (core: {core_claim[:60]}...)")
        prompt = build_attributed_prompt(
            core_claim, claim_text, speaker, claim_type, article_title, source_name
        )
    else:
        print(f"  -> Web search verification...")
        prompt = f"""Verify this claim using web search.

CLAIM: {claim_text}
SPEAKER: {speaker}
TYPE: {claim_type}
ARTICLE: {article_title}
SOURCE: {source_name}

Try at least 2-3 distinct search queries before concluding not_verifiable."""

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
            system=[{"type": "text", "text": VERDICT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        from token_logging import log_usage; log_usage(stage, message)
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
    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            print(f"    [API overload] 529 received — re-raising for retry with backoff")
            raise  # Let caller handle with appropriate backoff
        print(f"    Error: {str(e)}")
        return None
    except Exception as e:
        print(f"    Error: {str(e)}")
        return None
    
def update_source_profile(cursor, source_name, verdict):
    field_map = {
        'supported':      'supported_count',
        'plausible':      'plausible_count',
        'corroborated':   'corroborated_count',
        'disputed':       'disputed_count',
        'not_supported':  'not_supported_count',
        'overstated':     'overstated_count',
        'not_verifiable': 'not_verifiable_count',
        'opinion':        'opinion_count',
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
                         WHEN verdict = 'corroborated' THEN 0.75
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
            # Get old counts for audit trail
            cursor.execute("""
                SELECT total_claims_checked, supported_count FROM sources WHERE name = %s
            """, (source_name,))
            old_row = cursor.fetchone()
            old_verified_val = old_row[1] if old_row else None
            old_total_val = old_row[0] if old_row else None
            cursor.execute("""
                INSERT INTO score_history
                (source_name, old_score, new_score, old_verified, new_verified, old_total, new_total, trigger_claim_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (source_name, old_score, new_score, old_verified_val, scoreable, old_total_val, scoreable, trigger_claim_id))

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
        AND COALESCE(c.verification_attempts, 0) < 3
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
            import json as _ejson
            _src_raw = result.get('sources_used', '')
            if isinstance(_src_raw, list): _src_structured = _src_raw
            elif isinstance(_src_raw, str):
                try:
                    _p = _ejson.loads(_src_raw)
                    _src_structured = _p if isinstance(_p, list) else []
                except Exception: _src_structured = []
            else: _src_structured = []
            sources = _sources_to_prose(_src_structured) if _src_structured else (str(_src_raw) if _src_raw else '')
            if not _src_structured: print(f'[engine] sources_structured empty for claim {claim_id}')
            cursor.execute("""
                UPDATE claims
                SET verdict = %s,
                    confidence_score = %s,
                    verdict_summary = %s,
                    full_analysis = %s,
                    sources_used = %s,
                    sources_structured = %s,
                    verification_depth = COALESCE(%s, verification_depth),
                    methodology_version = %s,
                    last_checked = NOW()
                WHERE id = %s;
            """, (verdict, confidence, summary,
                  analysis, sources, _ejson.dumps(_src_structured),
                  depth or 99, METHODOLOGY_VERSION, claim_id))
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
    return f"""Verify this claim using web search.

CLAIM: {claim_text}
SPEAKER: {speaker}
TYPE: {claim_type}
ARTICLE: {article_title}
SOURCE: {source_name}

Try at least 2-3 distinct search queries before concluding not_verifiable."""



# ---------------------------------------------------------------------------
# Synchronous debate claim verifier (surge mode)
# Completely separate from outlet_claim batch pipeline.
# Never touches leaderboard scoring or source profiles.
# ---------------------------------------------------------------------------
def _verify_single_claim(claim, event_id):
    """Verify one debate claim in its own DB connection. Thread-safe."""
    import time
    claim_id, claim_text, speaker, claim_type, event_name = claim
    conn = get_connection()
    cursor = conn.cursor()
    try:
        result = None
        for _attempt in range(3):
            try:
                result = analyse_claim(
                    claim_text,
                    speaker or 'Debate participant',
                    claim_type or 'factual',
                    event_name,
                    'Debate transcript',
                    stage='verdicts-debate'
                )
            except anthropic.APIStatusError as _api_err:
                if _api_err.status_code == 529 and _attempt < 2:
                    _wait = 5 * (2 ** _attempt)  # 5s, 10s, 20s
                    print(f"    [surge] 529 overload, retry {_attempt+1}/3 in {_wait}s...")
                    time.sleep(_wait)
                    continue
                result = None
            if result:
                break
            if _attempt < 2:
                print(f"    [surge] Parse failed, retrying ({_attempt+1}/3)...")
                time.sleep(5 * (_attempt + 1))

        if not result:
            cursor.execute("""
                UPDATE claims SET verification_attempts = COALESCE(verification_attempts, 0) + 1,
                    verdict = CASE WHEN COALESCE(verification_attempts, 0) >= 2 THEN 'not_verifiable' ELSE verdict END,
                    verdict_summary = CASE WHEN COALESCE(verification_attempts, 0) >= 2
                        THEN 'Automated verification failed to parse API response after 3 attempts.'
                        ELSE verdict_summary END,
                    verdict_status = CASE WHEN COALESCE(verification_attempts, 0) >= 2 THEN 'final' ELSE verdict_status END,
                    methodology_version = CASE WHEN COALESCE(verification_attempts, 0) >= 2 THEN %s ELSE methodology_version END,
                    last_checked = CASE WHEN COALESCE(verification_attempts, 0) >= 2 THEN NOW() ELSE last_checked END
                WHERE id = %s
            """, (METHODOLOGY_VERSION, claim_id,))
            conn.commit()
            cursor.execute("SELECT verification_attempts FROM claims WHERE id = %s", (claim_id,))
            attempts = cursor.fetchone()[0]
            if attempts >= 3:
                print(f"    [surge] claim {claim_id}: auto-marked not_verifiable after 3 attempts")
            else:
                print(f"    [surge] Could not parse verdict for claim {claim_id} — will retry")
            return 0

        verdict = result.get('verdict', 'not_verifiable')
        confidence = result.get('confidence', 2)
        summary = result.get('verdict_summary', '')
        full_analysis = result.get('full_analysis', '')

        cursor.execute("""
            UPDATE claims SET
                verdict = %s,
                confidence_score = %s,
                verdict_summary = %s,
                full_analysis = %s,
                verdict_status = 'provisional',
                methodology_version = %s,
                last_checked = NOW()
            WHERE id = %s
              AND claim_origin = 'debate_claim'
        """, (verdict, confidence, summary, full_analysis[:2000], METHODOLOGY_VERSION, claim_id))
        conn.commit()
        print(f"    [surge] claim {claim_id}: {verdict} — {summary[:60]}")
        return 1

    except Exception as e:
        print(f"    [surge] Error on claim {claim_id}: {e}")
        conn.rollback()
        return 0
    finally:
        cursor.close()
        conn.close()


def verify_debate_claims_sync(event_id, limit=10):
    """
    Verify up to `limit` unverified debate_claim rows for event_id.
    Uses 3-worker thread pool for parallel verification (max_workers=3
    to stay within Anthropic rate limits).
    Returns number of claims verified.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.claim_text, c.speaker, c.claim_type, e.event_name
        FROM claims c
        JOIN events e ON e.id = c.event_id
        WHERE c.event_id = %s
          AND c.claim_origin = 'debate_claim'
          AND c.verdict IS NULL
          AND c.claim_text IS NOT NULL
          AND LENGTH(c.claim_text) > 20
          AND COALESCE(c.verification_attempts, 0) < 3
        ORDER BY c.id ASC
        LIMIT %s
    """, (event_id, limit))
    claims = cursor.fetchall()
    cursor.close()
    conn.close()

    if not claims:
        return 0

    print(f"  [surge] Verifying {len(claims)} debate claims for event_id={event_id} (parallel, max_workers=3)")
    verified = 0
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_verify_single_claim, claim, event_id): claim for claim in claims}
        for future in as_completed(futures):
            try:
                verified += future.result()
            except Exception as e:
                print(f"    [surge] Future error: {e}")
    return verified


def get_live_event_id():
    """Return event_id of any currently live public event, or None.
    Compares in UTC to handle server/DB/event timezone mismatches.
    """
    from datetime import datetime, timedelta, timezone as tz
    TZ_OFFSETS = {
        'ET': -4, 'EST': -5, 'EDT': -4,
        'CT': -5, 'CST': -6, 'CDT': -5,
        'MT': -6, 'MST': -7, 'MDT': -6,
        'PT': -7, 'PST': -8, 'PDT': -7,
    }
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, event_date, start_time, timezone FROM events
            WHERE is_public = TRUE
              AND start_time IS NOT NULL
              AND event_date >= CURRENT_DATE - INTERVAL '1 day'
              AND event_date <= CURRENT_DATE + INTERVAL '1 day'
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        now_utc = datetime.now(tz.utc)
        for eid, event_date, start_time, event_tz in rows:
            if not event_date or not start_time:
                continue
            offset = TZ_OFFSETS.get(event_tz or 'CT', -5)
            event_tz_obj = tz(timedelta(hours=offset))
            event_start = datetime.combine(event_date, start_time).replace(tzinfo=event_tz_obj)
            if (event_start - timedelta(minutes=45)) <= now_utc <= (event_start + timedelta(hours=3)):
                return eid
        return None
    except Exception:
        return None


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
    for claim_id, claim_text, speaker, claim_type, article_title, source_name, priority_score, claim_origin, attribution_context in claims:
        # Cache/consensus copying removed (Option B, 2026-05-28): every claim
        # falls through to fresh batch verification. See DECISIONS.md D-016.
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
                import json as _ejson2
                _src_raw2 = data.get("sources_used", "")
                if isinstance(_src_raw2, list): _src_structured2 = _src_raw2
                elif isinstance(_src_raw2, str):
                    try:
                        _p2 = _ejson2.loads(_src_raw2)
                        _src_structured2 = _p2 if isinstance(_p2, list) else []
                    except Exception: _src_structured2 = []
                else: _src_structured2 = []
                sources = _sources_to_prose(_src_structured2) if _src_structured2 else (str(_src_raw2) if _src_raw2 else "")
                cursor.execute("""SELECT a.source_name FROM claims c
                    JOIN articles a ON c.article_id = a.id WHERE c.id = %s""",
                    (claim_id,))
                row = cursor.fetchone()
                source_name = row[0] if row else "unknown"
                cursor.execute("""UPDATE claims SET verdict=%s, confidence_score=%s,
                    verdict_summary=%s, full_analysis=%s, sources_used=%s,
                    sources_structured=%s,
                    verification_depth=COALESCE(%s, verification_depth),
                    methodology_version=%s,
                    verdict_status='provisional',
                    last_checked=NOW() WHERE id=%s""",
                    (verdict, confidence, summary, analysis, sources,
                     _ejson2.dumps(_src_structured2),
                     99, METHODOLOGY_VERSION, claim_id))
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
