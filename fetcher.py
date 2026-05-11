"""
fetcher.py — three-method article fetcher (extracted from api.py May 7, 2026).

Provides:
  fetch_article_content(url, anthropic_client=None) -> {title, body, method} | {status: 'paywall'} | None

Internal:
  _try_direct_scrape(url)
  _try_jina_reader(url)
  _try_web_search(url, anthropic_client)

Helpers:
  _USER_AGENT, _is_paywall, _is_bot_protection, _clean_url_slug
"""

import os
import re as _re_slug

_BOT_TITLES = {
    'just a moment', 'just a moment...', 'checking your browser',
    'access denied', 'please verify you are a human', 'ddos protection',
    'attention required', 'cloudflare', 'one moment...', 'verifying you are human',
}
_USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
               'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')


def _is_bot_protection(title):
    if not title:
        return False
    return title.lower().strip().rstrip('.').strip() in _BOT_TITLES


def _is_paywall(title, body):
    """Detect paywall preview content. Returns True if the retrieved content
    appears to be a paywall lede + CTA wall rather than the full article."""
    if not body:
        return False
    body_lower = body.lower()
    paywall_markers = [
        'subscribe to read',
        'subscribe to continue',
        'sign in to continue reading',
        'for subscribers only',
        'create a free account to continue',
        'become a subscriber',
        'already a subscriber',
        'unlimited access',
        'this article is for subscribers',
    ]
    return any(marker in body_lower for marker in paywall_markers)


def _clean_url_slug(url):
    """Last-resort title fallback. Walks back through URL path segments
    looking for one with readable content (not just a GUID or numeric ID)."""
    try:
        from urllib.parse import urlparse as _up
        path = _up(url).path
        segments = [s for s in path.rstrip('/').split('/') if s]
        if not segments:
            return ''
        for raw_segment in reversed(segments):
            seg = raw_segment.split('?')[0].split('#')[0]
            seg = seg.replace('-', ' ').replace('_', ' ')
            seg = _re_slug.sub(
                r'[0-9a-f]{8}[\s-]?[0-9a-f]{4}[\s-]?[0-9a-f]{4}[\s-]?[0-9a-f]{4}[\s-]?[0-9a-f]{12}',
                '', seg, flags=_re_slug.IGNORECASE
            )
            words = seg.split()
            while words and words[0].isdigit():
                words.pop(0)
            words = [w for w in words if not (len(w) >= 8 and all(c in '0123456789abcdefABCDEF' for c in w))]
            words = [w for w in words if not (w.isdigit() and len(w) <= 4)]
            if len(words) >= 2:
                return ' '.join(words[:12]).title().strip()
        return ''
    except Exception:
        return ''


def _extract_published_date(soup, raw_html_or_text=''):
    """Extract publication datetime from HTML or markdown.

    Tries the following sources in order, returns the first successful parse:
      1. <meta property="article:published_time">  (Open Graph -- most reliable)
      2. <meta property="og:article:published_time">
      3. <meta name="pubdate"> / <meta name="publish-date"> / <meta name="date">
      4. <meta itemprop="datePublished">
      5. <time datetime="...">  (first <time> element with a datetime attr)
      6. JSON-LD: { "datePublished": "..." } in any <script type="application/ld+json">
      7. Markdown header line "Published Time: ..." or "Date: ..." (Jina format)

    Returns datetime object (naive UTC) or None.
    """
    from datetime import datetime, timezone

    def _parse(s):
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        if not s:
            return None
        # Try ISO 8601 (with or without timezone)
        try:
            # Handle trailing Z (Python <3.11 doesn't accept it in fromisoformat)
            iso = s.replace('Z', '+00:00')
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            pass
        # Try common alternative formats
        for fmt in (
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d',
            '%a, %d %b %Y %H:%M:%S %z',  # RFC 822
            '%a, %d %b %Y %H:%M:%S GMT',
            '%d %b %Y %H:%M:%S',
            '%B %d, %Y',
            '%b %d, %Y',
        ):
            try:
                dt = datetime.strptime(s, fmt)
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
            except (ValueError, TypeError):
                continue
        return None

    # Path 1-4: meta tags via BeautifulSoup
    if soup is not None:
        # Path 1
        m = soup.find('meta', property='article:published_time')
        if m and m.get('content'):
            d = _parse(m['content'])
            if d: return d
        # Path 2
        m = soup.find('meta', property='og:article:published_time')
        if m and m.get('content'):
            d = _parse(m['content'])
            if d: return d
        # Path 3 (multiple name candidates)
        for nm in ('pubdate', 'publish-date', 'publication-date', 'date',
                   'DC.date.issued', 'sailthru.date', 'parsely-pub-date'):
            m = soup.find('meta', attrs={'name': nm})
            if m and m.get('content'):
                d = _parse(m['content'])
                if d: return d
        # Path 4
        m = soup.find('meta', attrs={'itemprop': 'datePublished'})
        if m and m.get('content'):
            d = _parse(m['content'])
            if d: return d
        # Path 5: <time datetime="...">
        for t in soup.find_all('time'):
            if t.get('datetime'):
                d = _parse(t['datetime'])
                if d: return d
        # Path 6: JSON-LD
        try:
            import json as _json
            for script in soup.find_all('script', type='application/ld+json'):
                txt = script.string or script.get_text()
                if not txt:
                    continue
                try:
                    data = _json.loads(txt)
                except Exception:
                    continue
                # JSON-LD can be a dict or a list of dicts
                candidates = data if isinstance(data, list) else [data]
                for c in candidates:
                    if isinstance(c, dict):
                        v = c.get('datePublished') or c.get('dateCreated')
                        if v:
                            d = _parse(v)
                            if d: return d
        except Exception:
            pass

    # Path 7: markdown / plain text header (Jina format)
    if raw_html_or_text and isinstance(raw_html_or_text, str):
        for line in raw_html_or_text.split('\n')[:30]:
            stripped = line.strip()
            for prefix in ('Published Time:', 'Published:', 'Date:', 'Publication Date:'):
                if stripped.startswith(prefix):
                    val = stripped[len(prefix):].strip()
                    d = _parse(val)
                    if d: return d

    return None



def _try_direct_scrape(url):
    try:
        import requests as _rq
        from bs4 import BeautifulSoup
        r = _rq.get(url, timeout=(8, 15), headers={'User-Agent': _USER_AGENT})
        if r.status_code != 200:
            print(f"[direct] HTTP {r.status_code}")
            return None
        soup = BeautifulSoup(r.text, 'html.parser')
        title_tag = soup.find('title')
        title = title_tag.text.strip() if title_tag else ''
        if _is_bot_protection(title):
            print(f"[direct] Bot protection: '{title}'")
            return None
        if not title:
            og = soup.find('meta', property='og:title')
            if og and og.get('content'):
                title = og['content'].strip()
        if not title:
            tw = soup.find('meta', attrs={'name': 'twitter:title'})
            if tw and tw.get('content'):
                title = tw['content'].strip()
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text().strip()
        body = ' '.join(p.get_text() for p in soup.find_all('p'))[:8000]
        if len(body) < 500:
            print(f"[direct] Too thin ({len(body)} chars)")
            return None
        if _is_paywall(title, body):
            print(f"[direct] Paywall detected")
            return None
        published_at = _extract_published_date(soup, r.text)
        return {'title': title, 'body': body, 'method': 'direct', 'published_at': published_at}
    except Exception as e:
        print(f"[direct] Failed: {e}")
        return None


def _try_jina_reader(url):
    try:
        import requests as _rq
        headers = {'Accept': 'text/plain', 'X-Return-Format': 'markdown'}
        jk = os.getenv('JINA_API_KEY')
        if jk:
            headers['Authorization'] = f'Bearer {jk}'
        r = _rq.get(f'https://r.jina.ai/{url}', headers=headers, timeout=(10, 25))
        if r.status_code != 200:
            print(f"[jina] HTTP {r.status_code}")
            return None
        text = r.text
        if len(text) < 500:
            print(f"[jina] Too thin ({len(text)} chars)")
            return None
        title = ''
        for line in text.split('\n')[:30]:
            if line.strip().startswith('Title:'):
                title = line.strip()[len('Title:'):].strip()
                break
        body = text.split('Markdown Content:', 1)[1].strip() if 'Markdown Content:' in text else '\n'.join(text.split('\n')[5:]).strip()
        body = body[:8000]
        if _is_bot_protection(title):
            print(f"[jina] Bot protection: '{title}'")
            return None
        if len(body) < 500:
            print(f"[jina] Body too thin ({len(body)} chars)")
            return None
        if _is_paywall(title, body):
            print(f"[jina] Paywall detected")
            return None
        if not title:
            for line in body.split('\n')[:50]:
                stripped = line.strip()
                if stripped.startswith('# '):
                    title = stripped[2:].strip()
                    break
                if stripped.startswith('## '):
                    title = stripped[3:].strip()
                    break
        if not title:
            title = _clean_url_slug(url)
        published_at = _extract_published_date(None, text)
        return {'title': title, 'body': body, 'method': 'jina', 'published_at': published_at}
    except Exception as e:
        print(f"[jina] Failed: {e}")
        return None


def _try_web_search(url, anthropic_client):
    try:
        from urllib.parse import urlparse as _up
        domain = _up(url).netloc.replace('www.', '')
        slug = url.rstrip('/').split('/')[-1]
        keywords = ' '.join(slug.replace('-', ' ').split()[:8])
        query = (f"I need to analyze a news article from {domain}. URL: {url}\n"
                 f"Topic: {keywords}\n\nSearch OTHER outlets (Reuters, AP, NPR, BBC, etc.) and return:\n"
                 f"HEADLINE: [headline]\n\nCONTENT:\n[1500+ words of factual content]\n\n"
                 f"Do NOT return {domain} content -- bot-protected.")
        msg = anthropic_client.messages.create(
            model='claude-sonnet-4-6', max_tokens=2500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{'role': 'user', 'content': query}])
        from token_logging import log_usage; log_usage('fetcher_web_search', msg)
        text = ''.join(b.text for b in msg.content if hasattr(b, 'text'))
        if not text or len(text) < 500:
            print(f"[web_search] Too thin")
            return None
        title = ''
        for line in text.split('\n')[:10]:
            if line.startswith('HEADLINE:'):
                title = line[9:].strip()
                break
        body = text.split('CONTENT:', 1)[1].strip() if 'CONTENT:' in text else text
        body = body[:8000]
        if _is_bot_protection(title):
            title = ''
        if len(body) < 500:
            return None
        if _is_paywall(title, body):
            print(f"[web_search] Paywall detected")
            return None
        if not title:
            title = _clean_url_slug(url)
        return {'title': title, 'body': body, 'method': 'web_search', 'published_at': None}
    except Exception as e:
        print(f"[web_search] Failed: {e}")
        return None


def fetch_article_content(url, anthropic_client=None):
    result = _try_direct_scrape(url)
    if result:
        print(f"[fetch] direct: {len(result['body'])} chars")
        return result
    result = _try_jina_reader(url)
    if result:
        print(f"[fetch] jina: {len(result['body'])} chars")
        return result
    if anthropic_client:
        result = _try_web_search(url, anthropic_client)
        if result:
            print(f"[fetch] web_search: {len(result['body'])} chars")
            return result
    try:
        import requests as _rq
        from bs4 import BeautifulSoup
        r = _rq.get(url, timeout=(8, 15), headers={'User-Agent': _USER_AGENT})
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            probe_body = ' '.join(p.get_text() for p in soup.find_all('p'))[:8000]
            if _is_paywall('', probe_body):
                print(f"[fetch] Paywall confirmed for {url}")
                return {'status': 'paywall'}
    except Exception as e:
        print(f"[fetch] Paywall probe failed: {e}")
    print(f"[fetch] All methods failed for {url}")
    return None
