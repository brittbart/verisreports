#!/usr/bin/env python3
"""
Patch: fix api_outlets column names (patches 7-11 from fix1, with specific anchors).
Run AFTER restoring from backup — this replaces fix1 entirely.

Run from ~/projects/veris with venv activated:
    python3 patch_mobile_routes_fix2.py
"""

import sys
import os
import shutil
from datetime import datetime

TARGET = os.path.join(os.path.dirname(__file__), 'mobile_routes.py')
BACKUP = TARGET + f'.bak.pre_schema_fix2_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

patches = []

# ── 1. outlets_leaderboard ─────────────────────────────────────────────────
patches.append(("leaderboard_1", 
    """        cur.execute(\"\"\"
            SELECT
                domain, name, score, tier, category,
                claim_count, verdict_counts, last_evaluated_at,
                rank() OVER (ORDER BY score DESC NULLS LAST) AS rank
            FROM api_outlets
            WHERE score IS NOT NULL
            ORDER BY score DESC NULLS LAST
            LIMIT %s OFFSET %s
        \"\"\", (limit, offset))

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        outlets_out = []
        for row in rows:
            r = dict(zip(cols, row))
            outlets_out.append({
                "domain":      r['domain'],
                "name":        r['name'],
                "score":       float(r['score']) if r['score'] is not None else None,
                "tier":        r['tier'],
                "category":    r['category'],
                "claim_count": r['claim_count'],
                "rank":        r['rank'],
                "verdict_counts": r['verdict_counts'] if r['verdict_counts'] else {},
                "last_evaluated_at": r['last_evaluated_at'].isoformat() if r['last_evaluated_at'] else None,
            })""",

    """        cur.execute(\"\"\"
            SELECT
                outlet_id, outlet_name, score, tier,
                total_scoreable_claims, verdict_counts, last_evaluated_at,
                rank() OVER (ORDER BY score DESC NULLS LAST) AS rank
            FROM api_outlets
            WHERE score IS NOT NULL
            ORDER BY score DESC NULLS LAST
            LIMIT %s OFFSET %s
        \"\"\", (limit, offset))

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        outlets_out = []
        for row in rows:
            r = dict(zip(cols, row))
            outlets_out.append({
                "domain":      r['outlet_id'],
                "name":        r['outlet_name'],
                "score":       float(r['score']) if r['score'] is not None else None,
                "tier":        r['tier'],
                "category":    None,
                "claim_count": r['total_scoreable_claims'],
                "rank":        r['rank'],
                "verdict_counts": r['verdict_counts'] if r['verdict_counts'] else {},
                "last_evaluated_at": r['last_evaluated_at'].isoformat() if r['last_evaluated_at'] else None,
            })"""
))

# ── 2. outlet_detail ───────────────────────────────────────────────────────
patches.append(("outlet_detail_1",
    """        cur.execute(\"\"\"
            SELECT domain, name, score, tier, category,
                   claim_count, verdict_counts, last_evaluated_at
            FROM api_outlets
            WHERE domain = %s
        \"\"\", (domain,))
        row = cur.fetchone()

        if not row:
            return err("Outlet not found", 404, "NOT_FOUND")

        cols = [d[0] for d in cur.description]
        o = dict(zip(cols, row))

        # Recent claims for this outlet
        cur.execute(\"\"\"
            SELECT c.id, c.claim_text, c.verdict, c.confidence_score,
                   c.verdict_summary, c.first_seen, a.title AS article_title,
                   a.id AS article_id
            FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE a.source_domain = %s
              AND c.claim_origin = 'outlet_claim'
              AND c.verdict IS NOT NULL
            ORDER BY c.first_seen DESC NULLS LAST
            LIMIT 20
        \"\"\", (domain,))

        claim_rows = cur.fetchall()
        claim_cols = [d[0] for d in cur.description]

        recent_claims = []
        for cr in claim_rows:
            c = dict(zip(claim_cols, cr))
            recent_claims.append({
                "id":             c['id'],
                "claim_text":     c['claim_text'],
                "verdict":        format_verdict(c['verdict']),
                "confidence_score": float(c['confidence_score']) if c['confidence_score'] else None,
                "verdict_summary": c['verdict_summary'],
                "first_seen":     c['first_seen'].isoformat() if c['first_seen'] else None,
                "article_id":     c['article_id'],
                "article_title":  c['article_title'],
            })

        return ok({
            "outlet": {
                "domain":      o['domain'],
                "name":        o['name'],
                "score":       float(o['score']) if o['score'] is not None else None,
                "tier":        o['tier'],
                "category":    o['category'],
                "claim_count": o['claim_count'],
                "verdict_counts": o['verdict_counts'] if o['verdict_counts'] else {},
                "last_evaluated_at": o['last_evaluated_at'].isoformat() if o['last_evaluated_at'] else None,
            },
            "recent_claims": recent_claims,
        })""",

    """        cur.execute(\"\"\"
            SELECT outlet_id, outlet_name, score, tier,
                   total_scoreable_claims, verdict_counts, last_evaluated_at
            FROM api_outlets
            WHERE outlet_id = %s
        \"\"\", (domain,))
        row = cur.fetchone()

        if not row:
            return err("Outlet not found", 404, "NOT_FOUND")

        cols = [d[0] for d in cur.description]
        o = dict(zip(cols, row))

        # Recent claims for this outlet
        cur.execute(\"\"\"
            SELECT c.id, c.claim_text, c.verdict, c.confidence_score,
                   c.verdict_summary, c.first_seen, a.title AS article_title,
                   a.id AS article_id
            FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE a.source_domain = %s
              AND c.claim_origin = 'outlet_claim'
              AND c.verdict IS NOT NULL
            ORDER BY c.first_seen DESC NULLS LAST
            LIMIT 20
        \"\"\", (domain,))

        claim_rows = cur.fetchall()
        claim_cols = [d[0] for d in cur.description]

        recent_claims = []
        for cr in claim_rows:
            c = dict(zip(claim_cols, cr))
            recent_claims.append({
                "id":             c['id'],
                "claim_text":     c['claim_text'],
                "verdict":        format_verdict(c['verdict']),
                "confidence_score": float(c['confidence_score']) if c['confidence_score'] else None,
                "verdict_summary": c['verdict_summary'],
                "first_seen":     c['first_seen'].isoformat() if c['first_seen'] else None,
                "article_id":     c['article_id'],
                "article_title":  c['article_title'],
            })

        return ok({
            "outlet": {
                "domain":      o['outlet_id'],
                "name":        o['outlet_name'],
                "score":       float(o['score']) if o['score'] is not None else None,
                "tier":        o['tier'],
                "category":    None,
                "claim_count": o['total_scoreable_claims'],
                "verdict_counts": o['verdict_counts'] if o['verdict_counts'] else {},
                "last_evaluated_at": o['last_evaluated_at'].isoformat() if o['last_evaluated_at'] else None,
            },
            "recent_claims": recent_claims,
        })"""
))

# ── 3. debates_list ────────────────────────────────────────────────────────
patches.append(("debates_list_1",
    """        cur.execute(\"\"\"
            SELECT
                e.id, e.slug, e.title, e.description,
                e.start_time, e.end_time, e.status,
                e.location, e.stream_url,
                COUNT(DISTINCT su.id) AS utterance_count,
                COUNT(DISTINCT c.id)  AS claim_count
            FROM events e
            LEFT JOIN speaker_utterances su ON su.event_id = e.id
            LEFT JOIN claims c ON c.debate_id = e.id
              AND c.verdict IS NOT NULL
            GROUP BY e.id, e.slug, e.title, e.description,
                     e.start_time, e.end_time, e.status,
                     e.location, e.stream_url
            ORDER BY e.start_time DESC
            LIMIT 50
        \"\"\")

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        now = datetime.now(timezone.utc)
        debates_out = []
        for row in rows:
            e = dict(zip(cols, row))
            start = e['start_time']
            if start and start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)

            is_live = e['status'] == 'live'
            is_upcoming = start and start > now and not is_live

            debates_out.append({
                "id":          e['id'],
                "slug":        e['slug'],
                "title":       e['title'],
                "description": e['description'],
                "start_time":  e['start_time'].isoformat() if e['start_time'] else None,
                "end_time":    e['end_time'].isoformat() if e['end_time'] else None,
                "status":      e['status'],
                "is_live":     is_live,
                "is_upcoming": is_upcoming,
                "location":    e['location'],
                "claim_count": int(e['claim_count']),
                "utterance_count": int(e['utterance_count']),
            })""",

    """        cur.execute(\"\"\"
            SELECT
                e.id, e.slug, e.event_name, e.event_subtitle,
                e.event_date, e.venue, e.stream_url, e.notes,
                COUNT(DISTINCT su.id) AS utterance_count,
                COUNT(DISTINCT c.id)  AS claim_count
            FROM events e
            LEFT JOIN speaker_utterances su ON su.event_id = e.id
            LEFT JOIN claims c ON c.event_id = e.id
              AND c.verdict IS NOT NULL
            GROUP BY e.id, e.slug, e.event_name, e.event_subtitle,
                     e.event_date, e.venue, e.stream_url, e.notes
            ORDER BY e.event_date DESC NULLS LAST
            LIMIT 50
        \"\"\")

        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

        now = datetime.now(timezone.utc).date()
        debates_out = []
        for row in rows:
            e = dict(zip(cols, row))
            event_date = e['event_date']

            is_upcoming = event_date and event_date > now
            is_live = False

            debates_out.append({
                "id":          e['id'],
                "slug":        e['slug'],
                "title":       e['event_name'],
                "description": e['event_subtitle'],
                "event_date":  event_date.isoformat() if event_date else None,
                "venue":       e['venue'],
                "notes":       e['notes'],
                "stream_url":  e['stream_url'],
                "is_live":     is_live,
                "is_upcoming": is_upcoming,
                "claim_count": int(e['claim_count']),
                "utterance_count": int(e['utterance_count']),
            })"""
))

# ── 4. debate_detail event query ───────────────────────────────────────────
patches.append(("debate_detail_event",
    """        cur.execute(\"\"\"
            SELECT id, slug, title, description, start_time, end_time,
                   status, location, stream_url
            FROM events
            WHERE slug = %s
        \"\"\", (slug,))
        row = cur.fetchone()

        if not row:
            return err("Debate not found", 404, "NOT_FOUND")

        cols = [d[0] for d in cur.description]
        e = dict(zip(cols, row))
        event_id = e['id']""",

    """        cur.execute(\"\"\"
            SELECT id, slug, event_name, event_subtitle,
                   event_date, venue, stream_url, notes
            FROM events
            WHERE slug = %s
        \"\"\", (slug,))
        row = cur.fetchone()

        if not row:
            return err("Debate not found", 404, "NOT_FOUND")

        cols = [d[0] for d in cur.description]
        e = dict(zip(cols, row))
        event_id = e['id']"""
))

# ── 5. debate_detail claims query ─────────────────────────────────────────
patches.append(("debate_detail_claims",
    """            WHERE c.debate_id = %s
              AND c.verdict IS NOT NULL
            ORDER BY c.first_seen ASC NULLS LAST
        \"\"\", (event_id,))""",

    """            WHERE c.event_id = %s
              AND c.verdict IS NOT NULL
            ORDER BY c.first_seen ASC NULLS LAST
        \"\"\", (event_id,))"""
))

# ── 6. debate_detail speaker join ──────────────────────────────────────────
patches.append(("debate_detail_speaker_join",
    "            LEFT JOIN speakers s ON s.name = c.attribution_context",
    "            LEFT JOIN speakers s ON s.id = c.speaker_id"
))

# ── 7. debate_detail response shape ───────────────────────────────────────
patches.append(("debate_detail_response",
    """            "debate": {
                "id":          event_id,
                "slug":        e['slug'],
                "title":       e['title'],
                "description": e['description'],
                "start_time":  e['start_time'].isoformat() if e['start_time'] else None,
                "end_time":    e['end_time'].isoformat() if e['end_time'] else None,
                "status":      e['status'],
                "is_live":     e['status'] == 'live',
                "location":    e['location'],
            },""",

    """            "debate": {
                "id":          event_id,
                "slug":        e['slug'],
                "title":       e['event_name'],
                "description": e['event_subtitle'],
                "event_date":  e['event_date'].isoformat() if e['event_date'] else None,
                "venue":       e['venue'],
                "notes":       e['notes'],
                "is_live":     False,
            },"""
))

# ── 8. articles list — outlet SELECT aliases ───────────────────────────────
patches.append(("articles_outlet_select",
    """                o.domain        AS outlet_domain,
                o.name          AS outlet_name,
                o.score         AS outlet_score,
                o.tier          AS outlet_tier,""",

    """                o.outlet_id     AS outlet_domain,
                o.outlet_name   AS outlet_name,
                o.score         AS outlet_score,
                o.tier          AS outlet_tier,"""
))

# ── 9. articles list — api_outlets JOIN ────────────────────────────────────
patches.append(("articles_outlet_join",
    "            LEFT JOIN api_outlets o ON o.domain = a.source_domain\n            LEFT JOIN claims c ON c.article_id = a.id",
    "            LEFT JOIN api_outlets o ON o.outlet_id = a.source_domain\n            LEFT JOIN claims c ON c.article_id = a.id"
))

# ── 10. article_report — api_outlets JOIN ──────────────────────────────────
patches.append(("report_outlet_join",
    "            LEFT JOIN api_outlets o ON o.domain = a.source_domain\n            LEFT JOIN report_links rl ON rl.article_id = a.id",
    "            LEFT JOIN api_outlets o ON o.outlet_id = a.source_domain\n            LEFT JOIN report_links rl ON rl.article_id = a.id"
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
            print(f"  [{name}] SKIP — anchor not found (may already be applied)")
            continue
        if count > 1:
            print(f"  [{name}] ERROR — anchor appears {count} times, ambiguous. Aborting.")
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
