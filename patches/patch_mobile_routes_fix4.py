#!/usr/bin/env python3
"""
Patch: fix remaining r['byline'] KeyError in articles response.
Run from ~/projects/veris with venv activated:
    python3 patch_mobile_routes_fix4.py
"""

import sys, os, shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'mobile_routes.py')
BACKUP = TARGET + f'.bak.pre_schema_fix4_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

OLD = '"byline":       r[\'byline\'],'
NEW = '"byline":       None,'

def main():
    with open(TARGET, 'r') as f:
        content = f.read()

    count = content.count(OLD)
    if count == 0:
        print("SKIP — already fixed")
        sys.exit(0)
    if count > 1:
        print(f"ERROR — appears {count} times, ambiguous")
        sys.exit(1)

    shutil.copy2(TARGET, BACKUP)
    print(f"✓ Backed up to {os.path.basename(BACKUP)}")

    content = content.replace(OLD, NEW, 1)
    with open(TARGET, 'w') as f:
        f.write(content)
    print("✓ Fixed r['byline'] → None")

if __name__ == '__main__':
    main()
