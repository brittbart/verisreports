#!/usr/bin/env python3
"""
patch_extract_claims_wire_detection.py

Adds wire-reprint detection to extract_claims.py per Methodology v1.6.

What it does:
  Injects WIRE_SOURCES, WIRE_BYLINE_PATTERNS, and a classify_claim_origin()
  helper into extract_claims.py. After this patch, you must wire the helper
  into wherever claims get persisted to the DB (the patch tells you where
  to look).

Usage:
    python3 scripts/patch_extract_claims_wire_detection.py --dry-run
    python3 scripts/patch_extract_claims_wire_detection.py --apply

Idempotent: if extract_claims.py already contains classify_claim_origin,
the patch is a no-op.
"""

import os
import sys
import re
import argparse
import shutil
from datetime import datetime

EXTRACT_PATH = os.path.expanduser('~/projects/veris/extract_claims.py')

INJECT_BLOCK = '''
# === claim_origin classification (v1.6 methodology) ===
# Mirrors api.py:865 -- keep in sync.
WIRE_SOURCES = {'reuters.com', 'apnews.com', 'ap.org', 'bloomberg.com', 'afp.com'}

WIRE_BYLINE_PATTERNS = [
    re.compile(r'\\(\\s*Reuters\\s*\\)', re.IGNORECASE),
    re.compile(r'\\(\\s*AP\\s*\\)'),
    re.compile(r'\\(\\s*Associated\\s+Press\\s*\\)', re.IGNORECASE),
    re.compile(r'\\(\\s*AFP\\s*\\)'),
    re.compile(r'\\(\\s*Bloomberg\\s*\\)', re.IGNORECASE),
]
BYLINE_SEARCH_CHARS = 800


def classify_claim_origin(source_name, article_body, speaker_field, attribution_field):
    """
    Returns one of: outlet_claim, attributed_claim, wire_reprint.
    Implements v1.6 categories.
    """
    source = (source_name or '').lower().strip()
    body = article_body or ''
    speaker = (speaker_field or '').strip()
    attribution = (attribution_field or '').strip()

    # Wires publishing on their own domain -> outlet_claim (Position B)
    if source in WIRE_SOURCES:
        return 'outlet_claim'

    # Non-wire outlet with a wire byline marker in opening -> wire_reprint
    head = body[:BYLINE_SEARCH_CHARS]
    for pat in WIRE_BYLINE_PATTERNS:
        if pat.search(head):
            return 'wire_reprint'

    # Speaker + attribution context strongly suggest an attributed claim
    if speaker and len(speaker) > 2 and speaker.lower() not in ('unknown', 'n/a', 'none'):
        if attribution and any(marker in attribution.lower() for marker in
                               ('said', 'told', 'according to', 'argued', 'claimed',
                                'stated', 'reported', 'wrote')):
            return 'attributed_claim'

    return 'outlet_claim'
# === end claim_origin classification ===

'''


def find_insertion_anchor(content):
    lines = content.split('\n')
    last_import_line = 0
    for i, line in enumerate(lines[:100]):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            last_import_line = i
    return last_import_line + 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Write changes (default: dry-run)')
    args = parser.parse_args()

    if not os.path.exists(EXTRACT_PATH):
        print(f"ERROR: {EXTRACT_PATH} not found.")
        sys.exit(1)

    with open(EXTRACT_PATH, 'r') as f:
        content = f.read()

    if 'classify_claim_origin' in content:
        print(f"{EXTRACT_PATH} already contains classify_claim_origin -- nothing to do.")
        sys.exit(0)

    if not re.search(r'^\s*import\s+re\b', content, re.MULTILINE):
        print("Note: 'import re' not found -- adding it with the injected block.")
        global INJECT_BLOCK
        INJECT_BLOCK = 'import re\n' + INJECT_BLOCK

    insertion_line = find_insertion_anchor(content)
    lines = content.split('\n')
    new_lines = lines[:insertion_line] + INJECT_BLOCK.split('\n') + lines[insertion_line:]
    new_content = '\n'.join(new_lines)

    print(f"Insertion point: after line {insertion_line}")
    print(f"Injecting {len(INJECT_BLOCK.split(chr(10)))} lines.")
    print()
    print("=== Diff preview (first 60 lines of new content) ===")
    for i, line in enumerate(new_content.split('\n')[:60], 1):
        print(f"{i:4d}  {line}")
    print()

    if args.apply:
        backup = f"{EXTRACT_PATH}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(EXTRACT_PATH, backup)
        print(f"Backed up to {backup}")
        with open(EXTRACT_PATH, 'w') as f:
            f.write(new_content)
        print(f"Wrote patched file: {EXTRACT_PATH}")
        print()
        print("NEXT STEPS:")
        print("  1. Review:  git diff extract_claims.py")
        print("  2. Find where claims get persisted to DB and route through classify_claim_origin().")
        print("     Likely an INSERT or update on the claims table inside extract_claims.py.")
        print("  3. Restart scheduler:  sudo systemctl restart veris.service")
    else:
        print("(dry-run -- no file written. Re-run with --apply to commit.)")


if __name__ == '__main__':
    main()
