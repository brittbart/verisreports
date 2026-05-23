#!/usr/bin/env python3
"""
Patch: add verdict_status='provisional' and methodology_version to
extract_debate_claims.py INSERT statement.

This is the critical v1.7 audit fix — without it, debate claims are inserted
without verdict_status, which means promote_provisional_verdicts() never
finds them and they stay unscored permanently.

Run from ~/projects/veris with venv activated:
    python3 patch_debate_claims_provisional.py
"""

import sys, os, shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'extract_debate_claims.py')
BACKUP = TARGET + f'.bak.pre_provisional_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

OLD_INSERT = """        cur.execute(\"\"\"
            INSERT INTO claims (
                article_id, claim_text, speaker, claim_type,
                why_checkworthy, claim_origin, attribution_context,
                speaker_id, utterance_id, event_id,
                first_seen, last_seen, priority_score
            ) VALUES (
                NULL, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, NOW(), NOW(), 70
            ) RETURNING id
        \"\"\", (
            claim_text,
            speaker_name,
            claim.get('claim_type', 'factual'),
            claim.get('why_checkworthy', ''),
            CLAIM_ORIGIN,
            claim.get('attribution_context', ''),
            speaker_id,
            utterance_id,
            event_id,
        ))"""

NEW_INSERT = """        cur.execute(\"\"\"
            INSERT INTO claims (
                article_id, claim_text, speaker, claim_type,
                why_checkworthy, claim_origin, attribution_context,
                speaker_id, utterance_id, event_id,
                first_seen, last_seen, priority_score,
                verdict_status, methodology_version
            ) VALUES (
                NULL, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, NOW(), NOW(), 70,
                'provisional', %s
            ) RETURNING id
        \"\"\", (
            claim_text,
            speaker_name,
            claim.get('claim_type', 'factual'),
            claim.get('why_checkworthy', ''),
            CLAIM_ORIGIN,
            claim.get('attribution_context', ''),
            speaker_id,
            utterance_id,
            event_id,
            METHODOLOGY_VERSION,
        ))"""

def main():
    with open(TARGET, 'r') as f:
        content = f.read()

    # Validate anchor
    count = content.count(OLD_INSERT)
    if count == 0:
        # Check if already patched
        if 'verdict_status' in content and 'provisional' in content:
            print("✓ Already patched — verdict_status/methodology_version present in INSERT")
            sys.exit(0)
        print("✗ Anchor not found in extract_debate_claims.py")
        print("  The INSERT statement may have changed. Manual review required.")
        sys.exit(1)
    if count > 1:
        print(f"✗ Anchor appears {count} times — ambiguous. Aborting.")
        sys.exit(1)

    # Check METHODOLOGY_VERSION is defined
    if 'METHODOLOGY_VERSION' not in content:
        print("✗ METHODOLOGY_VERSION constant not found in file.")
        print("  Add: METHODOLOGY_VERSION = 'v1.7' near top of file.")
        sys.exit(1)

    # Backup
    shutil.copy2(TARGET, BACKUP)
    print(f"✓ Backed up to {os.path.basename(BACKUP)}")

    # Patch
    patched = content.replace(OLD_INSERT, NEW_INSERT, 1)

    with open(TARGET, 'w') as f:
        f.write(patched)

    # Verify
    with open(TARGET, 'r') as f:
        verify = f.read()

    if "verdict_status, methodology_version" not in verify:
        print("✗ Verification failed after write")
        sys.exit(1)

    print("✓ extract_debate_claims.py patched")
    print("  Debate claims will now be inserted with verdict_status='provisional'")
    print("  and methodology_version from METHODOLOGY_VERSION constant.")
    print("\n  Auto-promotion will fire correctly after 60 minutes via")
    print("  promote_provisional_verdicts() in railway_api_refresh.py.")

if __name__ == '__main__':
    main()
