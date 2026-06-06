"""
seo.py — SEO infrastructure for Verum Signal.
Generates meta tags, Open Graph tags, Twitter cards, canonical URLs,
and JSON-LD structured data for all public pages.

All invisible until robots.txt flips from Disallow: / to Allow: /
"""

SITE_URL = "https://verumsignal.com"
SITE_NAME = "Verum Signal"
SITE_DESC = "Credibility signal reports for news articles. Claim-level verification with transparent methodology."
OG_IMAGE_DEFAULT = f"{SITE_URL}/static/og-default.png"


def meta_tags(*, title, description, url, og_image=None, og_type="website", extra=None):
    """Generate HTML meta tags for a page's <head> section."""
    img = og_image or OG_IMAGE_DEFAULT
    canonical = url if url.startswith("http") else f"{SITE_URL}{url}"

    tags = f'''<meta name="description" content="{_esc(description)}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{img}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_esc(title)}">
<meta name="twitter:description" content="{_esc(description)}">
<meta name="twitter:image" content="{img}">'''

    if extra:
        tags += "\n" + extra
    return tags


def report_meta(source, title, score, url, short_hash=None):
    """Meta tags for an article report page."""
    score_str = f"Score: {score}/100" if score is not None else "Unscored"
    desc = f"{score_str} — Verum Signal credibility report for {source}. Claim-level verification with sources."
    page_title = f"{source} — {score_str} — Verum Signal"
    page_url = f"{SITE_URL}/r/{short_hash}" if short_hash else f"{SITE_URL}/report?url={_urlenc(url)}"
    og_img = f"{SITE_URL}/api/og/report?source={_urlenc(source)}&score={score or ''}&title={_urlenc(title or '')}"
    return meta_tags(
        title=page_title,
        description=desc,
        url=page_url,
        og_image=og_img,
        og_type="article",
    )


def outlet_meta(domain, score, tier, scoreable_count):
    """Meta tags for an outlet detail page."""
    score_str = f"Score: {score}/100" if score is not None else "Not yet scored"
    desc = f"{domain} — {score_str}. {scoreable_count} claims verified. {tier} tier. Verum Signal outlet reliability profile."
    page_title = f"{domain} — {score_str} — Verum Signal"
    page_url = f"{SITE_URL}/outlet/{domain}"
    og_img = f"{SITE_URL}/api/og/outlet?domain={_urlenc(domain)}&score={score or ''}"
    return meta_tags(
        title=page_title,
        description=desc,
        url=page_url,
        og_image=og_img,
    )


def debate_meta(event_name, slug, claim_count, event_date_str):
    """Meta tags for a debate detail page."""
    desc = f"{event_name} — {claim_count} claims verified in real time. Verum Signal live debate coverage."
    page_title = f"{event_name} — Verum Signal"
    page_url = f"{SITE_URL}/debates/{slug}"
    og_img = f"{SITE_URL}/api/og/debate?name={_urlenc(event_name)}&claims={claim_count}"
    return meta_tags(
        title=page_title,
        description=desc,
        url=page_url,
        og_image=og_img,
    )


def debates_index_meta(total_events, total_claims):
    """Meta tags for the debates listing page."""
    desc = f"Live debate coverage — {total_events} events tracked, {total_claims} claims verified. Verum Signal."
    return meta_tags(
        title="Debates — Verum Signal",
        description=desc,
        url=f"{SITE_URL}/debates",
    )


def leaderboard_meta(scored_count, total_verdicts):
    """Meta tags for the leaderboard page."""
    desc = f"Outlet reliability rankings — {scored_count} outlets scored across {total_verdicts} verified claims. Verum Signal."
    return meta_tags(
        title="Leaderboard — Verum Signal",
        description=desc,
        url=f"{SITE_URL}/leaderboard",
    )


def methodology_meta():
    """Meta tags for the methodology page."""
    return meta_tags(
        title="Methodology — Verum Signal",
        description="How Verum Signal evaluates news credibility. Transparent, evidence-based claim verification methodology.",
        url=f"{SITE_URL}/methodology",
    )


def homepage_meta():
    """Meta tags for the homepage."""
    return meta_tags(
        title="Verum Signal — Signal through the noise",
        description=SITE_DESC,
        url=SITE_URL,
    )


def claim_review_jsonld(claim_text, verdict, article_url, article_title, source_name, review_date):
    """JSON-LD structured data for Google's ClaimReview rich result."""
    import json
    VERDICT_MAP = {
        'supported': 'True',
        'corroborated': 'True',
        'plausible': 'Mostly true',
        'overstated': 'Exaggerated',
        'disputed': 'Disputed',
        'not_supported': 'False',
        'not_verifiable': 'Not enough information',
        'opinion': 'Opinion',
    }
    alt_name = VERDICT_MAP.get(verdict, verdict or 'Unknown')

    ld = {
        "@context": "https://schema.org",
        "@type": "ClaimReview",
        "datePublished": review_date,
        "url": article_url,
        "claimReviewed": claim_text,
        "itemReviewed": {
            "@type": "Claim",
            "author": {"@type": "Organization", "name": source_name},
            "appearance": {"@type": "CreativeWork", "url": article_url, "headline": article_title},
        },
        "author": {
            "@type": "Organization",
            "name": "Verum Signal",
            "url": "https://verumsignal.com",
        },
        "reviewRating": {
            "@type": "Rating",
            "ratingValue": _verdict_rating(verdict),
            "bestRating": 5,
            "worstRating": 1,
            "alternateName": alt_name,
        },
    }
    return f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'


def _verdict_rating(verdict):
    """Map verdict to 1-5 rating for ClaimReview schema."""
    return {
        'supported': 5, 'corroborated': 5, 'plausible': 4,
        'overstated': 3, 'disputed': 2, 'not_supported': 1,
        'not_verifiable': 3, 'opinion': 3,
    }.get(verdict, 3)


def _esc(s):
    """Escape HTML attribute characters."""
    if not s:
        return ''
    return str(s).replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')


def _urlenc(s):
    """URL-encode a string."""
    from urllib.parse import quote
    return quote(str(s or ''), safe='')
