#!/usr/bin/env python3
"""
Patch: add lazy short URL generation to mobile_routes.py article_report endpoint.
When report_hash is None, calls get_or_create_short_hash() to generate one.

Run from ~/projects/veris with venv activated:
    python3 patch_mobile_short_url.py
"""

import sys, os, shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'mobile_routes.py')
BACKUP = TARGET + f'.bak.pre_short_url_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

patches = []

# ── 1. Import get_or_create_short_hash ─────────────────────────────────────
patches.append(("import_short_hash",
    "try:\n    from vs_summary_generator import get_or_generate_vs_summary\n    VS_SUMMARY_ENABLED = True\nexcept ImportError:\n    VS_SUMMARY_ENABLED = False",
    "try:\n    from vs_summary_generator import get_or_generate_vs_summary\n    VS_SUMMARY_ENABLED = True\nexcept ImportError:\n    VS_SUMMARY_ENABLED = False\n\ntry:\n    from api import get_or_create_short_hash\n    SHORT_URL_ENABLED = True\nexcept ImportError:\n    SHORT_URL_ENABLED = False"
))

# ── 2. Generate short hash lazily after vs_summary generation ──────────────
patches.append(("lazy_short_hash",
    "        # Lazy vs_summary generation — only fires if not already cached\n        if not vs_summary and VS_SUMMARY_ENABLED:\n            vs_summary = get_or_generate_vs_summary(article_id, db)",
    "        # Lazy vs_summary generation — only fires if not already cached\n        if not vs_summary and VS_SUMMARY_ENABLED:\n            vs_summary = get_or_generate_vs_summary(article_id, db)\n\n        # Lazy short URL generation — only fires if no hash exists yet\n        if not report_hash and SHORT_URL_ENABLED:\n            try:\n                report_hash = get_or_create_short_hash(article_id)\n            except Exception:\n                pass  # non-fatal — share URL just won't work this request"
))

# ── 3. Update share_url to use generated hash ──────────────────────────────
# Already uses report_hash variable — no change needed, it's dynamic

def main():
    with open(TARGET, 'r') as f:
        content = f.read()

    shutil.copy2(TARGET, BACKUP)
    print(f"✓ Backed up to {os.path.basename(BACKUP)}")

    all_ok = True
    for name, old, new in patches:
        count = content.count(old)
        if count == 0:
            print(f"  [{name}] SKIP — anchor not found")
            continue
        if count > 1:
            print(f"  [{name}] ERROR — appears {count} times. Aborting.")
            all_ok = False
            break
        content = content.replace(old, new, 1)
        print(f"  [{name}] ✓ applied")

    if not all_ok:
        print("\n✗ Aborted — no changes written")
        sys.exit(1)

    with open(TARGET, 'w') as f:
        f.write(content)

    print(f"\n✓ Lazy short URL generation wired into article_report endpoint.")
    print("  First report fetch for any article will now generate a share URL.")

if __name__ == '__main__':
    main()
