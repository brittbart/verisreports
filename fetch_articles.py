import os, json, socket, feedparser
from newsapi import NewsApiClient
from dotenv import load_dotenv
from datetime import datetime

socket.setdefaulttimeout(10)
if os.path.exists(".env"):
    load_dotenv(override=False)
newsapi = NewsApiClient(api_key=os.getenv("NEWSAPI_KEY"))
FEEDS = [
    # Conservative US
    "https://news.google.com/rss/search?q=site:breitbart.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:dailywire.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:nypost.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:newsmax.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:thefederalist.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:washingtontimes.com&hl=en-US&gl=US&ceid=US:en",
    # Libertarian
    "https://news.google.com/rss/search?q=site:reason.com&hl=en-US&gl=US&ceid=US:en",
    # African American press
    "https://news.google.com/rss/search?q=site:theroot.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:thegrio.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:atlantablackstar.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:blackenterprise.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:essence.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:ebony.com&hl=en-US&gl=US&ceid=US:en",
    # Indigenous
    "https://news.google.com/rss/search?q=site:indiancountrytoday.com&hl=en-US&gl=US&ceid=US:en",
    # Latino
    "https://news.google.com/rss/search?q=site:univision.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:latinousa.org&hl=en-US&gl=US&ceid=US:en",
    # International
    "https://news.google.com/rss/search?q=site:haaretz.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:scmp.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:premiumtimesng.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:nation.africa&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:middleeasteye.net&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:thehindu.com&hl=en-US&gl=US&ceid=US:en",
    # Progressive left
    "https://news.google.com/rss/search?q=site:truthout.org&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:commondreams.org&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:democracynow.org&hl=en-US&gl=US&ceid=US:en",
    # BBC
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.bbci.co.uk/news/politics/rss.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    # US Politics
    "https://feeds.npr.org/1001/rss.xml",
    "https://feeds.npr.org/1014/rss.xml",
    "https://thehill.com/rss/syndicator/19109",
    "https://www.axios.com/feeds/feed.rss",
    "https://feeds.foxnews.com/foxnews/latest",
    "https://feeds.foxnews.com/foxnews/politics",
    "https://www.aljazeera.com/xml/rss/all.xml",
    # Mainstream news
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/us-news/rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "https://feeds.washingtonpost.com/rss/politics",
    "https://feeds.washingtonpost.com/rss/business",
    "https://news.google.com/rss/search?q=site:bbc.co.uk&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:dawn.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:theguardian.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:politico.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:theatlantic.com&hl=en-US&gl=US&ceid=US:en",
    # Reuters and AP via Google News RSS
    "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:apnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:dw.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:france24.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:washingtonexaminer.com&hl=en-US&gl=US&ceid=US:en",
    # Left-leaning US
    "https://news.google.com/rss/search?q=site:thenation.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:jacobin.com&hl=en-US&gl=US&ceid=US:en",
    # Latin America
    "https://news.google.com/rss/search?q=site:mercopress.com&hl=en-US&gl=US&ceid=US:en",
    # Africa
    "https://news.google.com/rss/search?q=site:dailymaverick.co.za&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:allafrica.com&hl=en-US&gl=US&ceid=US:en",
    # Crypto
    "https://decrypt.co/feed",
    "https://blockworks.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://coindesk.com/arc/outboundfeeds/rss",
    "https://cointelegraph.com/rss",
    "https://theblock.co/rss",
    # US Local — Northeast
    "https://news.google.com/rss/search?q=site:bostonglobe.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:bostonherald.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:inquirer.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:baltimoresun.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:post-gazette.com&hl=en-US&gl=US&ceid=US:en",
    # US Local — Southeast
    "https://news.google.com/rss/search?q=site:ajc.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:miamiherald.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:tampabay.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:charlotteobserver.com&hl=en-US&gl=US&ceid=US:en",
    # US Local — Midwest
    "https://news.google.com/rss/search?q=site:chicagotribune.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:suntimes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:freep.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:startribune.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:cleveland.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:stltoday.com&hl=en-US&gl=US&ceid=US:en",
    # US Local — Southwest
    "https://news.google.com/rss/search?q=site:dallasnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:houstonchronicle.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:azcentral.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:denverpost.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:reviewjournal.com&hl=en-US&gl=US&ceid=US:en",
    # US Local — West Coast
    "https://news.google.com/rss/search?q=site:latimes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:sfchronicle.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:seattletimes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:oregonlive.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:sandiegouniontribune.com&hl=en-US&gl=US&ceid=US:en",
    # International — UK
    "https://news.google.com/rss/search?q=site:thetimes.co.uk&hl=en-GB&gl=GB&ceid=GB:en",
    "https://news.google.com/rss/search?q=site:telegraph.co.uk&hl=en-GB&gl=GB&ceid=GB:en",
    "https://news.google.com/rss/search?q=site:independent.co.uk&hl=en-GB&gl=GB&ceid=GB:en",
    "https://news.google.com/rss/search?q=site:standard.co.uk&hl=en-GB&gl=GB&ceid=GB:en",
    # International — Australia
    "https://news.google.com/rss/search?q=site:smh.com.au&hl=en-AU&gl=AU&ceid=AU:en",
    "https://news.google.com/rss/search?q=site:theaustralian.com.au&hl=en-AU&gl=AU&ceid=AU:en",
    # International — Canada
    "https://news.google.com/rss/search?q=site:thestar.com&hl=en-CA&gl=CA&ceid=CA:en",
    "https://news.google.com/rss/search?q=site:theglobeandmail.com&hl=en-CA&gl=CA&ceid=CA:en",
    # International — Asia & Other
    "https://news.google.com/rss/search?q=site:timesofindia.com&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site:japantimes.co.jp&hl=en-US&gl=US&ceid=US:en",
    # International — Ireland
    "https://news.google.com/rss/search?q=site:irishtimes.com&hl=en-IE&gl=IE&ceid=IE:en",
    "https://news.google.com/rss/search?q=site:independent.ie&hl=en-IE&gl=IE&ceid=IE:en",
    # International — Europe
    "https://news.google.com/rss/search?q=site:lemonde.fr&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:spiegel.de/international&hl=en-US&gl=US&ceid=US:en",
    # Religious — Catholic
    "https://news.google.com/rss/search?q=site:religionnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:ncronline.org&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:catholicherald.co.uk&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:ewtn.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:cruxnow.com&hl=en-US&gl=US&ceid=US:en",
    # Religious — Evangelical/Protestant
    "https://news.google.com/rss/search?q=site:christianitytoday.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:christianpost.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:baptistpress.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:wng.org&hl=en-US&gl=US&ceid=US:en",
    # Religious — Progressive Christian
    "https://news.google.com/rss/search?q=site:sojo.net&hl=en-US&gl=US&ceid=US:en",
    # Religious — Jewish
    "https://news.google.com/rss/search?q=site:jta.org&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:forward.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:jewishjournal.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:tabletmag.com&hl=en-US&gl=US&ceid=US:en",
    # Religious — Muslim
    "https://news.google.com/rss/search?q=site:muslimobserver.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:islam21c.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:aa.com.tr/en&hl=en-US&gl=US&ceid=US:en",
    # Religious — Mormon
    "https://news.google.com/rss/search?q=site:deseret.com&hl=en-US&gl=US&ceid=US:en",
    # Religious — Buddhist
    "https://news.google.com/rss/search?q=site:tricycle.org&hl=en-US&gl=US&ceid=US:en",
    # Religion journalism
    "https://news.google.com/rss/search?q=site:religiondispatches.org&hl=en-US&gl=US&ceid=US:en",
    # Entertainment / Celebrity
    "https://news.google.com/rss/search?q=site:tmz.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:people.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:ew.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:variety.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:hollywoodreporter.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:deadline.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:eonline.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:usmagazine.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:pagesix.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:vulture.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:pitchfork.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:rollingstone.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:billboard.com&hl=en-US&gl=US&ceid=US:en",
]

KEYWORDS = ["bitcoin", "ethereum", "crypto",
    "Trump", "Congress", "inflation", "economy"]

def fetch_rss():
    articles = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            name = feed.feed.get("title", url)
            for e in feed.entries[:10]:
                a = {
                    "title": e.get("title", ""),
                    "description": e.get("summary", ""),
                    "content": e.get("summary", ""),
                    "url": e.get("link", ""),
                    "publishedAt": e.get("published", ""),
                    "source": {"name": name},
                    "fetch_source": "rss"
                }
                if a["title"] and a["url"]:
                    articles.append(a)
            print(f"  ok {name}")
        except Exception as e:
            print(f"  skip: {str(e)[:40]}")
    return articles

def fetch_newsapi():
    articles = []
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
    return articles

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

if __name__ == "__main__":
    fetch_articles()