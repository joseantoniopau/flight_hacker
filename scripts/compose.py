"""
compose.py — Travel-hacking composition tree.

Takes a single (origin, destination, dates) query and fans out across
travel-hacking techniques (positioning, hidden-city, open-jaw, stopover),
returning a unified list of normalized itinerary dicts whose
`composition.*` fields describe how each variant was assembled.

Public API:
    compose(origin, destination, depart_date, return_date=None,
            cabin="economy", passengers=1,
            techniques=("positioning","hidden_city","open_jaw","stopover"),
            base_results=None, max_per_technique=5, search_cash_fn=None)
        -> list[dict]

    positioning_candidates(origin_iata, max_ground_minutes=240,
                           max_ground_cost_usd=80) -> list[tuple]

    open_jaw_candidates(dest_iata, max_ground_minutes=180) -> list[tuple]

    hidden_city_beyonds(dest_iata) -> list[str]

    stopover_program_allows(program_name) -> bool

    rank_compositions(rows, weights=None) -> list[dict]

CLI:
    python compose.py --origin JFK --dest CDG --depart 2026-09-01 \
        --return 2026-09-10 --cabin economy \
        --techniques positioning,hidden_city,open_jaw,stopover
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable

# Make sibling imports work whether run as a script or imported as a module.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from common import load_data, log, make_empty_itinerary, normalize_iata  # noqa: E402

try:
    import search_cash as _search_cash_module  # noqa: E402
except Exception as _exc:  # pragma: no cover — defensive only
    _search_cash_module = None
    log("compose_import_error", module="search_cash", error=str(_exc))

try:
    import search_award as _search_award_module  # noqa: E402
except Exception as _exc:  # pragma: no cover — defensive only
    _search_award_module = None
    log("compose_import_error", module="search_award", error=str(_exc))


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_TOTAL_SECONDS = 60
TECHNIQUE_TIMEOUT_SECONDS = 45
POSITIONING_MAX_WORKERS = 6
OPEN_JAW_MAX_WORKERS = 4
HIDDEN_CITY_MAX_WORKERS = 4
DEFAULT_MAX_STOPS = 2

HIDDEN_CITY_NOTE = (
    "Hidden-city ticket. No checked bags (will fly to final destination). "
    "No return segment usable (entire PNR voids on no-show). "
    "Do not enter frequent-flyer number. May void status. "
    "Airline ToS violation — repeat use risks account closure."
)

# Curated beyond-airport map for hidden-city construction.
# Maps destination IATA -> list of plausible "beyond" airports that the same
# carriers routinely sell cheap fares to via that destination as a layover.
HIDDEN_CITY_BEYOND_MAP: dict[str, list[str]] = {
    # North America
    "LAX": ["LAS", "SAN", "PSP", "SJC", "SMF", "RNO"],
    "SFO": ["LAS", "SJC", "SMF", "RNO", "PDX"],
    "SEA": ["BOI", "PDX", "GEG", "SMF"],
    "JFK": ["BOS", "BUF", "PWM", "BTV", "ALB"],
    "LGA": ["BOS", "BUF", "PWM", "ROC"],
    "EWR": ["BOS", "BDL", "PWM"],
    "BOS": ["PWM", "BGR", "BTV", "MHT"],
    "ORD": ["MKE", "MSN", "GRR", "DSM"],
    "DFW": ["AUS", "OKC", "ABQ"],
    "DEN": ["ABQ", "COS", "BOI", "SLC"],
    "ATL": ["GSP", "BHM", "CHA", "TLH"],
    "MIA": ["RSW", "EYW", "TPA", "FLL"],
    "PHX": ["TUS", "LAS", "ABQ"],
    "LAS": ["BUR", "ONT", "PSP", "SAN"],
    "IAD": ["RIC", "PIT", "ORF"],
    "IAH": ["AUS", "SAT", "MSY"],
    # Europe
    "LHR": ["EDI", "MAN", "GLA", "DUB", "ABZ"],
    "LGW": ["EDI", "MAN", "GLA", "DUB"],
    "CDG": ["NCE", "MRS", "TLS", "BOD", "LYS"],
    "ORY": ["NCE", "MRS", "TLS", "LYS"],
    "FRA": ["MUC", "HAM", "BER", "STR", "DUS"],
    "MUC": ["HAM", "BER", "DUS", "VIE"],
    "AMS": ["EIN", "BRU", "CGN", "DUS"],
    "MAD": ["BCN", "VLC", "LIS", "OPO"],
    "BCN": ["VLC", "PMI", "MAD"],
    "FCO": ["CAI", "ATH", "CTA", "NAP"],
    "VIE": ["BUD", "PRG", "KSC", "LJU"],
    "ZRH": ["GVA", "MXP", "LIN"],
    "IST": ["SAW", "ESB", "AYT", "TLV"],
    # Asia / Oceania / ME
    "NRT": ["KIX", "ITM", "FUK", "CTS"],
    "HND": ["KIX", "ITM", "FUK", "OKA"],
    "ICN": ["PUS", "CJU", "TAE"],
    "HKG": ["TPE", "KHH", "MFM"],
    "SIN": ["KUL", "BKK", "CGK"],
    "BKK": ["DMK", "HKT", "CNX", "USM"],
    "SYD": ["MEL", "BNE", "OOL", "CBR"],
    "MEL": ["SYD", "ADL", "OOL", "CBR"],
    "DXB": ["AUH", "SHJ", "DOH", "MCT"],
    "DOH": ["DXB", "BAH", "MCT", "RUH"],
}


# ---------------------------------------------------------------------------
# Data accessors (lazy + cached)
# ---------------------------------------------------------------------------

_HUBS_BY_IATA: dict[str, dict] | None = None
_STOPOVERS_DATA: dict | None = None
_SWEET_SPOTS_DATA: dict | None = None


def _hubs_by_iata() -> dict[str, dict]:
    global _HUBS_BY_IATA
    if _HUBS_BY_IATA is None:
        raw = load_data("airport_hubs") or {}
        idx: dict[str, dict] = {}
        for region_airports in (raw.get("regions") or {}).values():
            if not isinstance(region_airports, list):
                continue
            for ap in region_airports:
                code = (ap.get("iata") or "").upper()
                if code:
                    idx[code] = ap
        _HUBS_BY_IATA = idx
    return _HUBS_BY_IATA


def _stopovers() -> dict:
    global _STOPOVERS_DATA
    if _STOPOVERS_DATA is None:
        _STOPOVERS_DATA = load_data("stopovers") or {}
    return _STOPOVERS_DATA


def _sweet_spots() -> dict:
    global _SWEET_SPOTS_DATA
    if _SWEET_SPOTS_DATA is None:
        _SWEET_SPOTS_DATA = load_data("sweet_spots") or {}
    return _SWEET_SPOTS_DATA


# ---------------------------------------------------------------------------
# Candidate generators
# ---------------------------------------------------------------------------

def positioning_candidates(
    origin_iata: str,
    max_ground_minutes: int = 240,
    max_ground_cost_usd: int = 80,
) -> list[tuple[str, int, float]]:
    """Return [(hub_iata, ground_minutes, ground_cost_usd), ...] for positioning.

    Pulls from airport_hubs.json `nearby_hubs` entries on the origin airport.
    """
    origin = normalize_iata(origin_iata)
    ap = _hubs_by_iata().get(origin)
    if not ap:
        return []
    out: list[tuple[str, int, float]] = []
    for nb in ap.get("nearby_hubs") or []:
        try:
            hub_code = (nb.get("iata") or "").upper()
            mins = int(nb.get("ground_minutes") or 0)
            cost = float(nb.get("ground_cost_usd") or 0)
        except Exception:
            continue
        if not hub_code:
            continue
        if mins and mins > max_ground_minutes:
            continue
        if cost and cost > max_ground_cost_usd:
            continue
        out.append((hub_code, mins, cost))
    return out


def open_jaw_candidates(
    dest_iata: str,
    max_ground_minutes: int = 180,
) -> list[tuple[str, int, float]]:
    """Return nearby-hub airports usable as alternate return points."""
    dest = normalize_iata(dest_iata)
    ap = _hubs_by_iata().get(dest)
    if not ap:
        return []
    out: list[tuple[str, int, float]] = []
    for nb in ap.get("nearby_hubs") or []:
        try:
            hub_code = (nb.get("iata") or "").upper()
            mins = int(nb.get("ground_minutes") or 0)
            cost = float(nb.get("ground_cost_usd") or 0)
        except Exception:
            continue
        if not hub_code:
            continue
        if mins and mins > max_ground_minutes:
            continue
        out.append((hub_code, mins, cost))
    return out


def hidden_city_beyonds(dest_iata: str) -> list[str]:
    """Plausible 'beyond' airports for hidden-city construction.

    Uses curated HIDDEN_CITY_BEYOND_MAP, with metro-area alternates from
    airport_hubs.json excluded (those aren't truly 'beyond'; they're alternate
    metro airports the airline wouldn't price as a further-out connection).
    """
    dest = normalize_iata(dest_iata)
    base = list(HIDDEN_CITY_BEYOND_MAP.get(dest, []))
    nearby = {
        (nb.get("iata") or "").upper()
        for nb in (_hubs_by_iata().get(dest, {}).get("nearby_hubs") or [])
    }
    return [b for b in base if b and b != dest and b not in nearby]


def stopover_program_allows(program_name: str) -> bool:
    """True if the loyalty program has a free or cheap stopover policy."""
    if not program_name:
        return False
    p = program_name.lower().strip()
    for entry in (_stopovers().get("allowed") or []):
        name = (entry.get("program") or "").lower()
        if not name:
            continue
        if name == p or name in p or p in name:
            return True
    return False


def _stopover_entry_for(program_name: str) -> dict | None:
    if not program_name:
        return None
    p = program_name.lower().strip()
    for entry in (_stopovers().get("allowed") or []):
        name = (entry.get("program") or "").lower()
        if not name:
            continue
        if name == p or name in p or p in name:
            return entry
    return None


# ---------------------------------------------------------------------------
# Helper — cheapest valid itinerary
# ---------------------------------------------------------------------------

def _is_real_offer(it: dict) -> bool:
    return (
        isinstance(it, dict)
        and "error" not in it
        and it.get("price_usd") is not None
    )


def _cheapest(rows: Iterable[dict]) -> dict | None:
    best: dict | None = None
    best_price = float("inf")
    for r in rows or []:
        if not _is_real_offer(r):
            continue
        price = float(r["price_usd"])
        if price < best_price:
            best_price = price
            best = r
    return best


def _resolve_search_fn(injected: Callable | None) -> Callable | None:
    if injected is not None:
        return injected
    if _search_cash_module is not None:
        return getattr(_search_cash_module, "search", None)
    return None


def _safe_search(
    search_fn: Callable,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    cabin: str,
    passengers: int,
    max_stops: int = DEFAULT_MAX_STOPS,
) -> list[dict]:
    """Call the cash-search function with defensive error handling."""
    try:
        return search_fn(
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            return_date=return_date,
            cabin=cabin,
            adults=int(passengers),
            max_stops=max_stops,
        ) or []
    except TypeError:
        # Fallback for callers with a leaner signature.
        try:
            return search_fn(origin, destination, depart_date, return_date, cabin) or []
        except Exception as exc:
            log("compose_search_error", origin=origin, dest=destination, error=str(exc))
            return []
    except Exception as exc:
        log("compose_search_error", origin=origin, dest=destination, error=str(exc))
        return []


# ---------------------------------------------------------------------------
# Technique: positioning
# ---------------------------------------------------------------------------

def _positioning_one(
    search_fn: Callable,
    origin: str,
    hub: str,
    ground_minutes: int,
    ground_cost_usd: float,
    destination: str,
    depart_date: str,
    return_date: str | None,
    cabin: str,
    passengers: int,
) -> dict | None:
    """Build one positioning variant for `hub`."""
    try:
        main_results = _safe_search(
            search_fn, hub, destination, depart_date, return_date, cabin, passengers
        )
        main = _cheapest(main_results)
        if not main:
            return None

        # Try positioning by flight too — if cheaper than the ground transfer,
        # prefer flight. Use a one-way same-day cash search.
        flight_pos = None
        try:
            pos_results = _safe_search(
                search_fn, origin, hub, depart_date, None, cabin, passengers
            )
            flight_pos = _cheapest(pos_results)
        except Exception as exc:
            log("compose_positioning_flight_search_error",
                origin=origin, hub=hub, error=str(exc))

        extra_cost = float(ground_cost_usd or 0)
        extra_time = int(ground_minutes or 0)
        positioning_mode = "ground"

        if flight_pos and flight_pos.get("price_usd") is not None:
            flight_price = float(flight_pos["price_usd"])
            # Prefer flight when it beats ground (or ground is unknown).
            if flight_price <= extra_cost or extra_cost <= 0:
                positioning_mode = "flight"
                extra_cost = flight_price
                extra_time = int(flight_pos.get("duration_minutes") or extra_time or 120)
                positioning_leg = {
                    "kind": "positioning_flight",
                    "from": origin,
                    "to": hub,
                    "carrier": flight_pos.get("carrier"),
                    "duration_minutes": flight_pos.get("duration_minutes"),
                    "price_usd": flight_price,
                    "source": flight_pos.get("source"),
                }
            else:
                positioning_leg = {
                    "kind": "ground_transfer",
                    "from": origin,
                    "to": hub,
                    "duration_minutes": ground_minutes,
                    "price_usd": float(ground_cost_usd or 0),
                }
        else:
            positioning_leg = {
                "kind": "ground_transfer",
                "from": origin,
                "to": hub,
                "duration_minutes": ground_minutes,
                "price_usd": float(ground_cost_usd or 0),
            }

        main_leg = {
            "kind": "main_flight",
            "from": hub,
            "to": destination,
            "carrier": main.get("carrier"),
            "duration_minutes": main.get("duration_minutes"),
            "price_usd": main.get("price_usd"),
            "stops": main.get("stops"),
            "source": main.get("source"),
        }

        composed = copy.deepcopy(main)
        # Re-stamp identifying fields so this row represents the full trip.
        composed["origin"] = origin  # caller's true origin
        composed["destination"] = destination
        # IMPORTANT: keep `price_usd` as the BASE (main flight only). The
        # ranker (rank.py:_row_total_cost) adds composition.extra_cost_usd to
        # produce `total_cost_usd`. If we baked the extra into `price_usd` here,
        # the ground/positioning cost would be double-counted.
        try:
            composed["price_usd"] = float(main["price_usd"])
        except Exception:
            composed["price_usd"] = main.get("price_usd")
        composed["composition"] = {
            "type": "positioning",
            "legs": [positioning_leg, main_leg],
            "extra_cost_usd": float(extra_cost),
            "extra_time_minutes": int(extra_time or 0),
            "risk": "LEGAL",
            "notes": (
                f"Position from {origin} -> {hub} ({positioning_mode}), "
                f"then fly {hub} -> {destination}. "
                f"Ground cost ${float(ground_cost_usd or 0):.0f}, "
                f"ground time {int(ground_minutes or 0)} min."
            ),
            "positioning_mode": positioning_mode,
            "hub": hub,
        }
        return composed
    except Exception as exc:
        log("compose_positioning_error", hub=hub, error=str(exc),
            tb=traceback.format_exc(limit=2))
        return None


def _compose_positioning(
    search_fn: Callable,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    cabin: str,
    passengers: int,
    max_results: int,
) -> list[dict]:
    cands = positioning_candidates(origin)
    if not cands:
        log("compose_positioning_no_candidates", origin=origin)
        return []
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=POSITIONING_MAX_WORKERS) as ex:
        futs = {
            ex.submit(
                _positioning_one,
                search_fn,
                origin,
                hub,
                mins,
                cost,
                destination,
                depart_date,
                return_date,
                cabin,
                passengers,
            ): hub
            for (hub, mins, cost) in cands
        }
        for fut in as_completed(futs):
            try:
                row = fut.result(timeout=TECHNIQUE_TIMEOUT_SECONDS)
            except Exception as exc:
                log("compose_positioning_future_error",
                    hub=futs[fut], error=str(exc))
                continue
            if row:
                out.append(row)
    out.sort(key=lambda r: (r.get("price_usd") or float("inf")))
    return out[:max_results]


# ---------------------------------------------------------------------------
# Technique: hidden-city
# ---------------------------------------------------------------------------

# Carrier → likely hub airports map. Used by the hidden-city composer when the
# upstream search (e.g. fast-flights) returns synthesized single-segment rows
# and we have no real per-leg data. If a 1-stop fare on carrier X plausibly
# connects through one of X's hubs AND the user's destination is one of those
# hubs, that fare is a hidden-city candidate (confidence: low).
_CARRIER_HUBS: dict[str, list[str]] = {
    # US legacy
    "AA": ["DFW", "CLT", "PHL", "MIA", "ORD", "PHX", "LAX", "JFK", "DCA"],
    "American Airlines": ["DFW", "CLT", "PHL", "MIA", "ORD", "PHX", "LAX", "JFK", "DCA"],
    "American": ["DFW", "CLT", "PHL", "MIA", "ORD", "PHX", "LAX", "JFK", "DCA"],
    "UA": ["ORD", "IAH", "EWR", "DEN", "SFO", "IAD", "LAX"],
    "United": ["ORD", "IAH", "EWR", "DEN", "SFO", "IAD", "LAX"],
    "United Airlines": ["ORD", "IAH", "EWR", "DEN", "SFO", "IAD", "LAX"],
    "DL": ["ATL", "DTW", "MSP", "LAX", "JFK", "SEA", "SLC", "BOS"],
    "Delta": ["ATL", "DTW", "MSP", "LAX", "JFK", "SEA", "SLC", "BOS"],
    "Delta Air Lines": ["ATL", "DTW", "MSP", "LAX", "JFK", "SEA", "SLC", "BOS"],
    "AS": ["SEA", "PDX", "ANC", "LAX", "SFO"],
    "Alaska": ["SEA", "PDX", "ANC", "LAX", "SFO"],
    "Alaska Airlines": ["SEA", "PDX", "ANC", "LAX", "SFO"],
    "B6": ["JFK", "BOS", "FLL"],
    "JetBlue": ["JFK", "BOS", "FLL"],
    "WN": ["MDW", "LAS", "DAL", "BWI", "STL"],
    "Southwest": ["MDW", "LAS", "DAL", "BWI", "STL"],
    # Foreign flag carriers
    "BA": ["LHR", "LGW"],
    "British Airways": ["LHR", "LGW"],
    "LH": ["FRA", "MUC"],
    "Lufthansa": ["FRA", "MUC"],
    "AF": ["CDG"],
    "Air France": ["CDG"],
    "KL": ["AMS"],
    "KLM": ["AMS"],
    "EK": ["DXB"],
    "Emirates": ["DXB"],
    "QR": ["DOH"],
    "Qatar Airways": ["DOH"],
    "TK": ["IST"],
    "Turkish Airlines": ["IST"],
    "EY": ["AUH"],
    "Etihad": ["AUH"],
    "NH": ["NRT", "HND"],
    "ANA": ["NRT", "HND"],
    "JL": ["NRT", "HND"],
    "JAL": ["NRT", "HND"],
    "Japan Airlines": ["NRT", "HND"],
    "SQ": ["SIN"],
    "Singapore Airlines": ["SIN"],
    "CX": ["HKG"],
    "Cathay": ["HKG"],
    "Cathay Pacific": ["HKG"],
    "AC": ["YYZ", "YUL", "YVR"],
    "Air Canada": ["YYZ", "YUL", "YVR"],
    "IB": ["MAD"],
    "Iberia": ["MAD"],
}


def _carrier_likely_hubs(carrier: str | None) -> list[str]:
    if not carrier:
        return []
    return _CARRIER_HUBS.get(carrier, [])


def _hidden_city_one(
    search_fn: Callable,
    origin: str,
    destination: str,
    beyond: str,
    depart_date: str,
    cabin: str,
    passengers: int,
    benchmark_price: float | None,
) -> list[dict]:
    """Search origin->beyond and post-filter to routings via destination.

    Strategy: prefer rows with real multi-segment data where the first segment's
    'to' equals our desired destination. When the upstream source (e.g.
    fast-flights) only returns synthesized single-segment rows, fall back to
    a carrier-hub heuristic: a 1-stop fare on a carrier whose hubs include
    `destination` is plausibly routing through `destination` (confidence: low).
    """
    out: list[dict] = []
    try:
        # Always one-way for hidden-city — there is no usable return segment
        # once you walk away at the intentional layover.
        rows = _safe_search(
            search_fn, origin, beyond, depart_date, None, cabin, passengers,
            max_stops=2,
        )
        for r in rows:
            if not _is_real_offer(r):
                continue
            segs = r.get("segments") or []
            price = r.get("price_usd")
            if price is None:
                continue
            # Only keep if cheaper than the direct origin->destination benchmark.
            if benchmark_price is not None and float(price) >= float(benchmark_price):
                continue

            confidence = "high"
            has_real_segments = len(segs) >= 2 and any(
                (s.get("to") or "").upper() == destination.upper() for s in segs
            )
            if has_real_segments:
                # Real multi-segment data — confirm first hop goes to destination.
                first = segs[0] or {}
                if (first.get("to") or "").upper() != destination.upper():
                    continue
            else:
                # Heuristic path: row only has synthesized segments. Accept if
                # the row has stops >= 1 AND the carrier's hubs include destination.
                stops = r.get("stops")
                if stops is None or stops < 1:
                    continue
                hubs = _carrier_likely_hubs(r.get("carrier"))
                if destination.upper() not in [h.upper() for h in hubs]:
                    continue
                confidence = "low"

            hidden = copy.deepcopy(r)
            hidden["destination"] = destination
            # Keep only segments up to (and including) destination — anything
            # after is the deliberately-discarded segment.
            kept_segs: list[dict] = []
            if has_real_segments:
                for s in segs:
                    kept_segs.append(s)
                    if (s.get("to") or "").upper() == destination.upper():
                        break
            else:
                # Heuristic path — synthesize one leg origin->destination,
                # the actual layover/hub is unknown, so we set 'to' to dest.
                kept_segs = [{
                    "carrier": r.get("carrier"),
                    "flight_no": None,
                    "from": origin,
                    "to": destination,
                    "depart": None,
                    "arrive": None,
                    "duration_minutes": None,
                    "aircraft": None,
                }]
            hidden["segments"] = kept_segs
            hidden["stops"] = max(0, len(kept_segs) - 1)
            hidden["return_date"] = None
            hidden["composition"] = {
                "type": "hidden_city",
                "confidence": confidence,
                "legs": [
                    {
                        "kind": "ticketed_to",
                        "from": origin,
                        "to": beyond,
                        "carrier": r.get("carrier"),
                        "price_usd": r.get("price_usd"),
                        "source": r.get("source"),
                    },
                    {
                        "kind": "hidden_exit",
                        "at": destination,
                        "discarded_segment_to": beyond,
                    },
                ],
                "extra_cost_usd": 0.0,
                "extra_time_minutes": 0,
                "risk": "TOS-RISK",
                "notes": HIDDEN_CITY_NOTE,
                "beyond_airport": beyond,
                "benchmark_price_usd": benchmark_price,
            }
            out.append(hidden)
    except Exception as exc:
        log("compose_hidden_city_one_error",
            beyond=beyond, error=str(exc),
            tb=traceback.format_exc(limit=2))
    return out


def _compose_hidden_city(
    search_fn: Callable,
    origin: str,
    destination: str,
    depart_date: str,
    cabin: str,
    passengers: int,
    max_results: int,
    benchmark_price: float | None,
) -> list[dict]:
    if search_fn is None:
        log("compose_hidden_city_skipped", reason="no_search_fn",
            dest=destination)
        return []
    if benchmark_price is None:
        # Without a direct origin->destination cash benchmark we cannot judge
        # whether a hidden-city ticket actually saves money. Log + skip rather
        # than emitting unfiltered hidden-city rows that may be more expensive
        # than the direct fare.
        log("compose_hidden_city_skipped", reason="no_benchmark_price",
            origin=origin, dest=destination)
        return []
    beyonds = hidden_city_beyonds(destination)
    if not beyonds:
        log("compose_hidden_city_no_beyonds", dest=destination)
        return []
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=HIDDEN_CITY_MAX_WORKERS) as ex:
        futs = {
            ex.submit(
                _hidden_city_one,
                search_fn,
                origin,
                destination,
                beyond,
                depart_date,
                cabin,
                passengers,
                benchmark_price,
            ): beyond
            for beyond in beyonds[:8]  # cap fan-out so we stay inside budget
        }
        for fut in as_completed(futs):
            try:
                rows = fut.result(timeout=TECHNIQUE_TIMEOUT_SECONDS)
            except Exception as exc:
                log("compose_hidden_city_future_error",
                    beyond=futs[fut], error=str(exc))
                continue
            out.extend(rows or [])
    out.sort(key=lambda r: (r.get("price_usd") or float("inf")))
    return out[:max_results]


# ---------------------------------------------------------------------------
# Technique: open-jaw
# ---------------------------------------------------------------------------

def _open_jaw_one(
    search_fn: Callable,
    origin: str,
    destination: str,
    alt_return: str,
    ground_minutes: int,
    ground_cost_usd: float,
    depart_date: str,
    return_date: str,
    cabin: str,
    passengers: int,
) -> dict | None:
    try:
        # Two one-ways: origin -> destination (depart_date),
        # then          alt_return -> origin (return_date).
        outbound_rows = _safe_search(
            search_fn, origin, destination, depart_date, None, cabin, passengers
        )
        inbound_rows = _safe_search(
            search_fn, alt_return, origin, return_date, None, cabin, passengers
        )
        outbound = _cheapest(outbound_rows)
        inbound = _cheapest(inbound_rows)
        if not outbound or not inbound:
            return None
        out_price = float(outbound["price_usd"])
        in_price = float(inbound["price_usd"])
        ground = float(ground_cost_usd or 0)
        # `price_usd` is the BASE cash sum of the two flights only. Ground
        # transfer goes into `composition.extra_cost_usd` and is added to the
        # base by rank.py:_row_total_cost. Adding it here too would
        # double-count.
        base_price = out_price + in_price

        composed = make_empty_itinerary()
        composed.update({
            "source": f"{outbound.get('source')}+{inbound.get('source')}",
            "kind": "cash",
            "origin": origin,
            "destination": destination,
            "depart_date": depart_date,
            "return_date": return_date,
            "carrier": outbound.get("carrier"),
            "carriers_all": list(filter(None, list(outbound.get("carriers_all") or []) +
                                        list(inbound.get("carriers_all") or []))),
            "cabin": cabin,
            "stops": max(int(outbound.get("stops") or 0), int(inbound.get("stops") or 0)),
            "duration_minutes": (
                (outbound.get("duration_minutes") or 0)
                + (inbound.get("duration_minutes") or 0)
            ) or None,
            "price_usd": base_price,
            "currency": outbound.get("currency") or "USD",
            "segments": (outbound.get("segments") or []) + (inbound.get("segments") or []),
            "raw": {
                "outbound": outbound.get("raw"),
                "inbound": inbound.get("raw"),
            },
        })
        composed["composition"] = {
            "type": "open_jaw",
            "legs": [
                {
                    "kind": "outbound",
                    "from": origin,
                    "to": destination,
                    "depart_date": depart_date,
                    "price_usd": out_price,
                    "carrier": outbound.get("carrier"),
                },
                {
                    "kind": "ground_transfer",
                    "from": destination,
                    "to": alt_return,
                    "duration_minutes": ground_minutes,
                    "price_usd": ground,
                },
                {
                    "kind": "inbound",
                    "from": alt_return,
                    "to": origin,
                    "depart_date": return_date,
                    "price_usd": in_price,
                    "carrier": inbound.get("carrier"),
                },
            ],
            "extra_cost_usd": ground,
            "extra_time_minutes": int(ground_minutes or 0),
            "risk": "LEGAL",
            "notes": (
                f"Open-jaw return: outbound {origin}->{destination}, "
                f"ground {destination}->{alt_return} ({ground_minutes} min, "
                f"${ground:.0f}), inbound {alt_return}->{origin}."
            ),
            "alt_return_airport": alt_return,
        }
        return composed
    except Exception as exc:
        log("compose_open_jaw_error",
            alt_return=alt_return, error=str(exc),
            tb=traceback.format_exc(limit=2))
        return None


def _compose_open_jaw(
    search_fn: Callable,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    cabin: str,
    passengers: int,
    max_results: int,
) -> list[dict]:
    if not return_date:
        log("compose_open_jaw_skipped", reason="no_return_date")
        return []
    cands = open_jaw_candidates(destination)
    if not cands:
        log("compose_open_jaw_no_candidates", dest=destination)
        return []
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=OPEN_JAW_MAX_WORKERS) as ex:
        futs = {
            ex.submit(
                _open_jaw_one,
                search_fn,
                origin,
                destination,
                alt_return,
                mins,
                cost,
                depart_date,
                return_date,
                cabin,
                passengers,
            ): alt_return
            for (alt_return, mins, cost) in cands
        }
        for fut in as_completed(futs):
            try:
                row = fut.result(timeout=TECHNIQUE_TIMEOUT_SECONDS)
            except Exception as exc:
                log("compose_open_jaw_future_error",
                    alt_return=futs[fut], error=str(exc))
                continue
            if row:
                out.append(row)
    out.sort(key=lambda r: (r.get("price_usd") or float("inf")))
    return out[:max_results]


# ---------------------------------------------------------------------------
# Technique: stopover (annotation-only)
# ---------------------------------------------------------------------------

# Program-anchor -> preferred hub city for a free stopover where geography
# is restricted (Icelandair=KEF, TAP=LIS, Turkish=IST, etc.).
_PROGRAM_HUB_HINTS: dict[str, list[str]] = {
    "icelandair": ["KEF"],
    "tap air portugal": ["LIS", "OPO"],
    "turkish airlines": ["IST"],
    "emirates": ["DXB"],
    "etihad": ["AUH"],
    "qatar airways": ["DOH"],
    "copa": ["PTY"],
    "singapore airlines": ["SIN"],
    "ana": ["HND", "NRT"],
    "japan airlines": ["HND", "NRT"],
    "korean air": ["ICN"],
    "cathay pacific": ["HKG"],
    "lufthansa": ["FRA", "MUC"],
    "air france-klm": ["CDG", "AMS"],
    "air canada aeroplan": ["YYZ", "YVR", "YUL"],
    "alaska": ["SEA", "ANC"],
    "avianca": ["BOG", "SAL"],
}


def _stopover_hub_for(program_name: str) -> str | None:
    if not program_name:
        return None
    p = program_name.lower()
    # Try both directions: short program names (e.g. "Aeroplan") should match
    # longer entries ("air canada aeroplan"), and vice versa. Also try matching
    # individual words so e.g. "Lufthansa Miles & More" matches "lufthansa".
    for k, hubs in _PROGRAM_HUB_HINTS.items():
        if k in p or p in k:
            return hubs[0]
    p_tokens = {tok for tok in p.replace("-", " ").split() if len(tok) >= 4}
    for k, hubs in _PROGRAM_HUB_HINTS.items():
        k_tokens = {tok for tok in k.replace("-", " ").split() if len(tok) >= 4}
        if p_tokens & k_tokens:
            return hubs[0]
    # Otherwise pick the first airport_hubs.json hub whose long-haul carrier
    # list overlaps with the program string.
    for code, ap in _hubs_by_iata().items():
        for name in (ap.get("major_long_haul_carriers") or []):
            if name and name.lower() in p:
                return code
    return None


def _fetch_award_base_results(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    cabin: str,
    passengers: int,
) -> list[dict]:
    """Fetch award rows via search_award.search() for the stopover branch.

    Returns a flat list of award rows. If `search_award.search` returns a dict
    `{"outbound": [...], "return": [...]}` (round-trip), the two lists are
    concatenated; `direction` is already stamped by search_award itself, so we
    leave it as-is (and only stamp it defensively if missing).

    Returns [] (with a warning) when:
      - the `search_award` module did not import (e.g. missing API key, missing
        dep), or
      - the `search()` call raises for any reason at run time.
    """
    if _search_award_module is None:
        log(
            "compose_stopover_award_unavailable",
            reason="search_award_import_failed",
            origin=origin,
            destination=destination,
        )
        return []

    award_search = getattr(_search_award_module, "search", None)
    if award_search is None:
        log(
            "compose_stopover_award_unavailable",
            reason="search_award_search_missing",
            origin=origin,
            destination=destination,
        )
        return []

    try:
        result = award_search(
            origin,
            destination,
            depart_date,
            return_date,
            cabins=(cabin,),
            passengers=int(passengers),
        )
    except Exception as exc:
        log(
            "compose_stopover_award_error",
            origin=origin,
            destination=destination,
            error=str(exc),
            tb=traceback.format_exc(limit=2),
        )
        return []

    if isinstance(result, dict):
        outbound = list(result.get("outbound") or [])
        ret = list(result.get("return") or [])
        for r in outbound:
            if isinstance(r, dict):
                r.setdefault("direction", "outbound")
        for r in ret:
            if isinstance(r, dict):
                r.setdefault("direction", "return")
        return outbound + ret
    if isinstance(result, list):
        return result
    log(
        "compose_stopover_award_unexpected_shape",
        type=type(result).__name__,
    )
    return []


def _compose_stopovers(
    base_rows: list[dict],
    max_results: int,
) -> list[dict]:
    """Annotate award rows whose program allows free stopovers."""
    if not base_rows:
        return []
    out: list[dict] = []
    for row in base_rows:
        if not isinstance(row, dict) or "error" in row:
            continue
        if row.get("kind") != "award":
            continue
        program = row.get("program")
        if not stopover_program_allows(program or ""):
            continue
        entry = _stopover_entry_for(program or "")
        stop_hub = _stopover_hub_for(program or "")
        annotated = copy.deepcopy(row)
        annotated.setdefault("composition", {})
        annotated["composition"] = {
            "type": "stopover",
            "legs": list(row.get("segments") or []),
            "extra_cost_usd": float((entry or {}).get("fee_usd_saver") or 0),
            "extra_time_minutes": 0,
            "risk": "LEGAL",
            "notes": (
                f"{program} permits a free stopover. "
                f"Rule: {(entry or {}).get('rule', 'see program docs')}. "
                f"Suggested stopover hub: {stop_hub or 'program-defined city'}. "
                f"Booking method: {(entry or {}).get('booking_method', 'unspecified')}."
            ),
            "program": program,
            "stopover_hub": stop_hub,
            "stopover_rule": (entry or {}).get("rule"),
            "stopover_max_days": (entry or {}).get("max_days"),
        }
        out.append(annotated)
        if len(out) >= max_results:
            break
    return out


# ---------------------------------------------------------------------------
# Public — compose()
# ---------------------------------------------------------------------------

def compose(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None = None,
    cabin: str = "economy",
    passengers: int = 1,
    techniques: tuple[str, ...] = ("positioning", "hidden_city", "open_jaw", "stopover"),
    base_results: list[dict] | None = None,
    max_per_technique: int = 5,
    search_cash_fn: Callable | None = None,
) -> list[dict]:
    """Compose itinerary variants across travel-hacking techniques.

    See module docstring for semantics. Returns a unified list of normalized
    itinerary dicts (composition.* populated on every row), ranked best-first.
    """
    origin = normalize_iata(origin)
    destination = normalize_iata(destination)

    search_fn = _resolve_search_fn(search_cash_fn)
    if search_fn is None and any(
        t in techniques for t in ("positioning", "hidden_city", "open_jaw")
    ):
        log("compose_no_search_fn", techniques=list(techniques))
        # Without a search fn the live techniques cannot run; only stopover
        # annotation remains useful.
        techniques = tuple(t for t in techniques if t == "stopover")

    # Direct results — either passed in or fetched once for benchmark/stopover.
    direct = list(base_results or [])
    if not direct and search_fn is not None:
        try:
            direct = _safe_search(
                search_fn, origin, destination, depart_date, return_date,
                cabin, passengers,
            )
        except Exception as exc:
            log("compose_base_search_error", error=str(exc))
            direct = []

    benchmark: float | None = None
    for r in direct:
        if _is_real_offer(r):
            p = r.get("price_usd")
            if p is not None and (benchmark is None or float(p) < benchmark):
                benchmark = float(p)

    started = time.time()
    composed_all: list[dict] = []

    # Each technique runs in its own thread so it can have its own time budget.
    def _run_positioning() -> list[dict]:
        return _compose_positioning(
            search_fn, origin, destination, depart_date, return_date,
            cabin, passengers, max_per_technique,
        )

    def _run_hidden_city() -> list[dict]:
        return _compose_hidden_city(
            search_fn, origin, destination, depart_date,
            cabin, passengers, max_per_technique, benchmark,
        )

    def _run_open_jaw() -> list[dict]:
        return _compose_open_jaw(
            search_fn, origin, destination, depart_date, return_date,
            cabin, passengers, max_per_technique,
        )

    def _run_stopover() -> list[dict]:
        # Stopover composer is a pure annotator over award rows. If the caller
        # didn't pre-fetch a usable `base_results`, we auto-fetch award rows
        # from search_award here — `direct` (the cash baseline) cannot drive
        # stopover annotation because stopover_program_allows() only matches
        # award rows.
        stop_base: list[dict] = [
            r for r in (direct or [])
            if isinstance(r, dict) and r.get("kind") == "award"
        ]
        if not stop_base:
            stop_base = _fetch_award_base_results(
                origin, destination, depart_date, return_date,
                cabin, passengers,
            )
        if not stop_base:
            log("compose_stopover_skipped", reason="no_award_base",
                origin=origin, destination=destination)
            return []
        return _compose_stopovers(stop_base, max_per_technique)

    runners: dict[str, Callable[[], list[dict]]] = {
        "positioning": _run_positioning,
        "hidden_city": _run_hidden_city,
        "open_jaw": _run_open_jaw,
        "stopover": _run_stopover,
    }
    selected = [t for t in techniques if t in runners]
    if not selected:
        return []

    with ThreadPoolExecutor(max_workers=max(1, len(selected))) as ex:
        futs = {ex.submit(runners[t]): t for t in selected}
        for fut in as_completed(futs):
            tname = futs[fut]
            elapsed = time.time() - started
            remaining = max(1.0, MAX_TOTAL_SECONDS - elapsed)
            try:
                rows = fut.result(timeout=remaining)
            except FutTimeout:
                log("compose_technique_timeout", technique=tname,
                    elapsed_s=round(elapsed, 1))
                continue
            except Exception as exc:
                log("compose_technique_error", technique=tname, error=str(exc),
                    tb=traceback.format_exc(limit=2))
                continue
            log("compose_technique_done", technique=tname,
                count=len(rows or []),
                elapsed_s=round(time.time() - started, 1))
            composed_all.extend(rows or [])

    return rank_compositions(composed_all)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS = {
    "cost": 1.0,             # $ per dollar of total price
    "time_minutes": 0.05,    # $ per minute of added ground/extra time
    "risk_penalty": {
        "LEGAL": 0.0,
        "GRAY": 75.0,
        "TOS-RISK": 200.0,
    },
}


def rank_compositions(
    rows: list[dict],
    weights: dict | None = None,
) -> list[dict]:
    """Annotate each row with a numeric `score` (lower=better). Returns sorted."""
    w = dict(_DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    risk_pen = w.get("risk_penalty") or _DEFAULT_WEIGHTS["risk_penalty"]
    cost_w = float(w.get("cost", 1.0))
    time_w = float(w.get("time_minutes", 0.05))

    out: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        comp = r.get("composition") or {}
        price = r.get("price_usd")
        try:
            base = float(price) if price is not None else 1e9
        except Exception:
            base = 1e9
        time_pen = time_w * float(comp.get("extra_time_minutes") or 0)
        risk = (comp.get("risk") or "LEGAL").upper()
        risk_p = float(risk_pen.get(risk, 0.0))
        score = cost_w * base + time_pen + risk_p
        r2 = copy.deepcopy(r)
        r2["score"] = round(score, 2)
        out.append(r2)
    out.sort(key=lambda x: (x.get("score") if x.get("score") is not None else float("inf")))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Compose travel-hacking variants for a single query.",
    )
    ap.add_argument("--origin", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--depart", required=True)
    ap.add_argument("--return", dest="return_date", default=None)
    ap.add_argument("--cabin", default="economy")
    ap.add_argument("--passengers", type=int, default=1)
    ap.add_argument(
        "--techniques",
        default="positioning,hidden_city,open_jaw,stopover",
        help="Comma-separated list",
    )
    ap.add_argument("--max-per-technique", type=int, default=5)
    ap.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only a per-technique count summary.",
    )
    return ap


def _summary(rows: list[dict]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for r in rows or []:
        comp = r.get("composition") or {}
        t = comp.get("type") or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
        risk = (comp.get("risk") or "LEGAL").upper()
        by_risk[risk] = by_risk.get(risk, 0) + 1
    cheapest = None
    for r in rows or []:
        p = r.get("price_usd")
        if p is None:
            continue
        try:
            p = float(p)
        except Exception:
            continue
        if cheapest is None or p < cheapest["price_usd"]:
            cheapest = {
                "price_usd": p,
                "type": (r.get("composition") or {}).get("type"),
                "origin": r.get("origin"),
                "destination": r.get("destination"),
            }
    return {
        "total": len(rows or []),
        "by_technique": by_type,
        "by_risk": by_risk,
        "cheapest": cheapest,
    }


def _cli(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    techs = tuple(t.strip() for t in args.techniques.split(",") if t.strip())
    rows = compose(
        origin=args.origin,
        destination=args.dest,
        depart_date=args.depart,
        return_date=args.return_date,
        cabin=args.cabin,
        passengers=args.passengers,
        techniques=techs,
        max_per_technique=args.max_per_technique,
    )
    if args.summary_only:
        print(json.dumps(_summary(rows), indent=2, default=str))
    else:
        print(json.dumps(rows, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Smoke test (covers all four techniques, hermetic — no network)
# ---------------------------------------------------------------------------

def _smoke_test() -> int:
    print("# compose.py smoke test", file=sys.stderr)

    def fake_search(*, origin, destination, depart_date, return_date=None,
                    cabin="economy", adults=1, max_stops=2):
        # Two-segment routings for some pairs so the hidden-city post-filter
        # has something to match. Otherwise single segment.
        if destination in ("LAS", "BOI", "NCE", "MRS") and origin in (
                "JFK", "SEA", "CDG", "LGA", "EWR", "BOS", "PHL"):
            via = "CDG" if origin in ("JFK", "LGA", "EWR", "BOS", "PHL") else "LAX"
            segs = [
                {"carrier": "TT", "flight_no": "1",
                 "from": origin, "to": via,
                 "depart": f"{depart_date}T08:00",
                 "arrive": f"{depart_date}T10:00",
                 "duration_minutes": 120, "aircraft": None},
                {"carrier": "TT", "flight_no": "2",
                 "from": via, "to": destination,
                 "depart": f"{depart_date}T12:00",
                 "arrive": f"{depart_date}T14:30",
                 "duration_minutes": 150, "aircraft": None},
            ]
        else:
            segs = [{
                "carrier": "TT", "flight_no": "1",
                "from": origin, "to": destination,
                "depart": f"{depart_date}T10:00",
                "arrive": f"{depart_date}T13:00",
                "duration_minutes": 180,
                "aircraft": None,
            }]
        # Deterministic-but-varied prices: base on string lengths so that
        # different (origin, dest) pairs land at different price points.
        return [{
            "source": "fake",
            "kind": "cash",
            "origin": origin, "destination": destination,
            "depart_date": depart_date, "return_date": return_date,
            "carrier": "TT", "carriers_all": ["TT"],
            "cabin": cabin, "stops": max(0, len(segs) - 1),
            "duration_minutes": sum(s["duration_minutes"] for s in segs),
            "price_usd": 199.0 + len(destination) + len(origin),
            "currency": "USD",
            "segments": segs,
            "raw": {},
        }]

    # Award row to drive the stopover annotator.
    base_award = [{
        "source": "seats.aero", "kind": "award",
        "origin": "JFK", "destination": "CDG",
        "depart_date": "2026-09-01", "return_date": "2026-09-10",
        "program": "Air Canada Aeroplan",
        "carrier": "AC", "carriers_all": ["AC"],
        "cabin": "business", "stops": 1,
        "duration_minutes": 480,
        "price_usd": None, "miles": 70000, "taxes_usd": 120.0,
        "segments": [
            {"carrier": "AC", "flight_no": "85",
             "from": "JFK", "to": "YYZ",
             "depart": "2026-09-01T18:00",
             "arrive": "2026-09-01T20:00",
             "duration_minutes": 120, "aircraft": "B788"},
            {"carrier": "AC", "flight_no": "880",
             "from": "YYZ", "to": "CDG",
             "depart": "2026-09-01T22:00",
             "arrive": "2026-09-02T11:30",
             "duration_minutes": 450, "aircraft": "B789"},
        ],
        "raw": {},
    }]

    rows = compose(
        origin="JFK",
        destination="CDG",
        depart_date="2026-09-01",
        return_date="2026-09-10",
        cabin="economy",
        techniques=("positioning", "hidden_city", "open_jaw", "stopover"),
        base_results=base_award,
        max_per_technique=4,
        search_cash_fn=fake_search,
    )
    summary = _summary(rows)
    print(json.dumps({"smoke_summary": summary}, indent=2, default=str))
    required = {"positioning", "open_jaw", "stopover"}
    missing = required - set(summary.get("by_technique") or {})
    if missing:
        print(f"SMOKE FAIL: missing techniques {missing}", file=sys.stderr)
        return 2
    print("SMOKE OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    # No CLI args -> hermetic smoke test; otherwise CLI mode.
    if len(sys.argv) <= 1:
        sys.exit(_smoke_test())
    sys.exit(_cli())
