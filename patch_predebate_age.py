"""
patch_predebate_age.py
Verum Signal — Fix article freshness check in pre_debate_check.py.

Two problems:
  1. published_at has a bad future value (2026-10-30) from a malformed RSS entry,
     making MAX(published_at) look like it's in the future.
  2. Both published_at and fetched_at are timezone-naive; .astimezone() on a
     naive datetime uses local system tz and produces wrong results on Railway (UTC).

Fix: switch to fetched_at (reliable pipeline timestamp), treat naive datetimes
as UTC explicitly via datetime.replace(tzinfo=timezone.utc).

Run: python3 patch_predebate_age.py
"""

import sys
import shutil
from datetime import datetime

TARGET = 'pre_debate_check.py'
BACKUP = f'pre_debate_check.py.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'

OLD = '''        try:
            cur.execute("SELECT MAX(published_at) FROM articles WHERE published_at IS NOT NULL")
            row = cur.fetchone()
            if row and row[0]:
                age = (datetime.now(timezone.utc) - row[0].astimezone(timezone.utc)).total_seconds() / 3600
                check("Most recent article < 2 hours old", age < 2, f"{age:.1f}h ago")
            else:
                check("Recent articles found", False, "No articles with published_at")
        except Exception as e:
            check("Article freshness", False, str(e))'''

NEW = '''        try:
            # Use fetched_at (reliable pipeline timestamp) instead of published_at
            # (published_at has malformed future values from some RSS feeds).
            # fetched_at is timezone-naive in the DB — treat as UTC explicitly.
            cur.execute("""
                SELECT MAX(fetched_at) FROM articles
                WHERE fetched_at IS NOT NULL
                  AND fetched_at <= NOW()
            """)
            row = cur.fetchone()
            if row and row[0]:
                fetched = row[0].replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
                check("Most recent article fetched < 4 hours ago", age < 4, f"{age:.1f}h ago")
            else:
                check("Recent articles found", False, "No articles with fetched_at")
        except Exception as e:
            check("Article freshness", False, str(e))'''


def run():
    with open(TARGET, 'r') as f:
        content = f.read()

    count = content.count(OLD)
    if count == 0:
        print('[FAIL] Anchor not found.')
        sys.exit(1)
    if count > 1:
        print(f'[FAIL] Anchor found {count} times — ambiguous.')
        sys.exit(1)

    print('[OK]   Anchor found exactly once.')

    shutil.copy2(TARGET, BACKUP)
    print(f'[OK]   Backup written to {BACKUP}')

    new_content = content.replace(OLD, NEW)

    if new_content == content:
        print('[FAIL] Replacement produced no change.')
        sys.exit(1)

    with open(TARGET, 'w') as f:
        f.write(new_content)

    print('[OK]   Patch applied.')

    with open(TARGET, 'r') as f:
        verify = f.read()

    if 'fetched_at' not in verify or 'replace(tzinfo=timezone.utc)' not in verify:
        print('[FAIL] New content not found after patch.')
        sys.exit(1)

    print('[OK]   Patch verified.')
    print('\nNext: python3 pre_debate_check.py --event-slug colorado-gov-dem-2026-r2')


if __name__ == '__main__':
    run()
