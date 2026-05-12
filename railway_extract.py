"""
Day 24 — Railway extract entrypoint.

Single-invocation script that Railway cron calls every hour.
Reads up to 50 unextracted articles from the DB, extracts claims, writes
claims directly to the DB.
"""
import os
import sys
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)

from stages._common import run_stage


def main() -> int:
    with run_stage("extract") as ctx:
        import extract_claims
        results = extract_claims.process_articles_from_db(
            limit=50,
            min_content_chars=500,
            days_window=30,
        )
        ctx.record(items_processed=len(results))

    # Run priority scoring on freshly-extracted claims so verdict cron
    # has them in queue.
    with run_stage("priority") as ctx:
        import priority_scorer
        priority_scorer.score_all_claims()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
