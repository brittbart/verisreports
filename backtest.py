import sys
sys.path.insert(0, '.')
from verdict_engine import get_connection

# Import filter lists directly — bypass _log_filtered by calling logic inline
from extract_debate_claims import (
    DEBATE_SKIP_PREFIXES, DEBATE_OPINION_SIGNALS,
    SKIP_CONTAINS, BIOGRAPHICAL_PATTERNS_PRE, RELATIONSHIP_PATTERNS,
    CAREER_TIMELINE_PATTERNS, AGREEMENT_PATTERNS, MODERATOR_PATTERNS,
    INVALID_STARTS, pre_filter_utterance
)

conn = get_connection()
cur = conn.cursor()
cur.execute("SELECT id, utterance_text, speaker_id FROM speaker_utterances WHERE event_id=9")
rows = cur.fetchall()
conn.close()

passed, skipped, reasons = 0, 0, {}
for uid, text, spk_id in rows:
    # Call without utterance_id/event_id so _log_filtered skips DB insert
    skip, reason = pre_filter_utterance(text, is_debate=True)
    if skip:
        skipped += 1
        reasons[reason] = reasons.get(reason, 0) + 1
    else:
        passed += 1

total = len(rows)
print(f"Total: {total}  Passed: {passed} ({passed/total*100:.1f}%)  Skipped: {skipped}")
print("\nTop filter reasons:")
for r, c in sorted(reasons.items(), key=lambda x: -x[1])[:15]:
    print(f"  {c:4d}  {r}")
