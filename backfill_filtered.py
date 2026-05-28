import psycopg2, os
from verdict_engine import get_connection
from extract_debate_claims import pre_filter_utterance

conn = get_connection()
cur = conn.cursor()
cur.execute('SELECT id, utterance_text, speaker_id FROM speaker_utterances WHERE event_id=9')
rows = cur.fetchall()

# Delete any existing backfill rows for event 9
cur.execute('DELETE FROM filtered_utterances WHERE event_id=9')

records = []
passed, skipped = 0, 0
for uid, text, spk_id in rows:
    skip, reason = pre_filter_utterance(text, is_debate=True)  # no logging
    if skip:
        skipped += 1
        records.append((uid, 9, spk_id, 'pre', reason, text[:500]))
    else:
        passed += 1

# Bulk insert
cur.executemany(
    'INSERT INTO filtered_utterances (utterance_id, event_id, speaker_id, filter_stage, filter_reason, utterance_text) VALUES (%s,%s,%s,%s,%s,%s)',
    records
)
conn.commit()

print(f'Passed: {passed}, Skipped: {skipped}, Logged: {len(records)}')

# Show breakdown
cur.execute('''
    SELECT filter_reason, COUNT(*) 
    FROM filtered_utterances 
    WHERE event_id=9 
    GROUP BY filter_reason 
    ORDER BY COUNT(*) DESC
''')
print('\nFilter reason breakdown:')
for r, c in cur.fetchall():
    print(f'  {c:4d}  {r}')
conn.close()
