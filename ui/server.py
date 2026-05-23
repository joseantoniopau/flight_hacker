"""
server.py — FastAPI backend for the flight-hacker brutalist UI.

Wires every UI section to the underlying scripts in /scripts and the data files
in /data. No stubs: every endpoint is end-to-end functional. If an underlying
script is missing at import time (e.g. rank.py, ingest_mistakes.py, watch.py),
a sensible fallback is used and the absence is logged once.

Run:
    python -m uvicorn server:app --host 127.0.0.1 --port 8721
    # or directly
    python /Users/admin/Desktop/flight-hacker/ui/server.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap so we can import from scripts/
# ---------------------------------------------------------------------------
SKILL_ROOT = Path("/Users/admin/Desktop/flight-hacker")
SCRIPTS_DIR = SKILL_ROOT / "scripts"
UI_DIR = SKILL_ROOT / "ui"
DATA_DIR = SKILL_ROOT / "data"
WATCHES_DIR = SKILL_ROOT / "watches"
CACHE_DIR = SKILL_ROOT / "cache"
ENV_FILE = SKILL_ROOT / ".env"

for p in (str(SCRIPTS_DIR), str(SKILL_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Common always available
from common import (  # type: ignore
    load_data,
    save_json,
    effective_balances,
    cpp_floor,
    log as common_log,
)

# Optional script imports — every one wrapped so a missing module never breaks boot.
try:
    import search_cash  # type: ignore
    _HAS_CASH = True
except Exception as _e:
    search_cash = None  # type: ignore
    _HAS_CASH = False
    common_log("import_fallback", module="search_cash", error=str(_e))

try:
    import search_award  # type: ignore
    _HAS_AWARD = True
except Exception as _e:
    search_award = None  # type: ignore
    _HAS_AWARD = False
    common_log("import_fallback", module="search_award", error=str(_e))

try:
    import compose as compose_mod  # type: ignore
    _HAS_COMPOSE = True
except Exception as _e:
    compose_mod = None  # type: ignore
    _HAS_COMPOSE = False
    common_log("import_fallback", module="compose", error=str(_e))

try:
    import rank as rank_mod  # type: ignore
    _HAS_RANK = True
except Exception as _e:
    rank_mod = None  # type: ignore
    _HAS_RANK = False
    common_log("import_fallback", module="rank",
               error=str(_e), note="using identity-rank fallback")

try:
    import ingest_mistakes as ingest_mod  # type: ignore
    _HAS_INGEST = True
except Exception as _e:
    ingest_mod = None  # type: ignore
    _HAS_INGEST = False
    common_log("import_fallback", module="ingest_mistakes",
               error=str(_e), note="using cached-only mistakes")

try:
    import watch as watch_mod  # type: ignore
    _HAS_WATCH = True
except Exception as _e:
    watch_mod = None  # type: ignore
    _HAS_WATCH = False
    common_log("import_fallback", module="watch",
               error=str(_e), note="using inline watch.run_one fallback")

# ---------------------------------------------------------------------------
# FastAPI (with Starlette fallback if FastAPI missing)
# ---------------------------------------------------------------------------
try:
    from fastapi import FastAPI, HTTPException, Request, Query
    from fastapi.responses import JSONResponse, Response, HTMLResponse, FileResponse
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from pydantic import BaseModel, Field, field_validator
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover — kept for true stdlib fallback
    _HAS_FASTAPI = False
    raise RuntimeError(
        "FastAPI is required. Install with: "
        "pip install --break-system-packages fastapi uvicorn"
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START_TIME = time.time()
MISTAKES_CACHE_PATH = CACHE_DIR / "mistakes_feed.json"
MISTAKES_TTL_SECONDS = 60 * 60  # 60 min

CACHE_DIR.mkdir(parents=True, exist_ok=True)
WATCHES_DIR.mkdir(parents=True, exist_ok=True)

# Cap composer/searcher parallelism: each spawns its own pool internally.
_EXEC = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fh-srv")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _err(message: str, code: int = 500) -> JSONResponse:
    return JSONResponse({"error": message, "code": code}, status_code=code)


def _mask_key(val: str | None) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return val[:2] + "****"
    return val[:8] + "****"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _normalize_cabin_list(cabin_in: Any) -> list[str]:
    """Map UI codes (Y/W/J/F) and full names to script-friendly cabin names."""
    out: list[str] = []
    if not cabin_in:
        return ["economy"]
    if isinstance(cabin_in, str):
        cabin_in = [cabin_in]
    code_map = {
        "Y": "economy", "ECONOMY": "economy", "ECON": "economy",
        "W": "premium_economy", "PE": "premium_economy",
        "PREMIUM_ECONOMY": "premium_economy",
        "J": "business", "BUSINESS": "business",
        "F": "first", "FIRST": "first",
    }
    for c in cabin_in:
        if not c:
            continue
        key = str(c).strip().upper()
        out.append(code_map.get(key, key.lower()))
    return out or ["economy"]


# Human-readable labels for cabin storage codes / internal names. Used by
# the watch + result adapters so the UI table renders "Economy" instead of
# the raw "Y" / "economy" the storage layer keeps. New codes go here only,
# so any caller (watch, mistakes, results) stays consistent.
_CABIN_LABELS = {
    "Y": "Economy", "ECONOMY": "Economy", "ECON": "Economy",
    "W": "Premium Economy", "PE": "Premium Economy",
    "PREMIUM_ECONOMY": "Premium Economy", "PREMIUM ECONOMY": "Premium Economy",
    "J": "Business", "BUSINESS": "Business",
    "F": "First", "FIRST": "First",
}


def _cabin_label(code: Any) -> str:
    """Translate a single cabin code/name to its human label. Returns the
    input unchanged when no mapping exists (so legitimate display strings
    like 'mixed' or human-typed values pass through)."""
    if code is None:
        return ""
    s = str(code).strip()
    if not s:
        return ""
    return _CABIN_LABELS.get(s.upper(), s)


def _identity_rank(rows: list[dict], user_balances: dict | None = None) -> list[dict]:
    """Fallback ranker when rank.py is missing — fills score + total_cost_usd."""
    for r in rows:
        if not isinstance(r, dict):
            continue
        price = r.get("price_usd")
        miles = r.get("miles") or 0
        taxes = float(r.get("taxes_usd") or 0.0)
        comp = r.get("composition") or {}
        extra = float(comp.get("extra_cost_usd") or 0.0)
        if price is not None:
            total = float(price) + extra
        elif miles:
            program = r.get("program") or ""
            cpp = cpp_floor(program) or 1.5
            total = (float(miles) * float(cpp) / 100.0) + taxes + extra
        else:
            total = float("inf")
        r["total_cost_usd"] = None if total == float("inf") else round(total, 2)
        # Lower is better → simple inverse score for UI sorting.
        r["score"] = 0.0 if total == float("inf") else round(10000.0 / (1.0 + total), 4)
    rows.sort(key=lambda x: (x.get("total_cost_usd") is None,
                             x.get("total_cost_usd") or float("inf")))
    return rows


def _do_rank(rows: list[dict], user_balances: dict | None = None) -> list[dict]:
    if _HAS_RANK and rank_mod is not None and hasattr(rank_mod, "rank"):
        try:
            return rank_mod.rank(rows, user_balances=user_balances)  # type: ignore[attr-defined]
        except Exception as e:
            common_log("rank_error_fallback", error=str(e))
    return _identity_rank(rows, user_balances=user_balances)


# ---------------------------------------------------------------------------
# Hub data (loaded once, flattened)
# ---------------------------------------------------------------------------

def _flat_hubs() -> list[dict]:
    raw = load_data("airport_hubs") or {}
    regions = raw.get("regions") or {}
    flat: list[dict] = []
    if isinstance(regions, dict):
        for region_code, items in regions.items():
            if not isinstance(items, list):
                continue
            for h in items:
                if not isinstance(h, dict):
                    continue
                flat.append({
                    "iata": (h.get("iata") or "").upper(),
                    "city": h.get("city") or "",
                    "country": h.get("country") or "",
                    "region": h.get("region") or region_code,
                })
    return flat


_HUBS = _flat_hubs()


# ---------------------------------------------------------------------------
# Mistakes feed (cached + on-demand refresh)
# ---------------------------------------------------------------------------

def _read_mistakes_cache() -> dict | None:
    if not MISTAKES_CACHE_PATH.exists():
        return None
    try:
        return json.loads(MISTAKES_CACHE_PATH.read_text())
    except Exception as e:
        common_log("mistakes_cache_read_error", error=str(e))
        return None


def _write_mistakes_cache(payload: dict) -> None:
    try:
        _atomic_write_text(MISTAKES_CACHE_PATH, json.dumps(payload, indent=2, default=str))
    except Exception as e:
        common_log("mistakes_cache_write_error", error=str(e))


def _fallback_mistakes() -> list[dict]:
    """Read /data/mistake_sources.md and surface a structured stub list.
    Used only when ingest_mistakes.py is unavailable AND no cache exists.
    """
    src = DATA_DIR / "mistake_sources.md"
    out: list[dict] = []
    if src.exists():
        try:
            for line in src.read_text().splitlines():
                m = re.match(r"^\s*-\s+\[([^\]]+)\]\((https?://[^)]+)\)\s*(.*)$", line)
                if m:
                    out.append({
                        "title": m.group(1).strip(),
                        "url": m.group(2).strip(),
                        "summary": m.group(3).strip() or "(source watchlist entry)",
                        "source": "mistake_sources.md",
                        "captured_at": _now_iso(),
                    })
        except Exception as e:
            common_log("mistakes_fallback_error", error=str(e))
    return out


def _do_ingest() -> dict:
    if _HAS_INGEST and ingest_mod is not None and hasattr(ingest_mod, "ingest_all"):
        try:
            result = ingest_mod.ingest_all()  # type: ignore[attr-defined]
            if isinstance(result, list):
                payload = {"mistakes": result, "refreshed_at": _now_iso(),
                           "source": "ingest_mistakes.ingest_all"}
            elif isinstance(result, dict):
                payload = {"mistakes": result.get("mistakes")
                           or result.get("items") or result.get("entries") or [],
                           "refreshed_at": _now_iso(),
                           "source": "ingest_mistakes.ingest_all"}
            else:
                payload = {"mistakes": [], "refreshed_at": _now_iso(),
                           "source": "ingest_mistakes.ingest_all"}
        except Exception as e:
            common_log("ingest_error", error=str(e))
            payload = {"mistakes": _fallback_mistakes(),
                       "refreshed_at": _now_iso(),
                       "source": "fallback_after_error",
                       "error": str(e)}
    else:
        payload = {"mistakes": _fallback_mistakes(),
                   "refreshed_at": _now_iso(),
                   "source": "fallback_no_module"}
    _write_mistakes_cache(payload)
    return payload


# ---------------------------------------------------------------------------
# Watchlist helpers
# ---------------------------------------------------------------------------

def _watch_path(wid: str) -> Path:
    # Sanitize id — only word/dash characters
    safe = re.sub(r"[^A-Za-z0-9_\-]", "", wid)
    if not safe:
        raise ValueError("invalid watch id")
    return WATCHES_DIR / f"{safe}.json"


def _list_watches() -> list[dict]:
    out: list[dict] = []
    for p in sorted(WATCHES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                data.setdefault("id", p.stem)
                out.append(data)
        except Exception as e:
            common_log("watch_read_error", file=str(p), error=str(e))
    return out


def _save_watch(watch: dict) -> dict:
    if not watch.get("id"):
        watch["id"] = uuid.uuid4().hex[:12]
    watch.setdefault("paused", False)
    watch.setdefault("created_at", _now_iso())
    watch["updated_at"] = _now_iso()
    save_json(_watch_path(watch["id"]), watch)
    return watch


def _delete_watch(wid: str) -> bool:
    p = _watch_path(wid)
    if not p.exists():
        return False
    p.unlink()
    return True


def _run_watch_inline(watch: dict) -> dict:
    """Fallback when watch.py is missing — runs a single cash+award sweep
    on the watch's route and stamps last_run / last_results."""
    t_start = time.time()
    route = watch.get("route") or {}
    origin = (route.get("origin") or watch.get("origin") or "").upper()
    dest = (route.get("destination") or watch.get("destination") or "").upper()
    depart_window = (watch.get("depart_window") or
                     {"from": watch.get("window_from"), "to": watch.get("window_to")})
    depart = (depart_window or {}).get("from") if isinstance(depart_window, dict) \
        else watch.get("window_from")
    cabin = (watch.get("min_cabin") or watch.get("cabin") or "economy")
    cabin_list = _normalize_cabin_list(cabin)
    cabin_one = cabin_list[0]
    mode = (watch.get("mode") or "both").lower()
    try:
        adults = max(1, int(watch.get("adults") or 1))
    except (TypeError, ValueError):
        adults = 1

    rows: list[dict] = []
    cash_err: str | None = None
    award_err: str | None = None
    # Cash branch — search_cash.search accepts singular `cabin=` + `adults=`.
    if mode in ("cash", "both") and _HAS_CASH and search_cash is not None \
            and origin and dest and depart:
        try:
            rows.extend(search_cash.search(  # type: ignore[attr-defined]
                origin, dest, depart,
                cabin=cabin_one, adults=adults) or [])
        except Exception as e:
            cash_err = str(e)
    # Award branch — search_award.search uses plural `cabins=` (iterable) and
    # `passengers=`. Passing `cabin=` here would TypeError at the function
    # boundary — that's exactly the bug the audit flagged.
    if mode in ("award", "both") and _HAS_AWARD and search_award is not None \
            and origin and dest and depart:
        try:
            award_out = search_award.search(  # type: ignore[attr-defined]
                origin, dest, depart,
                cabins=(cabin_one,), passengers=adults,
            )
            # Round-trip search returns {"outbound": [...], "return": [...]};
            # one-way returns a list. Flatten both shapes here so downstream
            # rank/adapter sees a uniform list.
            if isinstance(award_out, dict):
                for direction in ("outbound", "return"):
                    for r in (award_out.get(direction) or []):
                        if isinstance(r, dict):
                            r.setdefault("direction", direction)
                            rows.append(r)
            elif isinstance(award_out, list):
                rows.extend(award_out)
        except Exception as e:
            award_err = str(e)
    rows = _do_rank(rows, user_balances=_load_user_balances())
    summary = {
        "ran_at": _now_iso(),
        "result_count": len(rows),
        "cheapest_usd": None,
        "cheapest_miles": None,
        "errors": {k: v for k, v in {"cash": cash_err, "award": award_err}.items() if v},
    }
    if rows:
        cash_rows = [r for r in rows if r.get("price_usd") is not None]
        award_rows = [r for r in rows if (r.get("miles") or 0) > 0]
        if cash_rows:
            summary["cheapest_usd"] = min(
                (float(r["price_usd"]) for r in cash_rows), default=None)
        if award_rows:
            summary["cheapest_miles"] = min(
                (int(r.get("miles") or 0) for r in award_rows), default=None)
    elapsed_s = round(time.time() - t_start, 1)
    # Match scripts/watch.py.run_one schema: last_run is an ISO timestamp
    # string (so is_due() can parse it). The summary dict lives under
    # last_run_summary so neither shape is lost.
    watch["last_run"] = summary["ran_at"]
    watch["last_run_summary"] = summary
    watch["last_results"] = rows[:5]
    watch["last_count"] = len(rows[:5])
    watch["last_elapsed_s"] = elapsed_s
    _save_watch(watch)
    return {
        "id": watch.get("id"),
        "count": len(rows[:5]),
        "top": rows[:5],
        "elapsed_s": elapsed_s,
        "watch": watch,
        "summary": summary,
        "results": rows[:25],
    }


def _run_watch(wid: str) -> dict:
    p = _watch_path(wid)
    if not p.exists():
        raise FileNotFoundError(f"watch {wid} not found")
    watch = json.loads(p.read_text())
    if _HAS_WATCH and watch_mod is not None and hasattr(watch_mod, "run_one"):
        try:
            result = watch_mod.run_one(wid)  # type: ignore[attr-defined]
            if isinstance(result, dict):
                return result
            return {"watch": watch, "result": result}
        except Exception as e:
            common_log("watch_run_error_fallback", id=wid, error=str(e))
    return _run_watch_inline(watch)


# ---------------------------------------------------------------------------
# Balances (with example fallback)
# ---------------------------------------------------------------------------

USER_BAL_PATH = DATA_DIR / "user_balances.json"
USER_BAL_EXAMPLE_PATH = DATA_DIR / "user_balances.example.json"

# UI abbreviation ↔ full canonical currency-name mapping. The on-disk
# user_balances.json keeps full names ONLY — abbreviations are an API-layer
# convenience so the brutalist UI inputs (data-currency="UR" etc.) round-trip.
_CURRENCY_ABBREV_TO_FULL: dict[str, str] = {
    "UR": "Chase Ultimate Rewards",
    "MR": "Amex Membership Rewards",
    "TY": "Citi ThankYou",
    "VENTURE": "Capital One Miles",
    "BILT": "Bilt Rewards",
    "BONVOY": "Marriott Bonvoy",
}
_CURRENCY_FULL_TO_ABBREV: dict[str, str] = {
    v: k for k, v in _CURRENCY_ABBREV_TO_FULL.items()
}


def _mirror_currency_abbrevs(currencies: dict) -> dict:
    """Return a copy of `currencies` with both full-name keys AND their
    UI-facing abbreviations populated. Full names always win when both are
    supplied. Missing abbreviations default to 0 so the UI inputs never go
    blank."""
    if not isinstance(currencies, dict):
        return {}
    out: dict = dict(currencies)
    # Project every known full name down to its abbreviation.
    for full, abbrev in _CURRENCY_FULL_TO_ABBREV.items():
        if full in out:
            try:
                out[abbrev] = int(out[full] or 0)
            except (TypeError, ValueError):
                out[abbrev] = 0
    # Backfill any abbreviation the UI expects but disk didn't have, so
    # the JS reading b.currencies.UR doesn't crash on undefined.
    for abbrev in _CURRENCY_ABBREV_TO_FULL:
        out.setdefault(abbrev, 0)
    return out


def _expand_currency_abbrevs(currencies: dict) -> dict:
    """Inverse of _mirror_currency_abbrevs for incoming POST bodies.

    Accepts a dict that may use abbreviations, full names, or both. Returns
    a dict keyed by full canonical names (suitable for persisting to disk).
    When both UR and "Chase Ultimate Rewards" are present, full names win.
    Unknown keys are preserved verbatim so we never lose user-supplied data.
    """
    if not isinstance(currencies, dict):
        return {}
    out: dict = {}
    # First pass — copy through any full-name keys verbatim, and pass through
    # unknown keys we don't recognize as abbreviations.
    for k, v in currencies.items():
        if k in _CURRENCY_ABBREV_TO_FULL:
            continue  # handled in second pass
        out[k] = v
    # Second pass — only fill in abbreviations whose full name wasn't already
    # supplied by the caller.
    for abbrev, full in _CURRENCY_ABBREV_TO_FULL.items():
        if abbrev not in currencies:
            continue
        if full in out:
            continue  # full name already won
        try:
            out[full] = int(currencies[abbrev] or 0)
        except (TypeError, ValueError):
            out[full] = 0
    return out


def _load_user_balances() -> dict:
    if USER_BAL_PATH.exists():
        try:
            return json.loads(USER_BAL_PATH.read_text())
        except Exception as e:
            common_log("user_balances_read_error", error=str(e))
    if USER_BAL_EXAMPLE_PATH.exists():
        try:
            return json.loads(USER_BAL_EXAMPLE_PATH.read_text())
        except Exception:
            pass
    return {"currencies": {}, "programs": {}}


# ---------------------------------------------------------------------------
# Env (.env) read/write
# ---------------------------------------------------------------------------

ENV_KEYS_PRIMARY = [
    "SEATS_AERO_API_KEY",
    "DUFFEL_API_TOKEN",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_CHAT_ID",
    "AWARDWALLET_API_KEY",
    "FH_CPP_MODE",
    "FH_CACHE_TTL_MIN",
]
MASKED_KEYS = {"SEATS_AERO_API_KEY", "DUFFEL_API_TOKEN",
               "AWARDWALLET_API_KEY", "TELEGRAM_WEBHOOK_URL"}


def _read_env_dict() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _quote_env_value(v: str) -> str:
    """Render an .env value safely.

    Strips CR/LF (which would break the key=value line format) and wraps
    the value in single quotes when it contains anything a POSIX shell
    would interpret on `source` (e.g. $, `, \\, space, quotes, parens, &|;).
    Single-quoted strings cannot themselves contain a single quote, so we
    escape ' as the standard '"'"' sequence.

    The python parser (_read_env_dict above) already strips wrapping quotes,
    so this is transparent to the application.
    """
    s = "" if v is None else str(v)
    s = s.replace("\r", "").replace("\n", "")
    if not s:
        return ""
    needs_quoting = any(c in s for c in ' \t"$`\\!&|;()<>{}*?#') or "'" in s
    if not needs_quoting:
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _write_env_dict(merged: dict[str, str]) -> None:
    """Preserve comments + ordering of the existing .env where possible.

    Every value is shell-safe-quoted so a literal `$(rm -rf /)` round-trips
    as a string, never as a command, even if someone `source`s the file.
    """
    lines_out: list[str] = []
    seen: set[str] = set()
    existing_text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    for raw in existing_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines_out.append(raw)
            continue
        k = stripped.split("=", 1)[0].strip()
        if k in merged:
            v = _quote_env_value(merged[k])
            lines_out.append(f"{k}={v}")
            seen.add(k)
        else:
            lines_out.append(raw)
    # Append any new keys that weren't in the file before.
    appended: list[str] = []
    for k, v in merged.items():
        if k in seen:
            continue
        appended.append(f"{k}={_quote_env_value(v)}")
    if appended:
        if lines_out and lines_out[-1].strip():
            lines_out.append("")
        lines_out.append("# --- added by ui/server.py ---")
        lines_out.extend(appended)
    _atomic_write_text(ENV_FILE, "\n".join(lines_out).rstrip() + "\n")


def _masked_settings() -> dict[str, Any]:
    env = _read_env_dict()
    settings: dict[str, Any] = {}
    for k in ENV_KEYS_PRIMARY:
        v = env.get(k, "")
        if k in MASKED_KEYS and v:
            settings[k] = _mask_key(v)
        else:
            settings[k] = v
    # UI-friendly aliases
    settings["seats_aero_key"] = _mask_key(env.get("SEATS_AERO_API_KEY", "")) \
        if env.get("SEATS_AERO_API_KEY") else ""
    settings["telegram_webhook"] = _mask_key(env.get("TELEGRAM_WEBHOOK_URL", "")) \
        if env.get("TELEGRAM_WEBHOOK_URL") else ""
    settings["cpp_source"] = env.get("FH_CPP_MODE") or "avg"
    settings["cpp_mode"] = env.get("FH_CPP_MODE") or "avg"
    try:
        settings["cache_ttl"] = int(env.get("FH_CACHE_TTL_MIN") or 60) * 60
        settings["cache_ttl_minutes"] = int(env.get("FH_CACHE_TTL_MIN") or 60)
    except ValueError:
        settings["cache_ttl"] = 3600
        settings["cache_ttl_minutes"] = 60
    return settings


# ---------------------------------------------------------------------------
# Sweet spots
# ---------------------------------------------------------------------------

# Sweet-spot program strings sometimes diverge from the canonical
# transfer_partners.json keys — e.g. "American AAdvantage" vs.
# "American Airlines AAdvantage", or a slash-joined dual-program string
# like "Iberia Plus / British Airways Club Avios". Substring containment
# (used elsewhere) doesn't catch these. _program_aliases() expands a
# sweet-spot program string into the set of canonical names it should
# match against in transfer_partners.json + balances effective-map.
_SWEET_SPOT_PROGRAM_ALIASES: dict[str, list[str]] = {
    "American AAdvantage": ["American Airlines AAdvantage"],
    "Cathay Asia Miles": ["Cathay Pacific Asia Miles", "Asia Miles (Cathay)",
                          "Cathay Pacific"],
    "Finnair Plus Avios": ["Finnair Plus"],
    "Qatar Privilege Club Avios": ["Qatar Airways Privilege Club"],
    # Slash-joined dual-program — expand to both halves so the UI shows
    # transfer paths via either currency-pool.
    "Iberia Plus / British Airways Club Avios": [
        "Iberia Plus",
        "British Airways Club",
        "British Airways Executive Club",
    ],
}


def _program_aliases(program: str) -> list[str]:
    """Return the list of canonical program names a sweet-spot string maps to.
    Always includes the original (trimmed) program as the first entry so
    direct matches keep working."""
    p = (program or "").strip()
    out = [p] if p else []
    for alias in _SWEET_SPOT_PROGRAM_ALIASES.get(p, []):
        if alias not in out:
            out.append(alias)
    return out


def _all_sweet_spots() -> list[dict]:
    raw = load_data("sweet_spots") or {}
    if isinstance(raw, dict):
        for k in ("entries", "sweet_spots", "data", "items"):
            if isinstance(raw.get(k), list):
                return raw[k]
    if isinstance(raw, list):
        return raw
    return []


def _sweet_spot_by_id(spid: str) -> dict | None:
    for ss in _all_sweet_spots():
        if str(ss.get("id")) == spid:
            return ss
    return None


def _transfer_paths(ss: dict, user_balances: dict) -> list[dict]:
    """For a sweet-spot row, return reachable transfer paths from user balances.
    A path = direct balance OR transferable_from currency with non-zero balance.
    """
    target_program = (ss.get("program") or "").strip()
    # Prefer one-way miles in the math the UI shows — matches the
    # `miles` mirror field used in the sweet-spots table + overlay.
    miles_needed = ss.get("miles_oneway") or ss.get("miles_roundtrip") or 0
    # Expand the sweet-spot program string into all canonical names it
    # maps to (handles "American AAdvantage" vs "American Airlines
    # AAdvantage", slash-joined dual programs, etc.).
    target_aliases = [a.lower() for a in _program_aliases(target_program)]
    cur_balances = user_balances.get("currencies", {}) or {}
    prog_balances = user_balances.get("programs", {}) or {}
    transferable = [c.lower() for c in (ss.get("transferable_from") or [])]

    paths: list[dict] = []
    # 1) Direct program balance — match against any alias for the sweet
    # spot's program (sweet-spot strings like "American AAdvantage" must
    # match canonical "American Airlines AAdvantage" in balances).
    direct = 0
    for k, v in prog_balances.items():
        if k.lower().strip() in target_aliases:
            direct = int(v or 0)
            break
    if direct > 0:
        paths.append({
            "type": "direct",
            "currency": target_program,
            "available_miles": direct,
            "miles_needed": miles_needed,
            "covers": miles_needed and direct >= miles_needed,
            "ratio": "1:1",
        })

    # 2) Card currencies that list this program among their transfer partners
    tp_root = (load_data("transfer_partners") or {}).get("currencies", {}) or {}
    for card, bal in cur_balances.items():
        if not bal:
            continue
        card_key = card.lower()
        if transferable and not any(t in card_key or card_key in t for t in transferable):
            # Skip cards the sweet spot says aren't valid sources.
            continue
        # Find this card in transfer_partners.json
        card_node = None
        for kk, vv in tp_root.items():
            kk_l = kk.lower()
            if kk_l == card_key or card_key in kk_l or kk_l in card_key:
                card_node = vv
                break
        if not card_node:
            continue
        for partner in (card_node.get("transfer_partners") or []):
            prog = (partner.get("program") or "").strip()
            prog_l = prog.lower()
            # Match if the partner program equals any alias for the
            # sweet-spot target, OR substring-contains in either direction
            # (preserves the old permissive behavior for unaliased rows).
            if prog_l not in target_aliases \
                    and prog_l != target_program.lower() \
                    and target_program.lower() not in prog_l \
                    and prog_l not in target_program.lower() \
                    and not any(a == prog_l or a in prog_l or prog_l in a
                                for a in target_aliases):
                continue
            ratio_s = partner.get("ratio") or "1:1"
            try:
                num, den = ratio_s.split(":")
                # Ratio convention: "<source>:<destination>" — e.g. "5:4"
                # means 5 source miles transfer to 4 destination miles
                # (you lose value). destination = source * (den/num).
                # The previous formula (num/den) was inverted — it
                # inflated lossy transfers. Matches FH.balances.reachable
                # in app.js, which uses rhs/lhs.
                num_f = float(num)
                den_f = float(den)
                mult = (den_f / num_f) if num_f else 1.0
            except Exception:
                mult = 1.0
            paths.append({
                "type": "transfer",
                "currency": card,
                "card_balance": int(bal),
                "available_miles": int(bal * mult),
                "miles_needed": miles_needed,
                "covers": miles_needed and (bal * mult) >= miles_needed,
                "ratio": ratio_s,
                "via_program": prog,
                "min_transfer": partner.get("min_transfer"),
                "transfer_time": partner.get("transfer_time"),
            })
    return paths


# ---------------------------------------------------------------------------
# UI adapters — keep raw shapes intact and add UI-friendly mirror fields.
# The UI consumes the flat fields; underlying scripts keep using the nested
# shapes. Adapter additions are pure mirrors — never strip originals.
# ---------------------------------------------------------------------------

_ROUTE_PATTERN_RE = re.compile(r"\b([A-Z]{3})[-\s–—]+([A-Z]{3})\b")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&apos;": "'", "&#39;": "'", "&#x27;": "'", "&nbsp;": " ",
    "&ndash;": "-", "&mdash;": "-", "&hellip;": "...",
}


def _strip_html(text: str | None) -> str:
    """Strip HTML tags + decode common entities, collapse whitespace.
    Defensive — most sources already deliver plain text, but RSS bodies
    occasionally carry inline tags."""
    if not text:
        return ""
    s = str(text)
    if "<" in s and ">" in s:
        s = _HTML_TAG_RE.sub(" ", s)
    if "&" in s:
        for ent, ch in _HTML_ENTITIES.items():
            if ent in s:
                s = s.replace(ent, ch)
        # numeric refs &#NNN;
        s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def _stable_id_from_url(url: str | None) -> str:
    """Deterministic 12-char id derived from a URL (sha256 prefix)."""
    if not url:
        return uuid.uuid4().hex[:12]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return digest[:12]


def _parse_route_from_title(title: str | None) -> str | None:
    if not title:
        return None
    m = _ROUTE_PATTERN_RE.search(title)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def _short_title(title: str | None, limit: int = 40) -> str:
    """Truncate a title to ~limit chars on a word boundary, add an ellipsis."""
    t = _strip_html(title)
    if not t:
        return ""
    if len(t) <= limit:
        return t
    cut = t[:limit]
    # back off to last space so we don't slice mid-word
    sp = cut.rfind(" ")
    if sp > limit * 0.5:
        cut = cut[:sp]
    return cut.rstrip(" ,.-:") + "..."


def _adapt_mistake(m: dict) -> dict:
    """Wrap a raw mistakes record with the flat fields the UI renders.

    Originals are preserved verbatim — only mirror fields are added.
    """
    if not isinstance(m, dict):
        return m
    out = dict(m)
    extracted = m.get("extracted") or {}
    if not isinstance(extracted, dict):
        extracted = {}

    # id — sha256(url)[:12], deterministic
    out["id"] = _stable_id_from_url(m.get("url"))

    # route — prefer extracted, then IATA pair from title, then short title.
    # Never let the full title leak into the route line (UI render breaks
    # when "route" is 100+ chars of marketing copy). Hard cap at 45 chars
    # so even an extracted IATA-pair with extras can't blow past the limit.
    route = extracted.get("route_pattern")
    if not route:
        route = _parse_route_from_title(m.get("title"))
    if not route:
        route = _short_title(m.get("title"), limit=40)
    route = route or ""
    if len(route) > 45:
        route = route[:42].rstrip(" ,.-:") + "..."
    out["route"] = route

    # price (USD number) — explicit None when absent so UI's fmtMoney shows '-'
    price = extracted.get("price_hint_usd")
    out["price"] = price if isinstance(price, (int, float)) else None

    # cabin / carrier — empty string never null. Translate the cabin hint to
    # the human label ("Economy" / "Business") that the UI card renders;
    # default to Economy when extraction couldn't pin a class.
    out["cabin"] = _cabin_label(extracted.get("cabin_hint") or "economy")
    out["carrier"] = extracted.get("carrier_hint") or ""
    out["carrier_logo_url"] = _carrier_logo_url(out["carrier"])

    # source / posted_at carry through (already present in raw shape)
    out["source"] = m.get("source") or ""
    out["posted_at"] = m.get("posted_at") or m.get("captured_at") or ""

    # note — always populated. Strip HTML (some RSS bodies include tags),
    # decode entities, collapse whitespace. Falls back to title if no body.
    raw_note = m.get("note") if "note" in out else m.get("summary")
    if raw_note is None:
        raw_note = m.get("summary") or m.get("title") or ""
    out["note"] = _strip_html(raw_note)

    # risk — mistake fares are by definition gray-area.
    if "risk" not in out or not out.get("risk"):
        out["risk"] = "GRAY"

    return out


def _adapt_mistakes_payload(payload: dict) -> dict:
    """Ensure a {mistakes:[...adapted...], refreshed_at, source?} envelope."""
    if not isinstance(payload, dict):
        return {"mistakes": [], "refreshed_at": _now_iso()}
    out = dict(payload)
    items = out.get("mistakes")
    if not isinstance(items, list):
        items = []
    out["mistakes"] = [_adapt_mistake(m) for m in items if isinstance(m, dict)]
    out.setdefault("refreshed_at", _now_iso())
    return out


def _watch_last_check(w: dict) -> str | None:
    """Pull a usable timestamp from last_run (dict with ran_at) or last_check."""
    lr = w.get("last_run")
    if isinstance(lr, dict):
        ts = lr.get("ran_at") or lr.get("at") or lr.get("timestamp")
        if ts:
            return ts
    elif isinstance(lr, str):
        return lr
    return w.get("last_check") or None


def _watch_best_found_usd(w: dict) -> float | None:
    """Best cash price seen: prefer w.last_results[0].price_usd, else last_run.cheapest_usd."""
    results = w.get("last_results")
    if isinstance(results, list):
        for r in results:
            if isinstance(r, dict) and r.get("price_usd") is not None:
                try:
                    return float(r["price_usd"])
                except (TypeError, ValueError):
                    continue
    lr = w.get("last_run")
    if isinstance(lr, dict) and lr.get("cheapest_usd") is not None:
        try:
            return float(lr["cheapest_usd"])
        except (TypeError, ValueError):
            return None
    return None


def _watch_alerts_count(w: dict) -> int:
    """1 if last alert was within the last 7 days, else 0.

    Also honors an explicit numeric `alerts_count` if a future script sets it.
    """
    explicit = w.get("alerts_count")
    if isinstance(explicit, int):
        return explicit
    last_alert = w.get("last_alert_at")
    if not last_alert:
        return 0
    try:
        # Tolerant ISO parser: accept trailing Z and tz-aware/naive strings.
        s = str(last_alert).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return 1 if (datetime.now(timezone.utc) - dt) <= timedelta(days=7) else 0
    except Exception:
        return 0


# Carrier name (as fast-flights returns it) → airline.com booking landing.
# Top ~30 carriers covering the vast majority of routes a US-based user sees.
_AIRLINE_HOMEPAGE = {
    "American": "https://www.aa.com/booking/find-flights",
    "American Airlines": "https://www.aa.com/booking/find-flights",
    "United": "https://www.united.com/en/us",
    "United Airlines": "https://www.united.com/en/us",
    "Delta": "https://www.delta.com/flight-search/book-a-flight",
    "Delta Air Lines": "https://www.delta.com/flight-search/book-a-flight",
    "JetBlue": "https://www.jetblue.com",
    "Alaska": "https://www.alaskaair.com",
    "Alaska Airlines": "https://www.alaskaair.com",
    "Southwest": "https://www.southwest.com",
    "Spirit": "https://www.spirit.com",
    "Frontier": "https://www.flyfrontier.com",
    "Hawaiian": "https://www.hawaiianairlines.com",
    "Allegiant": "https://www.allegiantair.com",
    "Air Canada": "https://www.aircanada.com",
    "WestJet": "https://www.westjet.com",
    "Air Transat": "https://www.airtransat.com",
    "Iberia": "https://www.iberia.com",
    "Air France": "https://www.airfrance.com",
    "KLM": "https://www.klm.com",
    "Lufthansa": "https://www.lufthansa.com",
    "SWISS": "https://www.swiss.com",
    "Austrian": "https://www.austrian.com",
    "British Airways": "https://www.britishairways.com",
    "Virgin Atlantic": "https://www.virginatlantic.com",
    "LEVEL": "https://www.flylevel.com",
    "TAP": "https://www.flytap.com",
    "TAP Air Portugal": "https://www.flytap.com",
    "ITA": "https://www.ita-airways.com",
    "ITA Airways": "https://www.ita-airways.com",
    "Aer Lingus": "https://www.aerlingus.com",
    "Finnair": "https://www.finnair.com",
    "SAS": "https://www.flysas.com",
    "Norwegian": "https://www.norwegian.com",
    "Norse": "https://flynorse.com",
    "Norse Atlantic": "https://flynorse.com",
    "Icelandair": "https://www.icelandair.com",
    "LOT": "https://www.lot.com",
    "Air Serbia": "https://www.airserbia.com",
    "Turkish Airlines": "https://www.turkishairlines.com",
    "Turkish": "https://www.turkishairlines.com",
    "Emirates": "https://www.emirates.com",
    "Qatar Airways": "https://www.qatarairways.com",
    "Qatar": "https://www.qatarairways.com",
    "Etihad": "https://www.etihad.com",
    "Saudia": "https://www.saudia.com",
    "Singapore Airlines": "https://www.singaporeair.com",
    "Singapore": "https://www.singaporeair.com",
    "Cathay": "https://www.cathaypacific.com",
    "Cathay Pacific": "https://www.cathaypacific.com",
    "ANA": "https://www.ana.co.jp",
    "JAL": "https://www.jal.co.jp/en",
    "Japan Airlines": "https://www.jal.co.jp/en",
    "Korean Air": "https://www.koreanair.com",
    "Asiana": "https://flyasiana.com",
    "Qantas": "https://www.qantas.com",
    "Air New Zealand": "https://www.airnewzealand.com",
}

# Carrier display name → IATA airline code. Used to compose a logo URL via
# Google's airline-logo CDN (the one Google Flights itself uses). When the
# carrier isn't recognized we leave the logo URL null and the UI hides the
# <img>; lookups are case-insensitive after trimming.
_CARRIER_TO_IATA = {
    "delta": "DL",
    "delta air lines": "DL",
    "american": "AA",
    "american airlines": "AA",
    "united": "UA",
    "united airlines": "UA",
    "alaska": "AS",
    "alaska airlines": "AS",
    "jetblue": "B6",
    "southwest": "WN",
    "spirit": "NK",
    "frontier": "F9",
    "hawaiian": "HA",
    "hawaiian airlines": "HA",
    "allegiant": "G4",
    "air canada": "AC",
    "westjet": "WS",
    "air transat": "TS",
    "iberia": "IB",
    "air france": "AF",
    "klm": "KL",
    "lufthansa": "LH",
    "swiss": "LX",
    "austrian": "OS",
    "british airways": "BA",
    "virgin atlantic": "VS",
    "level": "IB",  # LEVEL operates under IB code
    "tap": "TP",
    "tap air portugal": "TP",
    "ita": "AZ",
    "ita airways": "AZ",
    "aer lingus": "EI",
    "finnair": "AY",
    "sas": "SK",
    "norwegian": "DY",
    "norse": "Z0",
    "norse atlantic": "Z0",
    "norse atlantic airways": "Z0",
    "icelandair": "FI",
    "lot": "LO",
    "lot polish airlines": "LO",
    "air serbia": "JU",
    "turkish": "TK",
    "turkish airlines": "TK",
    "emirates": "EK",
    "qatar": "QR",
    "qatar airways": "QR",
    "etihad": "EY",
    "etihad airways": "EY",
    "saudia": "SV",
    "singapore": "SQ",
    "singapore airlines": "SQ",
    "cathay": "CX",
    "cathay pacific": "CX",
    "ana": "NH",
    "all nippon": "NH",
    "all nippon airways": "NH",
    "jal": "JL",
    "japan airlines": "JL",
    "korean air": "KE",
    "asiana": "OZ",
    "asiana airlines": "OZ",
    "china eastern": "MU",
    "china southern": "CZ",
    "air china": "CA",
    "eva": "BR",
    "eva air": "BR",
    "thai": "TG",
    "thai airways": "TG",
    "vietnam airlines": "VN",
    "malaysia airlines": "MH",
    "philippine airlines": "PR",
    "qantas": "QF",
    "air new zealand": "NZ",
    "fiji airways": "FJ",
    "aeromexico": "AM",
    "copa": "CM",
    "copa airlines": "CM",
    "latam": "LA",
    "avianca": "AV",
    "gol": "G3",
    "aerolineas argentinas": "AR",
    "azul": "AD",
    "egyptair": "MS",
    "ethiopian": "ET",
    "ethiopian airlines": "ET",
    "kenya airways": "KQ",
    "south african airways": "SA",
    "royal air maroc": "AT",
    "tunisair": "TU",
}

# Google's airline-logo CDN. Same one Google Flights renders against; no auth,
# stable URL shape, 70px PNG with transparency. Returns None when we can't
# resolve the carrier to an IATA code so the UI can hide the <img> gracefully.
def _carrier_logo_url(carrier: str | None) -> str | None:
    if not carrier:
        return None
    iata = _CARRIER_TO_IATA.get(str(carrier).strip().lower())
    if not iata:
        return None
    return f"https://www.gstatic.com/flights/airline_logos/70px/{iata}.png"


# Award program name → operating carrier IATA, for sweet-spots rows where
# the only carrier hint is the program name. Best-effort: programs that span
# multiple carriers (Aeroplan, Flying Blue) map to the namesake carrier.
_PROGRAM_TO_IATA = {
    "Aeroplan": "AC",
    "Air Canada Aeroplan": "AC",
    "United MileagePlus": "UA",
    "American AAdvantage": "AA",
    "Delta SkyMiles": "DL",
    "Alaska Mileage Plan": "AS",
    "Alaska Atmos": "AS",
    "Virgin Atlantic Flying Club": "VS",
    "Avianca LifeMiles": "AV",
    "British Airways Avios": "BA",
    "British Airways Executive Club": "BA",
    "Iberia Plus": "IB",
    "Air France/KLM Flying Blue": "AF",
    "Air France-KLM Flying Blue": "AF",
    "Turkish Miles&Smiles": "TK",
    "ANA Mileage Club": "NH",
    "JAL Mileage Bank": "JL",
    "Singapore KrisFlyer": "SQ",
    "Singapore Airlines KrisFlyer": "SQ",
    "Cathay Asia Miles": "CX",
    "Cathay Pacific Asia Miles": "CX",
    "Qatar Privilege Club": "QR",
    "Qatar Airways Privilege Club": "QR",
    "Emirates Skywards": "EK",
    "Etihad Guest": "EY",
    "Korean Air SKYPASS": "KE",
    "Qantas Frequent Flyer": "QF",
    "Finnair Plus": "AY",
}


def _program_logo_url(program: str | None, operating_carrier: str | None = None) -> str | None:
    """Resolve a sweet-spot row to an airline logo URL.

    Prefer the operating carrier (when the row reports one) since that's the
    metal flying the route. Fall back to the program's namesake carrier.
    """
    if operating_carrier:
        url = _carrier_logo_url(operating_carrier)
        if url:
            return url
    if not program:
        return None
    iata = _PROGRAM_TO_IATA.get(program)
    if not iata:
        # Loose match — "ANA Mileage Club" vs "ANA"
        for k, code in _PROGRAM_TO_IATA.items():
            if program in k or k in program:
                iata = code
                break
    if not iata:
        # Last resort: try as if it were a carrier name
        return _carrier_logo_url(program)
    return f"https://www.gstatic.com/flights/airline_logos/70px/{iata}.png"


# Award program → booking page
_PROGRAM_BOOK_URL = {
    "Aeroplan": "https://www.aircanada.com/aeroplan",
    "Air Canada Aeroplan": "https://www.aircanada.com/aeroplan",
    "United MileagePlus": "https://www.united.com/en/us/mileageplus",
    "American AAdvantage": "https://www.aa.com/aadvantage",
    "Delta SkyMiles": "https://www.delta.com/skymiles",
    "Alaska Mileage Plan": "https://www.alaskaair.com/mileageplan",
    "Alaska Atmos": "https://www.alaskaair.com/mileageplan",
    "Virgin Atlantic Flying Club": "https://www.virginatlantic.com/flying-club",
    "Avianca LifeMiles": "https://www.lifemiles.com",
    "British Airways Avios": "https://www.britishairways.com/executive-club",
    "British Airways Executive Club": "https://www.britishairways.com/executive-club",
    "Iberia Plus": "https://www.iberia.com/iberiaplus",
    "Air France/KLM Flying Blue": "https://www.flyingblue.com",
    "Air France-KLM Flying Blue": "https://www.flyingblue.com",
    "Turkish Miles&Smiles": "https://www.turkishairlines.com/en-us/miles-and-smiles/",
    "ANA Mileage Club": "https://www.ana.co.jp/en/us/amc/",
    "JAL Mileage Bank": "https://www.jal.co.jp/jmb/",
    "Singapore KrisFlyer": "https://www.singaporeair.com/krisflyer",
    "Singapore Airlines KrisFlyer": "https://www.singaporeair.com/krisflyer",
    "Cathay Asia Miles": "https://www.asiamiles.com",
    "Cathay Pacific Asia Miles": "https://www.asiamiles.com",
    "Qatar Privilege Club": "https://www.qatarairways.com/en-us/privilegeclub.html",
    "Qatar Airways Privilege Club": "https://www.qatarairways.com/en-us/privilegeclub.html",
    "Emirates Skywards": "https://www.emirates.com/skywards/",
    "Etihad Guest": "https://www.etihadguest.com",
    "Korean Air SKYPASS": "https://www.koreanair.com/skypass",
    "Qantas Frequent Flyer": "https://www.qantas.com/frequentflyer",
    "Finnair Plus": "https://www.finnair.com/finnairplus",
}


def _airline_book_url(carrier: str, origin: str, dest: str, depart: str,
                      ret: str, is_award: bool, program: str | None) -> str:
    """Return airline.com / program URL when we recognise the carrier or program."""
    if is_award and program:
        url = _PROGRAM_BOOK_URL.get(program)
        if url:
            return url
        # Loose match
        for k, v in _PROGRAM_BOOK_URL.items():
            if program in k or k in program:
                return v
    if not carrier:
        return ""
    url = _AIRLINE_HOMEPAGE.get(carrier)
    if url:
        return url
    # Loose match (e.g. "ITA " vs "ITA Airways")
    c_norm = carrier.strip()
    for k, v in _AIRLINE_HOMEPAGE.items():
        if c_norm == k or c_norm in k or k in c_norm:
            return v
    return ""


_CABIN_TO_UI_CODE = {
    "economy": "Y",
    "econ": "Y",
    "y": "Y",
    "premium_economy": "W",
    "premium-economy": "W",
    "premium economy": "W",
    "premium": "W",
    "pe": "W",
    "w": "W",
    "business": "J",
    "biz": "J",
    "j": "J",
    "first": "F",
    "f": "F",
}

# Human-readable label shown in the results table cabin column.
_CABIN_TO_LABEL = {
    "Y": "Economy",
    "W": "Premium Economy",
    "J": "Business",
    "F": "First",
}


def _adapt_search_row(r: dict, index: int) -> dict:
    """Translate a normalized itinerary row to the shape the UI table expects.

    The UI table renders these fields per row:
      rank, route, carrier, depart, duration_min, stops, cabin, cash_usd,
      miles, miles_value_usd, miles_cpp_cents, total_usd, risk, is_award,
      fare_class, baggage, booking_instructions, segments[{from,to,carrier,
      flight,dep_local,arr_local,aircraft,duration_min}]
    """
    if not isinstance(r, dict):
        return r
    out = dict(r)
    comp = r.get("composition") or {}
    kind = r.get("kind") or ("award" if (r.get("miles") or 0) > 0 else "cash")

    out["rank"] = index + 1
    out["route"] = (r.get("origin") or "") + "-" + (r.get("destination") or "")
    # Prefer the actual segment departure time (e.g. "2026-06-05T14:45") over
    # the raw date. Falls back to date-only if segments lack times.
    _segs0 = (r.get("segments") or [{}])[0] if r.get("segments") else {}
    _seg_depart = (_segs0 or {}).get("depart") or ""
    out["depart"] = _seg_depart or r.get("depart_date") or r.get("depart") or ""
    out["duration_min"] = r.get("duration_minutes")
    out["stops"] = r.get("stops") if r.get("stops") is not None else "-"
    cab_raw = (r.get("cabin") or "").lower()
    cabin_code = _CABIN_TO_UI_CODE.get(cab_raw, cab_raw[:1].upper() or "Y")
    out["cabin_code"] = cabin_code                  # internal: used for filter chips
    out["cabin"] = _CABIN_TO_LABEL.get(cabin_code, "Economy")   # display label
    out["cash_usd"] = r.get("price_usd") if kind == "cash" else None
    out["miles"] = r.get("miles") or 0
    cpp = r.get("cpp_used")
    if cpp is None and kind == "award":
        try:
            cpp = cpp_floor(r.get("program") or "") or 1.0
        except Exception:
            cpp = 1.0
    out["miles_cpp_cents"] = round(float(cpp), 2) if cpp else None
    out["miles_value_usd"] = (
        round(float(r.get("miles") or 0) * float(cpp or 0) / 100.0, 2)
        if (kind == "award" and cpp)
        else None
    )
    out["total_usd"] = r.get("total_cost_usd")
    out["risk"] = comp.get("risk") or "LEGAL"
    out["is_award"] = kind == "award"
    out["fare_class"] = r.get("fare_brand")
    bag = r.get("baggage_included")
    if bag is True:
        out["baggage"] = "Carry-on + checked bag included"
    elif bag is False:
        out["baggage"] = "Bag fees apply — verify on airline.com"
    else:
        out["baggage"] = "Bag policy: verify on airline.com before booking"

    # Build a sensible booking URL. Order of preference:
    #   1. Existing deep_link from upstream (Duffel returns one occasionally).
    #   2. Airline-direct URL when we know the carrier (top ~30 carriers).
    #   3. Google Flights search URL — auto-fills the form so user can pick.
    # Also build an airline-direct URL alongside so the DETAIL overlay can
    # surface both options.
    o = r.get("origin") or ""
    d = r.get("destination") or ""
    dep = r.get("depart_date") or ""
    ret = r.get("return_date") or ""
    # Always set the three URL fields so the UI never has to undefined-check
    # them. Empty string when we can't build a real link.
    gf_url = ""
    airline_url = ""
    if o and d and dep:
        if ret:
            gf_url = f"https://www.google.com/travel/flights?q=Flights%20from%20{o}%20to%20{d}%20on%20{dep}%20through%20{ret}"
        else:
            gf_url = f"https://www.google.com/travel/flights?q=Flights%20from%20{o}%20to%20{d}%20on%20{dep}"
        airline_url = _airline_book_url(
            r.get("carrier") or "", o, d, dep, ret,
            kind == "award", r.get("program"),
        )
    out["google_flights_url"] = gf_url
    out["airline_url"] = airline_url
    out["deep_link"] = r.get("deep_link") or airline_url or gf_url or ""
    # Carrier logo URL — main-row carrier maps to Google's airline-logo CDN.
    # Null when the carrier isn't recognized; UI hides the <img>.
    out["carrier_logo_url"] = _carrier_logo_url(r.get("carrier"))

    # Booking instructions string.
    if kind == "award":
        program = r.get("program") or "the program"
        out["booking_instructions"] = (
            f"Book on {program} (or via partner). "
            f"For partner awards, search availability first; transfer points only after "
            f"confirming the saver seat. Cost: {r.get('miles')} miles + "
            f"${r.get('taxes_usd') or 0:.2f} in taxes."
        )
    else:
        carrier = r.get("carrier") or "the operating carrier"
        out["booking_instructions"] = (
            f"Book direct on {carrier}{'' if carrier == 'the operating carrier' else '.com'} "
            f"for the best refund and change protection. The 24-hour DOT cancellation rule "
            f"applies to US-ticketed itineraries. "
            f"If the carrier is missing above, verify the flight on Google Flights first."
        )

    # Adapt segments to UI shape.
    new_segs = []
    for s in (r.get("segments") or []):
        if not isinstance(s, dict):
            continue
        new_segs.append({
            "from": s.get("from") or "",
            "to": s.get("to") or "",
            "carrier": s.get("carrier") or "",
            "carrier_logo_url": _carrier_logo_url(s.get("carrier")),
            "flight": (" " + s.get("flight_no")) if s.get("flight_no") else "",
            "dep_local": s.get("depart") or "",
            "arr_local": s.get("arrive") or "",
            "aircraft": s.get("aircraft") or "",
            "duration_min": s.get("duration_minutes"),
        })
    out["segments"] = new_segs

    return out


def _adapt_watch(w: dict) -> dict:
    """Add the flat UI mirror fields without dropping the nested originals."""
    if not isinstance(w, dict):
        return w
    out = dict(w)

    origins = w.get("origins")
    if not origins and w.get("origin"):
        origins = [w["origin"]]
    if isinstance(origins, list) and origins:
        out["origin"] = "/".join(str(o) for o in origins if o)
    else:
        out["origin"] = w.get("origin") or ""

    destinations = w.get("destinations")
    if not destinations and w.get("destination"):
        destinations = [w["destination"]]
    if isinstance(destinations, list) and destinations:
        out["destination"] = "/".join(str(d) for d in destinations if d)
    else:
        out["destination"] = w.get("destination") or ""

    dw = w.get("depart_window") or {}
    if isinstance(dw, dict):
        out["window_from"] = dw.get("from") or w.get("window_from")
        out["window_to"] = dw.get("to") or w.get("window_to")
    else:
        out["window_from"] = w.get("window_from")
        out["window_to"] = w.get("window_to")

    if w.get("max_usd") is None:
        out["max_usd"] = w.get("max_price_usd")
    # else: keep existing max_usd

    # Cabin mirror: storage keeps the raw code ("Y" / "J" / "economy"), but
    # the watchlist table renders this directly — translate to "Economy" /
    # "Business" so the user reads a label, not a fare-class letter. List
    # forms (multi-cabin watches) join the labeled values with "/".
    cabin_val = w.get("cabin")
    if isinstance(cabin_val, list):
        out["cabin"] = "/".join(_cabin_label(c) for c in cabin_val if c)
    elif isinstance(cabin_val, str):
        out["cabin"] = _cabin_label(cabin_val)
    else:
        cabins = w.get("cabins")
        if isinstance(cabins, list) and cabins:
            out["cabin"] = "/".join(_cabin_label(c) for c in cabins if c)
        else:
            mc = w.get("min_cabin")
            if isinstance(mc, list):
                out["cabin"] = "/".join(_cabin_label(c) for c in mc if c)
            elif isinstance(mc, str):
                out["cabin"] = _cabin_label(mc)
            else:
                out["cabin"] = ""

    out["last_check"] = _watch_last_check(w)
    out["best_found_usd"] = _watch_best_found_usd(w)
    out["alerts"] = _watch_alerts_count(w)

    return out


def _airlines_from_balances(bal: dict, eff: dict) -> list[dict]:
    """Flatten programs+effective into the
    [{program, balance, miles, effective}, ...] list the UI renders directly.

    `miles` is an alias mirror of `balance` so the UI (which reads `a.miles`)
    and any back-compat consumer (which reads `a.balance`) both work."""
    programs = bal.get("programs") or {}
    if not isinstance(programs, dict):
        return []
    out: list[dict] = []
    for program, raw_balance in programs.items():
        try:
            balance_i = int(raw_balance or 0)
        except (TypeError, ValueError):
            balance_i = 0
        try:
            effective_f = float(eff.get(program, balance_i) or 0)
        except (TypeError, ValueError):
            effective_f = float(balance_i)
        out.append({
            "program": program,
            "balance": balance_i,
            "miles": balance_i,
            "effective": effective_f,
        })
    return out


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="flight-hacker", version="0.1.0")

# Localhost-only CORS — the UI is served from the same origin (127.0.0.1:8721),
# so wildcard isn't needed. Allow both 127.0.0.1 and localhost on any port.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Body-size guard: FastAPI by default has no max payload. A 10MB POST will
# OOM the parser + spawn thousands of fan-out subtasks. 1MB is generous for
# legitimate watchlist/search payloads — origins/destinations are 3-letter
# IATA codes, balances/settings are tiny dicts.
MAX_REQUEST_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB


@app.middleware("http")
async def _enforce_body_limit(request: Request, call_next):
    """Reject oversized request bodies before parsing.

    Checks Content-Length when the client supplies it; for chunked uploads
    we fall back to reading the body in-app, but uvicorn already buffers,
    so a hard cap via Content-Length is the practical defense.
    """
    if request.method in {"POST", "PUT", "PATCH"}:
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        {"error": "request body too large",
                         "code": 413,
                         "limit_bytes": MAX_REQUEST_BODY_BYTES},
                        status_code=413,
                    )
            except ValueError:
                return JSONResponse(
                    {"error": "invalid content-length header", "code": 400},
                    status_code=400,
                )
    return await call_next(request)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    t0 = time.time()
    try:
        response = await call_next(request)
        return response
    finally:
        dur_ms = int((time.time() - t0) * 1000)
        try:
            common_log(
                "http",
                method=request.method,
                path=request.url.path,
                qs=str(request.url.query) or None,
                ms=dur_ms,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ComposerFlags(BaseModel):
    positioning: bool = False
    hidden_city: bool = False
    open_jaw: bool = False
    stopover: bool = False
    # Alias from UI
    stopover_gaming: bool | None = None


class SearchRequest(BaseModel):
    # Industry caps: a single PNR can include up to 8 passengers; 16 airports
    # per side is far above any realistic positioning/open-jaw search.
    origins: list[str] = Field(default_factory=list, max_length=16)
    destinations: list[str] = Field(default_factory=list, max_length=16)
    # Accept either single string or window-from/window-to from UI.
    depart: str | None = Field(default=None, max_length=32)
    depart_from: str | None = Field(default=None, max_length=32)
    depart_to: str | None = Field(default=None, max_length=32)
    return_: str | None = Field(default=None, alias="return", max_length=32)
    return_from: str | None = Field(default=None, max_length=32)
    return_to: str | None = Field(default=None, max_length=32)
    one_way: bool = False
    cabin: list[str] = Field(default_factory=lambda: ["economy"], max_length=4)
    # Booking-realistic bounds: 8 adults max per PNR (industry standard),
    # 8 children max, 4 infants max. Negatives never accepted.
    adults: int = Field(default=1, ge=1, le=8)
    children: int = Field(default=0, ge=0, le=8)
    infants: int = Field(default=0, ge=0, le=4)
    max_stops: int = Field(default=2, ge=0, le=5)
    max_duration_hours: int | None = Field(default=None, ge=1, le=72)
    max_hours: int | None = Field(default=None, ge=1, le=72)
    composers: ComposerFlags = Field(default_factory=ComposerFlags)
    mode: str = "both"  # cash|award|both
    # Auto-expand origins / destinations to include neighboring airports
    # (e.g. MIA → MIA + FLL + PBI). Pulled from data/airport_hubs.json.
    expand_origins: bool = True
    expand_destinations: bool = True

    class Config:
        populate_by_name = True

    @field_validator("origins", "destinations", "cabin", mode="before")
    @classmethod
    def _cap_str_lengths(cls, v):
        """Each element of origins/destinations/cabin must be short. IATA
        codes are 3 chars; anything over 64 is junk or an attack."""
        if not isinstance(v, list):
            return v
        out: list[str] = []
        for x in v:
            if x is None:
                continue
            s = str(x)
            if len(s) > 64:
                raise ValueError("list element too long (max 64 chars)")
            out.append(s)
        return out

    @field_validator("mode", mode="before")
    @classmethod
    def _validate_mode(cls, v):
        if v is None:
            return "both"
        s = str(v).lower()
        if s not in {"cash", "award", "both"}:
            raise ValueError("mode must be one of cash|award|both")
        return s

    def effective_depart(self) -> str | None:
        return self.depart or self.depart_from

    def effective_return(self) -> str | None:
        if self.one_way:
            return None
        return self.return_ or self.return_from


class WatchUpsert(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    # Label is human-typed but storage/display can't reasonably take novels.
    label: str | None = Field(default=None, max_length=200)
    route: dict | None = None
    origin: str | None = Field(default=None, max_length=64)
    destination: str | None = Field(default=None, max_length=64)
    origins: list[str] | None = Field(default=None, max_length=16)
    destinations: list[str] | None = Field(default=None, max_length=16)
    depart_window: dict | None = None
    return_window: dict | None = None
    window_from: str | None = Field(default=None, max_length=32)
    window_to: str | None = Field(default=None, max_length=32)
    # Money/miles must be non-negative — a negative threshold is nonsense
    # and would cause every check to "match" downstream.
    max_price_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    max_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    max_miles: int | None = Field(default=None, ge=0, le=10_000_000)
    min_cabin: str | list[str] | None = None
    cabin: str | list[str] | None = None
    cabins: list[str] | None = Field(default=None, max_length=4)
    adults: int | None = Field(default=1, ge=1, le=8)
    children: int | None = Field(default=0, ge=0, le=8)
    infants: int | None = Field(default=0, ge=0, le=4)
    mode: str | None = "both"
    composers: dict | None = None
    alerts: dict | None = None
    frequency_hours: int | None = Field(default=12, ge=1, le=720)
    paused: bool = False

    class Config:
        extra = "allow"

    @field_validator("origins", "destinations", "cabins", mode="before")
    @classmethod
    def _cap_watch_list_elem(cls, v):
        if v is None or not isinstance(v, list):
            return v
        out: list[str] = []
        for x in v:
            if x is None:
                continue
            s = str(x)
            if len(s) > 64:
                raise ValueError("list element too long (max 64 chars)")
            out.append(s)
        return out


class WatchPatch(BaseModel):
    class Config:
        extra = "allow"


class SettingsBody(BaseModel):
    SEATS_AERO_API_KEY: str | None = None
    DUFFEL_API_TOKEN: str | None = None
    TELEGRAM_WEBHOOK_URL: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    AWARDWALLET_API_KEY: str | None = None
    cpp_mode: str | None = None
    cache_ttl_minutes: int | None = None
    # UI aliases
    seats_aero_key: str | None = None
    telegram_webhook: str | None = None
    cpp_source: str | None = None
    cache_ttl: int | None = None  # seconds, from UI

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Static file serving (index.html with MOCK_MODE injection)
# ---------------------------------------------------------------------------

def _inject_mock_flag(html: str) -> str:
    inject = "<script>window.MOCK_MODE = false;</script>"
    if "<!-- MOCK_MODE -->" in html:
        return html.replace("<!-- MOCK_MODE -->", inject)
    return re.sub(r"(<head[^>]*>)", r"\1\n" + inject, html, count=1)


@app.get("/")
async def root_index():
    p = UI_DIR / "index.html"
    if not p.exists():
        return _err("index.html not found", 404)
    html = p.read_text()
    return HTMLResponse(_inject_mock_flag(html))


@app.get("/index.html")
async def index_html():
    return await root_index()


@app.get("/styles.css")
async def styles():
    p = UI_DIR / "styles.css"
    if not p.exists():
        return _err("styles.css not found", 404)
    return FileResponse(str(p), media_type="text/css")


@app.get("/app.js")
async def app_js():
    p = UI_DIR / "app.js"
    if not p.exists():
        return _err("app.js not found", 404)
    return FileResponse(str(p), media_type="application/javascript")


@app.get("/mock_api.js")
async def mock_api_js():
    p = UI_DIR / "mock_api.js"
    if not p.exists():
        return _err("mock_api.js not found", 404)
    return FileResponse(str(p), media_type="application/javascript")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# /api/healthz
# ---------------------------------------------------------------------------

@app.get("/api/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_s": int(time.time() - START_TIME),
        "versions": {
            "server": "0.1.0",
            "search_cash": _HAS_CASH,
            "search_award": _HAS_AWARD,
            "compose": _HAS_COMPOSE,
            "rank": _HAS_RANK,
            "ingest_mistakes": _HAS_INGEST,
            "watch": _HAS_WATCH,
        },
    }


# ---------------------------------------------------------------------------
# /api/hubs
# ---------------------------------------------------------------------------

@app.get("/api/hubs")
async def hubs(q: str = Query(default="", max_length=64)):
    qn = (q or "").strip().upper()
    if not qn:
        return {"hubs": _HUBS[:20]}
    out: list[dict] = []
    # Prefix match on IATA first; substring on city/country secondary.
    for h in _HUBS:
        if h["iata"].startswith(qn):
            out.append(h)
        if len(out) >= 20:
            break
    if len(out) < 20:
        seen = {h["iata"] for h in out}
        qcity = qn.lower()
        for h in _HUBS:
            if h["iata"] in seen:
                continue
            if qcity in (h["city"] or "").lower() \
                    or qcity in (h["country"] or "").lower():
                out.append(h)
                seen.add(h["iata"])
            if len(out) >= 20:
                break
    return {"hubs": out}


# ---------------------------------------------------------------------------
# /api/search — the heavy lifter
# ---------------------------------------------------------------------------

def _resolve_techniques(c: ComposerFlags, mode: str = "both") -> list[str]:
    out = []
    if c.positioning:
        out.append("positioning")
    if c.hidden_city:
        out.append("hidden_city")
    if c.open_jaw:
        out.append("open_jaw")
    # Stopover only applies to award rows (free-stopover programs). Silently
    # drop it from techniques when the caller asked for cash-only search;
    # otherwise compose.py will auto-fetch award rows just to annotate them,
    # which is wasted work and surfaces an award row in cash-only results.
    if (c.stopover or c.stopover_gaming) and (mode or "both").lower() != "cash":
        out.append("stopover")
    return out


def _do_cash(origin: str, dest: str, depart: str, ret: str | None,
             cabin: str, adults: int, children: int, infants: int,
             max_stops: int) -> list[dict]:
    if not _HAS_CASH or search_cash is None:
        return []
    try:
        return search_cash.search(  # type: ignore[attr-defined]
            origin, dest, depart, return_date=ret,
            cabin=cabin, adults=adults, children=children, infants=infants,
            max_stops=max_stops,
        ) or []
    except Exception as e:
        common_log("cash_search_error", origin=origin, dest=dest, error=str(e))
        return []


def _do_award(origin: str, dest: str, depart: str, ret: str | None,
              cabin: str, passengers: int) -> list[dict]:
    if not _HAS_AWARD or search_award is None:
        return []
    try:
        result = search_award.search(  # type: ignore[attr-defined]
            origin, dest, depart, return_date=ret,
            cabins=(cabin,), passengers=passengers,
        )
        # search_award returns dict {outbound, return} on round-trip; flatten with direction stamped
        if isinstance(result, dict):
            out = []
            for direction in ("outbound", "return"):
                for row in result.get(direction, []) or []:
                    if isinstance(row, dict):
                        row.setdefault("direction", direction)
                        out.append(row)
            return out
        return result or []
    except Exception as e:
        common_log("award_search_error", origin=origin, dest=dest, error=str(e))
        return []


def _do_compose(origin: str, dest: str, depart: str, ret: str | None,
                cabin: str, adults: int, techniques: list[str]) -> list[dict]:
    if not _HAS_COMPOSE or compose_mod is None or not techniques:
        return []
    fn = getattr(compose_mod, "compose_all", None) or getattr(compose_mod, "compose", None)
    if fn is None:
        return []
    try:
        return fn(origin, dest, depart, return_date=ret,
                  cabin=cabin, passengers=max(1, int(adults or 1)),
                  techniques=tuple(techniques)) or []
    except Exception as e:
        common_log("compose_error", origin=origin, dest=dest, error=str(e))
        return []


def _expand_with_nearby_airports(
    iatas: list[str], max_nearby_per: int = 2, max_ground_minutes: int = 180
) -> tuple[list[str], list[str]]:
    """Augment a list of IATA codes with their nearby_hubs from airport_hubs.json.

    Returns (expanded_list, added_list). The expanded list preserves the
    user's input order and appends any added neighbors deduped to the end.
    Only neighbors within `max_ground_minutes` ground transport are added,
    capped at `max_nearby_per` per input airport.
    """
    if not iatas:
        return iatas, []
    hubs_data = load_data("airport_hubs") or {}
    regions = hubs_data.get("regions") or {}
    # Build a flat IATA → hub_record index.
    index: dict[str, dict] = {}
    for _region, hubs in regions.items():
        if not isinstance(hubs, list):
            continue
        for h in hubs:
            code = (h.get("iata") or "").upper()
            if code:
                index[code] = h
    have = set(c.upper() for c in iatas)
    added: list[str] = []
    out: list[str] = list(iatas)
    for code in list(iatas):
        rec = index.get(code.upper())
        if not rec:
            continue
        neighbors = rec.get("nearby_hubs") or []
        # Keep only viable neighbors (under ground time threshold), preserve order.
        picked = 0
        for n in neighbors:
            if picked >= max_nearby_per:
                break
            ncode = (n.get("iata") or "").upper()
            if not ncode or ncode in have:
                continue
            mins = n.get("ground_minutes") or 0
            if mins and mins > max_ground_minutes:
                continue
            have.add(ncode)
            out.append(ncode)
            added.append(ncode)
            picked += 1
    return out, added


@app.post("/api/search")
async def api_search(body: SearchRequest):
    t0 = time.time()
    origins = [o.upper().strip() for o in body.origins if o and o.strip()]
    dests = [d.upper().strip() for d in body.destinations if d and d.strip()]
    if not origins or not dests:
        return _err("origins and destinations are required", 400)
    # Auto-expand to nearby airports if the user enabled either toggle.
    origins_added: list[str] = []
    dests_added: list[str] = []
    if body.expand_origins:
        origins, origins_added = _expand_with_nearby_airports(origins)
    if body.expand_destinations:
        dests, dests_added = _expand_with_nearby_airports(dests)

    depart = body.effective_depart()
    if not depart:
        return _err("depart date is required", 400)
    # Date-window sanity: depart_from must not exceed depart_to.
    if body.depart_from and body.depart_to and body.depart_from > body.depart_to:
        return _err("depart_from must not be after depart_to", 400)
    if (not body.one_way) and body.return_from and body.return_to \
            and body.return_from > body.return_to:
        return _err("return_from must not be after return_to", 400)
    ret = body.effective_return()
    # Depart must be on or before return — otherwise upstream sources happily
    # return a one-way for the return date and waste the user's deadline.
    if (not body.one_way) and ret and depart and depart > ret:
        return _err("depart date must not be after return date", 400)
    # Fan out across every requested cabin (Y/PE/J/F), deduped, preserving order.
    raw_cabins = _normalize_cabin_list(body.cabin)
    seen_cab: set[str] = set()
    cabins: list[str] = []
    for c in raw_cabins:
        if c not in seen_cab:
            seen_cab.add(c)
            cabins.append(c)
    if not cabins:
        cabins = ["economy"]
    mode = (body.mode or "both").lower()
    techniques = _resolve_techniques(body.composers, mode)
    timed_out = False
    deadline = time.time() + 110.0  # generous; multi-cabin / nearby expansion can take 60-90s

    loop = asyncio.get_running_loop()
    tasks: list[asyncio.Future] = []
    task_labels: list[str] = []

    for o in origins:
        for d in dests:
            for cabin_one in cabins:
                if mode in ("cash", "both"):
                    tasks.append(loop.run_in_executor(
                        _EXEC, _do_cash, o, d, depart, ret, cabin_one,
                        body.adults, body.children, body.infants, body.max_stops))
                    task_labels.append(f"cash:{o}->{d}:{cabin_one}")
                if mode in ("award", "both"):
                    tasks.append(loop.run_in_executor(
                        _EXEC, _do_award, o, d, depart, ret, cabin_one,
                        max(1, body.adults)))
                    task_labels.append(f"award:{o}->{d}:{cabin_one}")
                if techniques:
                    tasks.append(loop.run_in_executor(
                        _EXEC, _do_compose, o, d, depart, ret, cabin_one,
                        body.adults, techniques))
                    task_labels.append(f"compose:{o}->{d}:{cabin_one}")

    results: list[dict] = []
    cash_count = award_count = composed_count = 0
    sources_used: set[str] = set()

    pending = set(tasks)
    label_for: dict[asyncio.Future, str] = dict(zip(tasks, task_labels))
    while pending:
        remaining = deadline - time.time()
        if remaining <= 0:
            timed_out = True
            for t in pending:
                t.cancel()
            break
        try:
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        except Exception as e:
            common_log("search_wait_error", error=str(e))
            break
        for t in done:
            label = label_for.get(t, "")
            # Label format: "<kind>:<O>-><D>:<cabin>"
            label_cabin = ""
            try:
                _kp, _rest = label.split(":", 1)
                if ":" in _rest:
                    label_cabin = _rest.split(":", 1)[1]
            except ValueError:
                label_cabin = ""
            try:
                rows = t.result() or []
            except asyncio.CancelledError:
                continue
            except Exception as e:
                common_log("search_subtask_error", label=label, error=str(e))
                continue
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                if r.get("error"):
                    # Per-source error markers from search_cash — drop from
                    # results list but record source.
                    src = r.get("source") or label.split(":")[0]
                    sources_used.add(str(src))
                    continue
                # Stamp cabin from search task if the source did not set one,
                # so multi-cabin fan-out produces rows correctly tagged for the
                # results table and downstream dedupe.
                if label_cabin and not r.get("cabin"):
                    r["cabin"] = label_cabin
                results.append(r)
                src = r.get("source") or ""
                if src:
                    sources_used.add(str(src))
            if label.startswith("cash:"):
                cash_count += sum(1 for r in rows
                                  if isinstance(r, dict) and not r.get("error"))
            elif label.startswith("award:"):
                award_count += sum(1 for r in rows
                                   if isinstance(r, dict) and not r.get("error"))
            elif label.startswith("compose:"):
                composed_count += sum(1 for r in rows
                                      if isinstance(r, dict) and not r.get("error"))

    # Dedupe near-identical itineraries (same source+carrier+dep+arr+price).
    # When a composed row (e.g. stopover-annotated award) collides with the
    # un-annotated original on every key field, prefer the composed copy —
    # otherwise the annotation gets silently dropped and meta.composed_count
    # disagrees with the visible result types.
    seen_keys: dict[tuple, int] = {}
    deduped: list[dict] = []

    def _has_composition(row: dict) -> bool:
        comp = row.get("composition") or {}
        ctype = comp.get("type")
        return bool(ctype) and ctype != "direct"

    for r in results:
        key = (
            r.get("source") or "",
            r.get("origin") or "",
            r.get("destination") or "",
            r.get("depart_date") or "",
            r.get("return_date") or "",
            r.get("cabin") or "",
            r.get("carrier") or "",
            round(float(r.get("price_usd") or 0.0), 2),
            int(r.get("miles") or 0),
        )
        if key in seen_keys:
            idx = seen_keys[key]
            # If existing has no composition and the new one does, swap in.
            if _has_composition(r) and not _has_composition(deduped[idx]):
                deduped[idx] = r
            continue
        seen_keys[key] = len(deduped)
        deduped.append(r)

    # Duration filter: drop itineraries exceeding max_hours (if specified).
    max_hours = body.max_hours if body.max_hours is not None else body.max_duration_hours
    duration_filtered = 0
    if max_hours and max_hours > 0:
        cap_min = int(max_hours) * 60
        kept: list[dict] = []
        for r in deduped:
            dur = r.get("duration_minutes")
            if dur is not None and dur > cap_min:
                duration_filtered += 1
                continue
            kept.append(r)
        deduped = kept

    try:
        balances = _load_user_balances()
        ranked = await loop.run_in_executor(_EXEC, _do_rank, deduped, balances)
    except Exception as e:
        common_log("rank_pipeline_error", error=str(e))
        ranked = deduped

    meta = {
        "ms": int((time.time() - t0) * 1000),
        "cash_count": cash_count,
        "award_count": award_count,
        "composed_count": composed_count,
        "sources_used": sorted(sources_used),
        "timed_out": timed_out,
        "techniques": techniques,
        "origins": origins,
        "destinations": dests,
        "origins_added": origins_added,
        "destinations_added": dests_added,
        "cabins": cabins,
        "one_way": body.one_way,
        "max_hours": max_hours,
        "duration_filtered": duration_filtered,
    }
    # Apply UI shape adapter so the table can render every field.
    adapted = [_adapt_search_row(r, i) for i, r in enumerate(ranked) if isinstance(r, dict)]
    return {"results": adapted, "meta": meta}


# ---------------------------------------------------------------------------
# /api/calendar — flexible-date cheapest-per-day heatmap
# ---------------------------------------------------------------------------

CALENDAR_CACHE_DIR = CACHE_DIR / "calendar"
CALENDAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
CALENDAR_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
CALENDAR_MAX_DAYS = 62                    # safety cap (~2 months)
CALENDAR_PER_DAY_TIMEOUT_S = 30
CALENDAR_TOTAL_TIMEOUT_S = 120
CALENDAR_MAX_WORKERS = 4                  # matches fast-flights semaphore


class CalendarRequest(BaseModel):
    origin: str = Field(min_length=2, max_length=8)
    destination: str = Field(min_length=2, max_length=8)
    start_date: str = Field(min_length=10, max_length=10)
    end_date: str = Field(min_length=10, max_length=10)
    cabin: str = Field(default="Y", max_length=32)
    adults: int = Field(default=1, ge=1, le=8)
    mode: str = Field(default="cash", max_length=8)

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def _upper(cls, v):
        return str(v or "").strip().upper()

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, v):
        if v is None:
            return "cash"
        s = str(v).lower().strip()
        if s not in {"cash", "award", "both"}:
            raise ValueError("mode must be cash|award|both")
        return s

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _date(cls, v):
        s = str(v or "").strip()
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except Exception as exc:
            raise ValueError(f"date must be YYYY-MM-DD, got {v!r}: {exc}") from exc
        return s


def _calendar_cache_path(key_parts: tuple) -> Path:
    raw = json.dumps(list(key_parts), sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return CALENDAR_CACHE_DIR / f"cal_{digest}.json"


def _calendar_cache_read(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > CALENDAR_CACHE_TTL_SECONDS:
            return None
        return json.loads(path.read_text())
    except Exception as e:
        common_log("calendar_cache_read_error", error=str(e))
        return None


def _calendar_cache_write(path: Path, data: dict) -> None:
    try:
        _atomic_write_text(path, json.dumps(data, default=str))
    except Exception as e:
        common_log("calendar_cache_write_error", error=str(e))


def _cheapest_cash_for_day(origin: str, dest: str, day: str,
                           cabin: str, adults: int) -> dict | None:
    """Run a single-day cash search and reduce to the cheapest itinerary record.
    Returns None when no real offer exists, or {'_error': msg} on hard error."""
    if not _HAS_CASH or search_cash is None:
        return None
    try:
        rows = search_cash.search(  # type: ignore[attr-defined]
            origin, dest, day, return_date=None,
            cabin=cabin, adults=adults, children=0, infants=0,
            max_stops=2,
        ) or []
    except Exception as e:
        common_log("calendar_cash_error", origin=origin, dest=dest,
                   day=day, error=str(e))
        return {"_error": str(e)}
    real = [r for r in rows
            if isinstance(r, dict) and not r.get("error")
            and r.get("price_usd") is not None]
    if not real:
        return None
    cheapest = min(real, key=lambda r: float(r.get("price_usd") or 1e12))
    return cheapest


def _cheapest_award_for_day(origin: str, dest: str, day: str,
                            cabin: str, adults: int) -> dict | None:
    """Cheapest award itinerary (by miles) for a single day."""
    if not _HAS_AWARD or search_award is None:
        return None
    try:
        result = search_award.search(  # type: ignore[attr-defined]
            origin, dest, day, return_date=None,
            cabins=(cabin,), passengers=max(1, adults),
        )
    except Exception as e:
        common_log("calendar_award_error", origin=origin, dest=dest,
                   day=day, error=str(e))
        return {"_error": str(e)}
    rows: list[dict]
    if isinstance(result, dict):
        rows = list(result.get("outbound") or [])
    elif isinstance(result, list):
        rows = result
    else:
        rows = []
    real = [r for r in rows
            if isinstance(r, dict) and not r.get("error")
            and (r.get("miles") or r.get("award_miles"))]
    if not real:
        return None

    def _miles_of(r: dict) -> float:
        return float(r.get("miles") or r.get("award_miles") or 1e12)
    return min(real, key=_miles_of)


def _enumerate_dates(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    if e < s:
        return []
    out: list[str] = []
    d = s
    while d <= e:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


@app.post("/api/calendar")
async def api_calendar(body: CalendarRequest):
    t0 = time.time()
    origin = body.origin
    destination = body.destination
    if not origin or not destination:
        return _err("origin and destination are required", 400)
    if origin == destination:
        return _err("origin and destination must differ", 400)
    if body.start_date > body.end_date:
        return _err("start_date must not be after end_date", 400)

    dates = _enumerate_dates(body.start_date, body.end_date)
    if not dates:
        return _err("empty date range", 400)
    if len(dates) > CALENDAR_MAX_DAYS:
        return _err(
            f"date range too large (max {CALENDAR_MAX_DAYS} days, got {len(dates)})",
            400,
        )

    # Translate UI cabin code (Y/PE/J/F) to canonical name used by search_*.
    cabin_internal = (_normalize_cabin_list([body.cabin]) or ["economy"])[0]
    mode = (body.mode or "cash").lower()

    cache_key = (
        "calendar.v1", origin, destination,
        body.start_date, body.end_date,
        cabin_internal, body.adults, mode,
    )
    cpath = _calendar_cache_path(cache_key)
    cached = _calendar_cache_read(cpath)
    if cached is not None:
        cached_meta = dict(cached.get("meta") or {})
        cached_meta["cached"] = True
        cached["meta"] = cached_meta
        return cached

    loop = asyncio.get_running_loop()
    # Local executor capped at 4 workers (matches fast-flights default semaphore).
    pool = ThreadPoolExecutor(max_workers=CALENDAR_MAX_WORKERS,
                              thread_name_prefix="fh-cal")
    try:
        cash_futs: dict[asyncio.Future, str] = {}
        award_futs: dict[asyncio.Future, str] = {}
        for d in dates:
            if mode in ("cash", "both"):
                fut = loop.run_in_executor(
                    pool, _cheapest_cash_for_day,
                    origin, destination, d, cabin_internal, body.adults)
                cash_futs[fut] = d
            if mode in ("award", "both"):
                fut = loop.run_in_executor(
                    pool, _cheapest_award_for_day,
                    origin, destination, d, cabin_internal, body.adults)
                award_futs[fut] = d

        deadline = time.time() + CALENDAR_TOTAL_TIMEOUT_S
        per_day_cash: dict[str, dict | None] = {d: None for d in dates}
        per_day_award: dict[str, dict | None] = {d: None for d in dates}
        errors_per_day: dict[str, str] = {}

        async def _drain(futs: dict[asyncio.Future, str],
                         dest_map: dict[str, dict | None]) -> None:
            pending = set(futs.keys())
            while pending:
                remaining = deadline - time.time()
                if remaining <= 0:
                    for t in pending:
                        d = futs[t]
                        errors_per_day[d] = errors_per_day.get(d) or "deadline_exceeded"
                        t.cancel()
                    return
                try:
                    done, pending = await asyncio.wait(
                        pending, timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED)
                except Exception as e:
                    common_log("calendar_wait_error", error=str(e))
                    return
                for t in done:
                    d = futs[t]
                    try:
                        row = t.result()
                    except asyncio.CancelledError:
                        errors_per_day[d] = errors_per_day.get(d) or "cancelled"
                        continue
                    except Exception as e:
                        errors_per_day[d] = str(e)
                        continue
                    if isinstance(row, dict) and row.get("_error"):
                        errors_per_day[d] = row.get("_error") or "search_error"
                        continue
                    dest_map[d] = row

        if cash_futs:
            await _drain(cash_futs, per_day_cash)
        if award_futs:
            await _drain(award_futs, per_day_award)

        days_out: list[dict] = []
        for d in dates:
            cash_row = per_day_cash.get(d)
            aw_row = per_day_award.get(d)
            entry: dict[str, Any] = {"date": d}
            sample_carrier = None
            sample_program = None
            if cash_row:
                try:
                    entry["cheapest_cash_usd"] = (
                        round(float(cash_row.get("price_usd")), 2)
                        if cash_row.get("price_usd") is not None else None
                    )
                except Exception:
                    entry["cheapest_cash_usd"] = None
                sample_carrier = cash_row.get("carrier") or cash_row.get("airline")
            else:
                entry["cheapest_cash_usd"] = None
            if aw_row:
                miles = aw_row.get("miles") or aw_row.get("award_miles")
                try:
                    entry["cheapest_award_miles"] = int(miles) if miles else None
                except Exception:
                    entry["cheapest_award_miles"] = None
                taxes = aw_row.get("taxes_usd") or aw_row.get("taxes") or 0
                try:
                    entry["cheapest_award_taxes"] = round(float(taxes), 2)
                except Exception:
                    entry["cheapest_award_taxes"] = None
                sample_program = (aw_row.get("program")
                                  or aw_row.get("source") or sample_program)
            else:
                entry["cheapest_award_miles"] = None
                entry["cheapest_award_taxes"] = None
            entry["sample_carrier"] = sample_carrier
            entry["sample_program"] = sample_program
            if d in errors_per_day and not (cash_row or aw_row):
                entry["error"] = errors_per_day[d]
            days_out.append(entry)

        meta = {
            "ms": int((time.time() - t0) * 1000),
            "errors_per_day": errors_per_day,
            "days_searched": len(dates),
            "origin": origin,
            "destination": destination,
            "cabin": cabin_internal,
            "adults": body.adults,
            "mode": mode,
            "start_date": body.start_date,
            "end_date": body.end_date,
            "cached": False,
            "timed_out": time.time() > deadline,
        }
        payload = {"days": days_out, "meta": meta}
        _calendar_cache_write(cpath, payload)
        return payload
    finally:
        pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# /api/mistakes
# ---------------------------------------------------------------------------

@app.get("/api/mistakes")
async def get_mistakes():
    cached = _read_mistakes_cache()
    fresh_enough = False
    if cached:
        try:
            age = time.time() - MISTAKES_CACHE_PATH.stat().st_mtime
            fresh_enough = age < MISTAKES_TTL_SECONDS
        except Exception:
            fresh_enough = False
    if cached and fresh_enough:
        return _adapt_mistakes_payload(cached)
    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(_EXEC, _do_ingest)
    return _adapt_mistakes_payload(payload)


@app.post("/api/mistakes/refresh")
async def refresh_mistakes():
    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(_EXEC, _do_ingest)
    return _adapt_mistakes_payload(payload)


# ---------------------------------------------------------------------------
# /api/watchlist
# ---------------------------------------------------------------------------

@app.get("/api/watchlist")
async def watchlist_list():
    return {"watches": [_adapt_watch(w) for w in _list_watches()]}


@app.post("/api/watchlist")
async def watchlist_create(body: WatchUpsert):
    watch = body.model_dump(exclude_none=True)
    # Require enough to be runnable — at least one origin/destination.
    origins = watch.get("origins") or ([watch["origin"]] if watch.get("origin") else [])
    dests = watch.get("destinations") or ([watch["destination"]] if watch.get("destination") else [])
    if not origins or not dests:
        return _err("watch must include origins[] and destinations[]", 400)
    try:
        saved = _save_watch(watch)
    except Exception as e:
        return _err(f"save failed: {e}", 500)
    return {"watch": _adapt_watch(saved)}


@app.patch("/api/watchlist/{wid}")
async def watchlist_patch(wid: str, body: WatchPatch):
    try:
        p = _watch_path(wid)
    except ValueError as e:
        return _err(str(e), 400)
    if not p.exists():
        return _err("not found", 404)
    try:
        existing = json.loads(p.read_text())
        patch = body.model_dump(exclude_none=True)
        existing.update(patch)
        existing["id"] = wid
        saved = _save_watch(existing)
    except Exception as e:
        return _err(f"patch failed: {e}", 500)
    return {"watch": _adapt_watch(saved)}


@app.delete("/api/watchlist/{wid}")
async def watchlist_delete(wid: str):
    try:
        ok = _delete_watch(wid)
    except ValueError as e:
        return _err(str(e), 400)
    if not ok:
        return _err("not found", 404)
    return {"ok": True, "id": wid}


@app.post("/api/watchlist/{wid}/run")
async def watchlist_run(wid: str):
    try:
        _watch_path(wid)
    except ValueError as e:
        return _err(str(e), 400)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(_EXEC, _run_watch, wid)
    except FileNotFoundError:
        return _err("not found", 404)
    except Exception as e:
        return _err(f"run failed: {e}", 500)
    return result


# ---------------------------------------------------------------------------
# /api/sweet-spots
# ---------------------------------------------------------------------------

@app.get("/api/sweet-spots")
async def sweet_spots():
    entries = _all_sweet_spots()
    meta = (load_data("sweet_spots") or {}).get("_meta", {})

    # ---- Adapter: add UI-facing aliases without dropping originals ----
    # The UI (ui/app.js FH.spots.renderRows) reads s.route / s.miles / s.notes,
    # but the dataset stores route_pattern / miles_oneway|miles_roundtrip /
    # constraints. Map each, preferring an existing UI-named field if the
    # dataset already provides one (back-compat with future data shapes).
    adapted: list[dict] = []
    for s in entries:
        if not isinstance(s, dict):
            adapted.append(s)
            continue
        row = dict(s)  # keep all original fields
        if not row.get("route"):
            row["route"] = s.get("route_pattern") or ""
        if not row.get("miles"):
            row["miles"] = s.get("miles_oneway") or s.get("miles_roundtrip") or 0
        if row.get("notes") is None:
            row["notes"] = s.get("constraints") or ""
        # Program/operating-carrier logo for the row's program cell. Prefer
        # operating_carrier when the row reports one (it's the metal flying
        # the route); fall back to the program's namesake carrier.
        row["program_logo_url"] = _program_logo_url(
            row.get("program"), row.get("operating_carrier")
        )
        # _search blob: bundle every field a user might type into the
        # filter (program, route, cabin, notes, title, operating_carrier,
        # status) plus country/region synonyms keyed off IATA codes or
        # city names that appear in the route/notes — e.g. typing
        # "japan" should hit any row mentioning Tokyo / NRT / HND / JAL /
        # ANA. The UI filter ORs this in alongside the visible fields.
        blob_parts = [
            str(row.get("program") or ""),
            str(row.get("route") or ""),
            str(row.get("cabin") or ""),
            str(row.get("notes") or ""),
            str(row.get("title") or ""),
            str(row.get("operating_carrier") or ""),
            str(row.get("status") or ""),
            str(row.get("id") or ""),
        ]
        blob_l = " ".join(blob_parts).lower()
        # Region synonyms — append on the basis of substrings that
        # already exist in the blob. Each entry is (trigger_substring,
        # synonym_to_add). Triggers are lowercase. Conservative list —
        # only common, unambiguous mappings.
        _REGION_SYNONYMS = [
            ("tokyo", "japan"), ("nrt", "japan"), ("hnd", "japan"),
            ("jal", "japan"), ("ana", "japan"),
            ("seoul", "korea"), ("icn", "korea"),
            ("hong kong", "hongkong china asia"), ("hkg", "hongkong"),
            ("bangkok", "thailand"), ("bkk", "thailand"),
            ("singapore", "asia"), ("sin", "singapore"),
            ("doha", "qatar middleeast"), ("doh", "qatar"),
            ("dubai", "uae middleeast"), ("dxb", "uae"),
            ("abu dhabi", "uae"), ("auh", "uae"),
            ("istanbul", "turkey"), ("ist", "turkey"),
            ("madrid", "spain europe"), ("mad", "spain"),
            ("paris", "france europe"), ("cdg", "france"),
            ("frankfurt", "germany europe"), ("fra", "germany"),
            ("munich", "germany"), ("muc", "germany"),
            ("london", "uk europe"), ("lhr", "uk"),
            ("amsterdam", "netherlands europe"), ("ams", "netherlands"),
            ("zurich", "switzerland europe"), ("zrh", "switzerland"),
            ("rome", "italy europe"), ("fco", "italy"),
            ("sydney", "australia"), ("syd", "australia"),
            ("auckland", "newzealand"), ("akl", "newzealand"),
            ("sao paulo", "brazil southamerica"), ("gru", "brazil"),
            ("buenos aires", "argentina southamerica"), ("eze", "argentina"),
            ("lima", "peru southamerica"),
        ]
        extras = []
        for trig, syn in _REGION_SYNONYMS:
            if trig in blob_l:
                extras.append(syn)
        row["_search"] = (blob_l + " " + " ".join(extras)).strip()
        adapted.append(row)

    # ---- Reshape transfer_partners: keyed-by-airline-program ----------
    # Source shape (data/transfer_partners.json):
    #   { "currencies": { "Chase Ultimate Rewards": { "transfer_partners":
    #       [ {"program": "...", "ratio": "1:1", ...}, ... ] }, ... } }
    # UI expects (app.js FH.spots / FH.balances):
    #   { "Air Canada Aeroplan": [ {"card": "Chase Ultimate Rewards",
    #                                "ratio": "1:1"}, ... ], ... }
    tp_root = load_data("transfer_partners") or {}
    tp_currencies = tp_root.get("currencies", {}) or {}
    by_program: dict[str, list[dict]] = {}
    for card_name, info in tp_currencies.items():
        if not isinstance(info, dict):
            continue
        for p in info.get("transfer_partners", []) or []:
            if not isinstance(p, dict):
                continue
            prog = p.get("program")
            if not prog:
                continue
            by_program.setdefault(prog, []).append({
                "card": card_name,
                "ratio": p.get("ratio") or p.get("ratio_premium") or "1:1",
            })

    # ---- Mirror partners under sweet-spot program aliases -----------
    # Sweet-spot entries may use program strings that don't match the
    # canonical keys in transfer_partners.json (e.g. "American AAdvantage"
    # vs. "American Airlines AAdvantage", or "Iberia Plus / British
    # Airways Club Avios" which is two programs joined). For each
    # sweet-spot program that has aliases, copy the canonical partner
    # list under the sweet-spot string so FH.spots.partners[s.program]
    # resolves in the UI without further mapping. Dedupe on card name
    # in case a partner appears under multiple aliases (e.g. both
    # "Iberia Plus" and "British Airways Club" share Chase).
    for ss_program, aliases in _SWEET_SPOT_PROGRAM_ALIASES.items():
        merged: list[dict] = []
        seen_cards: set[str] = set()
        for canonical in aliases:
            for entry in by_program.get(canonical, []):
                card = entry.get("card", "")
                if card in seen_cards:
                    continue
                seen_cards.add(card)
                merged.append(entry)
        if merged:
            # Only set if absent — never clobber a real exact-match list
            # (defensive; today no sweet-spot string collides with a
            # canonical key, but data drift is cheap insurance).
            by_program.setdefault(ss_program, merged)

    return {
        "sweet_spots": adapted,
        # UI-facing: keyed by airline program → list of {card, ratio}.
        "transfer_partners": by_program,
        # Original currencies-keyed shape, preserved for any non-UI consumer.
        "transfer_partners_raw": tp_currencies,
        "_meta": meta,
        "count": len(adapted),
    }


@app.get("/api/sweet-spots/{spid}/transfers")
async def sweet_spot_transfers(spid: str):
    ss = _sweet_spot_by_id(spid)
    if not ss:
        return _err("sweet spot not found", 404)
    bal = _load_user_balances()
    paths = _transfer_paths(ss, bal)
    eff = effective_balances(bal)
    # Resolve effective-balance through the same alias map used by
    # _transfer_paths so sweet-spot strings like "American AAdvantage"
    # find their canonical "American Airlines AAdvantage" effective row,
    # and the slash-joined "Iberia Plus / British Airways Club Avios"
    # picks the max of its underlying program effectives (best path).
    eff_for_prog = 0
    for alias in _program_aliases(ss.get("program") or ""):
        v = eff.get(alias)
        if isinstance(v, (int, float)) and v > eff_for_prog:
            eff_for_prog = int(v)
    return {
        "id": spid,
        "sweet_spot": ss,
        "paths": paths,
        "effective_balance_for_program": eff_for_prog,
        # Prefer one-way miles in the math the UI surfaces — matches the
        # `miles` mirror field shown in the sweet-spots table + overlay.
        "miles_needed": ss.get("miles_oneway") or ss.get("miles_roundtrip") or 0,
    }


# ---------------------------------------------------------------------------
# /api/balances
# ---------------------------------------------------------------------------

@app.get("/api/balances")
async def balances_get():
    bal = _load_user_balances()
    eff = effective_balances(bal)
    # Mirror programs+effective into a flat list the UI renders directly,
    # and mirror full-name currency keys down to their UI abbreviations.
    enriched = dict(bal)
    enriched["currencies"] = _mirror_currency_abbrevs(bal.get("currencies") or {})
    enriched["airlines"] = _airlines_from_balances(bal, eff)
    return {
        "balances": enriched,
        "effective": eff,
        "source": "user_balances.json" if USER_BAL_PATH.exists()
                  else "user_balances.example.json",
    }


_BAL_KEY_BLOCKLIST = {"__proto__", "constructor", "prototype", "__defineGetter__",
                      "__defineSetter__", "__lookupGetter__", "__lookupSetter__"}
_BAL_MAX_KEYS = 64        # plenty for currencies + programs combined
_BAL_MAX_KEY_LEN = 80     # full canonical names like "Chase Ultimate Rewards" = 22


def _sanitize_balance_dict(d: Any, label: str) -> dict:
    """Reject prototype-pollution keys, oversized maps, and non-numeric values.

    Returns a cleaned dict. Raises ValueError on any sketchy input so the
    handler can return a 400 instead of silently dropping data.
    """
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError(f"{label} must be an object")
    if len(d) > _BAL_MAX_KEYS:
        raise ValueError(f"{label} has too many keys (max {_BAL_MAX_KEYS})")
    out: dict[str, Any] = {}
    for k, v in d.items():
        if not isinstance(k, str):
            raise ValueError(f"{label} keys must be strings")
        if k in _BAL_KEY_BLOCKLIST:
            # Drop silently — these are pollution attempts, not user data.
            continue
        if len(k) == 0 or len(k) > _BAL_MAX_KEY_LEN:
            raise ValueError(f"{label} key length out of range")
        out[k] = v
    return out


@app.post("/api/balances")
async def balances_post(body: dict):
    # Accept either {currencies, programs} (canonical) or {currencies, airlines}
    # (UI shape — list of {program, balance/miles}). Always persist canonical.
    # `currencies` may use UI abbreviations (UR/MR/TY/VENTURE/BILT/BONVOY),
    # full names, or both — full names always win when both are supplied.
    if not isinstance(body, dict):
        return _err("body must be an object", 400)
    body.setdefault("currencies", {})

    # Strip prototype-pollution keys before any further processing.
    try:
        body["currencies"] = _sanitize_balance_dict(
            body.get("currencies"), "currencies")
        if "programs" in body:
            body["programs"] = _sanitize_balance_dict(
                body.get("programs"), "programs")
    except ValueError as ve:
        return _err(str(ve), 400)

    # Expand abbreviations → full canonical names before persisting.
    body["currencies"] = _expand_currency_abbrevs(body.get("currencies") or {})

    if "airlines" in body and "programs" not in body:
        airlines = body.pop("airlines")
        programs: dict[str, int] = {}
        if isinstance(airlines, list):
            for entry in airlines:
                if not isinstance(entry, dict):
                    continue
                program = (entry.get("program") or "").strip()
                if not program:
                    continue
                raw = entry.get("balance")
                if raw is None:
                    raw = entry.get("miles")
                try:
                    programs[program] = int(raw or 0)
                except (TypeError, ValueError):
                    programs[program] = 0
        body["programs"] = programs
    else:
        body.setdefault("programs", {})
        # If both came in, keep canonical programs and drop the mirror list
        # before saving to disk so user_balances.json stays clean.
        body.pop("airlines", None)

    try:
        save_json(USER_BAL_PATH, body)
    except Exception as e:
        return _err(f"write failed: {e}", 500)
    eff = effective_balances(body)
    enriched = dict(body)
    enriched["currencies"] = _mirror_currency_abbrevs(body.get("currencies") or {})
    enriched["airlines"] = _airlines_from_balances(body, eff)
    return {"ok": True, "balances": enriched, "effective": eff}


# ---------------------------------------------------------------------------
# /api/settings
# ---------------------------------------------------------------------------

@app.get("/api/settings")
async def settings_get():
    return {"settings": _masked_settings()}


@app.post("/api/settings")
async def settings_post(body: SettingsBody):
    incoming = body.model_dump(exclude_none=True)

    # Reconcile UI aliases → canonical .env keys.
    # Masked values (anything ending in "****") are never written — they're
    # only how we display existing secrets to the UI. Saving a masked value
    # back as the real key would clobber the live secret.
    canonical: dict[str, str] = {}
    def _accept_secret(v: str) -> bool:
        return bool(v) and not v.endswith("****")

    if "SEATS_AERO_API_KEY" in incoming:
        v = str(incoming["SEATS_AERO_API_KEY"])
        if _accept_secret(v) or v == "":
            canonical["SEATS_AERO_API_KEY"] = v
    elif "seats_aero_key" in incoming:
        v = str(incoming["seats_aero_key"])
        if _accept_secret(v) or v == "":
            canonical["SEATS_AERO_API_KEY"] = v

    if "DUFFEL_API_TOKEN" in incoming:
        v = str(incoming["DUFFEL_API_TOKEN"])
        if _accept_secret(v) or v == "":
            canonical["DUFFEL_API_TOKEN"] = v

    if "TELEGRAM_WEBHOOK_URL" in incoming:
        v = str(incoming["TELEGRAM_WEBHOOK_URL"])
        if _accept_secret(v) or v == "":
            canonical["TELEGRAM_WEBHOOK_URL"] = v
    elif "telegram_webhook" in incoming:
        v = str(incoming["telegram_webhook"])
        if _accept_secret(v) or v == "":
            canonical["TELEGRAM_WEBHOOK_URL"] = v

    if "TELEGRAM_CHAT_ID" in incoming:
        v = str(incoming["TELEGRAM_CHAT_ID"])
        if _accept_secret(v) or v == "":
            canonical["TELEGRAM_CHAT_ID"] = v

    if "AWARDWALLET_API_KEY" in incoming:
        v = str(incoming["AWARDWALLET_API_KEY"])
        if _accept_secret(v) or v == "":
            canonical["AWARDWALLET_API_KEY"] = v

    cpp_mode = incoming.get("cpp_mode") or incoming.get("cpp_source")
    if cpp_mode:
        canonical["FH_CPP_MODE"] = str(cpp_mode)

    if "cache_ttl_minutes" in incoming:
        canonical["FH_CACHE_TTL_MIN"] = str(int(incoming["cache_ttl_minutes"]))
    elif "cache_ttl" in incoming:
        # UI sends seconds → convert
        try:
            canonical["FH_CACHE_TTL_MIN"] = str(max(1, int(incoming["cache_ttl"]) // 60))
        except Exception:
            pass

    if not canonical:
        return _err("no recognized settings keys", 400)

    try:
        _write_env_dict(canonical)
    except Exception as e:
        return _err(f"env write failed: {e}", 500)

    # Reset env-loaded cache so subsequent get_env() picks up changes
    try:
        import common as _c  # type: ignore
        _c._env_loaded = False  # noqa: SLF001
        for k in canonical:
            os.environ.pop(k, None)
    except Exception:
        pass

    return {"ok": True, "settings": _masked_settings()}


# ---------------------------------------------------------------------------
# /api/refresh-data + UI's /api/refresh alias
# ---------------------------------------------------------------------------

def _refresh_all() -> dict:
    counts: dict[str, Any] = {}
    # Mistakes feed
    try:
        m = _do_ingest()
        counts["mistakes"] = len(m.get("mistakes") or [])
    except Exception as e:
        counts["mistakes_error"] = str(e)
    # Transfer-partner cache reload (just bust the in-memory cache).
    try:
        import common as _c  # type: ignore
        _c._TRANSFER_CACHE = None  # noqa: SLF001
        tp = load_data("transfer_partners") or {}
        counts["transfer_currencies"] = len(tp.get("currencies", {}))
    except Exception as e:
        counts["transfer_error"] = str(e)
    # Sweet-spots reload count
    try:
        counts["sweet_spots"] = len(_all_sweet_spots())
    except Exception as e:
        counts["sweet_spots_error"] = str(e)
    counts["refreshed_at"] = _now_iso()
    return counts


@app.post("/api/refresh-data")
async def refresh_data():
    loop = asyncio.get_running_loop()
    counts = await loop.run_in_executor(_EXEC, _refresh_all)
    return counts


@app.post("/api/refresh")
async def refresh_alias():
    return await refresh_data()


# ---------------------------------------------------------------------------
# Generic /api/* error handler (last-resort)
# ---------------------------------------------------------------------------

def _http_error_message(detail: Any, fallback: str) -> str:
    """FastAPI sometimes hands us `detail` as str, sometimes as dict/list
    (e.g. raising HTTPException(detail={...})). Always return a string so
    the {error, code} contract holds."""
    if detail is None:
        return fallback
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, default=str)
    except Exception:
        return str(detail)


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    msg = _http_error_message(exc.detail, "http error")
    return JSONResponse(
        {"error": msg, "code": exc.status_code},
        status_code=exc.status_code,
    )


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exc(request: Request, exc: StarletteHTTPException):
    """Catches the lower-level 404/405 (no matching route / wrong method)
    that don't go through fastapi.HTTPException, so the JSON shape stays
    consistent with the rest of the API."""
    msg = _http_error_message(exc.detail, "http error")
    return JSONResponse(
        {"error": msg, "code": exc.status_code},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exc(request: Request, exc: RequestValidationError):
    """Pydantic body/query validation failures. Reshape from the default
    {"detail":[...]} to {"error","code","errors"} so the UI can treat every
    error response identically."""
    errors: list[dict] = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
        errors.append({
            "field": loc,
            "msg": e.get("msg") or "invalid",
            "type": e.get("type") or "",
        })
    if errors:
        first = errors[0]
        summary = f"{first['field']}: {first['msg']}" if first["field"] else first["msg"]
    else:
        summary = "validation failed"
    return JSONResponse(
        {"error": summary, "code": 422, "errors": errors},
        status_code=422,
    )


@app.exception_handler(Exception)
async def generic_exc(request: Request, exc: Exception):
    common_log("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        {"error": "internal server error", "code": 500, "detail": str(exc)},
        status_code=500,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=8721,
        reload=False,
        log_level="info",
        app_dir=str(UI_DIR),
    )
