#!/usr/bin/env python3
"""
Patch 1 — Add May 26 speaker name variants to build_name_map()
Target: debate_stream_deepgram.py
Risk addressed: D-16 (stale misspelling variants)

Adds phonetic/mis-transcription variants for:
  - Bottoms (Scott Bottoms)
  - Kirkmeyer (Barbara Kirkmeyer)
  - Marx (Victor Marx)

Retains existing Turek/Wahls variants (harmless if not in event_speakers).
Adds explicit 'marx' entry to misspellings dict to control collision risk.
"""

import sys

TARGET = 'debate_stream_deepgram.py'

OLD = """    # Rev AI misspelling variants
    misspellings = {
        'turek': ['turk', 'terk', 'turek'],
        'wahls': ['walz', 'walls', 'wals', 'wahls'],
    }"""

NEW = """    # Rev AI misspelling variants (retained — harmless if not in event_speakers)
    # May 26 Colorado Gov GOP R2: Bottoms, Kirkmeyer, Marx
    misspellings = {
        'turek': ['turk', 'terk', 'turek'],
        'wahls': ['walz', 'walls', 'wals', 'wahls'],
        'bottoms': ['bottoms', 'bottom'],
        'kirkmeyer': ['kirkmeyer', 'kirkmeier', 'kirkmyer', 'kirk meyer'],
        'marx': ['marx'],  # Explicit entry — log every match during dry run for false-positive audit
    }"""

def apply():
    with open(TARGET, 'r') as f:
        content = f.read()

    count = content.count(OLD)
    if count == 0:
        print(f"ERROR: anchor not found in {TARGET}. File may have changed. Aborting.")
        sys.exit(1)
    if count > 1:
        print(f"ERROR: anchor found {count} times — ambiguous. Aborting.")
        sys.exit(1)

    patched = content.replace(OLD, NEW)

    with open(TARGET, 'w') as f:
        f.write(patched)

    print(f"OK: patch applied to {TARGET}")
    print("Verify with: grep -A 10 'misspellings' debate_stream_deepgram.py")

if __name__ == '__main__':
    apply()
