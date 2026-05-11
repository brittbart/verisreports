"""
Extract the real publisher domain from a Google News RSS item.

Google News RSS items have opaque base64-blob URLs and the publisher
is encoded in the title suffix. Two suffix formats are common:

  Domain form:   "Headline - breitbart.com"
  Name form:     "Headline - The Daily Wire" / " - ESPN" / " - Nature"

This module handles both. The name->domain map is built at import time
from feeds.py (so it stays in sync with the ingestion list), plus a
small hand-curated map for outlets that appear in Google News but
aren't in feeds.py.

API
---
    resolve_publisher(url, title) -> str | None
"""
import re
import sys
from urllib.parse import urlparse


# --- Hand-curated map for publishers that don't appear in feeds.py ---
# Add to this as we find more Google News publishers that need mapping.
# Keys are lowercased, leading "the " stripped. Values are lowercase
# domains without scheme or www.
_EXTRA_NAME_TO_DOMAIN = {
    # Health / science
    "healthline":              "healthline.com",
    "webmd":                   "webmd.com",
    "nature":                  "nature.com",
    "scientific american":     "scientificamerican.com",
    "new scientist":           "newscientist.com",
    "science news":            "sciencenews.org",
    # Sports
    "espn":                    "espn.com",
    "sports illustrated":      "si.com",
    "athletic":                "theathletic.com",
    "bleacher report":         "bleacherreport.com",
    # Business / tech
    "fortune":                 "fortune.com",
    "forbes":                  "forbes.com",
    "business insider":        "businessinsider.com",
    "business insider africa": "africa.businessinsider.com",
    "cnbc":                    "cnbc.com",
    "marketwatch":             "marketwatch.com",
    "financial post":          "financialpost.com",
    # Entertainment
    "telemundo":               "telemundo.com",
    "people":                  "people.com",
    "variety":                 "variety.com",
    "hollywood reporter":      "hollywoodreporter.com",
    "deadline":                "deadline.com",
    "rolling stone":           "rollingstone.com",
    "billboard":               "billboard.com",
    "ew":                      "ew.com",
    "entertainment weekly":    "ew.com",
    "vulture":                 "vulture.com",
    # Niche / international / political
    "asamnews":                "asamnews.com",
    "next shark":              "nextshark.com",
    "irish independent":       "independent.ie",
    "spiegel":                 "spiegel.de",
    "der spiegel":             "spiegel.de",
    "daily wire":              "dailywire.com",
    "daily caller":            "dailycaller.com",
    "national review":         "nationalreview.com",
    "the dispatch":            "thedispatch.com",
    "dispatch":                "thedispatch.com",
    "real clear politics":     "realclearpolitics.com",
    "free press":              "thefp.com",
    "free beacon":             "freebeacon.com",
    "washington examiner":     "washingtonexaminer.com",
    "washington times":        "washingtontimes.com",
    "ny post":                 "nypost.com",
    "new york post":           "nypost.com",
    "newsmax":                 "newsmax.com",
    "federalist":              "thefederalist.com",
    # Other long-tail
    "religion news service":   "religionnews.com",
    "common dreams":           "commondreams.org",
    "truthout":                "truthout.org",
    "democracy now":           "democracynow.org",
    "jacobin":                 "jacobin.com",
    "mother jones":            "motherjones.com",
    "the nation":              "thenation.com",
    "nation":                  "thenation.com",
    "intercept":               "theintercept.com",
    "vice":                    "vice.com",
    "slate":                   "slate.com",
    "vox":                     "vox.com",
    "axios":                   "axios.com",
    # Patch 12: bulk additions from May 11 2026 production data
    "reuters": 'reuters.com',
    "independent": 'independent.co.uk',
    "wsj": 'wsj.com',
    "guardian": 'theguardian.com',
    "tmz": 'tmz.com',
    "people.com": 'people.com',
    "bloomberg.com": 'bloomberg.com',
    "politico": 'politico.com',
    "telegraph": 'telegraph.co.uk',
    "newsweek": 'newsweek.com',
    "nextshark": 'nextshark.com',
    "bbc": 'bbc.com',
    "london evening standard": 'standard.co.uk',
    "wng.org": 'wng.org',
    "daily nation": 'nation.africa',
    "star tribune": 'startribune.com',
    "hindu": 'thehindu.com',
    "ajc.com": 'ajc.com',
    "haaretz": 'haaretz.com',
    "globe and mail": 'theglobeandmail.com',
    "cbs news": 'cbsnews.com',
    "detroit free press": 'freep.com',
    "dawn": 'dawn.com',
    "tricycle: buddhist review": 'tricycle.org',
    "time magazine": 'time.com',
    "inquirer.com": 'inquirer.com',
    "france 24": 'france24.com',
    "ap news": 'apnews.com',
    "pitchfork": 'pitchfork.com',
    "baltimore sun": 'baltimoresun.com',
    "breitbart.com": 'breitbart.com',
    "nbc news": 'nbcnews.com',
    "thefederalist.com": 'thefederalist.com',
    "le monde.fr": 'lemonde.fr',
    "irish times": 'irishtimes.com',
    "thegrio": 'thegrio.com',
    "toronto star": 'thestar.com',
    "e! news": 'eonline.com',
    "reason magazine": 'reason.com',
    "denver post": 'denverpost.com',
    "allafrica.com": 'allafrica.com',
    "democracy now!": 'democracynow.org',
    "christian post": 'christianpost.com',
    "usa today": 'usatoday.com',
    "npr": 'npr.org',
    "c-span": 'c-span.org',
    "propublica": 'propublica.org',
    "the atlantic": 'theatlantic.com',
    "atlantic": 'theatlantic.com',
    "new yorker": 'newyorker.com',
    "the new yorker": 'newyorker.com',
    "semafor": 'semafor.com',
    "politico magazine": 'politico.com',
    "usa today network": 'usatoday.com',
    "financial times": 'ft.com',
    "ft": 'ft.com',
    "economist": 'economist.com',
    "the economist": 'economist.com',
}


def _normalize_name_key(name):
    """Lowercase, strip leading 'the ', collapse whitespace."""
    if not name:
        return ""
    s = name.strip().lower()
    if s.startswith("the "):
        s = s[4:]
    s = re.sub(r"\s+", " ", s)
    return s


def _domain_from_feed_url(url):
    """Extract a stable publisher domain from a feed URL."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if not host:
        return None
    # Strip common feed/CDN prefixes; keep base domain
    for prefix in ("www.", "feeds.", "feed.", "rss.", "moxie.", "api.", "feeds2.", "feed1."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def _build_name_to_domain():
    """Build name->domain mapping from feeds.py + hand-curated extras."""
    name_to_domain = dict(_EXTRA_NAME_TO_DOMAIN)  # start with extras
    try:
        # Import feeds at function call time so that import errors during
        # boot don't break load_to_database.py.
        from feeds import FEEDS
    except Exception:
        return name_to_domain  # extras only if feeds import fails

    for display_name, feed_url, _category in FEEDS:
        domain = _domain_from_feed_url(feed_url)
        if not domain:
            continue
        key = _normalize_name_key(display_name)
        if key and key not in name_to_domain:
            # Don't overwrite hand-curated entries
            name_to_domain[key] = domain
            # Also map a shortened version: "BBC News" -> "bbc"
            short = key.split(" ")[0] if " " in key else key
            if short and short not in name_to_domain and len(short) >= 3:
                name_to_domain[short] = domain
    return name_to_domain


_NAME_TO_DOMAIN = _build_name_to_domain()


# --- Domain-suffix regex (existing Patch 9 behavior) ---
_TITLE_SUFFIX_DOMAIN_RE = re.compile(
    r" - ([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+)\s*$"
)

# --- Name-suffix regex (new Patch 11 behavior) ---
# Captures everything after the last " - " — could be a publisher name.
_TITLE_SUFFIX_NAME_RE = re.compile(r" - ([^-]+?)\s*$")

# Domains we refuse to attribute (aggregators)
_NEVER_ATTRIBUTE = {
    "news.google.com", "google.com",
    "news.yahoo.com", "yahoo.com",
    "msn.com",
}


def resolve_publisher(url, title):
    """
    Extract publisher domain from a Google News RSS title suffix.
    Tries domain form first, name form second. Returns None when
    neither matches confidently.
    """
    if not title or not isinstance(title, str):
        return None

    # 1. Domain suffix (e.g. " - breitbart.com")
    m = _TITLE_SUFFIX_DOMAIN_RE.search(title)
    if m:
        domain = m.group(1).lower().strip()
        for prefix in ("www.", "m.", "amp."):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        if domain in _NEVER_ATTRIBUTE:
            return None
        parts = domain.split(".")
        if len(parts) >= 2:
            tld = parts[-1]
            if 2 <= len(tld) <= 24 and tld.isalpha():
                return domain

    # 2. Name suffix (e.g. " - The Daily Wire")
    m = _TITLE_SUFFIX_NAME_RE.search(title)
    if m:
        candidate = m.group(1).strip()
        key = _normalize_name_key(candidate)
        if key in _NAME_TO_DOMAIN:
            return _NAME_TO_DOMAIN[key]

    return None


# --- Self-test ---
if __name__ == "__main__":
    cases = [
        # Domain form (Patch 9)
        ("Trump Criticizes NFL Cost - breitbart.com", "breitbart.com"),
        ("Story - nytimes.com", "nytimes.com"),
        ("Story - bbc.co.uk", "bbc.co.uk"),
        ("Story - WWW.Example.com", "example.com"),
        ("Story - 1.2", None),

        # Name form (Patch 11 new)
        ("Article About COPD - Healthline", "healthline.com"),
        ("Climate paper - Nature", "nature.com"),
        ("NWSL Power Rankings - ESPN", "espn.com"),
        ("PFF Draft Assessment - Sports Illustrated", "si.com"),
        ("Rhino horns story - Business Insider Africa", "africa.businessinsider.com"),
        ("Demi Lovato video - Telemundo", "telemundo.com"),
        ("Devil Wears Prada clip - AsAmNews", "asamnews.com"),
        ("Wes Streeting story - The Irish Independent", "independent.ie"),
        ("Trump face - Spiegel", "spiegel.de"),
        ("Joseph Smith - The Daily Wire", "dailywire.com"),
        ("Pastor convicted - The Daily Wire", "dailywire.com"),

        # Name form via feeds.py (built from import)
        ("Some headline - BBC News", "bbci.co.uk"),
        ("Some headline - The Guardian", "theguardian.com"),
        ("Some headline - NPR", "npr.org"),
        ("Some headline - Reuters", "reuters.com"),

        # Should NOT match
        ("Some Story - This Is Just A Random Phrase With No Domain", None),
        ("No suffix at all", None),
        ("Headline - news.google.com", None),
        ("", None),
        (None, None),
    ]

    failures = 0
    for title, expected in cases:
        got = resolve_publisher(None, title)
        ok = got == expected
        marker = "OK " if ok else "FAIL"
        print(f"{marker} resolve_publisher(_, {title!r:65s}) -> {got!r:30s} (expected {expected!r})")
        if not ok:
            failures += 1
    print()
    print(f"{len(cases) - failures} / {len(cases)} passed")
    print(f"Name->domain map size: {len(_NAME_TO_DOMAIN)}")
    sys.exit(0 if failures == 0 else 1)
