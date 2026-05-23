#!/usr/bin/env python3
"""
Patch: register mobile_routes in api.py
Adds two lines after the existing register_debate_routes block.

Run from ~/projects/veris with venv activated:
    python3 patch_register_mobile_routes.py
"""

import sys
import os
import shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'api.py')
BACKUP = TARGET + f'.bak.pre_mobile_registration_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# Anchor: the two lines we insert immediately after
OLD = """from debate_routes import register_debate_routes
register_debate_routes(app, get_db)"""

NEW = """from debate_routes import register_debate_routes
register_debate_routes(app, get_db)
from mobile_routes import register_mobile_routes
register_mobile_routes(app, get_db)"""

def main():
    # Read
    with open(TARGET, 'r') as f:
        content = f.read()

    # Validate anchor appears exactly once
    count = content.count(OLD)
    if count == 0:
        print(f"✗ Anchor not found in {TARGET}")
        print(f"  Looking for:\n{OLD}")
        sys.exit(1)
    if count > 1:
        print(f"✗ Anchor appears {count} times — ambiguous. Aborting.")
        sys.exit(1)

    # Check if already patched
    if 'from mobile_routes import register_mobile_routes' in content:
        print("✓ Already patched — mobile_routes already registered in api.py")
        sys.exit(0)

    # Backup
    shutil.copy2(TARGET, BACKUP)
    print(f"✓ Backed up to {os.path.basename(BACKUP)}")

    # Patch
    patched = content.replace(OLD, NEW, 1)

    # Verify patch produced exactly one change
    if patched == content:
        print("✗ Patch produced no change — something went wrong")
        sys.exit(1)

    # Write
    with open(TARGET, 'w') as f:
        f.write(patched)

    # Verify written correctly
    with open(TARGET, 'r') as f:
        verify = f.read()

    if 'from mobile_routes import register_mobile_routes' not in verify:
        print("✗ Verification failed after write")
        sys.exit(1)

    print("✓ api.py patched — mobile_routes registered")
    print("\nNext: copy mobile_routes.py to ~/projects/veris/ then run:")
    print("  python3 -c \"import api\" 2>&1 | head -20")
    print("to verify import succeeds before pushing.")

if __name__ == '__main__':
    main()
