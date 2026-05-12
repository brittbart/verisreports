"""
stages/load.py — Database load stage.

Wraps scheduler.py:run_load(). Loads articles + claims from today\'s
JSON files into Postgres via load_to_database.load_articles().

If either input file is missing, exits 0 with a warning.

Run manually:
    /home/veris/projects/veris/venv/bin/python3 -m stages.load
"""
import os
import sys
from datetime import datetime

from stages._common import run_stage, log


def main() -> int:
    with run_stage("load") as ctx:
        today = datetime.now().strftime("%Y-%m-%d")
        articles_file = f"articles_{today}.json"
        claims_file = f"claims_{today}.json"

        if not os.path.exists(articles_file):
            log.warning(f"[load] No articles file: {articles_file} — skipping")
            return 0
        if not os.path.exists(claims_file):
            log.warning(f"[load] No claims file: {claims_file} — skipping")
            return 0

        import load_to_database
        load_to_database.load_articles(articles_file, claims_file)
        # load_articles prints \'✓ Articles added: N\' and \'✓ Claims added: N\'
        # but doesn\'t return them. items_processed stays NULL — we can
        # reconstruct counts from the articles table if needed via
        # fetched_at filtering on started_at.
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
