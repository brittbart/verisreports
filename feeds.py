"""
Verum Signal — RSS feed list (v2, April 2026)

Drop-in feed configuration for fetch_articles.py.

Each feed is (display_name, rss_url, category).
Categories drive priority scoring and feed-source attribution.

CATEGORY KEY:
  primary_gov         — US government primary sources (highest verdict weight)
  primary_data        — official data releases (Fed, BLS, SEC, etc.)
  wire                — wire services (independence rule applies aggressively)
  public_broadcaster  — public/state broadcasters
  newspaper_us        — US newspapers
  newspaper_intl      — international newspapers
  cable_broadcast     — cable and broadcast TV news
  digital_native      — digital-first news outlets
  investigative       — nonprofit investigative
  political           — explicitly political outlets (any direction)
  election            — election-cycle specific feeds
  business            — business/financial press
  international       — non-Anglo international perspectives
"""

FEEDS = [
    # PRIMARY GOV
    ("White House Briefings",      "https://www.whitehouse.gov/briefing-room/feed/",          "primary_gov"),
    ("White House Press Releases", "https://www.whitehouse.gov/news/feed/",                   "primary_gov"),
    ("State Department",           "https://www.state.gov/rss-feed/press-releases/feed/",     "primary_gov"),
    ("Department of Defense",      "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=10", "primary_gov"),
    ("Department of Justice",      "https://www.justice.gov/feeds/justice-news.xml",          "primary_gov"),
    ("CDC Newsroom",               "https://tools.cdc.gov/api/v2/resources/media/132608.rss", "primary_gov"),
    ("FDA Press Announcements",    "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", "primary_gov"),
    ("EPA News Releases",          "https://www.epa.gov/newsreleases/search/rss",             "primary_gov"),
    ("DHS News",                   "https://www.dhs.gov/news-releases/releases.xml",          "primary_gov"),
    ("GAO Reports",                "https://www.gao.gov/rss/reports.xml",                     "primary_gov"),
    ("Congress.gov - Bills",       "https://www.congress.gov/rss/most-viewed-bills.xml",      "primary_gov"),
    ("Federal Register",           "https://www.federalregister.gov/articles.rss",            "primary_gov"),
    ("Supreme Court Opinions",     "https://www.supremecourt.gov/rss/cases.aspx",             "primary_gov"),
    # PRIMARY DATA
    ("Federal Reserve Press",      "https://www.federalreserve.gov/feeds/press_all.xml",      "primary_data"),
    ("BLS News Releases",          "https://www.bls.gov/feed/news_release.rss",               "primary_data"),
    ("SEC Press Releases",         "https://www.sec.gov/news/pressreleases.rss",              "primary_data"),
    ("Treasury Press",             "https://home.treasury.gov/news/press-releases/feed",      "primary_data"),
    ("Census Bureau News",         "https://www.census.gov/newsroom/press-releases.xml",      "primary_data"),
    ("BEA News Releases",          "https://www.bea.gov/news/rss.xml",                        "primary_data"),
    # ELECTION
    ("FEC Press",                  "https://www.fec.gov/updates/feed/",                       "election"),
    ("OpenSecrets",                "https://www.opensecrets.org/news/feed/",                  "election"),
    ("Ballotpedia News",           "https://ballotpedia.org/Special:RecentChanges?feed=rss",  "election"),
    ("C-SPAN",                     "https://www.c-span.org/rss/?feed=podcast",                "election"),
    # WIRE
    ("Reuters",                    "https://www.reuters.com/arc/outboundfeeds/rss/?outputType=xml", "wire"),
    ("Reuters Top News",           "https://feeds.reuters.com/reuters/topNews",               "wire"),
    ("Associated Press",           "https://feeds.apnews.com/rss/apf-topnews",                "wire"),
    ("AFP English",                "https://www.afp.com/en/rss.xml",                          "wire"),
    ("Bloomberg",                  "https://feeds.bloomberg.com/news.rss",                    "wire"),
    ("Bloomberg Politics",         "https://feeds.bloomberg.com/politics/news.rss",           "wire"),
    ("Kyodo News",                 "https://english.kyodonews.net/rss/news.xml",              "wire"),
    ("DPA International",          "https://www.dpa-international.com/rss/topthemen.xml",     "wire"),
    # PUBLIC BROADCASTER
    ("BBC News",                   "https://feeds.bbci.co.uk/news/rss.xml",                   "public_broadcaster"),
    ("BBC World",                  "https://feeds.bbci.co.uk/news/world/rss.xml",             "public_broadcaster"),
    ("NPR",                        "https://feeds.npr.org/1001/rss.xml",                      "public_broadcaster"),
    ("NPR Politics",               "https://feeds.npr.org/1014/rss.xml",                      "public_broadcaster"),
    ("PBS NewsHour",               "https://www.pbs.org/newshour/feeds/rss/headlines",        "public_broadcaster"),
    ("DW (Deutsche Welle)",        "https://rss.dw.com/xml/rss-en-all",                       "public_broadcaster"),
    ("France 24",                  "https://www.france24.com/en/rss",                         "public_broadcaster"),
    ("CBC News",                   "https://www.cbc.ca/webfeed/rss/rss-topstories",           "public_broadcaster"),
    ("ABC Australia",              "https://www.abc.net.au/news/feed/45910/rss.xml",          "public_broadcaster"),
    ("Al Jazeera English",         "https://www.aljazeera.com/xml/rss/all.xml",               "public_broadcaster"),
    ("RNZ (New Zealand)",          "https://www.rnz.co.nz/rss/national.xml",                  "public_broadcaster"),
    # NEWSPAPER US
    ("New York Times",             "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "newspaper_us"),
    ("NYT Politics",               "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "newspaper_us"),
    ("Washington Post",            "https://feeds.washingtonpost.com/rss/national",           "newspaper_us"),
    ("WaPo Politics",              "https://feeds.washingtonpost.com/rss/politics",           "newspaper_us"),
    ("Wall Street Journal",        "https://feeds.content.dowjones.io/public/rss/RSSWorldNews", "newspaper_us"),
    ("USA Today",                  "https://www.usatoday.com/rss/",                           "newspaper_us"),
    ("LA Times",                   "https://www.latimes.com/rss2.0.xml",                      "newspaper_us"),
    ("Boston Globe",               "https://www.bostonglobe.com/rss/feed",                    "newspaper_us"),
    ("Chicago Tribune",            "https://www.chicagotribune.com/arc/outboundfeeds/rss/",   "newspaper_us"),
    ("Politico",                   "https://www.politico.com/rss/politicopicks.xml",          "newspaper_us"),
    ("The Hill",                   "https://thehill.com/homenews/feed/",                      "newspaper_us"),
    ("Axios",                      "https://api.axios.com/feed/",                             "digital_native"),
    # NEWSPAPER INTL
    ("The Guardian",               "https://www.theguardian.com/international/rss",           "newspaper_intl"),
    ("Guardian US",                "https://www.theguardian.com/us-news/rss",                 "newspaper_intl"),
    ("Financial Times",            "https://www.ft.com/?format=rss",                          "newspaper_intl"),
    ("The Economist",              "https://www.economist.com/the-world-this-week/rss.xml",   "newspaper_intl"),
    ("Times of India",             "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "newspaper_intl"),
    ("Hindustan Times",            "https://www.hindustantimes.com/feeds/rss/india-news/index.xml", "newspaper_intl"),
    ("South China Morning Post",   "https://www.scmp.com/rss/91/feed",                        "newspaper_intl"),
    ("Times of Israel",            "https://www.timesofisrael.com/feed/",                     "newspaper_intl"),
    ("Haaretz",                    "https://www.haaretz.com/cmlink/1.628152",                 "newspaper_intl"),
    ("Le Monde (English)",         "https://www.lemonde.fr/en/rss/une.xml",                   "newspaper_intl"),
    ("El Pais (English)",          "https://feeds.elpais.com/mrss-s/pages/ep/site/english.elpais.com/portada", "newspaper_intl"),
    ("Irish Times",                "https://www.irishtimes.com/cmlink/news-1.1319192",        "newspaper_intl"),
    ("Sydney Morning Herald",      "https://www.smh.com.au/rss/feed.xml",                     "newspaper_intl"),
    ("Globe and Mail (Canada)",    "https://www.theglobeandmail.com/arc/outboundfeeds/rss/",  "newspaper_intl"),
    ("Hong Kong Free Press",       "https://hongkongfp.com/feed/",                            "newspaper_intl"),
    # CABLE BROADCAST
    ("CNN",                        "https://rss.cnn.com/rss/cnn_topstories.rss",              "cable_broadcast"),
    ("CNN Politics",               "https://rss.cnn.com/rss/cnn_allpolitics.rss",             "cable_broadcast"),
    ("Fox News",                   "https://moxie.foxnews.com/google-publisher/latest.xml",   "cable_broadcast"),
    ("Fox News Politics",          "https://moxie.foxnews.com/google-publisher/politics.xml", "cable_broadcast"),
    ("MSNBC",                      "https://www.msnbc.com/feeds/latest",                      "cable_broadcast"),
    ("NBC News",                   "https://feeds.nbcnews.com/nbcnews/public/news",           "cable_broadcast"),
    ("CBS News",                   "https://www.cbsnews.com/latest/rss/main",                 "cable_broadcast"),
    ("ABC News",                   "https://abcnews.go.com/abcnews/topstories",               "cable_broadcast"),
    # DIGITAL NATIVE
    ("Vox",                        "https://www.vox.com/rss/index.xml",                       "digital_native"),
    ("BuzzFeed News",              "https://www.buzzfeednews.com/news.xml",                   "digital_native"),
    ("Business Insider",           "https://www.businessinsider.com/rss",                     "digital_native"),
    ("Quartz",                     "https://qz.com/rss",                                      "digital_native"),
    ("Semafor",                    "https://www.semafor.com/feed",                            "digital_native"),
    ("The Information",            "https://www.theinformation.com/feed",                    "digital_native"),
    # INVESTIGATIVE
    ("ProPublica",                 "https://www.propublica.org/feeds/propublica/main",        "investigative"),
    ("The Marshall Project",       "https://www.themarshallproject.org/rss/recent.rss",       "investigative"),
    ("Inside Climate News",        "https://insideclimatenews.org/feed/",                     "investigative"),
    ("The Markup",                 "https://themarkup.org/feeds/rss.xml",                     "investigative"),
    ("Grist",                      "https://grist.org/feed/",                                 "investigative"),
    ("ICIJ",                       "https://www.icij.org/feed/",                              "investigative"),
    ("Reveal (CIR)",               "https://revealnews.org/feed/",                            "investigative"),
    ("The Intercept",              "https://theintercept.com/feed/?lang=en",                  "investigative"),
    # POLITICAL — center-right / right-of-center
    ("National Review",            "https://www.nationalreview.com/feed/",                    "political"),
    ("The Dispatch",               "https://thedispatch.com/feed/",                           "political"),
    ("Washington Examiner",        "https://www.washingtonexaminer.com/feed",                 "political"),
    ("Reason",                     "https://reason.com/feed/",                                "political"),
    ("The Free Press",             "https://www.thefp.com/feed",                              "political"),
    ("WSJ Opinion",                "https://feeds.content.dowjones.io/public/rss/RSSOpinion", "political"),
    # POLITICAL — center-left / left-of-center
    ("The Atlantic",               "https://www.theatlantic.com/feed/all/",                   "political"),
    ("The New Yorker",             "https://www.newyorker.com/feed/everything",               "political"),
    ("The Nation",                 "https://www.thenation.com/feed/?post_type=article",       "political"),
    ("Mother Jones",               "https://www.motherjones.com/feed/",                       "political"),
    ("Slate",                      "https://slate.com/feeds/all.rss",                         "political"),
    # BUSINESS
    ("CNBC",                       "https://www.cnbc.com/id/100003114/device/rss/rss.html",   "business"),
    ("MarketWatch",                "https://feeds.marketwatch.com/marketwatch/topstories/",   "business"),
    ("Forbes",                     "https://www.forbes.com/business/feed/",                   "business"),
    ("Fortune",                    "https://fortune.com/feed/",                               "business"),
    ("Barron's",                   "https://www.barrons.com/feed/rss",                        "business"),
    # INTERNATIONAL
    ("Reuters Latam",              "https://www.reuters.com/arc/outboundfeeds/v3/category/world/americas/rss/", "international"),
    ("Nikkei Asia",                "https://asia.nikkei.com/rss/feed/nar",                    "international"),
    ("The Japan Times",            "https://www.japantimes.co.jp/feed/",                      "international"),
    ("Korea Herald",               "https://www.koreaherald.com/common/rss_xml.php?ct=102",   "international"),
    ("Mexico News Daily",          "https://mexiconewsdaily.com/feed/",                       "international"),
    ("Buenos Aires Herald",        "https://buenosairesherald.com/feed",                      "international"),
    ("Africa News",                "https://www.africanews.com/feed/rss",                     "international"),
    ("The East African",           "https://www.theeastafrican.co.ke/ke/News/-/index.rss",    "international"),
]


def feeds_by_category(category):
    return [(name, url) for name, url, cat in FEEDS if cat == category]


def all_categories():
    return sorted(set(cat for _, _, cat in FEEDS))


def feed_count_summary():
    from collections import Counter
    return dict(Counter(cat for _, _, cat in FEEDS))


if __name__ == "__main__":
    print(f"Total feeds: {len(FEEDS)}")
    print()
    print("By category:")
    for cat, count in sorted(feed_count_summary().items()):
        print(f"  {cat:25s} {count:3d}")
