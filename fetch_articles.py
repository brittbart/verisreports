import os, json, socket, feedparser, re
socket.setdefaulttimeout(10)
try:
    from langdetect import detect
    LANGDETECT = True
except:
    LANGDETECT = False

def is_english(text):
    if not LANGDETECT or not text or len(text) < 20:
        return True
    try:
        return detect(text) == "en"
    except:
        return True
from newsapi import NewsApiClient
from dotenv import load_dotenv
from datetime import datetime

socket.setdefaulttimeout(10)
if os.path.exists(".env"):
    load_dotenv(override=False)
newsapi = NewsApiClient(api_key=os.getenv("NEWSAPI_KEY"))
FEEDS = [
    # ── BBC (direct) ──────────────────────────────────────────────────────────
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.bbci.co.uk/news/politics/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",

    # ── NPR (direct) ──────────────────────────────────────────────────────────
    "https://feeds.npr.org/1001/rss.xml",
    "https://feeds.npr.org/1014/rss.xml",
    "https://feeds.npr.org/1004/rss.xml",

    # ── US mainstream (direct) ────────────────────────────────────────────────
    "https://thehill.com/rss/syndicator/19109",
    "https://feeds.foxnews.com/foxnews/latest",
    "https://feeds.foxnews.com/foxnews/politics",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/us-news/rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "http://rss.cnn.com/rss/cnn_allpolitics.rss",
    "https://www.msnbc.com/feeds/latest",
    "https://www.washingtonexaminer.com/section/news/feed",
    "https://theintercept.com/feed/?rss",
    "https://www.motherjones.com/feed/",
    "https://www.nationalreview.com/feed/",
    "https://www.vox.com/rss/index.xml",

    # ── US mainstream (now direct, was Google News) ───────────────────────────
    "https://api.axios.com/feed/",
    "https://rss.upi.com/news/top_news.rss",
    "https://news.yahoo.com/rss/world",
    "https://www.whitehouse.gov/news/feed/",
    "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=10",
    "https://www.gao.gov/rss/reports.xml",
    "https://rss.politico.com/politics-news.xml",
    "https://feeds.bloomberg.com/politics/news.rss",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "https://www.theatlantic.com/feed/all/",
    "https://thedispatch.com/feed/",

    # ── Wire services: now fetched via NewsAPI (see NEWSAPI_WIRE_DOMAINS) ──────

    # ── Conservative US (direct) ──────────────────────────────────────────────
    "https://feeds.feedburner.com/breitbart",
    "https://www.dailywire.com/feeds/rss.xml",
    "https://nypost.com/feed/",
    "https://thefederalist.com/feed/",
    "https://www.washingtontimes.com/rss/headlines/news/politics/",

    # ── Libertarian (direct) ──────────────────────────────────────────────────
    "https://reason.com/feed/",

    # ── Left / progressive (direct) ───────────────────────────────────────────
    "https://truthout.org/feed/",
    "https://www.commondreams.org/rss.xml",
    "https://www.democracynow.org/democracynow.rss",
    "https://www.thenation.com/feed/?post_type=article",
    "https://jacobin.com/feed/",
    "https://slate.com/feeds/all.rss",

    # ── Center / analysis ─────────────────────────────────────────────────────
    "https://www.propublica.org/feeds/propublica/main",
    "https://www.realclearpolitics.com/xml/politics.xml",
    "https://www.csmonitor.com/rss/top_stories.rss",

    # ── Business / finance ────────────────────────────────────────────────────
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://fortune.com/feed/",
    "https://www.forbes.com/real-time/feed2/",
    "https://feeds.businessinsider.com/business-insider/tech",
    "https://www.marketwatch.com/rss/topstories",

    # ── Science / health ──────────────────────────────────────────────────────
    "https://www.sciencenews.org/feed",
    "https://www.newscientist.com/feed/home/",
    "https://www.scientificamerican.com/platform/syndication/rss/",
    "https://www.nature.com/news.rss",
    "https://www.webmd.com/rss/rss.xml",
    "https://www.healthline.com/rss",

    # ── Sports ────────────────────────────────────────────────────────────────
    "https://www.espn.com/espn/rss/news",
    "https://bleacherreport.com/articles/feed",
    "https://www.si.com/rss/si_topstories.rss",

    # ── Military ──────────────────────────────────────────────────────────────
    "https://www.militarytimes.com/arc/outboundfeeds/rss/",
    "https://www.stripes.com/arc/outboundfeeds/rss/",

    # ── African American press ────────────────────────────────────────────────
    "https://theroot.com/rss",
    "https://thegrio.com/feed/",
    "https://atlantablackstar.com/feed/",
    "https://www.blackenterprise.com/feed/",
    "https://www.essence.com/feed/",
    "https://www.ebony.com/feed/",
    "https://colorlines.com/feed/",

    # ── Latino / Hispanic ─────────────────────────────────────────────────────
    "https://www.univision.com/rss",
    "https://latinousa.org/feed/",
    "https://feeds.nbcnews.com/nbcnews/sections/noticias",
    "https://www.telemundo.com/rss",
    "https://www.laopinion.com/feed/",
    "https://elpais.com/rss/america/portada.xml",

    # ── Asian American ────────────────────────────────────────────────────────
    "https://asamnews.com/feed/",
    "https://nextshark.com/feed/",

    # ── Indigenous ────────────────────────────────────────────────────────────
    "https://indiancountrytoday.com/feed",
    "https://ictnews.org/feed/",
    "https://nativenewsonline.net/feed/",

    # ── Religion ──────────────────────────────────────────────────────────────
    "https://www.catholicnewsagency.com/feed",
    "https://www.ncronline.org/rss.xml",
    "https://religionnews.com/feed/",
    "https://cruxnow.com/feed/",
    "https://jewishweek.timesofisrael.com/feed/",
    "https://forward.com/feed/",
    "https://muslimmatters.org/feed/",
    "https://www.christianitytoday.com/ct/rss.xml",
    "https://www.jpost.com/rss/rssfeedsheadlines.aspx",

    # ── International — UK ────────────────────────────────────────────────────
    "https://www.independent.co.uk/rss",
    "https://www.standard.co.uk/rss",
    "https://www.irishtimes.com/cmlink/news-1.1319192",
    "https://www.newyorker.com/feed/news",
    "https://www.economist.com/latest/rss.xml",
    "https://foreignpolicy.com/feed/",
    "https://www.spectator.co.uk/feed/",

    # ── International — Europe ────────────────────────────────────────────────
    "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
    "https://balkaninsight.com/feed/",
    "https://www.dw.com/rss/rss.xml",
    "https://www.france24.com/en/rss",
    "https://www.lemonde.fr/en/rss/une.xml",

    # ── International — Middle East ───────────────────────────────────────────
    "https://www.arabnews.com/rss.xml",
    "https://www.middleeasteye.net/rss",
    "https://www.haaretz.com/cmlink/1.628765",

    # ── International — Asia ──────────────────────────────────────────────────
    "https://www.japantimes.co.jp/feed/",
    "https://www.channelnewsasia.com/rssfeeds/8395986",
    "https://www.hindustantimes.com/rss/topnews/rssfeed.xml",
    "https://www.thehindu.com/feeder/default.rss",
    "https://www.dawn.com/feeds/home",
    "https://www.thenews.com.pk/rss/1/1",
    "https://bdnews24.com/?widgetName=rssfeed&widgetId=1150&getXmlFeed=true",
    "https://www.globaltimes.cn/rss/outbrain.xml",
    "https://www.scmp.com/rss/91/feed",
    "https://hongkongfp.com/feed/",

    # ── International — Africa & Diaspora ────────────────────────────────────
    "https://naija247news.com/feed/",
    "https://www.premiumtimesng.com/feed",
    "https://www.dailymaverick.co.za/feed/",
    "https://allafrica.com/tools/headlines/rdf.html",
    "https://nation.africa/feed",

    # ── International — Latin America ─────────────────────────────────────────
    "https://mercopress.com/rss",

    # ── International — Australia / Canada ────────────────────────────────────
    "https://www.smh.com.au/rss/feed.xml",
    "https://www.theaustralian.com.au/feed/",
    "https://www.thestar.com/rss/",
    "https://www.theglobeandmail.com/arc/outboundfeeds/rss/",
    "https://www.cbc.ca/cmlink/rss-topstories",

    # ── US Local — Colorado (debate coverage priority) ────────────────────────
    "https://coloradosun.com/feed/",
    "https://www.denverpost.com/feed/",
    "https://www.westword.com/rss.xml",

    # ── US Local — Northeast ──────────────────────────────────────────────────
    "https://www.bostonglobe.com/topstories/rss.xml",
    "https://www.bostonherald.com/feed/",
    "https://www.inquirer.com/arcio/rss/",
    "https://www.baltimoresun.com/arcio/rss/",
    "https://www.post-gazette.com/rss/feeds/news",
    "https://www.washingtonian.com/feed/",

    # ── US Local — Southeast ──────────────────────────────────────────────────
    "https://www.ajc.com/arcio/rss/",
    "https://www.miamiherald.com/arcio/rss/",
    "https://www.tampabay.com/arcio/rss/",
    "https://www.charlotteobserver.com/arcio/rss/",
    "https://www.nola.com/search/?f=rss&t=article",
    "https://richmond.com/search/?f=rss",
    "https://www.tennessean.com/arcio/rss/",

    # ── US Local — Midwest ────────────────────────────────────────────────────
    "https://www.chicagotribune.com/arcio/rss/",
    "https://chicago.suntimes.com/rss/index.xml",
    "https://www.freep.com/arcio/rss/",
    "https://www.startribune.com/local/rss",
    "https://www.cleveland.com/arcio/rss/",
    "https://www.stltoday.com/search/?f=rss",
    "https://omaha.com/search/?f=rss",
    "https://bridgedetroit.com/feed/",
    "https://www.columbiamissourian.com/search/?f=rss",

    # ── US Local — South / Southwest ──────────────────────────────────────────
    "https://www.dallasnews.com/arcio/rss/",
    "https://www.houstonchronicle.com/arcio/rss/",
    "https://www.azcentral.com/arcio/rss/",
    "https://tucson.com/search/?f=rss",
    "https://www.texastribune.org/feeds/recent/",

    # ── US Local — West Coast ────────────────────────────────────────────────
    "https://www.latimes.com/local/rss2.0.xml",
    "https://www.sfchronicle.com/arcio/rss/",
    "https://www.seattletimes.com/feed/",
    "https://www.oregonlive.com/arcio/rss/",
    "https://www.sandiegouniontribune.com/arcio/rss/",
    "https://calmatters.org/feed/",

    # ── US Local — Investigative / nonprofit ─────────────────────────────────
    "https://washingtonian.com/feed/",


    # ── International — Australia / New Zealand ───────────────────────────────
    "https://www.abc.net.au/news/feed/51120/rss.xml",
    "https://www.rnz.co.nz/rss/national.xml",
    "https://www.rnz.co.nz/rss/political.xml",

    # ── International — Africa (additional) ──────────────────────────────────
    "https://www.africanews.com/feed/",
    "https://punchng.com/feed/",

    # ── International — Latin America ─────────────────────────────────────────
    "https://mexiconewsdaily.com/feed/",
    "https://buenosairesherald.com/feed/",

    # ── International — India (additional) ───────────────────────────────────
    "https://indianexpress.com/feed/",
    "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
    "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
    "https://www.thehindubusinessline.com/feeder/default.rss",

    # ── International — UK (additional) ──────────────────────────────────────
    "https://www.dailymail.co.uk/news/index.rss",

    # ── US Public media ───────────────────────────────────────────────────────
    "https://www.pbs.org/newshour/feeds/rss/headlines",
    "https://www.kqed.org/news/feed",
    "https://wamu.org/feed/",

    # ── Heterodox / center ────────────────────────────────────────────────────
    "https://www.thefp.com/feed",
    "https://www.mediaite.com/feed/",

    # ── Climate / environment ─────────────────────────────────────────────────
    "https://insideclimatenews.org/feed/",

    # ── Analysis / policy ─────────────────────────────────────────────────────
    "https://theconversation.com/us/articles.atom",
    "https://stateline.org/feed/",

    # ── US Local — investigative / nonprofit ──────────────────────────────────
    "https://thecity.nyc/feed",
    "https://gothamist.com/feed",
    "https://denverite.com/feed/",
    "https://chicagoreader.com/feed/",
    "https://www.mississippifreepress.org/feed/",

    # ── Crypto / Web3 ────────────────────────────────────────────────────────
    "https://cointelegraph.com/rss",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://blockworks.co/feed/",
    "https://newsbtc.com/feed/",
    "https://cryptobriefing.com/feed/",
]

KEYWORDS = ["Trump", "Congress", "inflation", "economy"]


def _fetch_feed(url):
    import requests as _req
    try:
        _r = _req.get(url, timeout=(8, 12),
                      headers={'User-Agent': 'Mozilla/5.0'},
                      stream=True)
        raw = b''
        for chunk in _r.iter_content(chunk_size=16384):
            raw += chunk
            if len(raw) > 2_000_000: break
        return url, feedparser.parse(raw)
    except Exception as e:
        print(f'  skip {url[:60]} ({e})')
        return url, None


def fetch_rss():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    articles = []
    results = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_fetch_feed, url): url for url in FEEDS}
        for future in as_completed(futures, timeout=90):
            url, feed = future.result()
            results[url] = feed
    for url in FEEDS:
        feed = results.get(url)
        if not feed:
            continue
        try:
            match = re.search(r'site:([^&]+)', url)
            if match:
                name = match.group(1)
            else:
                from urllib.parse import urlparse
                host = urlparse(url).hostname or url
                name = host.replace('www.', '').replace('feeds.', '').replace('rss.', '')
            for e in feed.entries[:10]:
                a = {
                    'title': e.get('title', ''),
                    'description': e.get('summary', ''),
                    'content': e.get('summary', ''),
                    'url': e.get('link', ''),
                    'publishedAt': _parse_rss_date(e.get('published', '')),
                    'source': {'name': name},
                    'fetch_source': 'rss'
                }
                # Content quality gate: skip aggregator redirect URLs (v1.7)
                # news.google.com URLs produce degraded redirect HTML, not article bodies.
                # These articles are NOT inserted into the DB at all (Option A per brief).
                # Articles already in the DB from before this gate are flagged via
                # excluded_from_extraction column. If future requirements need us to
                # preserve aggregator URLs (vs skipping), update this to insert with
                # excluded_from_extraction=TRUE instead.
                is_aggregator = a['url'].startswith('https://news.google.com') or 'news.google.com' in a['url']
                if a['title'] and a['url'] and is_english(a['title'] + ' ' + a['description']) and not is_aggregator:
                    articles.append(a)
                elif is_aggregator:
                    pass  # silently skip aggregator redirects
            print(f'  ok {name}')
        except Exception as e:
            print(f'  skip: {str(e)[:40]}')
    return articles
# Wire services with no direct RSS — fetched via NewsAPI domains parameter
NEWSAPI_WIRE_DOMAINS = [
    'apnews.com',
    'washingtonpost.com',
    'newsmax.com',
]

def fetch_newsapi():
    articles = []
    # Keyword queries (existing)
    for kw in KEYWORDS:
        try:
            r = newsapi.get_everything(
                q=kw,
                language="en",
                sort_by="publishedAt",
                page_size=10
            )
            if r["status"] == "ok":
                for a in r["articles"]:
                    a["fetch_source"] = "newsapi"
                articles.extend(r["articles"])
                print(f"  ok {kw}: {len(r['articles'])}")
        except Exception as e:
            print(f"  skip {kw}: {str(e)[:40]}")
    # Wire service domain queries — replaces Google News feeds for these outlets
    for domain in NEWSAPI_WIRE_DOMAINS:
        try:
            r = newsapi.get_everything(
                domains=domain,
                language="en",
                sort_by="publishedAt",
                page_size=20
            )
            if r["status"] == "ok":
                for a in r["articles"]:
                    a["fetch_source"] = "newsapi"
                articles.extend(r["articles"])
                print(f"  ok newsapi:{domain}: {len(r['articles'])}")
        except Exception as e:
            print(f"  skip newsapi:{domain}: {str(e)[:40]}")
    return articles

def _parse_rss_date(raw):
    """Parse RSS/Atom date string to ISO format. Returns None on failure."""
    if not raw or not str(raw).strip():
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(str(raw)).isoformat()
    except Exception:
        try:
            from dateutil import parser as _dp
            return _dp.parse(str(raw)).isoformat()
        except Exception:
            return None

def fetch_articles():
    print("Fetching RSS...")
    rss = fetch_rss()
    print("Fetching NewsAPI...")
    na = fetch_newsapi()
    all_arts = rss + na
    seen = set()
    unique = []
    for a in all_arts:
        url = a.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(a)
    print(f"Total unique: {len(unique)}")
    today = datetime.now().strftime("%Y-%m-%d")
    fname = f"articles_{today}.json"
    with open(fname, "w") as f:
        json.dump(unique, f, indent=2)
    print(f"Saved to {fname}")
    for a in unique[:3]:
        print(f"  {a.get('title', '')[:60]}")
    return unique



def fetch_articles_to_db():
    """Day 24: Run fetch and write articles DIRECTLY to the database.

    Wraps the existing fetch_articles() so the JSON write path is still
    available for ad-hoc/local runs. This function is what Railway cron
    invokes via railway_fetch.py.

    Returns int: count of articles inserted (after dedupe).
    """
    from load_to_database import get_connection, load_single_article_with_claims

    articles = fetch_articles()
    if not articles:
        print("No articles fetched.")
        return 0

    print(f"Writing {len(articles)} fetched articles directly to DB...")
    conn = get_connection()
    cursor = conn.cursor()

    inserted_count = 0
    skipped_count = 0
    try:
        for i, article in enumerate(articles):
            result = load_single_article_with_claims(
                cursor=cursor,
                article=article,
                claims_list=[],
            )
            if result['inserted']:
                inserted_count += 1
            else:
                skipped_count += 1
            if (i + 1) % 25 == 0:
                conn.commit()
                print(f"  [commit] {i+1} articles processed (inserted={inserted_count}, dup={skipped_count})", flush=True)
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    print(f"\n\u2713 Inserted {inserted_count} new articles ({skipped_count} duplicates skipped)")
    return inserted_count


if __name__ == "__main__":
    fetch_articles()