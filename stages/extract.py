"""
stages/extract.py — Claim extraction stage.

Wraps scheduler.py:run_extract(). Loads today's articles_*.json and
sends each article through extract_claims.process_articles().

If the articles file is missing (fetch didn't run or failed), exits
0 with a warning rather than failing — this lets the pipeline degrade
gracefully when stages are chained.

Run manually:
    /home/veris/projects/veris/venv/bin/python3 -m stages.extract
"""
import os
import sys
from datetime import datetime

from stages._common import run_stage, log


def main() -> int:
    with run_stage("extract") as ctx:
        today = datetime.now().strftime("%Y-%m-%d")
        input_file = f"articles_{today}.json"

        if not os.path.exists(input_file):
            log.warning(f"[extract] No articles file found: {input_file} — skipping")
            ctx.record(items_processed=0)
            return 0

        import extract_claims
        # process_articles writes claims_YYYY-MM-DD.json as side effect.
        # It doesn\'t return a count, so we don\'t set items_processed here;
        # the load stage will report claim counts in its own job_runs row.
        extract_claims.process_articles(input_file, limit=50)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
