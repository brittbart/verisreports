"""
Day 24 — Railway fetch entrypoint.

Single-invocation script that Railway cron calls every 3 hours.
Runs:
    1. fetch (RSS + NewsAPI, direct-to-DB)
    2. backfill_published_at (for new articles)
    3. pre_verify (for top-outlet recently-fetched articles)

Each phase is wrapped in run_stage so /ops dashboard sees independent rows.
"""
import os
import sys
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(override=False)

from stages._common import run_stage


def main() -> int:
    # Phase 1: fetch
    with run_stage("fetch") as ctx:
        import fetch_articles
        count = fetch_articles.fetch_articles_to_db()
        ctx.record(items_processed=count)

    # Phase 1b (2026-09-16): fetch full article bodies for feed-summary stubs.
    # articles.content had only ever held the RSS summary, so extraction's
    # 500-char gate never saw most major outlets. Direct scrape -> Jina only;
    # no Anthropic client, no model credits. Anonymous Jina => 4 workers.
    with run_stage("bodies") as ctx:
        import subprocess, sys as _sys
        try:
            r = subprocess.run([_sys.executable, "enrich_article_bodies.py", "--apply",
                                "--limit", "120", "--workers", "4", "--hours", "24"],
                               capture_output=True, text=True, timeout=1500)
            tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
            print(f"[bodies] rc={r.returncode} {tail[0]}")
            if r.returncode != 0:
                print((r.stderr or "")[-2000:])
            import re as _re
            m = _re.search(r"full=(\d+)", tail[0])
            ctx.record(items_processed=int(m.group(1)) if m else 0)
        except Exception as e:
            print(f"[bodies] failed (non-fatal): {e}")
            ctx.record(items_processed=0)

    # Phase 2: backfill published_at for new articles
    with run_stage("backfill") as ctx:
        from scripts.backfill_published_at import backfill_recent_articles
        result = backfill_recent_articles(hours=4, limit=100)
        if isinstance(result, dict):
            ctx.record(items_processed=result.get("succeeded", 0))

    # Phase 3: pre_verify top-outlet unverified articles
    with run_stage("preverify") as ctx:
        import pre_verify
        pre_verify.pre_verify_articles(limit=30)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
