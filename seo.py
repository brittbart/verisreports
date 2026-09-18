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


import hmac as _hmac, hashlib as _hashlib, os as _os


def og_sig(kind, *parts):
    """S13: signature for a preview-image URL; empty when SECRET_KEY is unset."""
    key = _os.environ.get("SECRET_KEY")
    if not key:
        return ""
    msg = "\x1f".join(["og-preview-v1", kind] + [str(p) for p in parts]).encode("utf-8")
    return _hmac.new(key.encode("utf-8"), msg, _hashlib.sha256).hexdigest()[:32]


def og_sig_ok(kind, sig, *parts):
    expected = og_sig(kind, *parts)
    return bool(expected) and bool(sig) and _hmac.compare_digest(expected, sig)


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
    # S13: canonical = the URL that renders (/r/ redirects here), encoded as the /r/ redirect encodes it
    from urllib.parse import urlencode as _qenc
    page_url = f"{SITE_URL}/report?{_qenc({'url': url})}"
    _s = '' if score is None else str(score)
    og_img = (f"{SITE_URL}/api/og/report?source={_urlenc(source)}&score={_s}&title={_urlenc(title or '')}"
              f"&sig={og_sig('report', source, _s, title or '')}")
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
    desc = f"{domain} — {score_str}. {scoreable_count} claims evaluated. {tier} tier. Verum Signal outlet reliability profile."
    page_title = f"{domain} — {score_str} — Verum Signal"
    page_url = f"{SITE_URL}/outlet/{domain}"
    _s = '' if score is None else str(score)
    og_img = (f"{SITE_URL}/api/og/outlet?domain={_urlenc(domain)}&score={_s}&sig={og_sig('outlet', domain, _s)}"
              if scoreable_count else OG_IMAGE_DEFAULT)
    return meta_tags(
        title=page_title,
        description=desc,
        url=page_url,
        og_image=og_img,
    )


def debate_title(event_name, participants=None):
    """S13: candidate names first (the search terms), then the event name."""
    names = []
    for p in participants or []:
        if not isinstance(p, dict):
            continue
        n = " ".join(str(p.get("name") or "").split())
        role = str(p.get("role") or "").lower()
        if n and n.lower() != "moderator" and role != "moderator" and n not in names:
            names.append(n)
    if len(names) == 2:
        lead = f"{names[0]} vs. {names[1]}"
    elif 3 <= len(names) <= 4:
        lead = ", ".join(names[:-1]) + " and " + names[-1]
    elif len(names) == 1:
        lead = names[0]
    else:
        lead = ""
    return f"{lead}: {event_name} — Verum Signal" if lead else f"{event_name} — Verum Signal"


def debate_event_jsonld(event_name, page_url, description, image, start_iso, stream_url, participants=None):
    """S13: schema.org Event for a debate with a known stream; '' when either is missing."""
    import json as _json
    if not start_iso or not stream_url or not str(stream_url).startswith(("http://", "https://")):
        return ""
    names = []
    for p in participants or []:
        if not isinstance(p, dict):
            continue
        n = " ".join(str(p.get("name") or "").split())
        if n and n.lower() != "moderator" and str(p.get("role") or "").lower() != "moderator" and n not in names:
            names.append(n)
    ld = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": event_name,
        "startDate": str(start_iso),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
        "location": {"@type": "VirtualLocation", "url": str(stream_url)},
        "description": description,
        "image": [image],
        "url": page_url,
    }
    if names:
        ld["performer"] = [{"@type": "Person", "name": n} for n in names]
    body = _json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
    return '<script type="application/ld+json">' + body + "</script>"


def debate_meta(event_name, slug, claim_count, event_date_str, participants=None, start_iso=None, stream_url=None):
    """Meta tags for a debate detail page."""
    _when = f", {event_date_str}" if event_date_str else ""
    if claim_count:
        desc = f"{event_name}{_when} — {claim_count} claims evaluated in real time. Verum Signal live debate coverage."
    else:
        desc = f"{event_name}{_when}: follow each claim live, with the evidence behind it, on Verum Signal."
    page_title = debate_title(event_name, participants)
    page_url = f"{SITE_URL}/debates/{slug}"
    og_img = (f"{SITE_URL}/api/og/debate?name={_urlenc(event_name)}&claims={claim_count}"
              f"&sig={og_sig('debate', event_name, claim_count)}")
    return meta_tags(
        title=page_title,
        description=desc,
        url=page_url,
        og_image=og_img,
        extra=debate_event_jsonld(event_name, page_url, desc, og_img, start_iso, stream_url, participants),
    )


def debates_index_meta(total_events, total_claims):
    """Meta tags for the debates listing page."""
    desc = f"Live debate coverage — {total_events} events tracked, {total_claims} claims evaluated. Verum Signal."
    return meta_tags(
        title="Debates — Verum Signal",
        description=desc,
        url=f"{SITE_URL}/debates/list",
    )


def leaderboard_meta(scored_count, total_verdicts):
    """Meta tags for the leaderboard page."""
    desc = f"Outlet reliability rankings — {scored_count} outlets scored across {total_verdicts} claims evaluated. Verum Signal."
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
