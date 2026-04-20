import os, json, requests, time
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

OUTLETS = [
    "foxnews.com",
    "cnn.com",
    "bbc.com",
    "theguardian.com",
    "nytimes.com",
    "washingtonpost.com",
    "politico.com",
    "thehill.com",
    "axios.com",
    "npr.org",
    "coindesk.com",
    "cointelegraph.com",
    "decrypt.co",
    "theblock.co",
    "blockworks.co",
]
def query_gdelt(domain, days_back=30, max_records=100):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    start_str = start_date.strftime("%Y%m%d%H%M%S")
    end_str = end_date.strftime("%Y%m%d%H%M%S")
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": f"domain:{domain}",
        "mode": "artlist",
        "maxrecords": max_records,
        "startdatetime": start_str,
        "enddatetime": end_str,
        "format": "json"
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            data = r.json()
            articles = data.get("articles", [])
            print(f"  ok {domain}: {len(articles)}")
            return articles
        else:
            print(f"  skip {domain}: {r.status_code}")
            return []
    except Exception as e:
        pass

def convert_article(article, domain):
    return {
        "title": article.get("title", ""),
        "description": article.get("title", ""),
        "content": article.get("title", ""),
        "url": article.get("url", ""),
        "publishedAt": article.get("seendate", ""),
        "source": {"name": domain},
        "fetch_source": "gdelt"
    }

def seed_gdelt(days_back=30):
    print(f"GDELT seed — last {days_back} days")
    all_articles = []
    for domain in OUTLETS:
        articles = query_gdelt(domain, days_back)
        for a in articles:
            c = convert_article(a, domain)
            if c["title"] and c["url"]:
                all_articles.append(c)
    seen = set()
    unique = []
    for a in all_articles:
        url = a.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(a)
    print(f"Total unique: {len(unique)}")
    today = datetime.now().strftime("%Y-%m-%d")
    fname = f"gdelt_seed_{today}.json"
    with open(fname, "w") as f:
        json.dump(unique, f, indent=2)
    print(f"Saved to {fname}")
    return unique

if __name__ == "__main__":
    seed_gdelt(days_back=30)