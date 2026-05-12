"""
stages/fetch.py — RSS + NewsAPI ingestion stage.

Replaces scheduler.py:run_fetch(). Imports the same fetch_articles
module and calls the same function — behavior is identical to the
daemon version.

Exit codes:
    0  on success
    1  on any exception (systemd records the failure)

Run manually for testing:
    cd ~/projects/veris
    set -a; source .env; set +a
    python3 -m stages.fetch

Run as systemd service (after timer install):
    sudo systemctl start veris-fetch.service
    sudo journalctl -u veris-fetch.service -f
"""
import sys

from stages._common import run_stage


def main() -> int:
    with run_stage("fetch") as ctx:
        # Late import so the stage import is cheap (helpful when running
        # via systemd which times to first output for liveness signals).
        import fetch_articles

        articles = fetch_articles.fetch_articles()
        ctx.record(items_processed=len(articles))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # run_stage already logged + recorded the failure.
        # The exception was re-raised so we land here. Exit non-zero
        # so systemd sees the failure.
        sys.exit(1)
