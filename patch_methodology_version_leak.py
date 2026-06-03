"""
patch_methodology_version_leak.py
Verum Signal — Fix v1.7 string leak in debate_routes.py (Audit Finding 5.1)

METHODOLOGY_VERSION = 'v1.7' in api_leaderboard.py is the internal version.
Public-facing pages must show PUBLIC_METHODOLOGY_VERSIONS[-1] (currently 'v1.6').

Fix: add PUBLIC_METHODOLOGY_VERSION derived from env var at top of debate_routes.py,
replace all public template renders to use it instead of METHODOLOGY_VERSION.

Run: python3 patch_methodology_version_leak.py
"""

import sys
import shutil
from datetime import datetime

TARGET = 'debate_routes.py'
BACKUP = f'debate_routes.py.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# ── Step 1: Add PUBLIC_METHODOLOGY_VERSION after the import line ──────────────
OLD_IMPORT = 'from api_leaderboard import METHODOLOGY_VERSION, VERDICT_LABELS'

NEW_IMPORT = '''from api_leaderboard import METHODOLOGY_VERSION, VERDICT_LABELS
import ast as _ast, os as _os
# Public-facing methodology version — gated by attorney approval.
# Derived from PUBLIC_METHODOLOGY_VERSIONS env var (same source as mobile_routes.py).
# Internal METHODOLOGY_VERSION may be ahead of what's publicly published.
_raw_pmv = _os.environ.get('PUBLIC_METHODOLOGY_VERSIONS', "['v1.6']")
try:
    PUBLIC_METHODOLOGY_VERSION = _ast.literal_eval(_raw_pmv)[-1]
except Exception:
    PUBLIC_METHODOLOGY_VERSION = 'v1.6'
del _ast, _os, _raw_pmv'''

# ── Step 2: Replace methodology_version=METHODOLOGY_VERSION in template renders
# There are two render_template calls at lines 503 and 540 that pass
# methodology_version=METHODOLOGY_VERSION to the public debate template.
# Both need to use PUBLIC_METHODOLOGY_VERSION instead.
OLD_RENDER = 'methodology_version=METHODOLOGY_VERSION,'
NEW_RENDER = 'methodology_version=PUBLIC_METHODOLOGY_VERSION,'


def run():
    with open(TARGET, 'r') as f:
        content = f.read()

    # ── Patch 1: import block ─────────────────────────────────────────────────
    count = content.count(OLD_IMPORT)
    if count == 0:
        print('[FAIL] Import anchor not found.')
        sys.exit(1)
    if count > 1:
        print(f'[FAIL] Import anchor found {count} times — ambiguous.')
        sys.exit(1)
    print('[OK]   Import anchor found exactly once.')

    # ── Patch 2: render calls ─────────────────────────────────────────────────
    render_count = content.count(OLD_RENDER)
    if render_count == 0:
        print('[FAIL] Render anchor not found.')
        sys.exit(1)
    print(f'[OK]   Render anchor found {render_count} times (will replace all).')

    shutil.copy2(TARGET, BACKUP)
    print(f'[OK]   Backup written to {BACKUP}')

    # Apply patch 1
    content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)

    # Apply patch 2 — replace all occurrences
    content = content.replace(OLD_RENDER, NEW_RENDER)

    with open(TARGET, 'w') as f:
        f.write(content)

    print('[OK]   Patches applied.')

    # Verify
    with open(TARGET, 'r') as f:
        verify = f.read()

    checks = [
        ('PUBLIC_METHODOLOGY_VERSION defined', 'PUBLIC_METHODOLOGY_VERSION ='),
        ('render uses public version', 'methodology_version=PUBLIC_METHODOLOGY_VERSION,'),
        ('old render not present', 'methodology_version=METHODOLOGY_VERSION,' not in verify),
    ]
    all_ok = True
    for label, check in checks:
        if isinstance(check, bool):
            ok = check
        else:
            ok = check in verify
        print(f"  {'[OK]' if ok else '[FAIL]'}  {label}")
        if not ok:
            all_ok = False

    if not all_ok:
        print('\n[FAIL] Verification failed — review file manually.')
        sys.exit(1)

    print('\n[OK]   Patch verified.')
    print('\nNext: python3 -c "from debate_routes import register_debate_routes; print(\'import OK\')"')


if __name__ == '__main__':
    run()
