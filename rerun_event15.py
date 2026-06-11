import os
from dotenv import load_dotenv
load_dotenv(os.path.expanduser('~/projects/veris/.env'))
import psycopg2

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT', 5432),
    dbname=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)
cur = conn.cursor()

# Reset the 22 not_verifiable debate claims to NULL so surge verifier picks them up
cur.execute('''
    UPDATE claims
    SET verdict = NULL,
        verdict_summary = NULL,
        full_analysis = NULL,
        sources_used = NULL,
        confidence_score = NULL,
        verification_attempts = NULL,
        last_checked = NULL
    WHERE event_id = 15
      AND verdict = 'not_verifiable'
      AND claim_origin = 'debate_claim'
''')
reset_count = cur.rowcount
conn.commit()
cur.close()
conn.close()
print(f'Reset {reset_count} claims to NULL — ready for surge verifier')

# Now run the surge verifier for event 15
from verdict_engine import verify_debate_claims_sync
verified = verify_debate_claims_sync(event_id=15, limit=25)
print(f'Surge complete — {verified} claims verified')
