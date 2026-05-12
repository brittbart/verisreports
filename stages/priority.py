"""
stages/priority.py — Claim priority scoring stage.

Wraps scheduler.py:run_priority(). Scores all unscored claims via
priority_scorer.score_all_claims().

Run manually:
    /home/veris/projects/veris/venv/bin/python3 -m stages.priority
"""
import sys

from stages._common import run_stage


def main() -> int:
    with run_stage("priority"):
        import priority_scorer
        priority_scorer.score_all_claims()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
