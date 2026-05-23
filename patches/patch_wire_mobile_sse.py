#!/usr/bin/env python3
"""
Patch: wire mobile_sse.py SSE stream into mobile_routes.py.
Adds the /mobile/v1/debates/<slug>/stream endpoint.

Run from ~/projects/veris with venv activated:
    python3 patch_wire_mobile_sse.py
"""

import sys, os, shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'mobile_routes.py')
BACKUP = TARGET + f'.bak.pre_mobile_sse_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

patches = []

# ── 1. Import mobile_sse at top ────────────────────────────────────────────
patches.append(("import_sse",
    "try:\n    from vs_summary_generator import get_or_generate_vs_summary\n    VS_SUMMARY_ENABLED = True\nexcept ImportError:\n    VS_SUMMARY_ENABLED = False",
    "try:\n    from vs_summary_generator import get_or_generate_vs_summary\n    VS_SUMMARY_ENABLED = True\nexcept ImportError:\n    VS_SUMMARY_ENABLED = False\n\ntry:\n    from mobile_sse import register_sse_routes\n    SSE_ENABLED = True\nexcept ImportError:\n    SSE_ENABLED = False"
))

# ── 2. Wire SSE routes in register_mobile_routes ───────────────────────────
patches.append(("wire_sse_in_register",
    """    # Attach get_db to blueprint so endpoints can call mobile_bp.get_db()
    mobile_bp.get_db = get_db_fn
    app.register_blueprint(mobile_bp)
    print("[mobile_routes] registered /mobile/v1/* endpoints")""",

    """    # Attach get_db to blueprint so endpoints can call mobile_bp.get_db()
    mobile_bp.get_db = get_db_fn

    # Wire SSE stream routes
    if SSE_ENABLED:
        register_sse_routes(mobile_bp, get_db_fn)
        print("[mobile_routes] registered /mobile/v1/debates/<slug>/stream (SSE)")
    else:
        print("[mobile_routes] WARNING: mobile_sse.py not found — SSE stream unavailable")

    app.register_blueprint(mobile_bp)
    print("[mobile_routes] registered /mobile/v1/* endpoints")"""
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

    print(f"\n✓ SSE stream wired into mobile_routes.py")
    print("  Endpoint: GET /mobile/v1/debates/<slug>/stream")

if __name__ == '__main__':
    main()
