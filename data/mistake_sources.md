# Mistake-Fare / Deal Sources

Endpoint catalog for free mistake-fare and deal feeds. Used by the flight-hacker scanner. All endpoints verified May 2026.

Each Python snippet:
- Requires only `requests` (and `feedparser` for RSS); install via `pip install requests feedparser`
- Returns a list of `{title, url, posted_at, summary}` dicts
- Includes a realistic User-Agent (sites like Secret Flying and Reddit Cloudflare-block bare `python-requests/x.x`)
- Returns the latest 10 entries

Quality / signal-to-noise rating: 1 (mostly noise) - 5 (almost every post is actionable).

---

## 1. Secret Flying — Error Fares feed

- **URL**: https://www.secretflying.com/
- **Type**: RSS (WordPress)
- **Endpoint**: `https://www.secretflying.com/posts/category/error-fare/feed/` (category-scoped)
  - Backup full-site feed: `https://www.secretflying.com/feed/`
  - The Mistake Fares tag is folded into the Error Fare category; site uses one canonical taxonomy.
- **Auth**: None (must send a real browser User-Agent — Cloudflare returns 403 to bare `python-requests`)
- **Update frequency**: 5-30 posts/day; new error fares appear within minutes of publication
- **Signal**: 5/5 — this is the gold standard

```python
import feedparser
import requests
from email.utils import parsedate_to_datetime

URL = "https://www.secretflying.com/posts/category/error-fare/feed/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                  "Version/17.4 Safari/605.1.15"
}

def fetch_secretflying(limit: int = 10):
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    out = []
    for entry in feed.entries[:limit]:
        out.append({
            "title": entry.title,
            "url": entry.link,
            "posted_at": parsedate_to_datetime(entry.published).isoformat()
                         if getattr(entry, "published", None) else None,
            "summary": entry.get("summary", "").strip(),
        })
    return out

if __name__ == "__main__":
    for item in fetch_secretflying():
        print(item["posted_at"], "-", item["title"])
```

---

## 2. The Flight Deal

- **URL**: https://www.theflightdeal.com/
- **Type**: RSS (WordPress)
- **Endpoint**: `https://www.theflightdeal.com/feed/` (full site)
  - City-scoped: `https://www.theflightdeal.com/category/flight-deals/<iata>/feed/` (e.g. `/lax/feed/`)
  - Mistake-fare scoped: `https://www.theflightdeal.com/category/mistake-fare/feed/`
- **Auth**: None (User-Agent recommended; the site is Cloudflare-fronted)
- **Update frequency**: 10-40 posts/day across all categories
- **Signal**: 5/5 — US-departure focus, very accurate

```python
import feedparser
import requests
from email.utils import parsedate_to_datetime

URL = "https://www.theflightdeal.com/feed/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                  "Version/17.4 Safari/605.1.15"
}

def fetch_theflightdeal(limit: int = 10):
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    out = []
    for entry in feed.entries[:limit]:
        out.append({
            "title": entry.title,
            "url": entry.link,
            "posted_at": parsedate_to_datetime(entry.published).isoformat()
                         if getattr(entry, "published", None) else None,
            "summary": entry.get("summary", "").strip(),
        })
    return out
```

---

## 3. View From The Wing (Gary Leff)

- **URL**: https://viewfromthewing.com/
- **Type**: RSS (WordPress)
- **Endpoint**: `https://viewfromthewing.com/feed/`
- **Auth**: None
- **Update frequency**: ~10 posts/day; mix of news, deals, opinion
- **Signal**: 3/5 — high signal for award-travel/cabin-deal alerts; noisy for pure mistake fares

```python
import feedparser
from email.utils import parsedate_to_datetime

URL = "https://viewfromthewing.com/feed/"

def fetch_viewfromthewing(limit: int = 10):
    feed = feedparser.parse(URL)
    out = []
    for entry in feed.entries[:limit]:
        out.append({
            "title": entry.title,
            "url": entry.link,
            "posted_at": parsedate_to_datetime(entry.published).isoformat()
                         if getattr(entry, "published", None) else None,
            "summary": entry.get("summary", "").strip(),
        })
    return out
```

---

## 4. One Mile at a Time (Ben Schlappig)

- **URL**: https://onemileatatime.com/
- **Type**: RSS (WordPress)
- **Endpoint**: `https://onemileatatime.com/feed/`
- **Auth**: None
- **Update frequency**: 5-15 posts/day
- **Signal**: 3/5 — strong on premium-cabin redemption sweet spots and credit-card offers; lower mistake-fare hit rate

```python
import feedparser
from email.utils import parsedate_to_datetime

URL = "https://onemileatatime.com/feed/"

def fetch_omaat(limit: int = 10):
    feed = feedparser.parse(URL)
    out = []
    for entry in feed.entries[:limit]:
        out.append({
            "title": entry.title,
            "url": entry.link,
            "posted_at": parsedate_to_datetime(entry.published).isoformat()
                         if getattr(entry, "published", None) else None,
            "summary": entry.get("summary", "").strip(),
        })
    return out
```

---

## 5. God Save The Points (Gilbert Ott)

- **URL**: https://www.godsavethepoints.com/
- **Type**: RSS (WordPress)
- **Endpoint**: `https://godsavethepoints.com/feed/`
- **Auth**: None
- **Update frequency**: 3-7 posts/day
- **Signal**: 3/5 — UK/EU departure focus; useful for transatlantic deals

```python
import feedparser
from email.utils import parsedate_to_datetime

URL = "https://godsavethepoints.com/feed/"

def fetch_godsavethepoints(limit: int = 10):
    feed = feedparser.parse(URL)
    out = []
    for entry in feed.entries[:limit]:
        out.append({
            "title": entry.title,
            "url": entry.link,
            "posted_at": parsedate_to_datetime(entry.published).isoformat()
                         if getattr(entry, "published", None) else None,
            "summary": entry.get("summary", "").strip(),
        })
    return out
```

---

## 6. Thrifty Traveler (free / public posts only)

- **URL**: https://thriftytraveler.com/
- **Type**: RSS (WordPress)
- **Endpoint**: `https://thriftytraveler.com/feed/`
- **Auth**: None for the RSS feed. The premium deal alerts (Thrifty Traveler Premium) are gated behind a paid subscription and NOT in this feed; assume any "deal" surfaced via the public blog is a teaser
- **Update frequency**: 2-5 posts/day on the blog feed
- **Signal**: 2/5 (free tier) — most paid alerts are behind the paywall; blog covers credit cards, news, and the occasional public deal

```python
import feedparser
from email.utils import parsedate_to_datetime

URL = "https://thriftytraveler.com/feed/"

def fetch_thriftytraveler(limit: int = 10):
    feed = feedparser.parse(URL)
    out = []
    for entry in feed.entries[:limit]:
        out.append({
            "title": entry.title,
            "url": entry.link,
            "posted_at": parsedate_to_datetime(entry.published).isoformat()
                         if getattr(entry, "published", None) else None,
            "summary": entry.get("summary", "").strip(),
        })
    return out
```

---

## 7. Reddit r/awardtravel

- **URL**: https://www.reddit.com/r/awardtravel/
- **Type**: JSON API (Reddit listing)
- **Endpoint**: `https://www.reddit.com/r/awardtravel/new.json?limit=25`
  - `old.reddit.com` mirror also works: `https://old.reddit.com/r/awardtravel/new.json?limit=25`
  - Sort options: `/hot.json`, `/new.json`, `/top.json?t=day`
- **Auth**: None for public listings, but Reddit aggressively rate-limits / blocks default User-Agents — MUST set a unique descriptive UA per their API rules; otherwise expect 429s and 403s
- **Update frequency**: Continuous; ~50 new posts/day across all sort variants
- **Signal**: 4/5 — premium-cabin award sweet spots, mistake-fare discussion threads

```python
import requests
from datetime import datetime, timezone

URL = "https://www.reddit.com/r/awardtravel/new.json?limit=25"
HEADERS = {
    # Reddit's policy: include script-name, version, contact
    "User-Agent": "flight-hacker:0.1 (by /u/your_reddit_username)"
}

def fetch_reddit_awardtravel(limit: int = 10):
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    out = []
    for child in data["data"]["children"][:limit]:
        p = child["data"]
        out.append({
            "title": p["title"],
            "url": f"https://www.reddit.com{p['permalink']}",
            "posted_at": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).isoformat(),
            "summary": (p.get("selftext") or "")[:500],
        })
    return out
```

---

## 8. Reddit r/Flights (filter by "Deal" flair)

- **URL**: https://www.reddit.com/r/Flights/
- **Type**: JSON API
- **Endpoint**: `https://www.reddit.com/r/Flights/new.json?limit=50`
  - Flair-filter URL (server-side): `https://www.reddit.com/r/Flights/search.json?q=flair_name%3A%22Deal%22&restrict_sr=1&sort=new&limit=25`
- **Auth**: None; same User-Agent rules as #7
- **Update frequency**: Continuous
- **Signal**: 2/5 unfiltered, 4/5 when filtered to `flair == "Deal"`

```python
import requests
from datetime import datetime, timezone

URL = ("https://www.reddit.com/r/Flights/search.json"
       "?q=flair_name%3A%22Deal%22&restrict_sr=1&sort=new&limit=25")
HEADERS = {"User-Agent": "flight-hacker:0.1 (by /u/your_reddit_username)"}

def fetch_reddit_flights_deals(limit: int = 10):
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    out = []
    for child in data["data"]["children"][:limit]:
        p = child["data"]
        out.append({
            "title": p["title"],
            "url": f"https://www.reddit.com{p['permalink']}",
            "posted_at": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).isoformat(),
            "summary": (p.get("selftext") or "")[:500],
        })
    return out
```

---

## 9. Reddit r/churning + r/CreditCards (low-priority CC signal)

- **URLs**:
  - https://www.reddit.com/r/churning/
  - https://www.reddit.com/r/CreditCards/
- **Type**: JSON API
- **Endpoints**:
  - `https://www.reddit.com/r/churning/new.json?limit=25`
  - `https://www.reddit.com/r/CreditCards/new.json?limit=25`
- **Auth**: None; same User-Agent rules
- **Update frequency**: Continuous, very high volume on r/churning
- **Signal**: 1/5 for direct flight deals; 3/5 for credit-card sign-up bonus signals that fund travel. Treat as a secondary scoring input, not a primary deal source

```python
import requests
from datetime import datetime, timezone

HEADERS = {"User-Agent": "flight-hacker:0.1 (by /u/your_reddit_username)"}

def fetch_reddit_cc(subreddit: str, limit: int = 10):
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    out = []
    for child in data["data"]["children"][:limit]:
        p = child["data"]
        out.append({
            "title": p["title"],
            "url": f"https://www.reddit.com{p['permalink']}",
            "posted_at": datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).isoformat(),
            "summary": (p.get("selftext") or "")[:500],
        })
    return out

# fetch_reddit_cc("churning")
# fetch_reddit_cc("CreditCards")
```

---

## 10. Twitter / X — N/A

- **Status**: NOT PRACTICAL without paid API access
- The X API v2 free tier (post-2023) caps reads at 100/month, which is unusable for polling
- The Basic tier ($200/month) gives 10K reads/month, still tight for live polling of multiple deal accounts
- Scraping the web UI violates the X ToS and is technically defeated by login walls and rate-throttling
- **Recommendation**: Skip Twitter/X. The major deal accounts (`@secretflying`, `@TheFlightDeal`, `@viewfromthewing`, `@OneMileataTime`) all publish to RSS feeds covered above — you get the same content for free with no API headache

---

## Source priority order (use as a ranking weight)

1. Secret Flying error-fare feed — fire-and-forget, almost no false positives
2. The Flight Deal — US-departure deals + dedicated mistake-fare category
3. Reddit r/awardtravel + r/Flights (Deal flair) — community-discovered fares
4. View From The Wing / One Mile at a Time / God Save The Points — pundit signal
5. Thrifty Traveler public feed — secondary
6. Reddit r/churning + r/CreditCards — CC-funding signal only

## Operational notes

- All HTTP calls in async code must be wrapped with `asyncio.to_thread(...)` — `requests` is sync and will freeze the event loop
- Cache feeds for 60-120 seconds to stay polite; mistake fares die fast but feeds rarely update faster than that
- De-dupe by URL across sources; the same fare is often reported by multiple blogs within minutes
- Track first-seen timestamp per URL; a fare that has been live > 4 hours is usually already fixed
- HTML parse failures on Secret Flying summaries: fall back to plain title-only entries; never let one malformed item kill the batch
