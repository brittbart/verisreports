"""
patch_quota_gate.py
Verum Signal — Add quota check before verify_and_insert_claims in /report route.

What this does:
  - Inserts a quota check immediately before verify_and_insert_claims fires.
  - Anonymous users (no session) are treated as free tier (2 reports/month).
  - Users over quota are redirected to /pricing.html with a reason param.
  - Users with quota remaining: quota is incremented, verification proceeds.
  - depth parameter and all existing logic are completely unchanged.

Run: python3 patch_quota_gate.py
"""

import sys
import shutil
from datetime import datetime

TARGET = 'api.py'
BACKUP = f'api.py.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# ── Anchor — unique string that appears exactly once ──────────────────────────
OLD = '                        art_id = row2[0]\n                        verified_claims = verify_and_insert_claims(claims, art_id, title_text, domain, cur2, depth=depth)'

# ── Replacement — quota check inserted before verify_and_insert_claims ────────
NEW = '''                        art_id = row2[0]

                        # ── Quota gate (consumer product) ──────────────────
                        # Checks session-based user quota before firing the
                        # verification engine. Anonymous users get free-tier
                        # limit (2/month). Over-quota requests redirect to
                        # /pricing.html — no verification cost incurred.
                        # depth param and all existing logic are unchanged.
                        from auth_routes import get_current_user, check_quota, increment_quota
                        _gate_user = get_current_user(get_db)
                        if _gate_user:
                            _quota = check_quota(get_db, _gate_user['id'], 'consumer')
                            if not _quota['allowed']:
                                conn2.close()
                                conn.close()
                                return redirect(
                                    f'/pricing.html?reason=quota_exceeded'
                                    f'&used={_quota["used"]}'
                                    f'&limit={_quota["limit"]}'
                                    f'&tier={_quota["tier"]}'
                                )
                        # ── End quota gate ─────────────────────────────────

                        verified_claims = verify_and_insert_claims(claims, art_id, title_text, domain, cur2, depth=depth)

                        # Increment quota on successful verification
                        if _gate_user:
                            increment_quota(get_db, _gate_user['id'], 'consumer')'''

def run():
    with open(TARGET, 'r') as f:
        content = f.read()

    # Validate anchor appears exactly once
    count = content.count(OLD)
    if count == 0:
        print('[FAIL] Anchor not found in api.py — patch cannot be applied.')
        print('       The file may have changed. Review patch_quota_gate.py manually.')
        sys.exit(1)
    if count > 1:
        print(f'[FAIL] Anchor found {count} times — ambiguous. Patch aborted.')
        sys.exit(1)

    print(f'[OK]   Anchor found exactly once.')

    # Backup
    shutil.copy2(TARGET, BACKUP)
    print(f'[OK]   Backup written to {BACKUP}')

    # Apply
    new_content = content.replace(OLD, NEW)

    # Verify replacement happened
    if new_content == content:
        print('[FAIL] Replacement produced no change — aborting.')
        sys.exit(1)

    with open(TARGET, 'w') as f:
        f.write(new_content)

    print('[OK]   Patch applied.')

    # Verify anchor is gone and new content is present
    with open(TARGET, 'r') as f:
        verify = f.read()

    if OLD in verify:
        print('[FAIL] Old anchor still present after patch — check file manually.')
        sys.exit(1)

    if 'Quota gate (consumer product)' not in verify:
        print('[FAIL] New content not found after patch — check file manually.')
        sys.exit(1)

    print('[OK]   Patch verified in file.')
    print('\nNext: python3 -c "from api import app; print(\'import OK\')"')

if __name__ == '__main__':
    run()
