#!/usr/bin/env python3
"""
Patch: wire vs_summary_generator into mobile_routes.py article_report endpoint.
Adds lazy vs_summary generation on report fetch.

Run from ~/projects/veris with venv activated:
    python3 patch_wire_vs_summary.py
"""

import sys, os, shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'mobile_routes.py')
BACKUP = TARGET + f'.bak.pre_vs_summary_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

patches = []

# ── 1. Add import at top of file ───────────────────────────────────────────
patches.append(("add_import",
    "from flask import Blueprint, jsonify, request, g",
    "from flask import Blueprint, jsonify, request, g\ntry:\n    from vs_summary_generator import get_or_generate_vs_summary\n    VS_SUMMARY_ENABLED = True\nexcept ImportError:\n    VS_SUMMARY_ENABLED = False"
))

# ── 2. Wire lazy generation into article_report after fetching the row ─────
patches.append(("wire_lazy_generation",
    """        (art_id, url, title, published_at,
         source_name, vs_summary, outlet_domain, outlet_name,
         outlet_score, outlet_tier, report_hash) = row""",

    """        (art_id, url, title, published_at,
         source_name, vs_summary, outlet_domain, outlet_name,
         outlet_score, outlet_tier, report_hash) = row

        # Lazy vs_summary generation — only fires if not already cached
        if not vs_summary and VS_SUMMARY_ENABLED:
            vs_summary = get_or_generate_vs_summary(article_id, db)"""
))

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
            print(f"  [{name}] ERROR — anchor appears {count} times. Aborting.")
            all_ok = False
            break
        content = content.replace(old, new, 1)
        print(f"  [{name}] ✓ applied")

    if not all_ok:
        print("\n✗ Aborted — no changes written")
        sys.exit(1)

    with open(TARGET, 'w') as f:
        f.write(content)

    print(f"\n✓ Patches applied. vs_summary will generate lazily on report fetch.")
    print("  Import is wrapped in try/except — safe even if vs_summary_generator.py missing.")

if __name__ == '__main__':
    main()
