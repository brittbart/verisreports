#!/usr/bin/env python3
"""
Patch: fix SSE request context error.
Move request.args read into the route handler, pass since_id to generator.

Run from ~/projects/veris with venv activated:
    python3 patch_sse_request_context.py
"""

import sys, os, shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'mobile_sse.py')
BACKUP = TARGET + f'.bak.pre_ctx_fix_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

patches = []

# ── 1. Remove request.args read from generator ─────────────────────────────
patches.append(("remove_request_from_generator",
    """    # Parse client's last-seen claim ID (for reconnection support)
    last_claim_id = 0
    try:
        last_claim_id = int(request.args.get('since_id', 0))
    except (ValueError, TypeError):
        last_claim_id = 0""",

    """    # since_id passed in from route handler (outside generator, inside request ctx)
    # Default already set by caller"""
))

# ── 2. Add since_id parameter to generator signature ──────────────────────
patches.append(("add_since_id_param",
    "def debate_stream_generator(slug: str, get_db,\n                             poll_interval: int = 5,\n                             heartbeat_interval: int = 15,\n                             max_duration: int = 14400):  # 4 hours max",
    "def debate_stream_generator(slug: str, get_db,\n                             since_id: int = 0,\n                             poll_interval: int = 5,\n                             heartbeat_interval: int = 15,\n                             max_duration: int = 14400):  # 4 hours max"
))

# ── 3. Use since_id parameter instead of local var ─────────────────────────
patches.append(("use_since_id_param",
    "    # Send connection confirmation with all existing claims\n    existing_claims = _get_claims_since(event_id, last_claim_id, get_db)",
    "    # Send connection confirmation with all existing claims\n    last_claim_id = since_id\n    existing_claims = _get_claims_since(event_id, last_claim_id, get_db)"
))

# ── 4. Read request.args in route handler, pass to generator ───────────────
patches.append(("read_args_in_handler",
    """    @mobile_bp.route('/debates/<slug>/stream')
    def debate_stream(slug):
        def generate():
            yield from debate_stream_generator(slug, get_db)

        return Response(
            generate(),""",

    """    @mobile_bp.route('/debates/<slug>/stream')
    def debate_stream(slug):
        # Read request args HERE (inside request context) before generator runs
        try:
            since_id = int(request.args.get('since_id', 0))
        except (ValueError, TypeError):
            since_id = 0

        def generate():
            yield from debate_stream_generator(slug, get_db, since_id=since_id)

        return Response(
            generate(),"""
))

# ── 5. Add request import to mobile_sse.py ────────────────────────────────
patches.append(("add_request_import",
    "from flask import Response, request",
    "from flask import Response, request"  # already there, just verify
))

def main():
    with open(TARGET, 'r') as f:
        content = f.read()

    shutil.copy2(TARGET, BACKUP)
    print(f"✓ Backed up to {os.path.basename(BACKUP)}")

    all_ok = True
    for name, old, new in patches:
        if old == new:
            print(f"  [{name}] VERIFY — already correct")
            continue
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

    print(f"\n✓ Request context fix applied to mobile_sse.py")

if __name__ == '__main__':
    main()
