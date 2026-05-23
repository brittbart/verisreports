#!/usr/bin/env python3
"""
Patch 2 — Persist dg_to_db speaker mappings across stream restarts
Risk addressed: D-13 (post-restart speaker mis-attribution)

Changes to debate_stream_deepgram.py:
1. After dg_to_db = {} initialization: load persisted mappings from events.dg_speaker_map
2. Add persist_mapping() helper (try/except — stream must not crash on DB write failure)
3. Call persist_mapping() at [CONFIRMED] and [ORDER] assignment points
"""

import sys

TARGET = 'debate_stream_deepgram.py'

# --- Change 1: Load persisted mappings after dg_to_db initialization ---

OLD_INIT = """    dg_to_db = {}          # confirmed mappings
    pending_speaker = [None]  # pending name cue waiting for next new speaker
    # Order-based fallback: first new speaker = speaker_order[0], etc.
    order_assigned = []
    utterance_count = [0]"""

NEW_INIT = """    dg_to_db = {}          # confirmed mappings
    pending_speaker = [None]  # pending name cue waiting for next new speaker
    # Order-based fallback: first new speaker = speaker_order[0], etc.
    order_assigned = []
    utterance_count = [0]

    # Load persisted speaker mappings from DB (survive stream restarts)
    try:
        _map_conn = get_db_conn()
        _map_cur = _map_conn.cursor()
        _map_cur.execute("SELECT dg_speaker_map FROM events WHERE id = %s", (event_id,))
        _map_row = _map_cur.fetchone()
        if _map_row and _map_row[0]:
            for dg_idx_str, sid in _map_row[0].items():
                dg_to_db[int(dg_idx_str)] = sid
            print(f"  [PERSIST] Loaded {len(dg_to_db)} speaker mapping(s) from DB: {dg_to_db}")
        else:
            print("  [PERSIST] No prior speaker mappings found — starting fresh")
        _map_cur.close()
        _map_conn.close()
    except Exception as _e:
        print(f"  [PERSIST] WARNING: Could not load speaker mappings from DB: {_e} — proceeding with empty map")

    def persist_mapping(dg_idx, speaker_id):
        \"\"\"Write confirmed dg_idx -> speaker_id mapping to DB. Non-fatal on failure.\"\"\"
        try:
            _pc = get_db_conn()
            _pu = _pc.cursor()
            _pu.execute(
                "UPDATE events SET dg_speaker_map = jsonb_set(COALESCE(dg_speaker_map, '{}'::jsonb), %s, %s::jsonb) WHERE id = %s",
                ([str(dg_idx)], str(speaker_id), event_id)
            )
            _pc.commit()
            _pu.close()
            _pc.close()
        except Exception as _pe:
            print(f"  [PERSIST] WARNING: Could not persist mapping dg={dg_idx}->sid={speaker_id}: {_pe} — stream continues")"""

# --- Change 2: Call persist_mapping at [CONFIRMED] assignment ---

OLD_CONFIRMED = """            print(f"  [CONFIRMED] Deepgram spk {dg_idx} = DB speaker {sid}")
            return sid"""

NEW_CONFIRMED = """            print(f"  [CONFIRMED] Deepgram spk {dg_idx} = DB speaker {sid}")
            persist_mapping(dg_idx, sid)
            return sid"""

# --- Change 3: Call persist_mapping at [ORDER] assignment ---

OLD_ORDER = """                print(f"  [ORDER] Deepgram spk {dg_idx} = DB speaker {sid} (slot {idx})")
                return sid"""

NEW_ORDER = """                print(f"  [ORDER] Deepgram spk {dg_idx} = DB speaker {sid} (slot {idx})")
                persist_mapping(dg_idx, sid)
                return sid"""


def apply():
    with open(TARGET, 'r') as f:
        content = f.read()

    # Validate all three anchors before making any changes
    errors = []
    for name, old in [('INIT', OLD_INIT), ('CONFIRMED', OLD_CONFIRMED), ('ORDER', OLD_ORDER)]:
        count = content.count(old)
        if count == 0:
            errors.append(f"ERROR: anchor {name} not found")
        elif count > 1:
            errors.append(f"ERROR: anchor {name} found {count} times — ambiguous")

    if errors:
        for e in errors:
            print(e)
        sys.exit(1)

    # Apply all three changes
    content = content.replace(OLD_INIT, NEW_INIT)
    content = content.replace(OLD_CONFIRMED, NEW_CONFIRMED)
    content = content.replace(OLD_ORDER, NEW_ORDER)

    with open(TARGET, 'w') as f:
        f.write(content)

    print("OK: all 3 changes applied to debate_stream_deepgram.py")
    print("Verify with: grep -n 'PERSIST\\|persist_mapping' debate_stream_deepgram.py")


if __name__ == '__main__':
    apply()
