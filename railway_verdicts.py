"""
Day 24 — Railway verdicts entrypoint.
Single-invocation script that Railway cron calls every 6 hours.
In surge mode (live debate detected), loops every 60s verifying debate claims.
Falls through to normal batch when no debate claims are pending.
"""
import os
import sys
import time
from dotenv import load_dotenv
if os.path.exists(".env"):
    load_dotenv(override=False)
from stages._common import run_stage
VERDICTS_PER_RUN = 50

def main() -> int:
    from verdict_engine import (run_batch_verdict_engine, process_batch_results,
                                 verify_debate_claims_sync, get_live_event_id)

    # ── Surge mode: auto-detect live debate ──────────────────────────────────
    live_event_id = get_live_event_id()
    if live_event_id:
        print(f"SURGE MODE: live event_id={live_event_id}. Running sync verification.")
        cycle = 0
        while True:
            cycle += 1
            print(f"\n[surge cycle {cycle}]")
            with run_stage("verdicts") as ctx:
                n = verify_debate_claims_sync(live_event_id, limit=10)
                ctx.record(items_processed=n)
                # During surge mode, skip Batch API entirely — wastes spend
                # and overwrites pending_batch.txt. Outlet verdicts can wait.
                if n == 0:
                    pass  # no debate claims pending — wait for next cycle

            # Re-check if event is still live
            live_event_id = get_live_event_id()
            if not live_event_id:
                print("No live event detected — exiting surge mode.")
                break

            print(f"  [surge] Waiting 60s before next cycle...")
            time.sleep(60)

        return 0

    # ── Normal mode: batch verdict engine ────────────────────────────────────
    with run_stage("verdicts") as ctx:
        # NOTE: pending_batch.txt won't persist between Railway cron runs
        # because the container filesystem is ephemeral. After migration is
        # stable, refactor to store batch IDs in DB instead.
        if os.path.exists("pending_batch.txt"):
            process_batch_results()
        # Verify any unverified debate claims from recent events
        from verdict_engine import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT event_id FROM claims
            WHERE claim_origin = 'debate_claim'
              AND verdict IS NULL
              AND claim_text IS NOT NULL
            LIMIT 5
        """)
        event_ids = [r[0] for r in cur.fetchall()]
        cur.close(); conn.close()
        if event_ids:
            print(f"Post-debate verification: {len(event_ids)} events with unverified claims")
            for eid in event_ids:
                n = verify_debate_claims_sync(eid, limit=20)
                ctx.record(items_processed=n)
                print(f"  event_id={eid}: verified {n} claims")
        batch_id = run_batch_verdict_engine(limit=VERDICTS_PER_RUN)
        if batch_id:
            ctx.record(items_processed=VERDICTS_PER_RUN)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
