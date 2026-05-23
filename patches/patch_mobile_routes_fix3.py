#!/usr/bin/env python3
"""
Patch: fix articles table column names.
source_domain -> source_name, remove missing columns (lead_image_url, byline).

Run from ~/projects/veris with venv activated:
    python3 patch_mobile_routes_fix3.py
"""

import sys
import os
import shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'mobile_routes.py')
BACKUP = TARGET + f'.bak.pre_schema_fix3_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

patches = []

# ── 1. articles list SELECT ────────────────────────────────────────────────
patches.append(("articles_select",
    """            SELECT
                a.id,
                a.url,
                a.title,
                a.byline,
                a.published_at,
                a.lead_image_url,
                o.outlet_id     AS outlet_domain,
                o.outlet_name   AS outlet_name,
                o.score         AS outlet_score,
                o.tier          AS outlet_tier,
                COUNT(c.id)     AS claim_count,
                COUNT(c.id) FILTER (WHERE c.verdict IS NOT NULL AND c.verdict != 'opinion' AND c.verdict != 'not_verifiable') AS scoreable_count
            FROM articles a
            LEFT JOIN api_outlets o ON o.outlet_id = a.source_domain
            LEFT JOIN claims c ON c.article_id = a.id
              AND c.claim_origin = 'outlet_claim'
            WHERE {where_sql}
            GROUP BY a.id, a.url, a.title, a.byline, a.published_at,
                     a.lead_image_url, o.domain, o.name, o.score, o.tier""",

    """            SELECT
                a.id,
                a.url,
                a.title,
                a.source_name,
                a.published_at,
                o.outlet_id     AS outlet_domain,
                o.outlet_name   AS outlet_name,
                o.score         AS outlet_score,
                o.tier          AS outlet_tier,
                COUNT(c.id)     AS claim_count,
                COUNT(c.id) FILTER (WHERE c.verdict IS NOT NULL AND c.verdict != 'opinion' AND c.verdict != 'not_verifiable') AS scoreable_count
            FROM articles a
            LEFT JOIN api_outlets o ON o.outlet_id = a.source_name
            LEFT JOIN claims c ON c.article_id = a.id
              AND c.claim_origin = 'outlet_claim'
            WHERE {where_sql}
            GROUP BY a.id, a.url, a.title, a.source_name, a.published_at,
                     o.outlet_id, o.outlet_name, o.score, o.tier"""
))

# ── 2. articles list WHERE filter on category ──────────────────────────────
patches.append(("articles_category_filter",
    """        if filter_cat:
            where_clauses.append("o.category ILIKE %s")
            params.append(f"%{filter_cat}%")""",

    """        if filter_cat:
            # api_outlets has no category column; filter by source_name prefix
            where_clauses.append("a.source_name ILIKE %s")
            params.append(f"%{filter_cat}%")"""
))

# ── 3. articles list response ──────────────────────────────────────────────
patches.append(("articles_response",
    """                "headline":     r['title'],
                "byline":       r['byline'],
                "published_at": r['published_at'].isoformat() if r['published_at'] else None,
                "time_ago":     format_time_ago(r['published_at']),
                "lead_image_url": r['lead_image_url'],
                "outlet": {
                    "domain": r['outlet_domain'] or r.get('source_domain'),""",

    """                "headline":     r['title'],
                "byline":       None,
                "published_at": r['published_at'].isoformat() if r['published_at'] else None,
                "time_ago":     format_time_ago(r['published_at']),
                "lead_image_url": None,
                "outlet": {
                    "domain": r['outlet_domain'] or r['source_name'],"""
))

# ── 4. article_report SELECT ───────────────────────────────────────────────
patches.append(("report_select",
    """            SELECT a.id, a.url, a.title, a.byline, a.published_at,
                   a.lead_image_url, a.source_domain, a.vs_summary,
                   o.domain, o.name, o.score, o.tier,
                   rl.hash AS report_hash
            FROM articles a
            LEFT JOIN api_outlets o ON o.outlet_id = a.source_domain
            LEFT JOIN report_links rl ON rl.article_id = a.id
            WHERE a.id = %s""",

    """            SELECT a.id, a.url, a.title, a.published_at,
                   a.source_name, a.vs_summary,
                   o.outlet_id, o.outlet_name, o.score, o.tier,
                   rl.hash AS report_hash
            FROM articles a
            LEFT JOIN api_outlets o ON o.outlet_id = a.source_name
            LEFT JOIN report_links rl ON rl.article_id = a.id
            WHERE a.id = %s"""
))

# ── 5. article_report unpack ───────────────────────────────────────────────
patches.append(("report_unpack",
    """        (art_id, url, title, byline, published_at, lead_image_url,
         source_domain, vs_summary, outlet_domain, outlet_name,
         outlet_score, outlet_tier, report_hash) = row""",

    """        (art_id, url, title, published_at,
         source_name, vs_summary, outlet_domain, outlet_name,
         outlet_score, outlet_tier, report_hash) = row"""
))

# ── 6. article_report response ─────────────────────────────────────────────
patches.append(("report_article_response",
    """            "article": {
                "id":             art_id,
                "url":            url,
                "headline":       title,
                "byline":         byline,
                "published_at":   published_at.isoformat() if published_at else None,
                "lead_image_url": lead_image_url,
                "report_hash":    report_hash,
                "share_url":      f"https://verumsignal.com/r/{report_hash}" if report_hash else None,
            },
            "outlet": {
                "domain": outlet_domain or source_domain,""",

    """            "article": {
                "id":             art_id,
                "url":            url,
                "headline":       title,
                "byline":         None,
                "published_at":   published_at.isoformat() if published_at else None,
                "lead_image_url": None,
                "report_hash":    report_hash,
                "share_url":      f"https://verumsignal.com/r/{report_hash}" if report_hash else None,
            },
            "outlet": {
                "domain": outlet_domain or source_name,"""
))

# ── apply ──────────────────────────────────────────────────────────────────
def main():
    with open(TARGET, 'r') as f:
        content = f.read()

    shutil.copy2(TARGET, BACKUP)
    print(f"✓ Backed up to {os.path.basename(BACKUP)}")

    all_ok = True
    for name, old, new in patches:
        count = content.count(old)
        if count == 0:
            print(f"  [{name}] SKIP — anchor not found")
            continue
        if count > 1:
            print(f"  [{name}] ERROR — anchor appears {count} times. Aborting.")
            all_ok = False
            break
        content = content.replace(old, new, 1)
        print(f"  [{name}] ✓ applied")

    if not all_ok:
        print("\n✗ Aborted — no changes written")
        sys.exit(1)

    with open(TARGET, 'w') as f:
        f.write(content)

    print(f"\n✓ All patches applied to mobile_routes.py")

if __name__ == '__main__':
    main()
