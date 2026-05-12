"""
stages/backfill.py — Backfill published_at timestamps.

Wraps scheduler.py:run_backfill(). Fills missing published_at on
articles ingested in the last 4 hours (one cycle plus buffer).

Run manually:
    /home/veris/projects/veris/venv/bin/python3 -m stages.backfill
"""
import sys

from stages._common import run_stage, log


def main() -> int:
    with run_stage("backfill"):
        from scripts.backfill_published_at import backfill_recent_articles
        result = backfill_recent_articles(hours=4, limit=50, logger=log)
        # backfill_recent_articles returns True/False per scheduler.py
        # convention. We don\'t fail the stage on False — the existing
        # function logs its own warnings.
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
