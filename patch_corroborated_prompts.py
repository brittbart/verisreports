"""
patch_corroborated_prompts.py
Verum Signal — Harmonize corroborated definition across all 3 prompt builders.
Audit Finding 2.1. D-017 fixed the weight (+0.75) but not the prompt text.

Builder 2 (inline sync/debate path, lines 175+191) is canonical — correct per D-017.
Builder 1 (build_attributed_prompt) and Builder 3 (build_prompt) are brought into line.

Changes:
  1. Builder 1 consensus exception (one-liner → full paragraph)
  2. Builder 1 corroborated definition (terse → full with weight)
  3. Builder 3 consensus exception (one-liner → full paragraph)
  4. Builder 3 corroborated definition (missing → added to verdict definitions)

No logic, weights, DB schema, or route changes. Prompt text only.

Run: python3 patch_corroborated_prompts.py
"""

import sys
import shutil
from datetime import datetime

TARGET = 'verdict_engine.py'
BACKUP = f'verdict_engine.py.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# ── Canonical text from Builder 2 (do not modify) ────────────────────────────
CANONICAL_CONSENSUS = (
    "CONSENSUS EXCEPTION:\n"
    "If 5 or more outlets are consistently reporting the same claim without contradiction, "
    "assign corroborated at confidence 2/3 even if you cannot confirm each outlet independently "
    "sourced the information. Widespread consistent reporting across multiple outlets is a strong "
    "signal of accuracy. If any credible outlet contradicts the claim, use disputed instead "
    "regardless of how many outlets agree."
)

CANONICAL_DEFINITION = (
    "- corroborated: 5 or more outlets are consistently reporting the same claim without "
    "contradiction, but full independence cannot be established. Use this when the consensus "
    "exception applies. Counts as +0.75 (weaker than supported)."
)

# ── Builder 1 patches (build_attributed_prompt) ───────────────────────────────

B1_OLD_CONSENSUS = (
    "CONSENSUS EXCEPTION: If 5+ outlets consistently report the same factual content "
    "without contradiction, assign corroborated at confidence 2/3."
)

B1_OLD_DEFINITION = (
    "- corroborated: 5+ outlets consistently report same factual content"
)

B1_NEW_DEFINITION = CANONICAL_DEFINITION

# ── Builder 3 patches (build_prompt) ─────────────────────────────────────────

B3_OLD_CONSENSUS = (
    "CONSENSUS EXCEPTION: If 5+ outlets consistently report the same claim without "
    "contradiction, assign corroborated at confidence 2/3."
)

# Builder 3 is missing corroborated from definitions entirely.
# Insert it after not_verifiable definition.
B3_OLD_DEFINITION = (
    "- not_verifiable: Cannot confirm - sources unavailable\n"
    "- opinion: Value judgement or prediction"
)

B3_NEW_DEFINITION = (
    "- not_verifiable: Cannot confirm - sources unavailable\n"
    "- corroborated: 5 or more outlets are consistently reporting the same claim without "
    "contradiction, but full independence cannot be established. Use this when the consensus "
    "exception applies. Counts as +0.75 (weaker than supported).\n"
    "- opinion: Value judgement or prediction"
)


def apply(content, old, new, label):
    count = content.count(old)
    if count == 0:
        print(f'[FAIL] Anchor not found: {label}')
        sys.exit(1)
    if count > 1:
        print(f'[FAIL] Anchor ambiguous ({count} matches): {label}')
        sys.exit(1)
    print(f'[OK]   Found: {label}')
    return content.replace(old, new, 1)


def run():
    with open(TARGET, 'r') as f:
        content = f.read()

    shutil.copy2(TARGET, BACKUP)
    print(f'[OK]   Backup written to {BACKUP}\n')

    # Builder 1 — consensus exception
    content = apply(content, B1_OLD_CONSENSUS, CANONICAL_CONSENSUS,
                    'Builder 1 consensus exception')

    # Builder 1 — corroborated definition
    content = apply(content, B1_OLD_DEFINITION, B1_NEW_DEFINITION,
                    'Builder 1 corroborated definition')

    # Builder 3 — consensus exception
    content = apply(content, B3_OLD_CONSENSUS, CANONICAL_CONSENSUS,
                    'Builder 3 consensus exception')

    # Builder 3 — corroborated definition (insert)
    content = apply(content, B3_OLD_DEFINITION, B3_NEW_DEFINITION,
                    'Builder 3 corroborated definition (insert)')

    with open(TARGET, 'w') as f:
        f.write(content)

    print('\n[OK]   All patches applied.')

    # ── Verify ────────────────────────────────────────────────────────────────
    with open(TARGET, 'r') as f:
        verify = f.read()

    # Count canonical consensus — should appear exactly 3 times (one per builder)
    consensus_count = verify.count(CANONICAL_CONSENSUS)
    definition_count = verify.count(CANONICAL_DEFINITION)

    print(f'\n── Verification ──')
    print(f'  Canonical consensus exception appears: {consensus_count}x (expect 3)')
    print(f'  Canonical definition appears:          {definition_count}x (expect 3)')

    # Old terse versions should be gone
    old_b1_consensus_gone = B1_OLD_CONSENSUS not in verify
    old_b3_consensus_gone = B3_OLD_CONSENSUS not in verify
    old_b1_def_gone = B1_OLD_DEFINITION not in verify

    print(f'  Builder 1 old consensus gone: {old_b1_consensus_gone}')
    print(f'  Builder 3 old consensus gone: {old_b3_consensus_gone}')
    print(f'  Builder 1 old definition gone: {old_b1_def_gone}')

    if consensus_count == 3 and definition_count == 3 and all([
        old_b1_consensus_gone, old_b3_consensus_gone, old_b1_def_gone
    ]):
        print('\n[OK]   Verification passed.')
    else:
        print('\n[FAIL] Verification failed — review file manually.')
        sys.exit(1)

    print('\nNext: python3 -c "from verdict_engine import analyse_claim; print(\'import OK\')"')


if __name__ == '__main__':
    run()
