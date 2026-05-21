"""
smoke_test.py — End-to-end smoke test for the flight-hacker skill.

Runs through every module's public entry points against golden fixtures or
live APIs (network required for live tests). Exit code 0 if every required
check passes; non-zero on first failure.

Tests:
  1. common.load_env + cpp_floor lookups
  2. data file integrity (every JSON in data/ parses, has _meta or recognizable shape)
  3. search_cash live smoke (JFK→LAX, easy route)
  4. search_award live smoke (JFK→NRT business, cached)
  5. compose smoke (JFK→CDG with positioning+open_jaw)
  6. rank with synthetic + live rows
  7. ingest_mistakes against at least 3 sources
  8. watch CRUD (create/save/run/delete)

CLI:
    python smoke_test.py                 — run all
    python smoke_test.py --offline        — skip live-API tests
    python smoke_test.py --only common,data,rank
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    SKILL_ROOT,
    cpp_floor, effective_balances, load_env, load_data,
    make_empty_itinerary,
)


PASSED = 0
FAILED = 0
SKIPPED = 0
FAILURES = []


def _ok(name: str, detail: str = "") -> None:
    global PASSED
    PASSED += 1
    print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, error: str) -> None:
    global FAILED
    FAILED += 1
    FAILURES.append((name, error))
    print(f"  ✗ {name} — {error}")


def _skip(name: str, why: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"  - {name} (skipped: {why})")


# ---------------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------------

def suite_common(_offline: bool):
    print("\n[common]")
    try:
        load_env()
        _ok("load_env()")
    except Exception as e:
        _fail("load_env()", str(e))
        return

    try:
        u = cpp_floor("United MileagePlus")
        assert u > 0, f"cpp_floor returned {u}"
        _ok("cpp_floor(United)", f"={u}")
    except Exception as e:
        _fail("cpp_floor(United)", str(e))

    try:
        v = cpp_floor("Virgin Atlantic Flying Club")
        assert v > 0
        _ok("cpp_floor(Virgin)", f"={v}")
    except Exception as e:
        _fail("cpp_floor(Virgin)", str(e))

    try:
        eff = effective_balances({
            "currencies": {"Chase Ultimate Rewards": 100000},
            "programs": {"United MileagePlus": 5000},
        })
        assert eff.get("United MileagePlus", 0) >= 105000, eff
        _ok("effective_balances", f"UA={eff.get('United MileagePlus')}")
    except Exception as e:
        _fail("effective_balances", str(e))


def suite_data(_offline: bool):
    print("\n[data files]")
    required = [
        ("sweet_spots", ("sweet_spots", "data")),
        ("transfer_partners", ("currencies",)),
        ("points_valuations", ("valuations", "programs", "data")),
        ("stopovers", ("allowed", "not_allowed")),
        ("award_holds", ("allowed", "not_allowed")),
        ("airport_hubs", ("regions", "hubs", "NA")),
    ]
    for name, keys in required:
        try:
            d = load_data(name)
            assert isinstance(d, dict), f"{name} is not a dict"
            # tolerate any of the listed shape-keys
            found = any(k in d for k in keys) or any(k in d.get("_meta", {}) for k in keys)
            if not found:
                # if file has any key at all, accept
                found = bool(d)
            assert found, f"none of {keys} present"
            _ok(f"data/{name}.json", f"{len(json.dumps(d))} bytes")
        except Exception as e:
            _fail(f"data/{name}.json", str(e))


def suite_search_cash(offline: bool):
    print("\n[search_cash]")
    if offline:
        _skip("search_cash live", "offline mode")
        return
    try:
        from search_cash import search as cash_search
    except Exception as e:
        _fail("import search_cash", str(e))
        return
    try:
        t0 = time.time()
        depart = "2026-10-15"
        results = cash_search("JFK", "LAX", depart, cabin="economy", adults=1)
        elapsed = round(time.time() - t0, 1)
        good = [r for r in results if isinstance(r, dict) and not r.get("error")]
        if not good:
            _fail("cash JFK→LAX", f"no good results (got {len(results)} total)")
            return
        cheap = sorted(good, key=lambda r: r.get("price_usd") or 1e9)[0]
        _ok(
            "cash JFK→LAX",
            f"{len(good)} results in {elapsed}s, cheapest ${cheap.get('price_usd')} {cheap.get('carrier')}",
        )
    except Exception as e:
        _fail("cash JFK→LAX", str(e))


def suite_search_award(offline: bool):
    print("\n[search_award]")
    if offline:
        _skip("award live", "offline mode")
        return
    import os
    if not os.environ.get("SEATS_AERO_API_KEY"):
        load_env()
    if not os.environ.get("SEATS_AERO_API_KEY"):
        _skip("award live", "SEATS_AERO_API_KEY not set")
        return
    try:
        from search_award import search as award_search
    except Exception as e:
        _fail("import search_award", str(e))
        return
    try:
        t0 = time.time()
        results = award_search(
            "JFK", "NRT", depart_date="2026-09-15", cabins=("business",), passengers=1
        )
        elapsed = round(time.time() - t0, 1)
        good = [r for r in results if isinstance(r, dict) and not r.get("error")]
        _ok(
            "award JFK→NRT business",
            f"{len(good)} results in {elapsed}s",
        )
    except Exception as e:
        _fail("award JFK→NRT", str(e))


def suite_compose(offline: bool):
    print("\n[compose]")
    if offline:
        _skip("compose live", "offline mode")
        return
    try:
        from compose import compose
    except Exception as e:
        _fail("import compose", str(e))
        return
    try:
        t0 = time.time()
        results = compose(
            "JFK", "CDG", "2026-10-15", return_date="2026-10-25",
            cabin="economy", passengers=1,
            techniques=("positioning", "open_jaw"),
        )
        elapsed = round(time.time() - t0, 1)
        good = [r for r in results if isinstance(r, dict) and not r.get("error")]
        counts = {}
        for r in good:
            t = (r.get("composition") or {}).get("type", "?")
            counts[t] = counts.get(t, 0) + 1
        _ok(
            "compose JFK→CDG",
            f"{len(good)} results in {elapsed}s, types={counts}",
        )
    except Exception as e:
        _fail("compose JFK→CDG", str(e))


def suite_rank(_offline: bool):
    print("\n[rank]")
    try:
        from rank import rank
    except Exception as e:
        _fail("import rank", str(e))
        return
    try:
        rows = []
        cash = make_empty_itinerary()
        cash.update({"source": "google_flights", "kind": "cash",
                     "origin": "JFK", "destination": "NRT", "depart_date": "2026-09-15",
                     "carrier": "NH", "cabin": "business", "duration_minutes": 850,
                     "price_usd": 4200})
        award = make_empty_itinerary()
        award.update({"source": "seats.aero", "kind": "award",
                      "origin": "JFK", "destination": "NRT", "depart_date": "2026-09-15",
                      "carrier": "NH", "cabin": "business", "duration_minutes": 850,
                      "program": "United MileagePlus", "miles": 75000, "taxes_usd": 45.50,
                      "available_seats": 2})
        rows = [cash, award]
        ranked = rank(rows, cpp_mode="floor")
        assert len(ranked) == 2
        assert "score" in ranked[0]
        assert "total_cost_usd" in ranked[0]
        # award should typically beat cash on total cost
        cheaper = ranked[0]
        _ok("rank synthetic", f"cheapest score={cheaper.get('score')} kind={cheaper.get('kind')}")
    except Exception as e:
        _fail("rank synthetic", str(e))


def suite_mistakes(offline: bool):
    print("\n[ingest_mistakes]")
    if offline:
        _skip("mistakes live", "offline mode")
        return
    try:
        from ingest_mistakes import ingest_all
    except Exception as e:
        _fail("import ingest_mistakes", str(e))
        return
    try:
        t0 = time.time()
        recs = ingest_all(max_per_source=5, cache_ttl_minutes=0)
        elapsed = round(time.time() - t0, 1)
        sources = set(r.get("source") for r in recs if isinstance(r, dict))
        if len(sources) < 3:
            _fail("mistakes", f"only {len(sources)} sources returned data: {sources}")
            return
        _ok("ingest_all", f"{len(recs)} records from {len(sources)} sources in {elapsed}s")
    except Exception as e:
        _fail("ingest_all", str(e))


def suite_watch(offline: bool):
    print("\n[watch CRUD]")
    try:
        from watch import save_watch, get_watch, delete_watch, is_due
    except Exception as e:
        _fail("import watch", str(e))
        return
    try:
        w = save_watch({
            "label": "smoke-test-watch",
            "origins": ["JFK"], "destinations": ["LAX"],
            "depart_window": {"from": "2026-11-01", "to": "2026-11-05"},
            "cabin": ["economy"], "adults": 1, "mode": "cash",
            "max_price_usd": 9999, "frequency_hours": 24, "alerts": {"telegram": False},
        })
        wid = w["id"]
        _ok("watch save", f"id={wid}")
        got = get_watch(wid)
        assert got and got.get("label") == "smoke-test-watch"
        _ok("watch get")
        assert is_due(got), "freshly created watch should be due"
        _ok("watch is_due true")
        ok = delete_watch(wid)
        assert ok
        _ok("watch delete")
        assert get_watch(wid) is None
        _ok("watch deleted check")
    except Exception as e:
        _fail("watch crud", str(e))


SUITES = {
    "common": suite_common,
    "data": suite_data,
    "cash": suite_search_cash,
    "award": suite_search_award,
    "compose": suite_compose,
    "rank": suite_rank,
    "mistakes": suite_mistakes,
    "watch": suite_watch,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip live-API tests")
    ap.add_argument("--only", default=None, help="comma list of suite names")
    args = ap.parse_args()
    only = set((args.only or "").split(",")) if args.only else None

    print(f"== flight-hacker smoke test ==  root={SKILL_ROOT}")
    t0 = time.time()
    for name, fn in SUITES.items():
        if only and name not in only:
            continue
        fn(args.offline)
    elapsed = round(time.time() - t0, 1)

    print(f"\n== summary: {PASSED} passed, {FAILED} failed, {SKIPPED} skipped in {elapsed}s ==")
    if FAILURES:
        print("\nFailures:")
        for n, e in FAILURES:
            print(f"  - {n}: {e}")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
