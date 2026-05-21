"""
rank.py — Unified cash + award + composed ranking.

Takes a list of normalized itinerary dicts (from search_cash, search_award, compose)
and returns the same list with two new fields populated:

  total_cost_usd : float — true total cost in dollars
                           cash:      price_usd + composition.extra_cost_usd
                           award:     (miles * cpp_floor / 100) + taxes_usd + composition.extra_cost_usd
                           positioning, hidden-city, etc. inherit the composition.extra_cost_usd

  score          : float — total_cost_usd + time_penalty + risk_penalty
                           lower is better

Public API:
    rank(rows, user_balances=None, cpp_mode="floor", risk_penalty=None,
         time_weight_per_minute=0.05, return_top=None) -> list[dict]

    explain(row) -> str   # human-readable scoring breakdown for the UI
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    cpp_floor,
    effective_balances,
    load_data,
    log,
    make_empty_itinerary,
)


RISK_PENALTY_DEFAULTS = {
    "LEGAL": 0.0,
    "GRAY": 75.0,
    "TOS-RISK": 200.0,
}

# When CPP varies a lot for a single program across sources, allow caller to
# pick which to use. Floor is the conservative default per project memory.
CPP_MODES = ("floor", "avg", "ceiling")


def _cpp_for(program: str, mode: str = "floor") -> float:
    """Per-program CPP under the chosen mode. Falls back to 1.0 if unknown."""
    if not program:
        return 1.0
    vals = load_data("points_valuations") or {}
    entries = (
        vals.get("currencies")
        or vals.get("valuations")
        or vals.get("programs")
        or vals.get("data")
        or []
    )
    if isinstance(entries, dict):
        entries = list(entries.values())
    pn = program.lower().strip()
    tokens = [t for t in pn.replace(",", " ").split() if len(t) > 2]

    def _extract(e):
        f = e.get("floor_cpp") or e.get("floor") or e.get("cpp_floor")
        c = e.get("ceiling_cpp") or e.get("ceiling") or e.get("cpp_ceiling")
        sources = e.get("sources") or {}
        if mode == "floor" and f:
            return float(f)
        if mode == "ceiling" and c:
            return float(c)
        if mode == "avg" and sources:
            vals_ = [float(v) for v in sources.values() if isinstance(v, (int, float))]
            if vals_:
                return sum(vals_) / len(vals_)
        if mode == "avg" and f and c:
            return (float(f) + float(c)) / 2.0
        if f:
            return float(f)
        return None

    for e in entries:
        if not isinstance(e, dict):
            continue
        name = (e.get("program") or e.get("name") or "").lower()
        if not name:
            continue
        if name in pn or pn in name:
            r = _extract(e)
            if r is not None:
                return r
    # Token fallback for "Virgin Atlantic Flying Club" → "Virgin Atlantic", etc.
    for e in entries:
        if not isinstance(e, dict):
            continue
        name = (e.get("program") or e.get("name") or "").lower()
        if not name:
            continue
        if any(t in name for t in tokens):
            r = _extract(e)
            if r is not None:
                return r
    return cpp_floor(program) or 1.0


def _row_total_cost(row: dict, cpp_mode: str = "floor") -> float:
    """Compute true total cost in USD for one itinerary."""
    comp_extra = float((row.get("composition") or {}).get("extra_cost_usd") or 0.0)
    kind = row.get("kind") or ("award" if (row.get("miles") or 0) > 0 else "cash")

    if kind == "cash":
        price = float(row.get("price_usd") or 0.0)
        return price + comp_extra

    # Award
    miles = float(row.get("miles") or 0)
    taxes = float(row.get("taxes_usd") or 0.0)
    cpp = _cpp_for(row.get("program") or "", cpp_mode)
    miles_value_usd = miles * cpp / 100.0
    return miles_value_usd + taxes + comp_extra


def _affordability_penalty(row: dict, eff_balances: dict) -> float:
    """If user can't reach the required miles, penalize by miles shortfall × 0.02 ¢/mile."""
    if not eff_balances or row.get("kind") != "award":
        return 0.0
    program = row.get("program") or ""
    needed = float(row.get("miles") or 0)
    have = float(eff_balances.get(program, 0))
    shortfall = max(0.0, needed - have)
    return shortfall * 0.0002  # half a cent per mile shortfall


def rank(
    rows: list[dict],
    user_balances: dict | None = None,
    cpp_mode: str = "floor",
    risk_penalty: dict | None = None,
    time_weight_per_minute: float = 0.05,
    return_top: int | None = None,
) -> list[dict]:
    """
    Score and sort rows. Returns a NEW list (does not mutate the inputs by reference
    beyond setting score/total_cost_usd/cpp_used on each row dict).
    """
    if cpp_mode not in CPP_MODES:
        cpp_mode = "floor"
    if risk_penalty is None:
        risk_penalty = RISK_PENALTY_DEFAULTS

    eff = effective_balances(user_balances or {}) if user_balances else {}

    scored = []
    for r in rows:
        if not isinstance(r, dict) or r.get("error"):
            scored.append(r)
            continue
        # Ensure composition is always populated. Direct cash/award rows get
        # composition.type="direct" risk="LEGAL" if a producer didn't set it.
        comp = r.get("composition")
        if not comp or not isinstance(comp, dict):
            comp = {
                "type": "direct",
                "legs": [],
                "extra_cost_usd": 0.0,
                "extra_time_minutes": 0,
                "risk": "LEGAL",
                "notes": "",
            }
            r["composition"] = comp
        comp.setdefault("risk", "LEGAL")
        comp.setdefault("type", "direct")
        comp.setdefault("legs", [])
        comp.setdefault("extra_cost_usd", 0.0)
        comp.setdefault("extra_time_minutes", 0)
        comp.setdefault("notes", "")
        risk = (comp.get("risk") or "LEGAL").upper()

        total = _row_total_cost(r, cpp_mode=cpp_mode)
        time_minutes = float(r.get("duration_minutes") or 0) + float(
            comp.get("extra_time_minutes") or 0
        )
        time_pen = time_minutes * time_weight_per_minute
        risk_pen = float(risk_penalty.get(risk, 0.0))
        afford_pen = _affordability_penalty(r, eff)

        score = total + time_pen + risk_pen + afford_pen
        r["total_cost_usd"] = round(total, 2)
        r["cpp_used"] = round(_cpp_for(r.get("program") or "", cpp_mode), 3) if (
            r.get("kind") == "award"
        ) else None
        r["score"] = round(score, 2)
        r["score_breakdown"] = {
            "total_cost_usd": round(total, 2),
            "time_penalty": round(time_pen, 2),
            "risk_penalty": round(risk_pen, 2),
            "affordability_penalty": round(afford_pen, 2),
            "cpp_mode": cpp_mode,
        }
        scored.append(r)

    # Errors sort to the end. Real rows by score asc.
    def _key(r):
        if not isinstance(r, dict) or r.get("error"):
            return (1, float("inf"))
        return (0, r.get("score", float("inf")))

    scored.sort(key=_key)
    if return_top:
        return scored[:return_top]
    return scored


def explain(row: dict) -> str:
    """Human-readable scoring breakdown."""
    b = row.get("score_breakdown") or {}
    kind = row.get("kind") or "?"
    comp = row.get("composition") or {}
    parts = [
        f"kind={kind}",
        f"route={row.get('origin')}→{row.get('destination')}",
        f"carrier={row.get('carrier')}",
        f"cabin={row.get('cabin')}",
    ]
    if kind == "cash":
        parts.append(f"price=${row.get('price_usd', 0):.2f}")
    else:
        parts.append(
            f"{row.get('miles', 0):,} mi @ {row.get('cpp_used', '?')}cpp"
            f" + ${row.get('taxes_usd', 0) or 0:.2f} tax (program: {row.get('program')})"
        )
    if comp.get("extra_cost_usd"):
        parts.append(f"composition.extra=${comp['extra_cost_usd']:.2f}")
    if comp.get("type") and comp["type"] != "direct":
        parts.append(f"type={comp['type']} risk={comp.get('risk')}")
    parts.append(f"total=${b.get('total_cost_usd', 0):.2f}")
    parts.append(
        f"score={row.get('score', 0):.2f} "
        f"(time+{b.get('time_penalty', 0):.0f}, risk+{b.get('risk_penalty', 0):.0f}, "
        f"afford+{b.get('affordability_penalty', 0):.2f})"
    )
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# CLI smoke test — feed JSON rows on stdin
# ---------------------------------------------------------------------------

def _demo_rows() -> list[dict]:
    """Synthetic data so the CLI is runnable with no flags."""
    r1 = make_empty_itinerary()
    r1.update({
        "source": "google_flights", "kind": "cash",
        "origin": "JFK", "destination": "NRT", "depart_date": "2026-09-15",
        "carrier": "NH", "cabin": "business", "stops": 0, "duration_minutes": 850,
        "price_usd": 4200.00,
    })
    r2 = make_empty_itinerary()
    r2.update({
        "source": "seats.aero", "kind": "award",
        "origin": "JFK", "destination": "NRT", "depart_date": "2026-09-15",
        "carrier": "NH", "cabin": "business", "stops": 0, "duration_minutes": 850,
        "program": "United MileagePlus", "miles": 75000, "taxes_usd": 45.50,
        "available_seats": 2,
    })
    r3 = make_empty_itinerary()
    r3.update({
        "source": "seats.aero", "kind": "award",
        "origin": "JFK", "destination": "NRT", "depart_date": "2026-09-15",
        "carrier": "NH", "cabin": "first", "stops": 0, "duration_minutes": 850,
        "program": "Virgin Atlantic Flying Club", "miles": 110000, "taxes_usd": 200.00,
        "available_seats": 1,
    })
    r4 = make_empty_itinerary()
    r4.update({
        "source": "google_flights", "kind": "cash",
        "origin": "EWR", "destination": "NRT", "depart_date": "2026-09-15",
        "carrier": "UA", "cabin": "business", "stops": 0, "duration_minutes": 870,
        "price_usd": 3800.00,
        "composition": {
            "type": "positioning", "legs": [], "extra_cost_usd": 35.0,
            "extra_time_minutes": 90, "risk": "LEGAL",
            "notes": "Positioning JFK→EWR via NJ Transit",
        },
    })
    r5 = make_empty_itinerary()
    r5.update({
        "source": "google_flights", "kind": "cash",
        "origin": "JFK", "destination": "HND", "depart_date": "2026-09-15",
        "carrier": "JL", "cabin": "business", "stops": 1, "duration_minutes": 1050,
        "price_usd": 2950.00,
        "composition": {
            "type": "hidden_city", "legs": [], "extra_cost_usd": 0,
            "extra_time_minutes": 0, "risk": "TOS-RISK",
            "notes": "Get off at NRT. No bags, no return, no FF#.",
        },
    })
    return [r1, r2, r3, r4, r5]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="run on synthetic data")
    ap.add_argument("--cpp", default="floor", choices=CPP_MODES)
    ap.add_argument("--balances", default=None, help="path to user_balances.json")
    ap.add_argument("--top", type=int, default=None)
    args = ap.parse_args()

    if args.demo or sys.stdin.isatty():
        rows = _demo_rows()
    else:
        rows = json.loads(sys.stdin.read())

    balances = None
    if args.balances:
        balances = json.loads(Path(args.balances).read_text())

    ranked = rank(rows, user_balances=balances, cpp_mode=args.cpp, return_top=args.top)
    log("rank_done", n=len(ranked), cpp=args.cpp)
    for r in ranked:
        if not isinstance(r, dict) or r.get("error"):
            continue
        sys.stderr.write(explain(r) + "\n")
    print(json.dumps(ranked, indent=2, default=str))
