
import re

path = "/home/veris/projects/veris/fetch_articles.py"
content = open(path).read()

new_func = """def _fetch_one(url):
    import requests as _req
    try:
        _r = _req.get(url, timeout=(8,12), headers={"User-Agent":"Mozilla/5.0"}, stream=True)
        raw = b""
        for chunk in _r.iter_content(chunk_size=8192):
            raw += chunk
            if len(raw) > 2_000_000: break
        import feedparser as _fp
        return url, _fp.parse(raw)
    except Exception as e:
        print(f"  skip {url[:60]} ({e})")
        return url, None

def fetch_rss():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import re
    articles = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(_fetch_one, url): url for url in FEEDS}
        results = {}
        for f in as_completed(futures, timeout=120):
            url, feed = f.result()
            results[url] = feed
    for url in FEEDS:
        feed = results.get(url)
        if not feed:
            continue
        try:
            match = re.search(r"site:([^&]+)", url)
            if match:
                name = match.group(1)
            else:
                from urllib.parse import urlparse
                host = urlparse(url).hostname or url
                name = host.replace("www.","").replace("feeds.","").replace("rss.","")
            for e in feed.entries[:10]:
                a = {
                    "title": e.get("title", ""),
                    "url": e.get("link", ""),
                    "source_name": name,
                    "description": e.get("summary", "")[:500]
                }
                if a["url"] and any(k.lower() in a["title"].lower() + " " + a["description"].lower() for k in KEYWORDS):
                    articles.append(a)
            print(f"  ok {name}")
        except Exception as e:
            print(f"  skip: {str(e)[:40]}")
    return articles
"""

start = content.find("def fetch_rss():")
rest = content[start:]
import re as _re
nxt = _re.search(r"
def [a-z]", rest[10:])
end = start + 10 + nxt.start() if nxt else len(content)

new_content = content[:start] + new_func + "
" + content[end:]
open(path, "w").write(new_content)
print("Done")
