#!/usr/bin/env python3
"""
Day 24 — One-time cleanup of pre-Patch-25 news.google.com source attribution.
Run with --audit to preview, --apply to execute.
"""
import argparse, os, sys
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from google_news_parser import resolve_publisher

NON_ARTICLE_PATTERNS = [
    "Help Center", "Help Centre", "Search -", "Search Results", "DailyWire+ Help",
]

def is_non_article_title(title):
    if not title:
        return True
    for pat in NON_ARTICLE_PATTERNS:
        if pat.lower() in title.lower():
            return True
    return False

def get_conn():
    return psycopg2.connect(
        dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"], host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
    )

def plan_cleanup(conn):
    sql = """
        SELECT a.id AS article_id, a.title, a.url,
               COUNT(c.id) FILTER (WHERE c.verdict IS NOT NULL) AS verdict_count,
               COUNT(c.id) AS claim_count
        FROM articles a
        LEFT JOIN claims c ON c.article_id = a.id
        WHERE a.source_name = 'news.google.com'
        GROUP BY a.id
        HAVING COUNT(c.id) > 0
        ORDER BY a.id
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        rows = list(cur.fetchall())

    plan = []
    for row in rows:
        title, url, article_id = row["title"], row["url"], row["article_id"]
        if is_non_article_title(title):
            plan.append({"article_id": article_id, "action": "DELETE_NON_ARTICLE",
                         "target_source": None, "title": title,
                         "claim_count": row["claim_count"],
                         "reason": "non-article content"})
            continue
        resolved = resolve_publisher(url, title)
        if resolved:
            plan.append({"article_id": article_id, "action": "REATTRIBUTE",
                         "target_source": resolved, "title": title,
                         "claim_count": row["claim_count"],
                         "reason": f"title resolves to {resolved}"})
        else:
            plan.append({"article_id": article_id, "action": "DELETE_UNRESOLVABLE",
                         "target_source": None, "title": title,
                         "claim_count": row["claim_count"],
                         "reason": "no publisher suffix in title"})
    return plan

def print_plan(plan):
    print(f"\n=== Cleanup Plan ({len(plan)} articles) ===\n")
    summary = {"REATTRIBUTE": 0, "DELETE_NON_ARTICLE": 0, "DELETE_UNRESOLVABLE": 0}
    by_target = {}
    total_claims = 0
    for entry in plan:
        summary[entry["action"]] += 1
        total_claims += entry["claim_count"]
        if entry["action"] == "REATTRIBUTE":
            t = entry["target_source"]
            by_target[t] = by_target.get(t, 0) + entry["claim_count"]
        print(f"  Article {entry['article_id']:>7d} | {entry['action']:<20s} | "
              f"claims={entry['claim_count']} | {entry['reason']}")
        print(f"           title: {(entry['title'] or '')[:90]}")
        if entry["target_source"]:
            print(f"           target: {entry['target_source']}")
    print(f"\n=== Summary ===")
    print(f"  Total articles: {len(plan)}")
    print(f"  Total claims affected: {total_claims}")
    print(f"  REATTRIBUTE: {summary['REATTRIBUTE']} articles")
    if by_target:
        for target, count in sorted(by_target.items(), key=lambda x: -x[1]):
            print(f"    -> {target}: {count} claims")
    print(f"  DELETE_NON_ARTICLE: {summary['DELETE_NON_ARTICLE']} articles")
    print(f"  DELETE_UNRESOLVABLE: {summary['DELETE_UNRESOLVABLE']} articles")

def apply_plan(conn, plan):
    cur = conn.cursor()
    try:
        for entry in plan:
            article_id = entry["article_id"]
            if entry["action"] == "REATTRIBUTE":
                target = entry["target_source"]
                cur.execute("""
                    INSERT INTO sources (name, last_analysed)
                    VALUES (%s, NOW())
                    ON CONFLICT (name) DO UPDATE SET last_analysed = NOW()
                """, (target,))
                cur.execute("UPDATE articles SET source_name = %s WHERE id = %s",
                            (target, article_id))
                print(f"  REATTRIBUTE: article {article_id} -> {target}")
            elif entry["action"] in ("DELETE_NON_ARTICLE", "DELETE_UNRESOLVABLE"):
                cur.execute("DELETE FROM claims WHERE article_id = %s", (article_id,))
                deleted = cur.rowcount
                cur.execute("DELETE FROM articles WHERE id = %s", (article_id,))
                print(f"  {entry['action']}: article {article_id} ({deleted} claims deleted)")
        conn.commit()
        print("\n✓ All changes committed.")
    except Exception as e:
        conn.rollback()
        print(f"\n✗ Error: {e} — rolled back.")
        raise

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.audit and not args.apply:
        print("Specify --audit or --apply.")
        sys.exit(1)
    conn = get_conn()
    try:
        plan = plan_cleanup(conn)
        print_plan(plan)
        if args.apply:
            print("\n=== Applying ===")
            apply_plan(conn, plan)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
