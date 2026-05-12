"""
stages/verdicts.py — Batch verdict submission + harvest stage.

Wraps scheduler.py:run_verdicts(). Two-part work:
  1. If pending_batch.txt exists, harvest the previous batch\'s results
     (those that have completed since last cycle).
  2. Submit a new batch of up to VERDICTS_PER_RUN claims.

This stage is the most important for the 6-hour breaking-news gate
(v1.6 methodology) — the gate is enforced inside verdict_engine.py at
batch submission time. Stage-split doesn\'t touch that logic.

Run manually:
    /home/veris/projects/veris/venv/bin/python3 -m stages.verdicts
"""
import os
import sys

from stages._common import run_stage, log

VERDICTS_PER_RUN = 20


def main() -> int:
    with run_stage("verdicts") as ctx:
        from verdict_engine import run_batch_verdict_engine, process_batch_results

        # 1. Harvest pending batch if any.
        if os.path.exists("pending_batch.txt"):
            log.info("[verdicts] Processing pending batch results...")
            process_batch_results()

        # 2. Submit a new batch.
        batch_id = run_batch_verdict_engine(limit=VERDICTS_PER_RUN)
        if batch_id:
            log.info(f"[verdicts] Batch submitted: {batch_id}")
            ctx.record(items_processed=VERDICTS_PER_RUN)
        else:
            log.info("[verdicts] No claims to batch or all resolved via cache/consensus")
            ctx.record(items_processed=0)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
