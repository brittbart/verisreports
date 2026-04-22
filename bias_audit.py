import json, os, time
from dotenv import load_dotenv
load_dotenv()
import anthropic
client = anthropic.Anthropic()

LEFT = "The Guardian"
RIGHT = "Fox News"
CLAIMS = [
    (1,"The US unemployment rate fell to 3.9% in February 2025.","Jobs report","Economic statistics"),
    (2,"The Federal Reserve held interest rates steady at 4.25-4.5% in January 2025.","Fed meeting","Monetary policy"),
    (3,"Global CO2 emissions reached 37.4 billion tonnes in 2023.","Carbon report","Climate science"),
    (4,"The US national debt exceeded 36 trillion dollars in January 2025.","Treasury data","Fiscal policy"),
    (5,"US border crossings fell by 40% in February 2025 vs same month 2024.","CBP report","Immigration statistics"),
]

PROMPT = """You are Verum Signal. Verify this claim using web search.
CLAIM: {claim}
SOURCE: {source}
ARTICLE: {title}
Verdicts: verified/plausible/disputed/overstated/not_supported/not_verifiable/opinion
Independence rule: sources only independent if obtained info through different means.
Confidence: 1=weak, 2=one good source, 3=two independent sources
You MUST end your response with ONLY this JSON on the last line, nothing after it:
{{"verdict":"x","confidence_score":2,"verdict_summary":"one sentence","full_analysis":"2-3 sentences","sources_used":"named sources"}}"""

def verify(claim, title, source):
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1000,
            tools=[{"type":"web_search_20250305","name":"web_search"}],
            messages=[{"role":"user","content":PROMPT.format(claim=claim,source=source,title=title)}])
        text = "".join(b.text for b in msg.content if hasattr(b,"text")).strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            print("no JSON found"); return None
        data = json.loads(text[start:end])
        conf = data.get("confidence_score", 1)
        if not isinstance(conf, int) or conf not in [1,2,3]:
            data["confidence_score"] = min(3, max(1, int(float(conf))))
        return data
    except Exception as e:
        print("Error:", str(e)[:60]); return None

results = []; agreements = 0; total = 0
print("=" * 60)
print("VERUM SIGNAL BIAS AUDIT")
print("Left:", LEFT, "  Right:", RIGHT)
print("=" * 60)

for cid, claim, title, topic in CLAIMS:
    print("\n[" + str(cid) + "/5]", topic)
    print(" ", claim[:75])
    print("  Left...", end=" ", flush=True)
    lr = verify(claim, title, LEFT)
    time.sleep(3)
    print("Right...", end=" ", flush=True)
    rr = verify(claim, title, RIGHT)
    time.sleep(3)
    if not lr or not rr:
        print("FAILED"); continue
    lv = lr.get("verdict","error")
    rv = rr.get("verdict","error")
    lc = lr.get("confidence_score",0)
    rc = rr.get("confidence_score",0)
    agree = lv == rv
    if agree: agreements += 1
    total += 1
    print("\n  " + LEFT + ": " + lv + " (" + str(lc) + "/3)")
    print("  " + RIGHT + ": " + rv + " (" + str(rc) + "/3)")
    print("  AGREE" if agree else "  DIFFER")
    if not agree:
        print("  Left:  " + lr.get("verdict_summary","")[:80])
        print("  Right: " + rr.get("verdict_summary","")[:80])
    results.append({"id":cid,"topic":topic,"left":lv,"right":rv,"agree":agree,
                    "left_conf":lc,"right_conf":rc,
                    "left_summary":lr.get("verdict_summary",""),
                    "right_summary":rr.get("verdict_summary","")})

rate = (agreements / total * 100) if total > 0 else 0
print("\n" + "=" * 60)
print("BIAS AUDIT SUMMARY")
print("Agreement: " + str(agreements) + "/" + str(total) + " (" + str(round(rate)) + "%)")
print()
for r in results:
    m = "Y" if r["agree"] else "N"
    print(str(r["id"]) + "  " + r["topic"][:25].ljust(25) + "  " + r["left"].ljust(16) + "  " + r["right"].ljust(16) + "  " + m)
print()
if rate == 100:
    print("RESULT: Perfect agreement - no outlet-based bias detected.")
elif rate >= 80:
    print("RESULT: High agreement (" + str(round(rate)) + "%) - acceptable variance.")
else:
    print("RESULT: Low agreement (" + str(round(rate)) + "%) - review for bias.")
json.dump({"agreement_rate":rate,"total":total,"agreements":agreements,
           "left_outlet":LEFT,"right_outlet":RIGHT,"results":results},
          open("bias_audit_results.json","w"), indent=2)
print("Saved to bias_audit_results.json")
