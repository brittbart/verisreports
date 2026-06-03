"""
patch_token_cleanup.py
Verum Signal — Add magic_link_tokens cleanup to railway_api_refresh.py

Deletes expired or used magic_link_tokens older than 24 hours.
Runs after prune_usage(cur), same non-fatal pattern.
Single SQL call, no application logic.

Run: python3 patch_token_cleanup.py
"""

import sys
import shutil
from datetime import datetime

TARGET = 'railway_api_refresh.py'
BACKUP = f'railway_api_refresh.py.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'

OLD = '        prune_usage(cur)\n        conn.commit()'

NEW = '''        prune_usage(cur)

        # Clean up expired/used magic_link_tokens older than 24 hours.
        # Keeps the table from growing unbounded. Non-fatal.
        try:
            cur.execute("""
                DELETE FROM magic_link_tokens
                WHERE created_at < NOW() - INTERVAL '24 hours'
            """)
            deleted = cur.rowcount
            if deleted:
                log.info(f"token_cleanup: deleted {deleted} expired magic_link_tokens")
        except Exception:
            log.warning("token_cleanup failed — non-fatal")

        conn.commit()'''


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

    if OLD in verify:
        print('[FAIL] Old anchor still present.')
        sys.exit(1)

    if 'token_cleanup' not in verify:
        print('[FAIL] New content not found.')
        sys.exit(1)

    print('[OK]   Patch verified.')
    print('\nNext: python3 -c "import railway_api_refresh; print(\'import OK\')"')


if __name__ == '__main__':
    run()
