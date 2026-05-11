"""
Extract the real publisher domain from a Google News RSS item.

Google News RSS items have opaque base64-blob URLs that require HTTP
redirect resolution to decode — but they uniformly include the publisher
in the title suffix, e.g.:

    "Trump Criticizes NFL Cost - breitbart.com"
    "Parents Jailed in Covid Lockdown Horror House - breitbart.com"
    "Farage Says Work Begins Now - breitbart.com"

This module extracts the publisher domain from that suffix with no HTTP.

API
---
    resolve_publisher(url, title) -> str | None

    Returns the publisher domain as a lowercase hostname (e.g.
    "breitbart.com", "nytimes.com"), or None if extraction failed.

    Callers should fall back to 'news.google.com' when None is returned,
    rather than guessing — better a known-wrong attribution than a
    silently-wrong one polluting a real outlet's stats.
"""
import re
import sys

# Match " - domain.tld" at end of title. Domains can have multiple parts
# (e.g. "bbc.co.uk", "nytimes.com"). We require at least one dot, and
# we restrict to letters/digits/hyphen/dot to avoid matching publisher
# names that happen to include " - " (e.g. "Headline - Part 2").
#
# Captures the domain WITHOUT the leading " - ".
_TITLE_SUFFIX_RE = re.compile(
    r" - ([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+)\s*$"
)

# Domains we never want to attribute, even if they appear in the suffix.
# These are aggregator/wrapper domains, not real publishers.
_NEVER_ATTRIBUTE = {
    "news.google.com",
    "google.com",
    "news.yahoo.com",
    "yahoo.com",
    "msn.com",
}


def resolve_publisher(url, title):
    """
    Extract publisher domain from a Google News RSS title suffix.
    Returns lowercase domain string or None.

    Args:
        url:   The RSS item URL. Currently unused (Google News URLs are
               opaque), but kept in the signature so callers don't need
               to change if a future URL parser is added.
        title: The RSS item title, e.g.
               "Trump Criticizes NFL - breitbart.com"

    Returns:
        "breitbart.com" for the example above.
        None if no recognizable publisher suffix is present.
    """
    if not title or not isinstance(title, str):
        return None

    match = _TITLE_SUFFIX_RE.search(title)
    if not match:
        return None

    domain = match.group(1).lower().strip()

    # Strip common www. / m. prefixes — Google News usually doesn't add
    # these but harmless to normalize.
    for prefix in ("www.", "m.", "amp."):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]

    if domain in _NEVER_ATTRIBUTE:
        return None

    # Sanity: must contain a TLD-ish suffix (at least one dot, and the
    # last segment is 2-24 chars and all letters). Rejects things like
    # "1.2" or "version - 1.0".
    parts = domain.split(".")
    if len(parts) < 2:
        return None
    tld = parts[-1]
    if not (2 <= len(tld) <= 24 and tld.isalpha()):
        return None

    return domain


# -------------------------------------------------------------------------
# Lightweight self-test. Run `python3 google_news_parser.py` to verify.
# -------------------------------------------------------------------------
if __name__ == "__main__":
    cases = [
        # (title, expected)
        ("Trump Criticizes NFL Cost - breitbart.com", "breitbart.com"),
        ("Parents Jailed - breitbart.com", "breitbart.com"),
        ("Some Story - The New York Times", None),  # text suffix, not domain
        ("Some Story - nytimes.com", "nytimes.com"),
        ("Story With - Mid-Hyphen - bbc.co.uk", "bbc.co.uk"),
        ("Trailing whitespace - cnn.com   ", "cnn.com"),
        ("No suffix at all", None),
        ("Headline - news.google.com", None),  # aggregator filter
        ("", None),
        (None, None),
        ("Story - 1.2", None),  # version-like, not a domain
        ("Story - WWW.Example.com", "example.com"),  # case + www strip
    ]
    failures = 0
    for title, expected in cases:
        got = resolve_publisher(None, title)
        ok = got == expected
        marker = "OK " if ok else "FAIL"
        print(f"{marker} resolve_publisher(_, {title!r:60s}) -> {got!r}  (expected {expected!r})")
        if not ok:
            failures += 1
    print()
    print(f"{len(cases) - failures} / {len(cases)} passed")
    sys.exit(0 if failures == 0 else 1)
