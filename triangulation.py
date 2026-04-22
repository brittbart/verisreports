import json, os, time, psycopg2
from dotenv import load_dotenv
load_dotenv()
import anthropic

client = anthropic.Anthropic()

ADJACENT = {
    "verified": ["plausible"],
    "plausible": ["verified", "disputed", "overstated"],
    "disputed": ["plausible", "not_supported"],
    "overstated": ["plausible", "verified"],
    "not_supported": ["disputed"],
}

def get_conn():
    return psycopg2.connect(dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"))

def second_pass(claim_text, speaker, claim_type, title, source):
    prompt = ("Verum Signal verification — second pass.\n"
              "Use web search to independently assess this claim.\n"
              "SOURCE: " + source + "\nARTICLE: " + title + "\n"
              "SPEAKER: " + (speaker or "Unknown") + "\nTYPE: " + (claim_type or "factual") + "\n"
              "CLAIM: " + claim_text + "\n\n"
              "Apply the independence rule: sources only independent if obtained info through different means.\n"
              "Verdicts: verified/plausible/disputed/overstated/not_supported/not_verifiable/opinion\n"
              "Confidence: 1=weak 2=one good source 3=two independent sources\n"
              "End with ONLY this JSON on the final line:\n"
              '{"verdict":"x","confidence_score":2,"verdict_summary":"one sentence","sources_used":"named sources"}')
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=800,
            tools=[{"type":"web_search_20250305","name":"web_search"}],
            messages=[{"role":"user","content":prompt}])
        text = "".join(b.text for b in msg.content if hasattr(b,"text")).strip()
        start = text.rfind("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(text[start:end])
    except Exception as e:
        print("Error:", str(e)[:60]); return None

conn = get_conn()
cur = conn.cursor()

cur.execute("""
    SELECT c.id, c.claim_text, c.speaker, c.claim_type, c.verdict,
           c.verdict_summary, a.title, a.source_name
    FROM claims c
    JOIN articles a ON c.article_id = a.id
    WHERE c.verdict IS NOT NULL
    AND c.verdict NOT IN ('opinion','not_verifiable')
    AND c.confidence_score = 3
    AND c.claim_origin = 'outlet_claim'
    AND c.id NOT IN (SELECT claim_id FROM triangulation_results WHERE claim_id IS NOT NULL)
    ORDER BY RANDOM()
    LIMIT 5
""")
claims = cur.fetchall()

print("=" * 65)
print("VERUM SIGNAL — ALGORITHMIC TRIANGULATION")
print("Second-pass verification on 5 high-confidence outlet claims")
print("=" * 65)

agreements = 0
soft = 0
hard = 0
total = 0

for cid, claim_text, speaker, claim_type, orig_verdict, orig_summary, title, source in claims:
    print("\nClaim", cid, "|", source)
    print(" ", claim_text[:80])
    print("  Original:", orig_verdict)
    print("  Running second pass...", end=" ", flush=True)

    result = second_pass(claim_text, speaker, claim_type, title, source)
    time.sleep(2)

    if not result:
        print("FAILED")
        continue

    second_verdict = result.get("verdict","error")
    second_conf = result.get("confidence_score", 1)
    second_summary = result.get("verdict_summary","")
    second_sources = result.get("sources_used","")

    agree = orig_verdict == second_verdict
    soft_d = (not agree) and second_verdict in ADJACENT.get(orig_verdict, [])
    hard_d = (not agree) and (not soft_d)

    if agree: agreements += 1
    elif soft_d: soft += 1
    else: hard += 1
    total += 1

    cur.execute("""
        INSERT INTO triangulation_results
            (claim_id, original_verdict, second_verdict, agreement,
             soft_disagree, hard_disagree, second_summary, second_sources)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (cid, orig_verdict, second_verdict, agree, soft_d, hard_d,
          second_summary, second_sources))
    conn.commit()

    print("\n  Second pass:", second_verdict, "(" + str(second_conf) + "/3)")
    if agree:
        print("  AGREE")
    elif soft_d:
        print("  SOFT DISAGREE — adjacent verdicts, flag for review")
        print("  Original:", orig_summary[:70])
        print("  Second: ", second_summary[:70])
    else:
        print("  HARD DISAGREE — opposite verdicts, needs manual review")
        print("  Original:", orig_summary[:70])
        print("  Second: ", second_summary[:70])

rate = (agreements / total * 100) if total > 0 else 0
print("\n" + "=" * 65)
print("TRIANGULATION SUMMARY")
print("Agreement:      " + str(agreements) + "/" + str(total) + " (" + str(round(rate)) + "%)")
print("Soft disagree:  " + str(soft))
print("Hard disagree:  " + str(hard))
if rate == 100:
    print("RESULT: Perfect agreement — methodology is consistent.")
elif rate >= 80:
    print("RESULT: High agreement (" + str(round(rate)) + "%) — acceptable variance.")
else:
    print("RESULT: Low agreement (" + str(round(rate)) + "%) — review methodology.")

cur.close()
conn.close()
