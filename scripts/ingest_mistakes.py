"""
ingest_mistakes.py — multi-source mistake-fare / deal feed ingestor.

Pulls from blog RSS (Secret Flying, View From The Wing, One Mile at a Time,
The Flight Deal, God Save The Points, Thrifty Traveler) and Reddit JSON
(r/awardtravel, r/Flights, r/churning). Normalizes to a single record shape,
runs sources in parallel, de-dupes by URL, and caches the combined output.

Stdlib only (uses xml.etree.ElementTree for RSS/Atom). See data/mistake_sources.md
for endpoint catalog and Quality/Signal ratings.

CLI:
    python ingest_mistakes.py [--source NAME[,NAME...]] [--no-cache] [--max N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from common import (
    CACHE_DIR,
    cache_get,
    cache_set,
    http_get,
    load_json,
    log,
    save_json,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MISTAKES_CACHE_PATH = CACHE_DIR / "mistakes_feed.json"

# Reddit politely requires a unique, descriptive UA per their API rules.
REDDIT_UA = "flight-hacker:0.1 (by /u/flight-hacker-bot)"

# Substring keywords for "deal-y" posts (case-insensitive).
DEAL_KEYWORDS = [
    "mistake", "error fare", "errorfare", "error-fare",
    "deal", "deals", "cheap", "half price", "half-price",
    "fare sale", "fare deal", "fare drop", "glitch", "mispriced", "mispricing",
    "pricing error", "fat finger", "fat-finger",
    "sweet spot", "sweet-spot", "award sweet",
    "from $", "round trip", "round-trip", "one-way",
    "bargain", "steal", "low price", "lowest price",
]

# Regex-only keywords (any-match counts). These look for actual prices/codes,
# not bare symbols (a $ in prose like "I paid $50 for parking" is NOT a deal
# signal on its own — only paired with a flight context).
DEAL_KEYWORD_PATTERNS = [
    r"\$\d{2,4}\b",            # $99, $1299
    r"€\s?\d{2,4}\b",
    r"£\s?\d{2,4}\b",
    r"\b\d{2,4}\s?(?:usd|eur|gbp)\b",
    r"\b[A-Z]{3}\s*[-–→]\s*[A-Z]{3}\b",  # IATA-IATA pair
]

# Known IATA two-letter / three-letter airline codes + common carrier nicknames
# we want to surface as carrier hints in the title.
KNOWN_CARRIERS = {
    # legacy / mainline
    "American": "AA", "United": "UA", "Delta": "DL", "JetBlue": "B6",
    "Alaska": "AS", "Southwest": "WN", "Spirit": "NK", "Frontier": "F9",
    "Hawaiian": "HA", "Air Canada": "AC", "WestJet": "WS",
    # europe
    "British Airways": "BA", "Iberia": "IB", "Air France": "AF",
    "KLM": "KL", "Lufthansa": "LH", "SWISS": "LX", "Austrian": "OS",
    "Brussels": "SN", "TAP": "TP", "TAP Air Portugal": "TP",
    "Aer Lingus": "EI", "Finnair": "AY", "SAS": "SK",
    "Turkish": "TK", "ITA": "AZ", "ITA Airways": "AZ", "Alitalia": "AZ",
    "Norwegian": "DY", "Ryanair": "FR", "easyJet": "U2", "Wizz": "W6",
    "Virgin Atlantic": "VS", "Virgin Australia": "VA",
    # middle east / africa
    "Qatar": "QR", "Emirates": "EK", "Etihad": "EY",
    "Royal Jordanian": "RJ", "Saudia": "SV", "EgyptAir": "MS",
    "Ethiopian": "ET", "Kenya Airways": "KQ", "South African": "SA",
    # asia / pacific
    "ANA": "NH", "JAL": "JL", "Cathay": "CX", "Cathay Pacific": "CX",
    "Singapore": "SQ", "Singapore Airlines": "SQ",
    "Korean Air": "KE", "Asiana": "OZ",
    "China Eastern": "MU", "China Southern": "CZ", "Air China": "CA",
    "EVA": "BR", "EVA Air": "BR", "China Airlines": "CI",
    "Thai": "TG", "Vietnam Airlines": "VN",
    "Malaysia": "MH", "Garuda": "GA", "Philippine": "PR",
    "Qantas": "QF", "Air New Zealand": "NZ", "Fiji Airways": "FJ",
    # latam
    "LATAM": "LA", "Avianca": "AV", "Copa": "CM",
    "Aeromexico": "AM", "Aerolineas": "AR", "GOL": "G3", "Azul": "AD",
    # india
    "Air India": "AI", "IndiGo": "6E", "Vistara": "UK",
}

# Regional / route phrases we want to extract.
ROUTE_PATTERNS = [
    (r"\b(US|USA|United States|North America)[\s\-]*(?:to|→|-)[\s\-]*(Europe|EU|EUR)", "US-EU"),
    (r"\b(Europe|EU|EUR)[\s\-]*(?:to|→|-)[\s\-]*(US|USA|United States|North America)", "EU-US"),
    (r"\b(US|USA|United States)[\s\-]*(?:to|→|-)[\s\-]*(Asia|Japan|China|Korea|Thailand)", "US-Asia"),
    (r"\b(Asia|Japan|China|Korea)[\s\-]*(?:to|→|-)[\s\-]*(US|USA|United States)", "Asia-US"),
    (r"\b(US|USA|United States)[\s\-]*(?:to|→|-)[\s\-]*(South America|Latin America|Brazil|Argentina|Chile|Peru|Colombia)", "US-LATAM"),
    (r"\b(US|USA|United States)[\s\-]*(?:to|→|-)[\s\-]*(Africa|South Africa|Kenya|Ethiopia|Egypt|Morocco)", "US-Africa"),
    (r"\b(US|USA|United States)[\s\-]*(?:to|→|-)[\s\-]*(Australia|New Zealand|Oceania)", "US-Oceania"),
    (r"\b(Europe|EU)[\s\-]*(?:to|→|-)[\s\-]*(Asia|Japan|China|Thailand)", "EU-Asia"),
]

IATA_PAIR_RE = re.compile(r"\b([A-Z]{3})\s*[-–→]\s*([A-Z]{3})\b")
PRICE_RE = re.compile(r"(?P<sym>[$€£])\s?(?P<amt>\d{2,5}(?:[.,]\d{1,2})?)")
CABIN_WORDS = {
    "first": "first",
    "first class": "first",
    "business": "business",
    "business class": "business",
    "biz": "business",
    "j class": "business",
    "premium economy": "premium_economy",
    "premium": "premium_economy",
    "economy": "economy",
    "coach": "economy",
}

# Per-source baseline confidence (matches the source-priority order in the
# mistake_sources catalog). Reddit drops further when no deal keyword hits.
BASE_CONFIDENCE = {
    "secret_flying": 0.95,
    "flight_deal": 0.80,
    "vftw": 0.60,
    "omaat": 0.60,
    "gstp": 0.55,
    "thrifty_traveler": 0.50,
    "reddit_awardtravel": 0.45,
    "reddit_flights": 0.40,
    "reddit_churning": 0.30,
}

CURRENCY_TO_USD = {"$": 1.0, "€": 1.08, "£": 1.27}


# ---------------------------------------------------------------------------
# Helper: text + datetime utils
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = (text.replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", '"')
                .replace("&#39;", "'")
                .replace("&nbsp;", " "))
    return _WS_RE.sub(" ", text).strip()


def _to_iso(value: Any) -> str | None:
    """Coerce many date string formats into ISO-8601 UTC, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        dt = None
        # Try RFC 2822 (RSS pubDate) first.
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            dt = None
        if dt is None:
            # Try ISO-8601 (Atom <published>).
            try:
                # fromisoformat handles "2026-05-21T14:30:00+00:00" but
                # chokes on trailing "Z" pre-Py3.11. Normalize.
                s2 = s.replace("Z", "+00:00")
                dt = datetime.fromisoformat(s2)
            except ValueError:
                dt = None
        if dt is None:
            try:
                dt = datetime.fromtimestamp(float(s), tz=timezone.utc)
            except (TypeError, ValueError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_record(
    source: str,
    title: str,
    url: str,
    posted_at: str | None,
    summary: str,
    raw: dict,
    tags: list[str] | None = None,
    confidence: float | None = None,
) -> dict:
    title = (title or "").strip()
    summary = _strip_html(summary or "")[:1000]
    extracted = extract_metadata(title, summary)

    base = BASE_CONFIDENCE.get(source, 0.5) if confidence is None else float(confidence)
    # Bump confidence if title screams "mistake" / "error fare".
    low_title = title.lower()
    if "error fare" in low_title or "mistake fare" in low_title or "mistake" in low_title:
        base = min(1.0, base + 0.10)
    # Bump if we successfully extracted a price hint.
    if extracted.get("price_hint_usd"):
        base = min(1.0, base + 0.03)
    # Reddit-specific demotion when title has no deal vibe at all.
    if source.startswith("reddit_") and not filter_deal_keywords(title + " " + summary):
        base = max(0.05, base - 0.20)

    tag_set = set(tags or [])
    tag_set.add(source)
    if extracted.get("route_pattern"):
        tag_set.add(extracted["route_pattern"].lower())
    if extracted.get("cabin_hint"):
        tag_set.add(extracted["cabin_hint"])
    if "error fare" in low_title or "mistake" in low_title:
        tag_set.add("mistake_fare")
    if "award" in low_title or "miles" in low_title or "points" in low_title:
        tag_set.add("award")

    return {
        "source": source,
        "title": title,
        "url": (url or "").strip(),
        "posted_at": posted_at,
        "summary": summary,
        "tags": sorted(tag_set),
        "extracted": extracted,
        "confidence": round(base, 2),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

_DEAL_PATTERN_RE = re.compile("|".join(DEAL_KEYWORD_PATTERNS), re.IGNORECASE)


def filter_deal_keywords(text: str) -> bool:
    """True if any deal-y keyword OR price-like pattern hits.

    The substring list catches plain language ("error fare", "sweet spot");
    the regex list catches actual numeric signals ($350, EUR 199, JFK-CDG).
    A loose `$` alone never qualifies — it has to be followed by digits.
    """
    if not text:
        return False
    low = text.lower()
    if any(kw in low for kw in DEAL_KEYWORDS):
        return True
    if _DEAL_PATTERN_RE.search(text):
        return True
    return False


def extract_metadata(title: str, summary: str) -> dict:
    """Heuristic extractor → {route_pattern, price_hint_usd, carrier_hint, cabin_hint}."""
    text = f"{title or ''} {summary or ''}"
    low = text.lower()

    # ---- price ----
    price_usd: float | None = None
    for m in PRICE_RE.finditer(text):
        try:
            amt = float(m.group("amt").replace(",", ""))
        except ValueError:
            continue
        sym = m.group("sym")
        usd = amt * CURRENCY_TO_USD.get(sym, 1.0)
        # Filter obvious noise (year numbers, post counts).
        if 30 <= usd <= 50000:
            if price_usd is None or usd < price_usd:
                price_usd = usd
    if price_usd is not None:
        price_usd = round(price_usd, 2)

    # ---- route ----
    route_pattern: str | None = None
    for pat, label in ROUTE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            route_pattern = label
            break
    if not route_pattern:
        m = IATA_PAIR_RE.search(text)
        if m:
            route_pattern = f"{m.group(1)}-{m.group(2)}"

    # ---- carrier ----
    carrier_hint: str | None = None
    for name, code in KNOWN_CARRIERS.items():
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            carrier_hint = name
            break

    # ---- cabin ----
    cabin_hint: str | None = None
    # Match longest cabin phrase first so "premium economy" beats "economy".
    for phrase in sorted(CABIN_WORDS, key=len, reverse=True):
        if phrase in low:
            cabin_hint = CABIN_WORDS[phrase]
            break

    return {
        "route_pattern": route_pattern,
        "price_hint_usd": price_usd,
        "carrier_hint": carrier_hint,
        "cabin_hint": cabin_hint,
    }


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _fetch_url(url: str, headers: dict | None = None, ttl: int = 0) -> bytes | None:
    """Cached GET. ttl=0 disables cache. Returns body bytes on 2xx, else None."""
    cache_key = f"mistakes_http::{url}"
    if ttl:
        cached = cache_get(cache_key, ttl_seconds=ttl)
        if cached and isinstance(cached, dict) and cached.get("b64"):
            try:
                import base64
                return base64.b64decode(cached["b64"])
            except Exception:
                pass
    try:
        status, _hdrs, body = http_get(url, headers=headers or {}, timeout=20)
    except Exception as e:
        log("ingest_fetch_error", url=url, error=str(e))
        return None
    if not (200 <= status < 300):
        log("ingest_fetch_bad_status", url=url, status=status)
        return None
    if ttl:
        try:
            import base64
            cache_set(cache_key, {"b64": base64.b64encode(body).decode("ascii")})
        except Exception:
            pass
    return body


# ---------------------------------------------------------------------------
# RSS / Atom parsing (xml.etree only)
# ---------------------------------------------------------------------------

# Common namespace prefixes we strip so element local-names are stable.
_NS_STRIP_RE = re.compile(r"\{[^}]+\}")


def _local(tag: str) -> str:
    return _NS_STRIP_RE.sub("", tag or "")


def _children_by_local(elem: ET.Element, name: str) -> list[ET.Element]:
    name = name.lower()
    return [c for c in list(elem) if _local(c.tag).lower() == name]


def _first_child_text(elem: ET.Element, name: str) -> str:
    cs = _children_by_local(elem, name)
    if not cs:
        return ""
    return (cs[0].text or "").strip()


def _atom_link(elem: ET.Element) -> str:
    """Atom uses <link href="..."/>; RSS 2.0 uses <link>url</link>."""
    for link in _children_by_local(elem, "link"):
        href = link.attrib.get("href")
        if href:
            return href.strip()
        if link.text and link.text.strip():
            return link.text.strip()
    return ""


def _rss_categories(elem: ET.Element) -> list[str]:
    cats: list[str] = []
    for c in _children_by_local(elem, "category"):
        if c.text and c.text.strip():
            cats.append(c.text.strip())
        term = c.attrib.get("term")
        if term:
            cats.append(term.strip())
    return cats


def _parse_feed(body: bytes) -> list[dict]:
    """Parse RSS 2.0 or Atom 1.0 feed → list of normalized entry dicts."""
    if not body:
        return []
    # Strip leading whitespace/BOM that some feeds emit before <?xml ...?>.
    text = body.lstrip()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        log("ingest_feed_parse_error", error=str(e), preview=text[:120].decode("utf-8", "replace"))
        return []

    items: list[ET.Element] = []
    # RSS 2.0: <rss><channel><item>...
    for channel in _children_by_local(root, "channel"):
        items.extend(_children_by_local(channel, "item"))
    # Atom 1.0: <feed><entry>...
    if not items and _local(root.tag).lower() == "feed":
        items.extend(_children_by_local(root, "entry"))
    # Some feeds put <item> directly under root.
    if not items:
        items.extend(_children_by_local(root, "item"))

    out: list[dict] = []
    for it in items:
        title = _first_child_text(it, "title")
        url = _atom_link(it)
        date_raw = (_first_child_text(it, "pubDate")
                    or _first_child_text(it, "published")
                    or _first_child_text(it, "updated")
                    or _first_child_text(it, "date"))
        summary = (_first_child_text(it, "description")
                   or _first_child_text(it, "summary")
                   or _first_child_text(it, "content"))
        cats = _rss_categories(it)
        out.append({
            "title": title,
            "url": url,
            "posted_at_raw": date_raw,
            "summary": summary,
            "categories": cats,
        })
    return out


def _ingest_rss(
    source: str,
    url: str,
    *,
    cache_ttl: int,
    max_items: int,
    keyword_filter: bool = False,
    headers: dict | None = None,
) -> list[dict]:
    """Generic RSS → record pipeline used by every blog source."""
    hdrs = {"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    if headers:
        hdrs.update(headers)
    body = _fetch_url(url, headers=hdrs, ttl=cache_ttl)
    if not body:
        return []
    entries = _parse_feed(body)
    if not entries:
        # HTML fallback: feed empty → try grepping the HTML index page.
        log("ingest_rss_empty_trying_html", source=source, url=url)
        html_url = url.replace("/feed/", "/").replace("/feed", "/")
        html_body = _fetch_url(html_url, headers={"Accept": "text/html"}, ttl=cache_ttl)
        return _html_fallback(source, html_body or b"", max_items)

    records: list[dict] = []
    for entry in entries[: max_items * 3]:  # over-pull, filter, then trim
        title = entry["title"]
        summary = entry["summary"]
        cats = entry.get("categories") or []
        if keyword_filter:
            blob = f"{title} {summary} {' '.join(cats)}"
            if not filter_deal_keywords(blob):
                continue
        records.append(_make_record(
            source=source,
            title=title,
            url=entry["url"],
            posted_at=_to_iso(entry["posted_at_raw"]),
            summary=summary,
            raw={"categories": cats},
        ))
        if len(records) >= max_items:
            break
    return records


def _html_fallback(source: str, body: bytes, max_items: int) -> list[dict]:
    """Last-resort HTML scrape when the RSS feed comes back empty.

    Grep <article>...</article> blocks for the first <a href> with text;
    mark each result with confidence -= 0.1 via direct override.
    """
    if not body:
        return []
    try:
        html = body.decode("utf-8", errors="replace")
    except Exception:
        return []
    # Skip Cloudflare / bot-wall challenge pages so we don't surface garbage.
    low = html.lower()
    if ("just a moment" in low
            or "cf-mitigated" in low
            or "challenges.cloudflare.com" in low
            or "cf_chl_opt" in low):
        log("ingest_html_fallback_blocked", source=source, reason="cf_challenge")
        return []
    blocks = re.findall(r"<article[^>]*>(.*?)</article>", html, flags=re.DOTALL | re.IGNORECASE)
    if not blocks:
        blocks = re.findall(r"<item[^>]*>(.*?)</item>", html, flags=re.DOTALL | re.IGNORECASE)
    out: list[dict] = []
    seen_urls: set[str] = set()
    for block in blocks[: max_items * 3]:
        m = re.search(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block,
                      flags=re.DOTALL | re.IGNORECASE)
        if not m:
            continue
        url = m.group(1).strip()
        title = _strip_html(m.group(2))
        # Filter obvious nav/junk links.
        if not title or not url or len(title) < 8:
            continue
        if url.startswith(("#", "mailto:", "javascript:")):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        base_conf = max(0.05, BASE_CONFIDENCE.get(source, 0.5) - 0.10)
        out.append(_make_record(
            source=source,
            title=title,
            url=url,
            posted_at=None,
            summary="",
            raw={"html_fallback": True},
            confidence=base_conf,
        ))
        if len(out) >= max_items:
            break
    log("ingest_html_fallback", source=source, found=len(out))
    return out


# ---------------------------------------------------------------------------
# Reddit JSON ingest
# ---------------------------------------------------------------------------

def _ingest_reddit(
    source: str,
    url: str,
    *,
    cache_ttl: int,
    max_items: int,
    keyword_filter: bool = True,
    allowed_flairs: set[str] | None = None,
) -> list[dict]:
    headers = {"User-Agent": REDDIT_UA, "Accept": "application/json"}
    body = _fetch_url(url, headers=headers, ttl=cache_ttl)
    if not body:
        return []
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        log("ingest_reddit_parse_error", source=source, error=str(e))
        return []
    children = (data.get("data") or {}).get("children") or []
    records: list[dict] = []
    for ch in children:
        p = (ch or {}).get("data") or {}
        if not p:
            continue
        if p.get("over_18"):
            continue  # reject NSFW
        if p.get("stickied"):
            continue
        title = p.get("title", "") or ""
        flair = p.get("link_flair_text") or ""
        selftext = (p.get("selftext") or "")[:500]
        permalink = p.get("permalink") or ""
        url_full = f"https://www.reddit.com{permalink}" if permalink else (p.get("url") or "")
        created = p.get("created_utc")

        # Pass if (a) flair is in the allow-set OR (b) keyword filter matches.
        # Either signal alone is enough; we only reject when BOTH are absent.
        flair_match = bool(allowed_flairs) and (flair or "").strip() in allowed_flairs
        keyword_match = (not keyword_filter) or filter_deal_keywords(
            f"{title} {flair} {selftext}"
        )
        if not (flair_match or keyword_match):
            continue

        posted_at = None
        if created:
            try:
                posted_at = datetime.fromtimestamp(float(created), tz=timezone.utc) \
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError):
                posted_at = None

        records.append(_make_record(
            source=source,
            title=title,
            url=url_full,
            posted_at=posted_at,
            summary=selftext,
            raw={
                "flair": flair,
                "subreddit": p.get("subreddit"),
                "score": p.get("score"),
                "num_comments": p.get("num_comments"),
            },
        ))
        if len(records) >= max_items:
            break
    return records


# ---------------------------------------------------------------------------
# Per-source functions
# ---------------------------------------------------------------------------

def from_secret_flying(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    """Secret Flying — Error Fares category RSS."""
    try:
        # Primary: error-fare category feed (per data/mistake_sources.md verified URL).
        url = "https://www.secretflying.com/posts/category/error-fare/feed/"
        records = _ingest_rss("secret_flying", url, cache_ttl=cache_ttl,
                              max_items=max_items, keyword_filter=False)
        if records:
            return records
        # Backup: full-site feed filtered by keyword.
        url2 = "https://www.secretflying.com/feed/"
        return _ingest_rss("secret_flying", url2, cache_ttl=cache_ttl,
                           max_items=max_items, keyword_filter=True)
    except Exception as e:
        log("ingest_source_error", source="secret_flying", error=str(e))
        return []


def from_view_from_the_wing(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    try:
        return _ingest_rss("vftw", "https://viewfromthewing.com/feed/",
                           cache_ttl=cache_ttl, max_items=max_items,
                           keyword_filter=True)
    except Exception as e:
        log("ingest_source_error", source="vftw", error=str(e))
        return []


def from_one_mile_at_a_time(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    try:
        return _ingest_rss("omaat", "https://onemileatatime.com/feed/",
                           cache_ttl=cache_ttl, max_items=max_items,
                           keyword_filter=True)
    except Exception as e:
        log("ingest_source_error", source="omaat", error=str(e))
        return []


def from_flight_deal(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    try:
        return _ingest_rss("flight_deal", "https://www.theflightdeal.com/feed/",
                           cache_ttl=cache_ttl, max_items=max_items,
                           keyword_filter=False)
    except Exception as e:
        log("ingest_source_error", source="flight_deal", error=str(e))
        return []


def from_god_save_the_points(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    try:
        return _ingest_rss("gstp", "https://godsavethepoints.com/feed/",
                           cache_ttl=cache_ttl, max_items=max_items,
                           keyword_filter=True)
    except Exception as e:
        log("ingest_source_error", source="gstp", error=str(e))
        return []


def from_thrifty_traveler_public(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    try:
        return _ingest_rss("thrifty_traveler", "https://thriftytraveler.com/feed/",
                           cache_ttl=cache_ttl, max_items=max_items,
                           keyword_filter=False)
    except Exception as e:
        log("ingest_source_error", source="thrifty_traveler", error=str(e))
        return []


def from_reddit_awardtravel(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    try:
        # Try old.reddit.com per spec, fall back to www.reddit.com on failure.
        url = "https://old.reddit.com/r/awardtravel/.json?limit=50"
        recs = _ingest_reddit("reddit_awardtravel", url, cache_ttl=cache_ttl,
                              max_items=max_items, keyword_filter=True,
                              allowed_flairs={"Deal", "Sweet Spot", "Mistake", "Question"})
        if recs:
            return recs
        url = "https://www.reddit.com/r/awardtravel/new.json?limit=50"
        return _ingest_reddit("reddit_awardtravel", url, cache_ttl=cache_ttl,
                              max_items=max_items, keyword_filter=True,
                              allowed_flairs={"Deal", "Sweet Spot", "Mistake", "Question"})
    except Exception as e:
        log("ingest_source_error", source="reddit_awardtravel", error=str(e))
        return []


def from_reddit_flights(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    try:
        url = "https://old.reddit.com/r/Flights/.json?limit=50"
        recs = _ingest_reddit("reddit_flights", url, cache_ttl=cache_ttl,
                              max_items=max_items, keyword_filter=True,
                              allowed_flairs={"Deal"})
        if recs:
            return recs
        url = "https://www.reddit.com/r/Flights/new.json?limit=50"
        return _ingest_reddit("reddit_flights", url, cache_ttl=cache_ttl,
                              max_items=max_items, keyword_filter=True,
                              allowed_flairs={"Deal"})
    except Exception as e:
        log("ingest_source_error", source="reddit_flights", error=str(e))
        return []


def from_reddit_churning(cache_ttl: int = 3600, max_items: int = 30) -> list[dict]:
    try:
        url = "https://old.reddit.com/r/churning/.json?limit=50"
        recs = _ingest_reddit("reddit_churning", url, cache_ttl=cache_ttl,
                              max_items=max_items, keyword_filter=True,
                              allowed_flairs={"Deal", "Discussion", "Daily Discussion"})
        if recs:
            return recs
        url = "https://www.reddit.com/r/churning/new.json?limit=50"
        return _ingest_reddit("reddit_churning", url, cache_ttl=cache_ttl,
                              max_items=max_items, keyword_filter=True,
                              allowed_flairs={"Deal", "Discussion", "Daily Discussion"})
    except Exception as e:
        log("ingest_source_error", source="reddit_churning", error=str(e))
        return []


SOURCES: dict[str, Callable[..., list[dict]]] = {
    "secret_flying": from_secret_flying,
    "vftw": from_view_from_the_wing,
    "omaat": from_one_mile_at_a_time,
    "flight_deal": from_flight_deal,
    "gstp": from_god_save_the_points,
    "thrifty_traveler": from_thrifty_traveler_public,
    "reddit_awardtravel": from_reddit_awardtravel,
    "reddit_flights": from_reddit_flights,
    "reddit_churning": from_reddit_churning,
}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def ingest_all(
    sources: Iterable[str] | None = None,
    cache_ttl_minutes: int = 60,
    max_per_source: int = 30,
) -> list[dict]:
    """Run all (or named) sources in parallel, dedup by URL, return sorted records."""
    selected: dict[str, Callable[..., list[dict]]]
    if sources:
        selected = {name: SOURCES[name] for name in sources if name in SOURCES}
    else:
        selected = dict(SOURCES)

    cache_ttl_s = max(0, int(cache_ttl_minutes * 60))

    combined: list[dict] = []
    counts: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        future_to_name = {
            pool.submit(_safe_run, name, fn, cache_ttl_s, max_per_source): name
            for name, fn in selected.items()
        }
        for fut in as_completed(future_to_name):
            name = future_to_name[fut]
            try:
                records = fut.result() or []
            except Exception as e:
                log("ingest_source_crash", source=name, error=str(e))
                records = []
            counts[name] = len(records)
            combined.extend(records)
            log("ingest_source_done", source=name, count=len(records))

    # Dedup by URL.
    seen: set[str] = set()
    deduped: list[dict] = []
    for rec in combined:
        u = (rec.get("url") or "").strip()
        if not u:
            # Skip empty-URL records; without a stable key we can't dedup.
            continue
        if u in seen:
            continue
        seen.add(u)
        deduped.append(rec)

    # Sort by posted_at desc (None last).
    def _sort_key(r: dict) -> tuple[int, str]:
        pa = r.get("posted_at")
        return (0 if pa else 1, (pa or "")[::-1])

    deduped.sort(key=lambda r: (r.get("posted_at") or "", r.get("source")), reverse=True)

    log("ingest_complete", total=len(deduped), counts=counts)
    return deduped


def _safe_run(name: str, fn: Callable, ttl_s: int, max_items: int) -> list[dict]:
    """Run one source function with the standard kwargs, swallow any exception."""
    try:
        return fn(cache_ttl=ttl_s, max_items=max_items)
    except TypeError:
        # Older signature compatibility.
        try:
            return fn()
        except Exception as e:
            log("ingest_source_exception", source=name, error=str(e))
            return []
    except Exception as e:
        log("ingest_source_exception", source=name, error=str(e))
        return []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist(records: list[dict], path: Path | str | None = None) -> Path:
    """Append + dedup-by-url to mistakes_feed.json. Returns the cache path."""
    out_path = Path(path) if path else MISTAKES_CACHE_PATH
    existing = load_json(out_path, default=[]) or []
    if not isinstance(existing, list):
        existing = []

    by_url: dict[str, dict] = {}
    for rec in existing + (records or []):
        u = (rec.get("url") or "").strip()
        if not u:
            continue
        # Newer record (later in iteration order) wins; we put existing first so
        # fresh data clobbers stale fields like updated summaries / confidence.
        by_url[u] = rec

    merged = list(by_url.values())
    merged.sort(key=lambda r: (r.get("posted_at") or "", r.get("source")), reverse=True)
    save_json(out_path, merged)
    log("ingest_persisted", path=str(out_path), total=len(merged))
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ingest_mistakes",
        description="Ingest mistake-fare / deal feeds from multiple sources.",
    )
    p.add_argument("--source", default=None,
                   help=f"Comma-separated subset of: {','.join(SOURCES)}")
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass HTTP cache (ttl=0).")
    p.add_argument("--max", type=int, default=30,
                   help="Max records per source (default 30).")
    p.add_argument("--cache-minutes", type=int, default=60,
                   help="HTTP cache TTL in minutes (default 60).")
    p.add_argument("--no-persist", action="store_true",
                   help="Don't write mistakes_feed.json (still prints to stdout).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    sources: list[str] | None = None
    if args.source:
        sources = [s.strip() for s in args.source.split(",") if s.strip()]
        unknown = [s for s in sources if s not in SOURCES]
        if unknown:
            log("ingest_unknown_sources", unknown=unknown, known=list(SOURCES))
            sources = [s for s in sources if s in SOURCES]
        if not sources:
            print(json.dumps({"error": "no valid sources", "known": list(SOURCES)}, indent=2))
            return 0

    ttl_minutes = 0 if args.no_cache else args.cache_minutes
    records = ingest_all(
        sources=sources,
        cache_ttl_minutes=ttl_minutes,
        max_per_source=args.max,
    )

    if not args.no_persist:
        try:
            persist(records)
        except Exception as e:
            log("ingest_persist_error", error=str(e))

    counts: dict[str, int] = {}
    for r in records:
        counts[r["source"]] = counts.get(r["source"], 0) + 1

    sys.stdout.write(json.dumps({
        "total": len(records),
        "by_source": counts,
        "records": records,
    }, indent=2, default=str) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
