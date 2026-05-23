"""
vs_summary_generator.py — Verum Signal Mobile Article Summary Generator
========================================================================
Generates concise 3-5 sentence article summaries for the mobile report view.
Called lazily on first report fetch; result cached in articles.vs_summary.

Usage:
    from vs_summary_generator import get_or_generate_vs_summary
    summary = get_or_generate_vs_summary(article_id, db_conn)

Cost: ~$0.001-0.003 per article (claude-haiku-3, short prompt, short output).
Only fires once per article — result cached in articles.vs_summary column.
"""

import os
import anthropic
from datetime import datetime, timezone

# Use Haiku for summaries — fast, cheap, adequate for 3-5 sentence summaries
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 256
SUMMARY_MAX_CHARS = 800  # truncate cached summary if somehow over

SYSTEM_PROMPT = """You write concise article summaries for a media credibility app.
Your summaries are 3-5 sentences. You describe what the article reports — the key facts, claims, and context.
You never editorialize. You never say "the article" or "this piece." Just describe what happened or what was reported.
You never use the words: fact-check, fact-checked, verified, truth, misinformation.
Write in plain present tense. No markdown."""

def _build_prompt(title: str, description: str | None, content_snippet: str | None) -> str:
    parts = [f"Title: {title}"]
    if description:
        parts.append(f"Description: {description[:500]}")
    if content_snippet:
        parts.append(f"Content excerpt: {content_snippet[:1500]}")
    parts.append("\nWrite a 3-5 sentence summary of this article.")
    return "\n".join(parts)

def generate_vs_summary(title: str, description: str | None = None,
                        content_snippet: str | None = None) -> str | None:
    """
    Call Claude Haiku to generate a 3-5 sentence article summary.
    Returns the summary string, or None if generation fails.
    Does NOT write to DB — caller handles persistence.
    """
    if not title:
        return None

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_prompt(title, description, content_snippet)

        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )

        summary = message.content[0].text.strip()
        # Truncate if somehow over limit
        if len(summary) > SUMMARY_MAX_CHARS:
            summary = summary[:SUMMARY_MAX_CHARS].rsplit('.', 1)[0] + '.'
        return summary

    except Exception as e:
        print(f"[vs_summary] generation failed for '{title[:50]}': {e}")
        return None


def get_or_generate_vs_summary(article_id: int, db_conn) -> str | None:
    """
    Lazy get-or-generate for article vs_summary.
    1. Check if articles.vs_summary already populated — return it if so.
    2. Fetch article title/description/content from DB.
    3. Generate summary via Claude Haiku.
    4. Cache result in articles.vs_summary + vs_summary_generated_at.
    5. Return summary (or None if generation failed).
    """
    cur = db_conn.cursor()
    try:
        # Check cache first
        cur.execute("""
            SELECT vs_summary, title, description, content
            FROM articles
            WHERE id = %s
        """, (article_id,))
        row = cur.fetchone()

        if not row:
            return None

        vs_summary, title, description, content = row

        # Return cached if present
        if vs_summary:
            return vs_summary

        # Generate
        content_snippet = content[:1500] if content else None
        summary = generate_vs_summary(title, description, content_snippet)

        if summary:
            # Cache in DB
            cur.execute("""
                UPDATE articles
                SET vs_summary = %s,
                    vs_summary_generated_at = %s
                WHERE id = %s
            """, (summary, datetime.now(timezone.utc), article_id))
            db_conn.commit()

        return summary

    except Exception as e:
        print(f"[vs_summary] get_or_generate failed for article {article_id}: {e}")
        try:
            db_conn.rollback()
        except Exception:
            pass
        return None
    finally:
        cur.close()


def backfill_vs_summaries(db_conn, limit: int = 100, min_claim_count: int = 1):
    """
    Backfill vs_summary for high-value articles that don't have one yet.
    Prioritizes articles with claims (already analyzed) and recent articles.
    
    Run manually or from a scheduled job:
        from vs_summary_generator import backfill_vs_summaries
        backfill_vs_summaries(db_conn, limit=500)
    
    Safe to run multiple times — skips articles that already have summaries.
    """
    cur = db_conn.cursor()
    try:
        cur.execute("""
            SELECT a.id, a.title, a.description, a.content
            FROM articles a
            JOIN (
                SELECT article_id, COUNT(*) as claim_count
                FROM claims
                WHERE claim_origin = 'outlet_claim'
                  AND verdict IS NOT NULL
                GROUP BY article_id
                HAVING COUNT(*) >= %s
            ) c ON c.article_id = a.id
            WHERE a.vs_summary IS NULL
              AND a.excluded_from_extraction = FALSE
              AND a.title IS NOT NULL
            ORDER BY a.published_at DESC NULLS LAST
            LIMIT %s
        """, (min_claim_count, limit))

        rows = cur.fetchall()
        print(f"[vs_summary] backfill: {len(rows)} articles to process")

        success = 0
        failed = 0
        for article_id, title, description, content in rows:
            content_snippet = content[:1500] if content else None
            summary = generate_vs_summary(title, description, content_snippet)

            if summary:
                cur.execute("""
                    UPDATE articles
                    SET vs_summary = %s,
                        vs_summary_generated_at = %s
                    WHERE id = %s
                """, (summary, datetime.now(timezone.utc), article_id))
                db_conn.commit()
                success += 1
            else:
                failed += 1

            if (success + failed) % 10 == 0:
                print(f"[vs_summary] backfill progress: {success} ok, {failed} failed")

        print(f"[vs_summary] backfill complete: {success} ok, {failed} failed")
        return success, failed

    except Exception as e:
        print(f"[vs_summary] backfill error: {e}")
        try:
            db_conn.rollback()
        except Exception:
            pass
        return 0, 0
    finally:
        cur.close()
