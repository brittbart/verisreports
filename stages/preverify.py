"""
stages/preverify.py — Pre-verification of top-outlet articles.

Wraps scheduler.py:run_pre_verify(). Synchronously verifies up to 30
claims for top-outlet articles so user-paste reports load fast.

Run manually:
    /home/veris/projects/veris/venv/bin/python3 -m stages.preverify
"""
import sys

from stages._common import run_stage


def main() -> int:
    with run_stage("preverify") as ctx:
        import pre_verify
        count = pre_verify.pre_verify_articles(limit=30)
        ctx.record(items_processed=count)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
