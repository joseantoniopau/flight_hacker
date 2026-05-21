# Seats.aero Pro API — Reference Notes

Last verified: 2026-05-21. All endpoint paths, field names, and parameter names quoted verbatim from the official developer docs at <https://developers.seats.aero/> and cross-checked against two community SDKs (`gavgrego/seats.aero-mcp-server` TypeScript, `denverquane/seats-aero-go` Go).

---

## 1. Authentication

- **Header name (exact, case-sensitive):** `Partner-Authorization`
- **Header value:** the raw API key. **No `Bearer ` prefix.** No `X-API-Key`. No basic auth.
- **Key format:** `pro_xxxxxxxxxxxxxxxxxxxxx` (the Pro tier prefix is `pro_`).
- **Eligibility:** Pro membership required; key is generated from account settings once approved.

Example:

```
GET /partnerapi/search?... HTTP/1.1
Host: seats.aero
Accept: application/json
Partner-Authorization: pro_xxxxxxxxxxxxxxxxxxxxx
```

---

## 2. Base URL

```
https://seats.aero/partnerapi
```

All Partner API endpoints below are appended to this prefix.

---

## 3. Rate Limits & Quotas (Pro tier)

- **1,000 API calls per calendar day**, resetting daily at midnight **UTC**.
- Every response includes a header `X-RateLimit-Remaining` with the count left for the current day.
- This is a daily cap, **not** a per-second / per-minute throttle — but burst politely (1 req/sec is safe).
- **Live Search is NOT included in the Pro tier.** Live Search requires a separate commercial agreement. Plan around `GET /search` (cached) only.
- All other endpoints — cached search, bulk availability, get trips, get routes, OAuth consent/token — are available to Pro.

---

## 4. Endpoint Catalog

### 4.1 Cached Search — `GET /partnerapi/search`

Searches the pre-fetched availability cache. **This is the workhorse for award search.** Returns fast (cache-backed) results across all sources matching the city pair and date window.

**Query parameters:**

| Param | Type | Required | Notes |
|---|---|---|---|
| `origin_airport` | string | yes | Comma-delimited list of IATA codes, e.g. `SFO,LAX` |
| `destination_airport` | string | yes | Comma-delimited list, e.g. `FRA,LHR` |
| `start_date` | string | no | `YYYY-MM-DD` lower bound on departure |
| `end_date` | string | no | `YYYY-MM-DD` upper bound on departure |
| `cabins` | string | no | Comma-delimited filter, any of `economy,premium,business,first`. **Plural.** |
| `cursor` | int32 | no | Pagination cursor from a previous response |
| `take` | int32 | no | Default 500. Must be `>=10` and `<=1000` |
| `skip` | int32 | no | Offset for pagination |
| `order_by` | string | no | Default = departure-date order; alternative `lowest_mileage` |
| `include_trips` | boolean | no | Default false. Adds `AvailabilityTrips` segment-level detail inline |
| `only_direct_flights` | boolean | no | Default false |
| `carriers` | string | no | Comma-delimited carrier filter, e.g. `DL,AA` |
| `include_filtered` | boolean | no | Default false. Include dynamically-priced (expensive) results normally filtered out |
| `sources` | string | no | Comma-delimited program filter, e.g. `aeroplan,united` |
| `minify_trips` | boolean | no | When combined with `include_trips`, strips heavy fields for faster transfer |

**NOTE on cabin param naming:** The cached-search endpoint accepts the **plural** `cabins=` (comma-delimited multi-value filter). The Bulk Availability endpoint (§4.2) accepts the **singular** `cabin=` (one-of). The open-source TS MCP also sends `cabin_class=` which the API tolerates — but the documented spelling is `cabins` for `/search` and `cabin` for `/availability`. Stick with the documented spellings.

**Response shape** (`200 application/json`):

```json
{
  "data": [ CachedSearchData, ... ],
  "count": 137,
  "hasMore": true,
  "cursor": 1727382000
}
```

`CachedSearchData` (per-route-date row — costs are stored per cabin in parallel columns):

```
ID                string    // availability record id (pass to /trips/{id} for segments)
RouteID           string
Route             { ID, OriginAirport, OriginRegion, DestinationAirport, DestinationRegion, NumDaysOut, Distance, Source }
Date              string    // "YYYY-MM-DD" departure date
ParsedDate        ISO-8601 timestamp
YAvailable        bool      // economy
WAvailable        bool      // premium economy
JAvailable        bool      // business
FAvailable        bool      // first
YMileageCost      string    // formatted miles, e.g. "75000"
WMileageCost      string
JMileageCost      string
FMileageCost      string
YMileageCostRaw   int       // numeric miles, e.g. 75000
WMileageCostRaw   int
JMileageCostRaw   int
FMileageCostRaw   int
TaxesCurrency     string    // e.g. "USD"
YTotalTaxes       int       // in MINOR units of TaxesCurrency (cents/pence). 14250 == $142.50
WTotalTaxes       int
JTotalTaxes       int
FTotalTaxes       int
YRemainingSeats   int
WRemainingSeats   int
JRemainingSeats   int
FRemainingSeats   int
YAirlines         string    // comma-separated operating-carrier IATA codes, e.g. "UA,NH"
WAirlines         string
JAirlines         string
FAirlines         string
Source            string    // mileage program key — see §5
CreatedAt         ISO-8601
UpdatedAt         ISO-8601
AvailabilityTrips string    // populated when include_trips=true
```

**Important:** taxes are stored in **minor currency units** (i.e. integer cents). Divide by 100 to get the dollar/euro amount.

The cabin letters use airline-industry conventions: **Y**=economy, **W**=premium economy, **J**=business, **F**=first.

---

### 4.2 Bulk Availability — `GET /partnerapi/availability`

Retrieve a large amount of availability objects from **one specific mileage program**. Use for region-wide scans of a single source.

**Query parameters:**

| Param | Type | Required | Notes |
|---|---|---|---|
| `source` | string | yes | One mileage program key — see §5 |
| `cabin` | string | no | **Singular.** One of `economy`, `premium`, `business`, `first` |
| `start_date` | string | no | `YYYY-MM-DD` |
| `end_date` | string | no | `YYYY-MM-DD` |
| `origin_region` | string | no | One of `North America`, `South America`, `Africa`, `Asia`, `Europe`, `Oceania` |
| `destination_region` | string | no | Same enum as above |
| `take` | int32 | no | `>=10` and `<=1000`, default 500 |
| `skip` | int32 | no | Default 0 |
| `cursor` | int32 | no | From previous response |
| `include_filtered` | boolean | no | Default false |

**Response shape:** same envelope as Cached Search — `{ data: [CachedSearchData], count, hasMore, cursor }`.

---

### 4.3 Get Trips — `GET /partnerapi/trips/{id}`

Retrieve **flight-level (segment-level) information** for one availability object. `{id}` is the `ID` field from a `CachedSearchData` row.

**Path parameter:**

- `id` (string, required) — the availability object id.

**Query parameters:**

- `include_filtered` (boolean, optional, default false) — include expensive dynamically-priced results normally filtered out.

**Response shape:**

```
{
  "data": [ AvailabilityData, ... ],
  "origin_coordinates":      { "Lat": float, "Lon": float },
  "destination_coordinates": { "Lat": float, "Lon": float },
  "booking_links": [ { "label": str, "link": str, "primary": bool }, ... ],
  "revalidation_id": string
}
```

`AvailabilityData` (per individual flight/itinerary option):

```
ID                    string
RouteID               string
AvailabilityID        string
AvailabilitySegments  [AvailabilitySegment]
TotalDuration         int     // minutes
Stops                 int
Carriers              string  // comma-separated IATA, e.g. "UA,NH"
RemainingSeats        int
MileageCost           int     // miles for THIS specific itinerary in THIS cabin
TotalTaxes            int     // minor units of TaxesCurrency
TaxesCurrency         string  // "USD", "EUR", ...
TaxesCurrencySymbol   string  // "$", "€", ...
AllianceCost          int     // alternate redemption rate, when applicable
TotalSegmentDistance  int     // miles flown
FlightNumbers         string  // comma-separated, e.g. "UA851,NH7"
DepartsAt             ISO-8601
ArrivesAt             ISO-8601
Cabin                 string  // "economy" | "premium" | "business" | "first"
CreatedAt             ISO-8601
UpdatedAt             ISO-8601
Source                string
Filtered              bool
```

`AvailabilitySegment` (per leg):

```
ID, RouteID, AvailabilityID, AvailabilityTripID  (all string)
FlightNumber       string   // "UA851"
Distance           int
FareClass          string   // raw booking class, e.g. "I", "O", "X"
AircraftName       string   // "Boeing 787-9"
AircraftCode       string   // "789"
OriginAirport      string   // IATA
DestinationAirport string
DepartsAt          ISO-8601
ArrivesAt          ISO-8601
Source             string
Cabin              string
Order              int      // 0-indexed leg order
CreatedAt          ISO-8601
UpdatedAt          ISO-8601
```

---

### 4.4 Get Routes — `GET /partnerapi/routes`

List every origin-destination pair monitored for one mileage program.

**Query parameters:**

- `source` (string, required) — mileage program key (see §5)

**Response:** array of `Route` objects (same shape as `Route` nested in §4.1).

---

### 4.5 Live Search — `POST /partnerapi/live` (NOT available on Pro)

Documented for completeness. Returns fresh, real-time availability for one (origin, destination, date, source) tuple. Requires a separate commercial agreement — **`pro_*` keys will be rejected.** Do not call from a Pro-tier client.

**Body params:** `origin_airport`, `destination_airport`, `departure_date` (`YYYY-MM-DD`), `source`, `disable_filters` (bool, default false), `show_dynamic_pricing` (bool, default false), `seat_count` (int, default 1).

---

### 4.6 OAuth (for end-user delegated access — not needed for award search)

- `GET https://seats.aero/oauth2/consent` — redirect users here for consent.
- `POST https://seats.aero/oauth2/token` — exchange code for access/refresh tokens.

Not used by an award-search backend client; included so future integrations don't conflate consent flow with the Partner-Authorization header flow.

---

## 5. Mileage Program Sources (`source` enum)

Documented sources as of 2026-05-21. Key → human-readable program name (use this for the `program` field in the normalized output schema):

| Source key | Program (human label) |
|---|---|
| `aeroplan` | Air Canada Aeroplan |
| `american` | American AAdvantage |
| `delta` | Delta SkyMiles |
| `united` | United MileagePlus |
| `alaska` | Alaska Mileage Plan |
| `jetblue` | JetBlue TrueBlue |
| `aeromexico` | Aeromexico Club Premier |
| `azul` | Azul TudoAzul |
| `smiles` | GOL Smiles |
| `connectmiles` | Copa ConnectMiles |
| `velocity` | Virgin Australia Velocity |
| `virginatlantic` | Virgin Atlantic Flying Club |
| `flyingblue` | Air France/KLM Flying Blue |
| `eurobonus` | SAS EuroBonus |
| `etihad` | Etihad Guest |
| `emirates` | Emirates Skywards |
| `qatar` | Qatar Privilege Club |
| `turkish` | Turkish Miles&Smiles |
| `singapore` | Singapore KrisFlyer |
| `qantas` | Qantas Frequent Flyer |
| `ethiopian` | Ethiopian ShebaMiles |
| `saudia` | Saudia Alfursan |

Always check `Source` returned in each response — Seats.aero adds programs over time.

---

## 6. Python Client Reference Implementation

```python
"""
Seats.aero Pro API client. Usage:

    client = SeatsAeroClient(api_key=os.environ["SEATS_AERO_API_KEY"])
    results = client.cached_search("JFK", "NRT", cabins=["business"],
                                    start_date="2026-07-15", end_date="2026-07-15")

Returns records normalized to the flight-hacker schema.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

import requests

BASE_URL = "https://seats.aero/partnerapi"

# Cabin letter prefix used in CachedSearchData column names.
# Y=economy, W=premium, J=business, F=first.
_CABIN_TO_LETTER = {
    "economy":  "Y",
    "premium":  "W",
    "business": "J",
    "first":    "F",
}

SOURCE_TO_PROGRAM = {
    "aeroplan":       "Air Canada Aeroplan",
    "american":       "American AAdvantage",
    "delta":          "Delta SkyMiles",
    "united":         "United MileagePlus",
    "alaska":         "Alaska Mileage Plan",
    "jetblue":        "JetBlue TrueBlue",
    "aeromexico":     "Aeromexico Club Premier",
    "azul":           "Azul TudoAzul",
    "smiles":         "GOL Smiles",
    "connectmiles":   "Copa ConnectMiles",
    "velocity":       "Virgin Australia Velocity",
    "virginatlantic": "Virgin Atlantic Flying Club",
    "flyingblue":     "Air France/KLM Flying Blue",
    "eurobonus":      "SAS EuroBonus",
    "etihad":         "Etihad Guest",
    "emirates":       "Emirates Skywards",
    "qatar":          "Qatar Privilege Club",
    "turkish":        "Turkish Miles&Smiles",
    "singapore":      "Singapore KrisFlyer",
    "qantas":         "Qantas Frequent Flyer",
    "ethiopian":      "Ethiopian ShebaMiles",
    "saudia":         "Saudia Alfursan",
}


class SeatsAeroError(Exception):
    """Raised on non-2xx API responses."""


@dataclass
class SeatsAeroClient:
    api_key: str
    base_url: str = BASE_URL
    session: requests.Session = field(default_factory=requests.Session)
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self.session.headers.update({
            "Accept": "application/json",
            # Exact header name & format — NO "Bearer" prefix.
            "Partner-Authorization": self.api_key,
        })

    # ---------- low-level ----------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        # Drop None values so requests doesn't serialize them.
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self.session.get(url, params=clean, timeout=self.timeout)
        # Surface remaining quota for observability.
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            self.last_rate_limit_remaining = int(remaining)
        if not resp.ok:
            raise SeatsAeroError(f"GET {url} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # ---------- endpoints ----------

    def cached_search(
        self,
        origin: str | Iterable[str],
        dest:   str | Iterable[str],
        cabins: Iterable[str] | None = None,
        start_date: str | None = None,
        end_date:   str | None = None,
        sources:    Iterable[str] | None = None,
        only_direct_flights: bool = False,
        include_trips: bool = False,
        take: int = 500,
    ) -> list[dict[str, Any]]:
        """Cached search across all (or filtered) mileage programs.

        Auto-paginates via `cursor` until `hasMore` is false. Returns
        normalized records (one per cabin per row that is actually available).
        """
        params = {
            "origin_airport":      _csv(origin),
            "destination_airport": _csv(dest),
            "start_date":          start_date,
            "end_date":            end_date,
            "cabins":              _csv(cabins) if cabins else None,
            "sources":             _csv(sources) if sources else None,
            "only_direct_flights": str(only_direct_flights).lower(),
            "include_trips":       str(include_trips).lower(),
            "take":                take,
        }
        all_rows: list[dict[str, Any]] = []
        cursor: int | None = None
        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            page = self._get("/search", page_params)
            all_rows.extend(page.get("data", []))
            if not page.get("hasMore"):
                break
            cursor = page.get("cursor")
            if cursor is None:
                break
            time.sleep(0.2)  # polite throttle
        return [r for row in all_rows for r in _normalize_cached_row(row, cabins)]

    def live_search(
        self,
        origin: str,
        dest:   str,
        depart_date: str,
        source: str,
        seat_count: int = 1,
        show_dynamic_pricing: bool = False,
    ) -> dict[str, Any]:
        """POST /partnerapi/live. NOTE: Live Search is NOT included on the
        Pro tier — a Pro key will get HTTP 403. Provided for completeness
        / for future commercial-tier upgrades."""
        url = f"{self.base_url}/live"
        body = {
            "origin_airport":      origin.upper(),
            "destination_airport": dest.upper(),
            "departure_date":      depart_date,
            "source":              source,
            "seat_count":          seat_count,
            "show_dynamic_pricing": show_dynamic_pricing,
        }
        resp = self.session.post(url, json=body, timeout=self.timeout)
        if not resp.ok:
            raise SeatsAeroError(f"POST {url} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def bulk_availability(
        self,
        source: str,
        origin_region: str | None = None,
        destination_region: str | None = None,
        cabin: str | None = None,
        start_date: str | None = None,
        end_date:   str | None = None,
        take: int = 500,
    ) -> list[dict[str, Any]]:
        """Bulk scan one mileage program. Auto-paginates."""
        params = {
            "source":             source,
            "cabin":              cabin,                 # SINGULAR for this endpoint
            "origin_region":      origin_region,
            "destination_region": destination_region,
            "start_date":         start_date,
            "end_date":           end_date,
            "take":               take,
        }
        all_rows: list[dict[str, Any]] = []
        cursor: int | None = None
        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            page = self._get("/availability", page_params)
            all_rows.extend(page.get("data", []))
            if not page.get("hasMore"):
                break
            cursor = page.get("cursor")
            if cursor is None:
                break
            time.sleep(0.2)
        cabins = [cabin] if cabin else None
        return [r for row in all_rows for r in _normalize_cached_row(row, cabins)]

    def get_trips(self, availability_id: str, include_filtered: bool = False) -> dict[str, Any]:
        """Segment-level detail + booking links for one availability ID."""
        return self._get(
            f"/trips/{availability_id}",
            {"include_filtered": str(include_filtered).lower()},
        )

    def get_routes(self, source: str) -> list[dict[str, Any]]:
        """All monitored OD pairs for one mileage program."""
        return self._get("/routes", {"source": source})


# ---------- helpers ----------

def _csv(value: str | Iterable[str] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.upper() if len(value) == 3 else value
    return ",".join(v.upper() if len(v) == 3 else v for v in value)


def _normalize_cached_row(
    row: dict[str, Any],
    cabin_filter: Iterable[str] | None,
) -> Iterator[dict[str, Any]]:
    """Explode one CachedSearchData row (which carries 4 parallel cabin columns)
    into one normalized record per available cabin."""
    cabins = list(cabin_filter) if cabin_filter else list(_CABIN_TO_LETTER.keys())
    route = row.get("Route") or {}
    source = row.get("Source", "")
    program = SOURCE_TO_PROGRAM.get(source, source)
    depart_date = row.get("Date")
    for cabin in cabins:
        letter = _CABIN_TO_LETTER.get(cabin)
        if not letter:
            continue
        if not row.get(f"{letter}Available"):
            continue
        airlines = row.get(f"{letter}Airlines") or ""
        carrier = airlines.split(",")[0].strip() if airlines else ""
        miles = row.get(f"{letter}MileageCostRaw") or 0
        taxes_minor = row.get(f"{letter}TotalTaxes") or 0
        seats = row.get(f"{letter}RemainingSeats") or 0
        yield {
            "source":      "seats.aero",
            "program":     program,
            "origin":      route.get("OriginAirport"),
            "destination": route.get("DestinationAirport"),
            "depart_date": depart_date,
            "carrier":     carrier,
            "cabin":       cabin,
            "miles":       int(miles),
            # TotalTaxes is in MINOR units (cents) of TaxesCurrency.
            "taxes_usd":   round(int(taxes_minor) / 100, 2),
            "taxes_currency": row.get("TaxesCurrency"),
            "available_seats": int(seats),
            "raw": row,
        }


if __name__ == "__main__":
    import os, json
    client = SeatsAeroClient(api_key=os.environ["SEATS_AERO_API_KEY"])
    out = client.cached_search(
        "JFK", "NRT",
        cabins=["business"],
        start_date="2026-07-15",
        end_date="2026-07-22",
        only_direct_flights=False,
    )
    print(f"{len(out)} results, {getattr(client, 'last_rate_limit_remaining', '?')} calls left today")
    print(json.dumps(out[:3], indent=2, default=str))
```

---

## 7. Caveats & Known Gotchas

1. **No `Bearer` prefix.** Header is literally `Partner-Authorization: pro_xxxxxx`. Adding `Bearer ` returns 401.
2. **Taxes are in minor currency units.** `YTotalTaxes: 14250` with `TaxesCurrency: "USD"` means **$142.50**, not $14,250. Always `/100` on the way out (and check `TaxesCurrency` — some programs price in EUR/GBP/SGD).
3. **Cached vs Live data freshness.** Cached Search can be hours stale. Once you find a candidate via `/search`, treat the seat count + price as a *strong hint*, not a booking guarantee. For Pro tier (no Live Search), surface results with a "last updated `UpdatedAt`" caveat in your UI.
4. **Param name mismatch — `/search` vs `/availability`.** `/search` uses `cabins=` (plural, multi-value). `/availability` uses `cabin=` (singular). Don't unify them in one helper without remembering which endpoint you're calling.
5. **`origin_airport` accepts a list.** `"SFO,LAX,SJC"` is valid — useful for hub-flexible searches. Same for `destination_airport`. The client above passes lists through `_csv()`.
6. **`carriers` filter is by *operating* carrier, not marketing.** A United-codeshare-on-ANA result shows up as carrier `NH`, not `UA`.
7. **Cabin letters use industry conventions:** Y=economy, W=premium economy, J=business, F=first. Don't confuse with the API's lowercase enum (`economy`/`premium`/`business`/`first`) used in `cabin`/`cabins` query params and in the `Cabin` field of `AvailabilityData`.
8. **`include_trips=true` is expensive.** It inlines all segment data per row and balloons the payload. Default workflow: cheap `/search` first to pick candidates, then one `/trips/{id}` per chosen candidate.
9. **`MileageCost` in CachedSearchData is a *string*.** Use `*MileageCostRaw` (int) for math. (`AvailabilityData.MileageCost` in `/trips` *is* an int — different schema.)
10. **No native return-trip search.** The API is one-way only. For a round trip, call `cached_search(origin, dest, depart_date)` and `cached_search(dest, origin, return_date)` and merge client-side.
11. **No `passengers` parameter on `/search` or `/availability`.** Seat *availability counts* are returned (`*RemainingSeats`); your client must filter `available_seats >= passengers`. Only `/live` accepts `seat_count`, and Pro can't call it.
12. **Rate limit is daily, not rolling.** Burn through 1,000 by 09:00 UTC and you wait until 00:00 UTC the next day. Cache aggressively client-side; consider hashing query params and storing for 1–4 hours.
13. **Region enum is title-case with a space:** `"North America"`, not `"north_america"` or `"north-america"`.
14. **The Live Search endpoint exists but is gated.** A Pro key calling `POST /partnerapi/live` returns 403. The client method above raises `SeatsAeroError`; don't catch and retry silently.
15. **Pagination uses `cursor`, not `page`.** Each response returns `{ cursor, hasMore }`; pass `cursor` back in the next call. `take` × pages can hit the daily quota fast on wide searches — cap your loops.
16. **`ID` is the join key.** Pass `CachedSearchData.ID` (NOT `RouteID`) into `/trips/{id}`.

---

## 8. Sources

- Seats.aero Developer Hub: <https://developers.seats.aero/>
- Pro API access & limits: <https://docs.seats.aero/article/68-seatsaero-pro-api-access-limits-and-usage>
- Cached Search reference: <https://developers.seats.aero/reference/cached-search>
- Bulk Availability reference: <https://developers.seats.aero/reference/get-availability>
- Get Trips reference: <https://developers.seats.aero/reference/get-trips>
- Get Routes reference: <https://developers.seats.aero/reference/get-routes-1>
- Live Search reference: <https://developers.seats.aero/reference/live-search>
- LLM-friendly index: <https://developers.seats.aero/llms.txt>
- TypeScript MCP client (response shape & header confirmation): <https://github.com/gavgrego/seats.aero-mcp-server>
- Go unofficial client (struct definitions): <https://github.com/denverquane/seats-aero-go>
