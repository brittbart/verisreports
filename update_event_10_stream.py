"""
Update event 10 stream_url to the specific YouTube /watch?v= URL for Round 3.
Run tomorrow (June 2) at ~5:00 PM MT once 9News publishes the live stream URL.

Usage:
  1. Edit VIDEO_URL below to the actual /watch?v= link
  2. cd ~/projects/veris && source venv/bin/activate
  3. python3 update_event_10_stream.py
"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv('/home/veris/projects/veris/.env')

VIDEO_URL = "https://www.youtube.com/watch?v=REPLACE_ME"

if "REPLACE_ME" in VIDEO_URL:
    print("ERROR: edit VIDEO_URL in this script before running")
    raise SystemExit(1)

if "/watch?v=" not in VIDEO_URL:
    print(f"ERROR: URL doesn't look like a watch URL: {VIDEO_URL}")
    print("Expected format: https://www.youtube.com/watch?v=XXXXXXXXXXX")
    raise SystemExit(1)

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'),
    dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)
cur = conn.cursor()

# Show before
cur.execute("SELECT id, event_name, stream_url FROM events WHERE id = 10")
print("BEFORE:", cur.fetchone())

# Update
cur.execute("UPDATE events SET stream_url = %s WHERE id = 10", (VIDEO_URL,))
print(f"Rows updated: {cur.rowcount}")

# Show after
cur.execute("SELECT id, event_name, stream_url FROM events WHERE id = 10")
print("AFTER:", cur.fetchone())

conn.commit()
cur.close()
conn.close()
print("\n✅ Event 10 stream URL updated. veris-stream should pick it up on next poll.")
