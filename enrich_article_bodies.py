"""Fetch full article bodies for feed-summary stubs (2026-09-16 leaderboard review:
articles.content has only ever held the RSS summary; extraction's 500-char gate
therefore never sees most major outlets). Uses fetcher.fetch_article_content with
NO Anthropic client (direct scrape -> Jina only; no model credits).

Usage: python3 enrich_article_bodies.py [--limit N] [--hours H] [--source X] [--apply]
Dry-run by default: fetches and reports, writes nothing."""
import argparse, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

MIN_FULL = 500
TIME_BUDGET_S = 20 * 60

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=150)
    ap.add_argument('--hours', type=int, default=48)
    ap.add_argument('--source', default=None)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    from load_to_database import get_connection
    from fetcher import fetch_article_content
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name='articles' AND column_name IN ('body_fetched_at','body_fetch_method')""")
    have_cols = {r[0] for r in cur.fetchall()}
    if a.apply and len(have_cols) < 2:
        sys.exit("ABORT: run add_body_fetch_columns.py first (body_fetched_at / body_fetch_method missing)")
    where_src = "AND source_name = %s" if a.source else ""
    params = [a.hours] + ([a.source] if a.source else []) + [a.limit]
    unattempted = "AND body_fetched_at IS NULL" if 'body_fetched_at' in have_cols else ""
    cur.execute(f"""SELECT id, url, source_name, COALESCE(length(content),0)
                    FROM articles
                    WHERE fetched_at > NOW() - (%s || ' hours')::interval
                      AND (content IS NULL OR length(content) < {MIN_FULL})
                      AND url IS NOT NULL {unattempted} {where_src}
                    ORDER BY fetched_at DESC LIMIT %s""", params)
    rows = cur.fetchall()
    print(f"[enrich] candidates={len(rows)} apply={a.apply} hours={a.hours} limit={a.limit}")
    t0 = time.time(); ok = pay = fail = 0
    def work(row):
        aid, url, src, ln = row
        try:
            r = fetch_article_content(url, anthropic_client=None)
        except Exception as e:
            return aid, url, src, ln, None, f"error:{e}"
        if not r: return aid, url, src, ln, None, "none"
        if r.get('status') == 'paywall': return aid, url, src, ln, None, "paywall"
        return aid, url, src, ln, r.get('body') or '', r.get('method', 'direct')
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for f in as_completed(futs):
            aid, url, src, ln, body, method = f.result()
            if time.time() - t0 > TIME_BUDGET_S:
                print("[enrich] time budget reached; stopping"); break
            if body and len(body) >= MIN_FULL and len(body) > ln:
                ok += 1
                print(f"  OK   {src:28} {ln:5}->{len(body):6} {method}")
                if a.apply:
                    cur.execute("""UPDATE articles SET description = COALESCE(NULLIF(description,''), content),
                                   content = %s, body_fetched_at = NOW(), body_fetch_method = %s WHERE id = %s""",
                                (body, method, aid))
            else:
                (pay if method == 'paywall' else fail).__class__  # no-op for readability
                if method == 'paywall': pay += 1
                else: fail += 1
                print(f"  --   {src:28} {ln:5} {method}")
                if a.apply and 'body_fetched_at' in have_cols:
                    cur.execute("UPDATE articles SET body_fetched_at = NOW(), body_fetch_method = %s WHERE id = %s",
                                (method if method in ('paywall','none') else 'error', aid))
            if a.apply and (ok + pay + fail) % 25 == 0: conn.commit()
    if a.apply: conn.commit()
    cur.close(); conn.close()
    print(f"[enrich] done in {time.time()-t0:.0f}s: full={ok} paywall={pay} failed={fail} of {len(rows)}"
          + ("" if a.apply else "  (dry run, nothing written)"))
    return 0

if __name__ == '__main__':
    sys.exit(main())
