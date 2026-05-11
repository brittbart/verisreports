# Day 20 — Leaderboard freeze fix (May 11, 2026)

## Root cause
Synchronous Google News redirect resolution in load_to_database.py was making
HTTP GET requests with 5-second timeouts inside the Load loop. With ~60 Google
News URLs per cycle, Load hung for ~5 minutes before the first INSERT. The idle
DB connection died, and the whole load step failed with "server closed the
connection unexpectedly." No new claims since May 7. Verdict pipeline drained
the existing >=30 priority queue and went silent. Leaderboard froze.

## What was patched
- load_to_database.py: removed Google News redirect block (lines ~41-47)
- load_to_database.py: added per-row SAVEPOINTs (defensive, not the cause)
- load_to_database.py: sanitize empty publishedAt -> NULL (defensive)
- Backups at load_to_database.py.bak.20260511_004325

## Confirmation
- First successful Load: 01:54 May 11, 1670 articles + 43 claims
- Verdicts will resume on the next 6-hour batch cycle

## Followups
- Rotate DB_PASSWORD (leaked twice in debugging chat)
- Commit load_to_database.py to git so it survives Railway redeploys
- RSS date parser fix (root cause of empty publishedAt strings)
- Bulk COPY load step (per-row INSERTs slow now even when working)
- Priority scorer recalibration (non-statistical claims cap at 29)
- Transparency surface brief (separate doc) — build this week

## Open items not addressed tonight
- Verify verdicts actually land tomorrow:
  psql ... -c "SELECT MAX(last_checked) FROM claims;"
- Verify leaderboard UI updates verumsignal.com/leaderboard
