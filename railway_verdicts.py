"""
Day 24 — Railway verdicts entrypoint.

Single-invocation script that Railway cron calls every 6 hours.
Harvests previous batch (if any), then submits a new batch of up to
20 claims through Anthropic Batch API.
"""
import os
import sys
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)

from stages._common import run_stage

VERDICTS_PER_RUN = 20


def main() -> int:
    with run_stage("verdicts") as ctx:
        from verdict_engine import run_batch_verdict_engine, process_batch_results

        # NOTE: pending_batch.txt won't persist between Railway cron runs
        # because the container filesystem is ephemeral. After migration is
        # stable, refactor to store batch IDs in DB instead.
        if os.path.exists("pending_batch.txt"):
            process_batch_results()

        batch_id = run_batch_verdict_engine(limit=VERDICTS_PER_RUN)
        if batch_id:
            ctx.record(items_processed=VERDICTS_PER_RUN)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
