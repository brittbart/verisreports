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
# DISABLED (timeout/403):     "https://www.axios.com/feeds/feed.rss",
    "https://feeds.foxnews.com/foxnews/latest",
    "https://feeds.foxnews.com/foxnews/politics",
    "https://www.aljazeera.com/xml/rss/all.xml",
    # Mainstream news
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/us-news/rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
# DISABLED (timeout/403):     "https://feeds.washingtonpost.com/rss/politics",
# DISABLED (timeout/403):     "https://feeds.washingtonpost.com/rss/business",
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
    "https://www.washingtonexaminer.com/section/news/feed",
    # Left-leaning US
    "https://news.google.com/rss/search?q=site:thenation.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:jacobin.com&hl=en-US&gl=US&ceid=US:en",
    # Latin America
    "https://news.google.com/rss/search?q=site:mercopress.com&hl=en-US&gl=US&ceid=US:en",
    # Africa
    "https://news.google.com/rss/search?q=site:dailymaverick.co.za&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:allafrica.com&hl=en-US&gl=US&ceid=US:en",
    # Crypto
# DISABLED (timeout/403):     "https://coindesk.com/arc/outboundfeeds/rss",
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
    "https://news.google.com/rss/search?q=site:cbsnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:abcnews.go.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:nbcnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://www.msnbc.com/feeds/latest",
    "http://rss.cnn.com/rss/cnn_allpolitics.rss",
    "https://news.google.com/rss/search?q=site:usatoday.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:newsweek.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:time.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:wsj.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:bloomberg.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:propublica.org&hl=en-US&gl=US&ceid=US:en",
    "https://theintercept.com/feed/?rss",
    "https://www.motherjones.com/feed/",
    "https://news.google.com/rss/search?q=site:oann.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:dailycaller.com&hl=en-US&gl=US&ceid=US:en",
    "https://www.nationalreview.com/feed/",
    "https://news.google.com/rss/search?q=site:thedispatch.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:realclearpolitics.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:slate.com&hl=en-US&gl=US&ceid=US:en",
    "https://www.vox.com/rss/index.xml",
    "https://news.google.com/rss/search?q=site:vice.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:csmonitor.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:militarytimes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:stripes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:webmd.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:healthline.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:health.harvard.edu&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:sciencenews.org&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:newscientist.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:scientificamerican.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:nature.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:espn.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:si.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:theathletic.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:bleacherreport.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:forbes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:fortune.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:businessinsider.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:cnbc.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:marketwatch.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:telemundo.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:elpais.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:laopinion.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:asamnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:nextshark.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:cbsnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:abcnews.go.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:nbcnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:usatoday.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:newsweek.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:time.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:wsj.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:bloomberg.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:propublica.org&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:oann.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:dailycaller.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:thedispatch.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:realclearpolitics.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:slate.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:vice.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:csmonitor.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:militarytimes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:stripes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:webmd.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:healthline.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:health.harvard.edu&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:sciencenews.org&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:newscientist.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:scientificamerican.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:nature.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:espn.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:si.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:theathletic.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:bleacherreport.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:forbes.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:fortune.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:businessinsider.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:cnbc.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:marketwatch.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:telemundo.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:elpais.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:laopinion.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:asamnews.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:nextshark.com&hl=en-US&gl=US&ceid=US:en",
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
    # === ADDITIONS Apr 21 2026 ===
    # Government primary sources (highest verdict weight — verify claims against these)
    "https://www.whitehouse.gov/briefing-room/feed/",
    "https://www.whitehouse.gov/news/feed/",
    "https://www.state.gov/rss-feed/press-releases/feed/",
    "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=10",
    "https://www.justice.gov/feeds/justice-news.xml",
    "https://tools.cdc.gov/api/v2/resources/media/132608.rss",
    "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml",
    "https://www.epa.gov/newsreleases/search/rss",
    "https://www.dhs.gov/news-releases/releases.xml",
    "https://www.gao.gov/rss/reports.xml",
    "https://www.congress.gov/rss/most-viewed-bills.xml",
    "https://www.federalregister.gov/articles.rss",
    "https://www.supremecourt.gov/rss/cases.aspx",
    # Official data releases
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://www.bls.gov/feed/news_release.rss",
    "https://www.sec.gov/news/pressreleases.rss",
    "https://home.treasury.gov/news/press-releases/feed",
    "https://www.census.gov/newsroom/press-releases.xml",
    "https://www.bea.gov/news/rss.xml",
    # Election infrastructure
    "https://www.fec.gov/updates/feed/",
    "https://www.opensecrets.org/news/feed/",
    "https://ballotpedia.org/Special:RecentChanges?feed=rss",
    "https://www.c-span.org/rss/?feed=podcast",
    # Wire services not already covered (direct, not via Google News)
    "https://www.afp.com/en/rss.xml",
    "https://feeds.bloomberg.com/news.rss",
    "https://feeds.bloomberg.com/politics/news.rss",
    "https://english.kyodonews.net/rss/news.xml",
    # Public broadcasters not already covered
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.npr.org/1014/rss.xml",
    "https://www.pbs.org/newshour/feeds/rss/headlines",
    "https://www.cbc.ca/webfeed/rss/rss-topstories",
    "https://www.abc.net.au/news/feed/45910/rss.xml",
    "https://www.rnz.co.nz/rss/national.xml",
    # Investigative nonprofits not already covered
    "https://www.themarshallproject.org/rss/recent.rss",
    "https://insideclimatenews.org/feed/",
    "https://themarkup.org/feeds/rss.xml",
    "https://grist.org/feed/",
    "https://www.icij.org/feed/",
    "https://revealnews.org/feed/",
    # Digital-native not already covered
    "https://www.semafor.com/feed",
    "https://www.thefp.com/feed",
    # International perspectives not already covered
    "https://www.africanews.com/feed/rss",
    "https://buenosairesherald.com/feed",
    "https://mexiconewsdaily.com/feed/",
    "https://hongkongfp.com/feed/",
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
                    'publishedAt': e.get('published', ''),
                    'source': {'name': name},
                    'fetch_source': 'rss'
                }
                if a['title'] and a['url'] and is_english(a['title'] + ' ' + a['description']):
                    articles.append(a)
            print(f'  ok {name}')
        except Exception as e:
            print(f'  skip: {str(e)[:40]}')
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