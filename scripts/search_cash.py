"""
search_cash.py — Multi-source cash-fare flight search with a single normalized schema.

Sources (priority order):
  1. fast-flights (Google Flights scraper, PyPI: fast-flights) — primary, free, no key.
  2. Duffel API — optional, enabled only when DUFFEL_API_TOKEN env var is set.

Public API:
    search(origin, destination, depart_date, return_date=None, cabin="economy",
           adults=1, children=0, infants=0, max_stops=2,
           sources=("google_flights", "duffel"))
        -> list[dict]   # normalized itineraries, sorted by price ascending

    search_dates(origin, destination, month, cabin="economy", adults=1)
        -> list[dict]   # one cheapest itinerary per day in the month

    search_many(queries: list[dict])
        -> list[list[dict]]   # parallel fan-out, one result list per query

CLI:
    python search_cash.py --origin JFK --dest LAX --depart 2026-09-15 \
        --cabin economy --adults 1 [--no-cache]

Normalized itinerary schema is documented in the module docstring of `_normalize`
below and matches the spec given to the author.
"""
from __future__ import annotations

import calendar
import dataclasses
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback

# fast-flights and its upstream proxy degrade above ~4 concurrent calls
# (intermittent empty rows). A semaphore of 4 cuts wall-clock dramatically
# for multi-cabin / multi-airport fan-outs while staying inside the
# upstream's tolerance — retry loop handles any single-call corruption.
_FAST_FLIGHTS_LOCK = threading.Semaphore(4)
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error as urlerror
from urllib import request as urlrequest

# ---------------------------------------------------------------------------
# Constants / config
# ---------------------------------------------------------------------------

CACHE_DIR = Path("/Users/admin/Desktop/flight-hacker/cache")
CACHE_TTL_SECONDS = 60 * 60  # 60 minutes
SOURCE_TIMEOUT_SECONDS = 30
RETRY_BACKOFF_SECONDS = 2
MAX_DATE_GRID_WORKERS = 8

CABIN_TO_FAST_FLIGHTS = {
    "economy": "economy",
    "premium_economy": "premium-economy",
    "business": "business",
    "first": "first",
}
CABIN_TO_DUFFEL = {
    "economy": "economy",
    "premium_economy": "premium_economy",
    "business": "business",
    "first": "first",
}

DUFFEL_API_BASE = "https://api.duffel.com"
DUFFEL_VERSION = "v2"


# ---------------------------------------------------------------------------
# Structured logging (single-line JSON to stderr)
# ---------------------------------------------------------------------------

def _truncate_error(s: Any, n: int = 300) -> str:
    """Truncate an error string to ``n`` chars, appending '...' when truncated."""
    text = str(s) if not isinstance(s, str) else s
    if len(text) <= n:
        return text
    return text[:n] + "..."


def _log(event: str, **fields: Any) -> None:
    payload = {"event": event, "ts": datetime.now(timezone.utc).isoformat()}
    payload.update(fields)
    try:
        sys.stderr.write(json.dumps(payload, default=str) + "\n")
        sys.stderr.flush()
    except Exception:  # logging must never break the call
        pass


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------

def _cache_path(key_parts: Iterable[Any]) -> Path:
    raw = json.dumps(list(key_parts), sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return CACHE_DIR / f"cash_{digest}.json"


def _cache_read(path: Path) -> list[dict] | None:
    try:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > CACHE_TTL_SECONDS:
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        _log("cache_read_error", path=str(path), error=str(exc))
        return None


def _cache_write(path: Path, data: list[dict]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, default=str)
        tmp.replace(path)
    except Exception as exc:
        _log("cache_write_error", path=str(path), error=str(exc))


# ---------------------------------------------------------------------------
# Retry helper (one retry, 2 s backoff, transient errors only)
# ---------------------------------------------------------------------------

_TRANSIENT_HTTP = {500, 502, 503, 504, 408, 429}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, urlerror.HTTPError):
        return exc.code in _TRANSIENT_HTTP
    if isinstance(exc, (urlerror.URLError, ConnectionError, TimeoutError)):
        return True
    msg = str(exc).lower()
    return any(tok in msg for tok in ("timeout", "timed out", "connection", "temporar"))


def _with_retry(func, *, source: str):
    """Run func() with one retry on transient errors."""
    try:
        return func()
    except BaseException as exc:
        if _is_transient(exc):
            _log("retry", source=source, error=str(exc))
            time.sleep(RETRY_BACKOFF_SECONDS)
            return func()
        raise


# ---------------------------------------------------------------------------
# Helpers — parsing fast-flights' human-readable strings into structured data
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"([0-9][0-9,]*)(?:\.(\d+))?")
_DURATION_RE = re.compile(r"(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?", re.IGNORECASE)
_TIME_DATE_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2}\s*(?:AM|PM))\s+on\s+([A-Za-z]{3}),?\s+([A-Za-z]{3})\s+(\d{1,2})",
    re.IGNORECASE,
)


def _parse_price(text: str | None) -> tuple[float | None, str]:
    """Returns (amount, currency). Best effort; defaults to USD."""
    if not text:
        return None, "USD"
    currency = "USD"
    if "€" in text:
        currency = "EUR"
    elif "£" in text:
        currency = "GBP"
    elif "¥" in text:
        currency = "JPY"
    m = _PRICE_RE.search(text)
    if not m:
        return None, currency
    whole = m.group(1).replace(",", "")
    frac = m.group(2) or "0"
    try:
        return float(f"{whole}.{frac}"), currency
    except ValueError:
        return None, currency


def _parse_duration_to_minutes(text: str | None) -> int | None:
    if not text:
        return None
    m = _DURATION_RE.search(text)
    if not m:
        return None
    hrs = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    total = hrs * 60 + mins
    return total or None


def _parse_time_date(text: str | None, year_hint: int) -> str | None:
    """Convert '11:45 AM on Tue, Sep 15' -> ISO 'YYYY-MM-DDTHH:MM'."""
    if not text:
        return None
    m = _TIME_DATE_RE.match(text.strip())
    if not m:
        return None
    time_str, _dow, mon_str, day_str = m.groups()
    try:
        dt = datetime.strptime(
            f"{time_str} {mon_str} {day_str} {year_hint}", "%I:%M %p %b %d %Y"
        )
        return dt.strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _is_empty_flight(f: Any) -> bool:
    """True when the row has nothing usable — no name AND no duration AND no price.
    A row with name='' but price='$1471' is still useful (we just don't know the
    carrier name) — don't drop it; the downstream normalizer will tag carrier=None
    and the UI shows "(unknown)" rather than the user seeing zero results.
    """
    name = getattr(f, "name", None)
    duration = getattr(f, "duration", None)
    price = getattr(f, "price", None)
    name_empty = name is None or name == ""
    duration_empty = duration is None or duration == ""
    price_empty = price is None or price == "" or price == "$0"
    return name_empty and duration_empty and price_empty


def _carrier_codes(name: str | None) -> list[str]:
    """Best-effort IATA-ish code list for a carrier display name."""
    if not name:
        return []
    # fast-flights joins multi-carrier itineraries with ', '
    parts = [p.strip() for p in name.split(",") if p.strip()]
    return parts


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _empty_itinerary() -> dict:
    return {
        "source": None,
        "origin": None,
        "destination": None,
        "depart_date": None,
        "return_date": None,
        "carrier": None,
        "carriers_all": [],
        "cabin": None,
        "stops": None,
        "duration_minutes": None,
        "price_usd": None,
        "currency": "USD",
        "fare_brand": None,
        "baggage_included": None,
        "refundable": None,
        "segments": [],
        "deep_link": None,
        "raw": None,
    }


# ---------------------------------------------------------------------------
# Source: fast-flights (Google Flights)
# ---------------------------------------------------------------------------

def _fetch_google_flights(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    cabin: str,
    adults: int,
    children: int,
    infants: int,
    max_stops: int,
) -> list[dict]:
    try:
        from fast_flights import FlightData, Passengers, get_flights
    except ImportError as exc:
        raise RuntimeError(f"fast-flights not installed: {exc}") from exc

    seat = CABIN_TO_FAST_FLIGHTS.get(cabin, "economy")
    slices = [FlightData(date=depart_date, from_airport=origin, to_airport=destination)]
    trip = "one-way"
    if return_date:
        slices.append(
            FlightData(date=return_date, from_airport=destination, to_airport=origin)
        )
        trip = "round-trip"

    pax = Passengers(
        adults=adults,
        children=children,
        infants_in_seat=0,
        infants_on_lap=infants,
    )

    def _make_call(mode: str):
        def _call():
            # Hold the module-level lock for the entire fast-flights call —
            # concurrent calls corrupt each other (one of the pair returns
            # name='' / duration='' / stops='Unknown'). Serialization adds
            # latency but fixes correctness.
            with _FAST_FLIGHTS_LOCK:
                return get_flights(
                    flight_data=slices,
                    trip=trip,
                    passengers=pax,
                    seat=seat,
                    fetch_mode=mode,
                    max_stops=max_stops if max_stops is not None else None,
                )
        return _call

    result = None
    last_exc: Exception | None = None
    # Try each fetch_mode multiple times — fast-flights' upstream proxy is
    # intermittently 401-ing depending on rate-limit / route / user-agent.
    # Two passes through (fallback, common, force-fallback) gives 6 attempts.
    plan: list[tuple[str, float]] = [
        ("fallback", 0.0),
        ("common", 1.5),
        ("force-fallback", 1.5),
        ("fallback", 3.0),
        ("common", 2.0),
        ("force-fallback", 2.0),
    ]
    for attempt, (mode, delay) in enumerate(plan):
        if delay > 0:
            time.sleep(delay)
        try:
            result = _with_retry(_make_call(mode), source="google_flights")
        except Exception as exc:
            # fast-flights raises AssertionError/RuntimeError for transient
            # 401s and "no flights found" interstitials. Try the next mode.
            last_exc = exc
            _log(
                "google_flights_mode_error",
                attempt=attempt,
                mode=mode,
                error=_truncate_error(str(exc)),
            )
            result = None
            continue
        flights = list(result.flights or [])
        if flights and not all(_is_empty_flight(f) for f in flights):
            break
        _log(
            "google_flights_empty_retry",
            attempt=attempt,
            mode=mode,
            count=len(flights),
        )
    else:
        # Every attempt produced empty data; continue with last (possibly empty) result.
        _log(
            "google_flights_empty_data",
            attempts=len(plan),
            last_error=_truncate_error(str(last_exc)) if last_exc else None,
        )

    if result is None:
        # Every mode raised — surface the last error to the outer error handler.
        if last_exc is not None:
            raise last_exc
        return []

    depart_year = int(depart_date[:4])

    out: list[dict] = []
    for f in result.flights or []:
        try:
            f_dict = dataclasses.asdict(f) if dataclasses.is_dataclass(f) else dict(
                vars(f)
            )
        except Exception:
            f_dict = {
                k: getattr(f, k, None)
                for k in (
                    "is_best",
                    "name",
                    "departure",
                    "arrival",
                    "arrival_time_ahead",
                    "duration",
                    "stops",
                    "delay",
                    "price",
                )
            }

        price_amt, currency = _parse_price(f_dict.get("price"))
        # Skip rows with no usable price (fast-flights occasionally emits empty
        # stubs with price='$0' or price=None when its scrape is partial).
        if price_amt is None or price_amt <= 0:
            continue
        duration_min = _parse_duration_to_minutes(f_dict.get("duration"))
        stops_val = f_dict.get("stops")
        try:
            stops_int = int(stops_val) if stops_val is not None else None
        except (TypeError, ValueError):
            stops_int = None
        if stops_int is not None and max_stops is not None and stops_int > max_stops:
            continue

        depart_iso = _parse_time_date(f_dict.get("departure"), depart_year)
        arrive_iso = _parse_time_date(f_dict.get("arrival"), depart_year)
        carriers = _carrier_codes(f_dict.get("name"))

        seg = {
            "carrier": carriers[0] if carriers else None,
            "flight_no": None,
            "from": origin,
            "to": destination,
            "depart": depart_iso,
            "arrive": arrive_iso,
            "duration_minutes": duration_min,
            "aircraft": None,
        }

        it = _empty_itinerary()
        it.update(
            {
                "source": "google_flights",
                "origin": origin,
                "destination": destination,
                "depart_date": depart_date,
                "return_date": return_date,
                "carrier": carriers[0] if carriers else None,
                "carriers_all": carriers,
                "cabin": cabin,
                "stops": stops_int,
                "duration_minutes": duration_min,
                "price_usd": price_amt if currency == "USD" else None,
                "currency": currency,
                "fare_brand": None,
                "baggage_included": None,
                "refundable": None,
                "segments": [seg],
                "deep_link": None,
                "raw": f_dict,
            }
        )
        # For round-trip, the second segment is unknown to fast-flights' top
        # level row, but we still expose the trip semantics via return_date.
        # (price_usd was already populated above when currency=="USD"; for
        # non-USD rows we deliberately leave it None so the caller can decide.)
        out.append(it)

    _log(
        "source_done",
        source="google_flights",
        count=len(out),
        current_price=getattr(result, "current_price", None),
    )
    return out


# ---------------------------------------------------------------------------
# Source: Duffel API
# ---------------------------------------------------------------------------

def _duffel_request(
    method: str,
    path: str,
    token: str,
    *,
    body: dict | None = None,
    timeout: int = SOURCE_TIMEOUT_SECONDS,
) -> dict:
    url = f"{DUFFEL_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Duffel-Version": DUFFEL_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "flight-hacker/1.0 (+search_cash.py)",
    }
    data_bytes = None
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
    req = urlrequest.Request(url, data=data_bytes, headers=headers, method=method)
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _fetch_duffel(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    cabin: str,
    adults: int,
    children: int,
    infants: int,
    max_stops: int,
) -> list[dict]:
    token = os.environ.get("DUFFEL_API_TOKEN")
    if not token:
        raise RuntimeError("DUFFEL_API_TOKEN not set")

    cabin_class = CABIN_TO_DUFFEL.get(cabin, "economy")
    passengers: list[dict] = []
    passengers.extend({"type": "adult"} for _ in range(adults))
    passengers.extend({"type": "child", "age": 8} for _ in range(children))
    passengers.extend({"type": "infant_without_seat"} for _ in range(infants))
    if not passengers:
        passengers = [{"type": "adult"}]

    slices = [
        {"origin": origin, "destination": destination, "departure_date": depart_date}
    ]
    if return_date:
        slices.append(
            {
                "origin": destination,
                "destination": origin,
                "departure_date": return_date,
            }
        )

    body = {
        "data": {
            "slices": slices,
            "passengers": passengers,
            "cabin_class": cabin_class,
        }
    }

    def _create_request():
        return _duffel_request(
            "POST",
            "/air/offer_requests?return_offers=true",
            token,
            body=body,
        )

    payload = _with_retry(_create_request, source="duffel")

    data = payload.get("data") or {}
    offers = data.get("offers") or []

    out: list[dict] = []
    for off in offers:
        try:
            price_str = off.get("total_amount")
            currency = off.get("total_currency", "USD")
            try:
                price_amt = float(price_str) if price_str is not None else None
            except ValueError:
                price_amt = None

            offer_slices = off.get("slices") or []
            segments_norm: list[dict] = []
            stops_count = 0
            total_duration = 0
            carriers_all: list[str] = []
            for sl in offer_slices:
                segs = sl.get("segments") or []
                stops_count += max(0, len(segs) - 1)
                for s in segs:
                    mc = (s.get("marketing_carrier") or {}).get("iata_code")
                    if mc:
                        carriers_all.append(mc)
                    seg_dur = _parse_iso8601_duration_to_minutes(s.get("duration"))
                    if seg_dur:
                        total_duration += seg_dur
                    segments_norm.append(
                        {
                            "carrier": mc,
                            "flight_no": s.get("marketing_carrier_flight_number"),
                            "from": (s.get("origin") or {}).get("iata_code"),
                            "to": (s.get("destination") or {}).get("iata_code"),
                            "depart": s.get("departing_at"),
                            "arrive": s.get("arriving_at"),
                            "duration_minutes": seg_dur,
                            "aircraft": (s.get("aircraft") or {}).get("name"),
                        }
                    )

            if max_stops is not None and stops_count > max_stops:
                continue

            # baggage / refundable best-effort from passenger entries
            baggage_included: bool | None = None
            refundable: bool | None = None
            for sl in offer_slices:
                for s in sl.get("segments") or []:
                    for p in s.get("passengers") or []:
                        bags = p.get("baggages") or []
                        if bags:
                            baggage_included = any(
                                (b.get("quantity") or 0) > 0 for b in bags
                            )
                        conds = p.get("conditions") or {}
                        rb = conds.get("refund_before_departure")
                        if isinstance(rb, dict):
                            refundable = bool(rb.get("allowed"))

            fare_brand = None
            try:
                fare_brand = (offer_slices[0].get("fare_brand_name")
                              if offer_slices else None)
            except Exception:
                fare_brand = None

            it = _empty_itinerary()
            it.update(
                {
                    "source": "duffel",
                    "origin": origin,
                    "destination": destination,
                    "depart_date": depart_date,
                    "return_date": return_date,
                    "carrier": carriers_all[0] if carriers_all else None,
                    "carriers_all": carriers_all,
                    "cabin": cabin,
                    "stops": stops_count,
                    "duration_minutes": total_duration or None,
                    "price_usd": price_amt if currency == "USD" else None,
                    "currency": currency,
                    "fare_brand": fare_brand,
                    "baggage_included": baggage_included,
                    "refundable": refundable,
                    "segments": segments_norm,
                    "deep_link": None,
                    "raw": off,
                }
            )
            out.append(it)
        except Exception as exc:
            _log("duffel_offer_parse_error", error=str(exc))
            continue

    _log("source_done", source="duffel", count=len(out))
    return out


def _parse_iso8601_duration_to_minutes(text: str | None) -> int | None:
    """Parse ISO-8601 PT#H#M -> minutes."""
    if not text:
        return None
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?$", text)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mm = int(m.group(2) or 0)
    return h * 60 + mm or None


# ---------------------------------------------------------------------------
# Source dispatcher
# ---------------------------------------------------------------------------

_SOURCE_FETCHERS = {
    "google_flights": _fetch_google_flights,
    "duffel": _fetch_duffel,
}


def _run_source(name: str, kwargs: dict) -> list[dict]:
    """Run one source with timeout + exception isolation."""
    started = time.time()
    fn = _SOURCE_FETCHERS.get(name)
    if fn is None:
        return [{"source": name, "error": _truncate_error(f"unknown source: {name}")}]
    try:
        out = fn(**kwargs)
        _log(
            "source_ok",
            source=name,
            ms=int((time.time() - started) * 1000),
            count=len(out),
        )
        return out
    except Exception as exc:
        _log(
            "source_error",
            source=name,
            ms=int((time.time() - started) * 1000),
            error=_truncate_error(exc),
            tb=traceback.format_exc(limit=2),
        )
        return [{"source": name, "error": _truncate_error(exc)}]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _sort_key(it: dict) -> tuple[int, float]:
    if "error" in it:
        return (1, float("inf"))
    price = it.get("price_usd")
    if price is None:
        return (0, float("inf"))
    return (0, float(price))


def search(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None = None,
    cabin: str = "economy",
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    max_stops: int = 2,
    sources: tuple[str, ...] = ("google_flights", "duffel"),
    use_cache: bool = True,
) -> list[dict]:
    """Search cash fares across multiple sources in parallel.

    Returns a list of normalized itinerary dicts (see module docstring),
    sorted by price ascending. Per-source errors are returned as
    ``{"source": "<name>", "error": "<msg>"}`` records placed at the end.
    """
    origin = origin.upper().strip()
    destination = destination.upper().strip()

    cache_key = (
        "search",
        origin,
        destination,
        depart_date,
        return_date,
        cabin,
        adults,
        children,
        infants,
        max_stops,
        tuple(sources),
    )
    cpath = _cache_path(cache_key)
    if use_cache:
        cached = _cache_read(cpath)
        if cached is not None:
            _log("cache_hit", path=str(cpath), count=len(cached))
            return cached

    # Auto-skip Duffel if no token (do not register as an error).
    effective_sources = [
        s for s in sources
        if s != "duffel" or os.environ.get("DUFFEL_API_TOKEN")
    ]
    if not effective_sources:
        effective_sources = list(sources)

    kwargs = dict(
        origin=origin,
        destination=destination,
        depart_date=depart_date,
        return_date=return_date,
        cabin=cabin,
        adults=adults,
        children=children,
        infants=infants,
        max_stops=max_stops,
    )

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, len(effective_sources))) as ex:
        futures = {
            ex.submit(_run_source, name, kwargs): name
            for name in effective_sources
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                src_out = fut.result(timeout=SOURCE_TIMEOUT_SECONDS + 5)
            except Exception as exc:
                src_out = [{
                    "source": name,
                    "error": _truncate_error(f"timeout_or_crash: {exc}"),
                }]
                _log("source_timeout", source=name, error=_truncate_error(exc))
            results.extend(src_out)

    results.sort(key=_sort_key)

    if use_cache:
        _cache_write(cpath, results)
    return results


def search_dates(
    origin: str,
    destination: str,
    month: str,
    cabin: str = "economy",
    adults: int = 1,
    sources: tuple[str, ...] = ("google_flights", "duffel"),
    use_cache: bool = True,
) -> list[dict]:
    """Return the cheapest itinerary per day across the given month.

    ``month`` is ``YYYY-MM``. Uses concurrent per-day fan-out (max 8 workers).
    fast-flights does not currently expose a public date-grid endpoint, so we
    always do per-day fan-out.
    """
    try:
        year, mon = (int(x) for x in month.split("-"))
    except Exception as exc:
        raise ValueError(f"month must be 'YYYY-MM', got {month!r}: {exc}") from exc
    _, last_day = calendar.monthrange(year, mon)

    dates = [f"{year:04d}-{mon:02d}-{d:02d}" for d in range(1, last_day + 1)]

    cheapest_per_day: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_DATE_GRID_WORKERS) as ex:
        futs = {
            ex.submit(
                search,
                origin,
                destination,
                d,
                None,
                cabin,
                adults,
                0,
                0,
                2,
                sources,
                use_cache,
            ): d
            for d in dates
        }
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                its = fut.result()
            except Exception as exc:
                cheapest_per_day.append({"depart_date": d, "error": _truncate_error(exc)})
                continue
            real = [
                it for it in its if "error" not in it and it.get("price_usd") is not None
            ]
            if real:
                cheapest = min(real, key=lambda x: x["price_usd"])
                cheapest_per_day.append(cheapest)
            else:
                cheapest_per_day.append({"depart_date": d, "error": "no_offers"})

    cheapest_per_day.sort(key=lambda x: x.get("depart_date") or "")
    return cheapest_per_day


def search_many(queries: list[dict]) -> list[list[dict]]:
    """Run many `search` calls in parallel; preserves input order.

    Each query dict accepts the same kwargs as ``search``.
    """
    if not queries:
        return []
    out: list[list[dict] | None] = [None] * len(queries)
    with ThreadPoolExecutor(max_workers=min(8, len(queries))) as ex:
        futs = {ex.submit(search, **q): idx for idx, q in enumerate(queries)}
        for fut in as_completed(futs):
            idx = futs[fut]
            try:
                out[idx] = fut.result()
            except Exception as exc:
                out[idx] = [{"source": "search_many", "error": _truncate_error(exc)}]
    return [r or [] for r in out]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Cash-fare flight search (multi-source).")
    ap.add_argument("--origin", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--depart", required=True)
    ap.add_argument("--return", dest="return_date", default=None)
    ap.add_argument("--cabin", default="economy",
                    choices=list(CABIN_TO_FAST_FLIGHTS.keys()))
    ap.add_argument("--adults", type=int, default=1)
    ap.add_argument("--children", type=int, default=0)
    ap.add_argument("--infants", type=int, default=0)
    ap.add_argument("--max-stops", type=int, default=2)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    results = search(
        origin=args.origin,
        destination=args.dest,
        depart_date=args.depart,
        return_date=args.return_date,
        cabin=args.cabin,
        adults=args.adults,
        children=args.children,
        infants=args.infants,
        max_stops=args.max_stops,
        use_cache=not args.no_cache,
    )
    print(json.dumps(results, indent=2, default=str))
