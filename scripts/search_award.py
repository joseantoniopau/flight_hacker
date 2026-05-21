"""
search_award.py — Seats.aero Pro Partner API client for award-availability search.

Returns records normalized to the canonical ITINERARY_SCHEMA defined in common.py
(`kind: "award"`).

Endpoints used (see data/seats_aero_api_notes.md):
  GET /partnerapi/search        — cached cross-program search (workhorse)
  GET /partnerapi/availability  — bulk single-program region scan
  GET /partnerapi/trips/{id}    — segment-level detail for one availability row
  GET /partnerapi/routes        — monitored OD pairs for one program

Auth: header `Partner-Authorization: <raw_key>` (NO `Bearer` prefix).
Taxes are returned in MINOR currency units (cents) — divide by 100.
Cabin param is plural (`cabins=`) for /search, singular (`cabin=`) for /availability.

Public API:
    search(origin, destination, depart_date=None, return_date=None, ...)
        -> list[dict]   # normalized award itineraries (or dict with outbound/return
                        #  if return_date is provided)

    bulk_region(source, origin_region, destination_region, cabin, days_out=180)
        -> list[dict]

    trips(availability_id)  -> dict (hydrated /trips response with segments)
    routes(source)          -> list[dict] (cached 24h)
    programs()              -> dict (SOURCE_TO_PROGRAM)
    get_rate_limit_remaining() -> int | None

CLI:
    python search_award.py --origin JFK --dest NRT --depart 2026-07-15 \
        --cabin business --pax 2 [--source aeroplan,united] [--only-direct] [--no-cache]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Iterable
from urllib import error as urlerror

from common import (
    cache_get,
    cache_set,
    get_env,
    http_get,
    log,
    make_empty_itinerary,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://seats.aero/partnerapi"

# Cache TTLs (seconds)
CACHE_TTL_SEARCH = 6 * 60 * 60        # 6 hours for cached-search
CACHE_TTL_BULK = 6 * 60 * 60          # 6 hours for bulk-region
CACHE_TTL_ROUTES = 24 * 60 * 60       # 24 hours for routes/programs
CACHE_TTL_TRIPS = 6 * 60 * 60         # 6 hours for trips hydration

# Pagination caps (bound runtime + quota burn)
MAX_PAGES = 20
DEFAULT_TAKE = 500
INTER_PAGE_SLEEP = 0.2                # polite throttle
TRIPS_HYDRATION_TOP_N = 20
TRIPS_RATE_LIMIT_FLOOR = 50           # skip hydration if remaining < this

# Map our canonical cabin enum -> Seats.aero cabin enum.
CABIN_OUR_TO_API = {
    "economy":         "economy",
    "premium_economy": "premium",
    "business":        "business",
    "first":           "first",
}
CABIN_API_TO_OUR = {v: k for k, v in CABIN_OUR_TO_API.items()}

# Letter prefix on CachedSearchData per-cabin columns.
# Y=economy, W=premium, J=business, F=first
_CABIN_API_TO_LETTER = {
    "economy":  "Y",
    "premium":  "W",
    "business": "J",
    "first":    "F",
}

# Source slug -> human-readable program label.
# Per spec: include rebrands, defensive entries, and flag non-airline pseudo-sources.
SOURCE_TO_PROGRAM: dict[str, str] = {
    # Documented sources from seats.aero (verified 2026-05-21)
    "aeroplan":         "Aeroplan",
    "american":         "American AAdvantage",
    "americanairlines": "American AAdvantage",      # defensive alias (AA not on seats.aero)
    "delta":            "Delta SkyMiles",
    "united":           "United MileagePlus",
    "alaska":           "Alaska Mileage Plan",      # rebranded "Atmos" in 2025
    "atmos":            "Alaska Mileage Plan (Atmos)",
    "jetblue":          "JetBlue TrueBlue",
    "aeromexico":       "Aeromexico Club Premier",
    "azul":             "Azul TudoAzul",
    "smiles":           "Gol Smiles",
    "connectmiles":     "Copa ConnectMiles",
    "copa":             "Copa ConnectMiles",
    "velocity":         "Virgin Australia Velocity",
    "virginatlantic":   "Virgin Atlantic Flying Club",
    "flyingblue":       "Air France/KLM Flying Blue",
    "eurobonus":        "SAS EuroBonus",
    "etihad":           "Etihad Guest",
    "emirates":         "Emirates Skywards",
    "qatar":            "Qatar Privilege Club",
    "turkish":          "Turkish Miles&Smiles",
    "singapore":        "Singapore KrisFlyer",
    "qantas":           "Qantas Frequent Flyer",
    "ethiopian":        "Ethiopian ShebaMiles",
    "saudia":           "Saudia ALFURSAN",
    # Spec-required defensive entries (may not be in seats.aero today,
    # but normalize cleanly if they ever appear, and survive rebrands)
    "lifemiles":        "Avianca LifeMiles",
    "ba":               "British Airways Avios",
    "britishairways":   "British Airways Avios",
    "ana":              "ANA Mileage Club",
    "jal":              "JAL Mileage Bank",
    "cathay":           "Cathay Asia Miles",
    "asiamiles":        "Cathay Asia Miles",
    "finnair":          "Finnair Plus",
    "iberia":           "Iberia Plus",
    "korean":           "Korean Air SKYPASS",
    "gulfair":          "Gulf Air Falconflyer",
    "oman":             "Oman Air Sindbad",
    "hawaiian":         "Hawaiian Miles",
    # Non-airline pseudo-sources (transferable currencies, flagged separately)
    "americanexpress":  "Amex Membership Rewards [transferable currency, not an airline]",
}

# Module-global rate-limit tracker (updated from response headers).
_last_rate_limit_remaining: int | None = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SeatsAeroError(Exception):
    """Raised on non-2xx Partner API responses (or local validation failures)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _csv(value: Any) -> str | None:
    """Coerce a str / iterable / None into a comma-separated string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    try:
        items = [str(v).strip() for v in value if v is not None and str(v).strip()]
    except TypeError:
        return str(value)
    return ",".join(items) if items else None


def _auth_headers() -> dict[str, str]:
    key = get_env("SEATS_AERO_API_KEY")
    if not key:
        raise SeatsAeroError(0, "SEATS_AERO_API_KEY is not set (check .env)")
    return {
        "Partner-Authorization": key,   # NO "Bearer" prefix
        "Accept": "application/json",
    }


def _explain_status(status: int, path: str, body: bytes) -> str:
    snippet = body[:300].decode("utf-8", errors="replace") if body else ""
    if status == 401:
        return (
            "401 Unauthorized — check SEATS_AERO_API_KEY. The Seats.aero header is "
            "`Partner-Authorization: <key>` with NO 'Bearer' prefix. "
            f"Response: {snippet}"
        )
    if status == 403:
        if "/live" in path:
            return (
                "403 Forbidden on /live — Live Search is not included in the Pro tier. "
                "Use cached search (`search()`) instead. "
                f"Response: {snippet}"
            )
        return f"403 Forbidden — endpoint not allowed for this key. Response: {snippet}"
    if status == 429:
        return (
            "429 Too Many Requests — daily quota (1000/day, resets at 00:00 UTC) "
            "or burst-limit hit. Back off and retry tomorrow. "
            f"Response: {snippet}"
        )
    return f"HTTP {status}: {snippet}"


def _request(path: str, params: dict | None = None) -> dict:
    """Single GET to /partnerapi{path}; updates rate-limit module-global; raises on non-2xx."""
    global _last_rate_limit_remaining
    url = f"{BASE_URL}{path}"
    headers = _auth_headers()
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        status, resp_headers, body = http_get(
            url, headers=headers, params=clean_params, timeout=30
        )
    except (urlerror.URLError, ConnectionError, TimeoutError) as e:
        raise SeatsAeroError(0, f"Network error contacting {url}: {e}") from e

    # Update rate-limit tracker from response headers (case-insensitive search).
    for k, v in (resp_headers or {}).items():
        if k.lower() == "x-ratelimit-remaining":
            try:
                _last_rate_limit_remaining = int(v)
            except (TypeError, ValueError):
                pass
            break

    if status < 200 or status >= 300:
        raise SeatsAeroError(status, _explain_status(status, path, body))

    try:
        return json.loads(body.decode("utf-8"))
    except Exception as e:
        raise SeatsAeroError(status, f"Invalid JSON from {url}: {e}") from e


def _paginate(path: str, base_params: dict, max_pages: int = MAX_PAGES) -> list[dict]:
    """Auto-paginate a Seats.aero endpoint via `cursor`+`hasMore`. Returns all rows."""
    all_rows: list[dict] = []
    cursor: int | None = None
    pages_fetched = 0
    while pages_fetched < max_pages:
        page_params = dict(base_params)
        if cursor is not None:
            page_params["cursor"] = cursor
        page = _request(path, page_params)
        rows = page.get("data") or []
        all_rows.extend(rows)
        pages_fetched += 1
        log(
            "seats_aero_page",
            path=path,
            page=pages_fetched,
            rows=len(rows),
            total=len(all_rows),
            has_more=bool(page.get("hasMore")),
            remaining=_last_rate_limit_remaining,
        )
        if not page.get("hasMore"):
            break
        cursor = page.get("cursor")
        if cursor is None:
            break
        time.sleep(INTER_PAGE_SLEEP)
    if pages_fetched >= max_pages:
        log(
            "seats_aero_pagination_cap_hit",
            path=path,
            pages=pages_fetched,
            total=len(all_rows),
        )
    return all_rows


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_segments_from_trip(trip: dict) -> list[dict]:
    """Build canonical segment list from one AvailabilityData trip object."""
    segments: list[dict] = []
    for seg in trip.get("AvailabilitySegments") or []:
        flight_no = seg.get("FlightNumber") or ""
        segments.append({
            "carrier": flight_no[:2] if flight_no else None,
            "flight_no": flight_no or None,
            "from": seg.get("OriginAirport"),
            "to": seg.get("DestinationAirport"),
            "depart": seg.get("DepartsAt"),
            "arrive": seg.get("ArrivesAt"),
            "duration_minutes": None,  # segment-level duration not in payload
            "aircraft": seg.get("AircraftCode") or seg.get("AircraftName"),
            "fare_class": seg.get("FareClass"),
            "cabin": seg.get("Cabin"),
            "order": seg.get("Order"),
        })
    segments.sort(key=lambda s: s.get("order") if s.get("order") is not None else 0)
    return segments


def _normalize_cached_row(
    row: dict,
    requested_cabins_api: list[str] | None,
) -> list[dict]:
    """Explode one CachedSearchData row into 1-N normalized records (one per cabin)."""
    cabins = requested_cabins_api or list(_CABIN_API_TO_LETTER.keys())
    route = row.get("Route") or {}
    source_slug = (row.get("Source") or "").lower()
    program = SOURCE_TO_PROGRAM.get(source_slug, source_slug or "unknown")
    depart_date = row.get("Date")
    taxes_currency = row.get("TaxesCurrency") or "USD"
    out: list[dict] = []

    # Optional inline trips payload (when include_trips=true on /search).
    # The API returns it as a JSON-encoded string field; tolerate both shapes.
    inline_trips_raw = row.get("AvailabilityTrips")
    inline_trips: list[dict] = []
    if isinstance(inline_trips_raw, list):
        inline_trips = inline_trips_raw
    elif isinstance(inline_trips_raw, str) and inline_trips_raw.strip():
        try:
            parsed = json.loads(inline_trips_raw)
            if isinstance(parsed, list):
                inline_trips = parsed
        except Exception:
            inline_trips = []

    for api_cabin in cabins:
        letter = _CABIN_API_TO_LETTER.get(api_cabin)
        if not letter:
            continue
        if not row.get(f"{letter}Available"):
            continue
        miles_raw = row.get(f"{letter}MileageCostRaw") or 0
        if not miles_raw or int(miles_raw) <= 0:
            continue

        taxes_minor = int(row.get(f"{letter}TotalTaxes") or 0)
        if str(taxes_currency).upper() == "USD":
            taxes_usd: float | None = round(taxes_minor / 100.0, 2)
            taxes_native = None
        else:
            taxes_usd = None
            taxes_native = {
                "currency": taxes_currency,
                "amount": round(taxes_minor / 100.0, 2),
            }

        airlines = row.get(f"{letter}Airlines") or ""
        airlines_list = [a.strip() for a in airlines.split(",") if a.strip()]
        operating_carrier = airlines_list[0] if airlines_list else None

        # Segments / marketing carrier from inline trips (best-effort).
        segments: list[dict] = []
        marketing_carrier = operating_carrier
        if inline_trips:
            cabin_trips = [
                t for t in inline_trips
                if (t.get("Cabin") or "").lower() == api_cabin
            ]
            if cabin_trips:
                cabin_trips.sort(key=lambda t: t.get("MileageCost") or 1 << 62)
                cheapest = cabin_trips[0]
                segments = _normalize_segments_from_trip(cheapest)
                first_flight = (cheapest.get("FlightNumbers") or "").split(",")[0].strip()
                if first_flight:
                    marketing_carrier = first_flight[:2]

        itin = make_empty_itinerary()
        itin.update({
            "source":            "seats.aero",
            "kind":              "award",
            "origin":            route.get("OriginAirport"),
            "destination":       route.get("DestinationAirport"),
            "depart_date":       depart_date,
            "return_date":       None,
            "carrier":           marketing_carrier,
            "carriers_all":      airlines_list,
            "operating_carrier": operating_carrier,
            "cabin":             CABIN_API_TO_OUR.get(api_cabin, api_cabin),
            "stops":             max(0, len(segments) - 1) if segments else 0,
            "program":           program,
            "miles":             int(miles_raw),
            "taxes_usd":         taxes_usd,
            "available_seats":   int(row.get(f"{letter}RemainingSeats") or 0),
            "segments":          segments,
            "deep_link":         None,   # filled by trips() hydration when available
            "raw":               {
                "availability_id": row.get("ID"),
                "route_id": row.get("RouteID"),
                "source": source_slug,
                "updated_at": row.get("UpdatedAt"),
                "cached_search_row": row,
            },
        })
        if taxes_native is not None:
            itin["taxes_native"] = taxes_native
        out.append(itin)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_rate_limit_remaining() -> int | None:
    """Return last observed `X-RateLimit-Remaining` value (None until first call)."""
    return _last_rate_limit_remaining


def programs() -> dict[str, str]:
    """Return the SOURCE_TO_PROGRAM mapping."""
    return dict(SOURCE_TO_PROGRAM)


def routes(source: str, use_cache: bool = True) -> list[dict]:
    """Return monitored route OD pairs for a source. Cached 24h."""
    source = (source or "").strip().lower()
    if not source:
        raise SeatsAeroError(0, "routes(): source is required")
    cache_key = f"seats_aero:routes:{source}"
    if use_cache:
        cached = cache_get(cache_key, ttl_seconds=CACHE_TTL_ROUTES)
        if cached is not None:
            return cached
    payload = _request("/routes", {"source": source})
    # /routes returns either a bare array or { data: [...] }; tolerate both.
    if isinstance(payload, list):
        rows = payload
    else:
        rows = payload.get("data") or []
    cache_set(cache_key, rows)
    return rows


def trips(availability_id: str, include_filtered: bool = False) -> dict:
    """Hydrate one cached-search row into segment-level detail via /trips/{id}."""
    if not availability_id:
        raise SeatsAeroError(0, "trips(): availability_id is required")
    cache_key = f"seats_aero:trips:{availability_id}:{int(bool(include_filtered))}"
    cached = cache_get(cache_key, ttl_seconds=CACHE_TTL_TRIPS)
    if cached is not None:
        return cached
    payload = _request(
        f"/trips/{availability_id}",
        {"include_filtered": str(bool(include_filtered)).lower()},
    )
    cache_set(cache_key, payload)
    return payload


def bulk_region(
    source: str,
    origin_region: str,
    destination_region: str,
    cabin: str,
    days_out: int = 180,
    start_date: str | None = None,
    end_date: str | None = None,
    use_cache: bool = True,
) -> list[dict]:
    """Bulk-availability scan for a whole region. Returns normalized rows.

    `cabin` accepts either our enum ("economy"/"premium_economy"/"business"/"first")
    or the API enum ("economy"/"premium"/"business"/"first"). The /availability
    endpoint uses the **singular** `cabin=` param (one-of).

    `days_out` is convenience: when start_date is None, defaults to today + 0..days_out.
    """
    api_cabin = CABIN_OUR_TO_API.get(cabin, cabin)
    if api_cabin not in _CABIN_API_TO_LETTER:
        raise SeatsAeroError(0, f"bulk_region(): unknown cabin {cabin!r}")

    if start_date is None or end_date is None:
        from datetime import date, timedelta
        today = date.today()
        start_date = start_date or today.isoformat()
        end_date = end_date or (today + timedelta(days=days_out)).isoformat()

    base_params = {
        "source":             source,
        "cabin":              api_cabin,     # SINGULAR for /availability
        "origin_region":      origin_region,
        "destination_region": destination_region,
        "start_date":         start_date,
        "end_date":           end_date,
        "take":               DEFAULT_TAKE,
    }
    cache_key = (
        f"seats_aero:bulk:{source}:{origin_region}:{destination_region}:"
        f"{api_cabin}:{start_date}:{end_date}"
    )
    if use_cache:
        cached = cache_get(cache_key, ttl_seconds=CACHE_TTL_BULK)
        if cached is not None:
            return cached

    all_rows = _paginate("/availability", base_params)
    out: list[dict] = []
    for row in all_rows:
        out.extend(_normalize_cached_row(row, [api_cabin]))
    cache_set(cache_key, out)
    return out


def _hydrate_segments(rows: list[dict]) -> None:
    """In-place: hydrate /trips/{id} segments + deep links for the top-N cheapest rows."""
    if not rows:
        return
    remaining = get_rate_limit_remaining()
    if remaining is not None and remaining < TRIPS_RATE_LIMIT_FLOOR:
        log(
            "seats_aero_skip_trips_hydration",
            reason="rate_limit_floor",
            remaining=remaining,
            floor=TRIPS_RATE_LIMIT_FLOOR,
        )
        return
    ranked = sorted(
        rows,
        key=lambda r: (r.get("miles") or 1 << 62, r.get("taxes_usd") or 1e18),
    )
    for itin in ranked[:TRIPS_HYDRATION_TOP_N]:
        rem_now = get_rate_limit_remaining()
        if rem_now is not None and rem_now < TRIPS_RATE_LIMIT_FLOOR:
            log("seats_aero_stop_trips_hydration", remaining=rem_now)
            break
        avail_id = (itin.get("raw") or {}).get("availability_id")
        if not avail_id:
            continue
        try:
            detail = trips(avail_id)
        except SeatsAeroError as e:
            log("seats_aero_trips_error", avail_id=avail_id, error=str(e))
            continue
        our_cabin = itin.get("cabin")
        api_cabin_want = CABIN_OUR_TO_API.get(our_cabin, our_cabin)
        trip_options = [
            t for t in (detail.get("data") or [])
            if (t.get("Cabin") or "").lower() == api_cabin_want
        ]
        if trip_options:
            trip_options.sort(key=lambda t: t.get("MileageCost") or 1 << 62)
            chosen = trip_options[0]
            itin["segments"] = _normalize_segments_from_trip(chosen)
            itin["stops"] = chosen.get("Stops") or max(0, len(itin["segments"]) - 1)
            itin["duration_minutes"] = chosen.get("TotalDuration") or 0
        booking_links = detail.get("booking_links") or []
        primary = next((b for b in booking_links if b.get("primary")), None)
        chosen_link = primary or (booking_links[0] if booking_links else None)
        if chosen_link and chosen_link.get("link"):
            itin["deep_link"] = chosen_link["link"]
        itin["raw"]["trips_payload"] = detail


def _do_one_leg(
    origin: str,
    destination: str,
    api_cabins: list[str],
    start_date: str | None,
    end_date: str | None,
    sources: Iterable[str] | None,
    only_direct: bool,
    include_segments: bool,
    passengers: int,
    use_cache: bool,
) -> list[dict]:
    """Run a one-way cached search for a single OD pair and return normalized rows."""
    base_params = {
        "origin_airport":      origin,
        "destination_airport": destination,
        "start_date":          start_date,
        "end_date":            end_date,
        "cabins":              ",".join(api_cabins) if api_cabins else None,  # PLURAL
        "sources":             _csv(sources),
        "only_direct_flights": str(bool(only_direct)).lower(),
        "include_trips":       "false",   # we hydrate selectively via /trips
        "take":                DEFAULT_TAKE,
    }
    cache_key = (
        f"seats_aero:search:v2:{origin}->{destination}:{start_date}:{end_date}:"
        f"{','.join(api_cabins)}:{_csv(sources)}:{int(only_direct)}"
    )
    rows: list[dict] | None = None
    if use_cache:
        cached = cache_get(cache_key, ttl_seconds=CACHE_TTL_SEARCH)
        if cached is not None:
            rows = cached
    if rows is None:
        raw_rows = _paginate("/search", base_params)
        rows = []
        for row in raw_rows:
            rows.extend(_normalize_cached_row(row, api_cabins))
        cache_set(cache_key, rows)

    # Passenger filter.
    if passengers and passengers > 0:
        rows = [r for r in rows if (r.get("available_seats") or 0) >= passengers]

    if include_segments:
        _hydrate_segments(rows)

    return rows


def search(
    origin: str,
    destination: str,
    depart_date: str | None = None,
    return_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cabins: Iterable[str] = ("business", "first"),
    passengers: int = 1,
    sources: Iterable[str] | str | None = None,
    only_direct: bool = False,
    include_segments: bool = True,
    use_cache: bool = True,
) -> list[dict] | dict:
    """Award-availability search via the Seats.aero cached /search endpoint.

    - `depart_date` (or `start_date`+`end_date`) bounds the outbound date.
    - When `return_date` is set, a second call is made for the reverse leg, and
      the result is returned as `{"outbound": [...], "return": [...]}` with a
      `direction` field stamped on each itinerary.
    - `sources` is an optional comma-list / tuple of program slugs.
    - `passengers` filters to rows with `available_seats >= passengers`.

    Returns one of:
      - list[dict] of normalized award itineraries (one-way)
      - dict with `"outbound"` and `"return"` lists (round trip)
    """
    origin = (origin or "").strip().upper()
    destination = (destination or "").strip().upper()
    if not origin or not destination:
        raise SeatsAeroError(0, "search(): origin and destination required")

    # Map our cabin enum -> API cabin enum.
    api_cabins: list[str] = []
    for c in cabins or []:
        api = CABIN_OUR_TO_API.get(c, c)
        if api in _CABIN_API_TO_LETTER and api not in api_cabins:
            api_cabins.append(api)
    if not api_cabins:
        api_cabins = ["business", "first"]

    # Resolve outbound date window.
    out_start = start_date or depart_date
    out_end = end_date or depart_date
    # If neither is provided, leave both None (API returns whatever is cached).

    # Sources may be a CSV string, tuple, or None.
    if isinstance(sources, str):
        sources_iter: Iterable[str] | None = [
            s.strip().lower() for s in sources.split(",") if s.strip()
        ]
    elif sources is None:
        sources_iter = None
    else:
        sources_iter = [str(s).strip().lower() for s in sources if str(s).strip()]

    log(
        "seats_aero_search_start",
        origin=origin,
        destination=destination,
        cabins=api_cabins,
        start=out_start,
        end=out_end,
        sources=sources_iter,
        passengers=passengers,
        only_direct=only_direct,
        round_trip=bool(return_date),
    )

    outbound = _do_one_leg(
        origin=origin,
        destination=destination,
        api_cabins=api_cabins,
        start_date=out_start,
        end_date=out_end,
        sources=sources_iter,
        only_direct=only_direct,
        include_segments=include_segments,
        passengers=passengers,
        use_cache=use_cache,
    )
    for r in outbound:
        r["direction"] = "outbound"

    if not return_date:
        log(
            "seats_aero_search_done",
            results=len(outbound),
            remaining=get_rate_limit_remaining(),
        )
        return outbound

    return_leg = _do_one_leg(
        origin=destination,
        destination=origin,
        api_cabins=api_cabins,
        start_date=return_date,
        end_date=return_date,
        sources=sources_iter,
        only_direct=only_direct,
        include_segments=include_segments,
        passengers=passengers,
        use_cache=use_cache,
    )
    for r in return_leg:
        r["direction"] = "return"
        r["return_date"] = return_date

    log(
        "seats_aero_search_done",
        outbound=len(outbound),
        return_=len(return_leg),
        remaining=get_rate_limit_remaining(),
    )
    return {"outbound": outbound, "return": return_leg}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="search_award.py",
        description="Seats.aero Pro Partner API — award-availability search",
    )
    p.add_argument("--origin", required=True, help="Origin IATA (or comma-list)")
    p.add_argument("--dest", required=True, help="Destination IATA (or comma-list)")
    p.add_argument("--depart", required=False, help="Departure date YYYY-MM-DD")
    p.add_argument("--return", dest="return_date", help="Return date YYYY-MM-DD (optional)")
    p.add_argument("--start-date", help="Window lower bound YYYY-MM-DD")
    p.add_argument("--end-date", help="Window upper bound YYYY-MM-DD")
    p.add_argument(
        "--cabin",
        default="business",
        help="Comma-list of cabins (economy,premium_economy,business,first). Default: business",
    )
    p.add_argument("--pax", type=int, default=1, help="Passengers (filters available_seats). Default: 1")
    p.add_argument("--source", default=None, help="Optional comma-list of program slugs")
    p.add_argument("--only-direct", action="store_true", help="Only direct (non-stop) flights")
    p.add_argument("--no-cache", action="store_true", help="Bypass local file cache")
    p.add_argument(
        "--no-segments",
        action="store_true",
        help="Skip /trips hydration for top-N (saves API calls)",
    )
    return p


def _sort_key(r: dict) -> tuple:
    return (
        r.get("miles") or 1 << 62,
        r.get("taxes_usd") if r.get("taxes_usd") is not None else 1e18,
    )


def _cli(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    cabins = [c.strip() for c in (args.cabin or "").split(",") if c.strip()]
    try:
        result = search(
            origin=args.origin,
            destination=args.dest,
            depart_date=args.depart,
            return_date=args.return_date,
            start_date=args.start_date,
            end_date=args.end_date,
            cabins=cabins,
            passengers=args.pax,
            sources=args.source,
            only_direct=args.only_direct,
            include_segments=not args.no_segments,
            use_cache=not args.no_cache,
        )
    except SeatsAeroError as e:
        sys.stderr.write(f"search_award error: {e}\n")
        return 2

    if isinstance(result, dict):
        result["outbound"] = sorted(result.get("outbound") or [], key=_sort_key)
        result["return"] = sorted(result.get("return") or [], key=_sort_key)
        print(json.dumps(result, indent=2, default=str))
    else:
        result = sorted(result, key=_sort_key)
        print(json.dumps(result, indent=2, default=str))

    rem = get_rate_limit_remaining()
    if rem is not None:
        sys.stderr.write(f"[seats.aero] X-RateLimit-Remaining: {rem}\n")
    return 0


# ---------------------------------------------------------------------------
# Entrypoint (CLI + live smoke test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # If args were passed, run the CLI; otherwise run a minimal smoke test
    # against the live API to verify response shape.
    if len(sys.argv) > 1:
        sys.exit(_cli())

    print("[smoke] verifying Seats.aero Pro Partner API connectivity...", file=sys.stderr)
    try:
        records = search(
            origin="JFK",
            destination="NRT",
            depart_date="2026-08-15",
            cabins=("business",),
            passengers=1,
            only_direct=False,
            include_segments=False,   # keep smoke test cheap
            use_cache=True,
        )
    except SeatsAeroError as e:
        print(f"[smoke] FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    assert isinstance(records, list), f"expected list, got {type(records).__name__}"
    print(
        f"[smoke] OK: {len(records)} records, "
        f"X-RateLimit-Remaining={get_rate_limit_remaining()}",
        file=sys.stderr,
    )
    if records:
        sample = records[0]
        for field in ("source", "kind", "origin", "destination", "depart_date",
                      "program", "miles", "available_seats"):
            assert field in sample, f"missing field in normalized row: {field}"
        assert sample["source"] == "seats.aero"
        assert sample["kind"] == "award"
        print(json.dumps(records[0], indent=2, default=str))
    sys.exit(0)
