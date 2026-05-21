"""
common.py — shared utilities, schemas, and config for the flight-hacker skill.

Imported by every other script. Exposes:
  - SKILL_ROOT, DATA_DIR, CACHE_DIR, WATCHES_DIR
  - load_env()                  — reads ~/.flight-hacker/.env or $SKILL_ROOT/.env
  - get_env(key, default=None)
  - log(event, **fields)        — single-line JSON to stderr
  - cache_get(key, ttl), cache_set(key, value)
  - load_json(path), save_json(path, value)
  - load_data(name)             — loads data/<name>.json
  - normalize_iata(s), parse_date(s)
  - ITINERARY_SCHEMA            — reference shape, both cash + award normalized rows
  - cpp_floor(program)          — points-valuation floor lookup
  - effective_balances(user_balances) — direct + reachable transfers
  - http_get(url, **kw), http_post(url, json=..., headers=..., **kw)
  - retry_call(fn, attempts=2, backoff=2.0)
  - notify_telegram(text)       — posts to TELEGRAM_WEBHOOK_URL if set
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


SKILL_ROOT = Path("/Users/admin/Desktop/flight-hacker")
DATA_DIR = SKILL_ROOT / "data"
CACHE_DIR = SKILL_ROOT / "cache"
WATCHES_DIR = SKILL_ROOT / "watches"
ENV_FILE = SKILL_ROOT / ".env"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
WATCHES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(event: str, **fields: Any) -> None:
    payload = {"event": event, "ts": datetime.now(timezone.utc).isoformat()}
    payload.update(fields)
    try:
        sys.stderr.write(json.dumps(payload, default=str) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Env loader (no python-dotenv dependency)
# ---------------------------------------------------------------------------

_env_loaded = False


def load_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    candidates = [ENV_FILE, Path.home() / ".flight-hacker" / ".env"]
    for f in candidates:
        if not f.exists():
            continue
        try:
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception as e:
            log("env_load_error", file=str(f), error=str(e))
    _env_loaded = True


def get_env(key: str, default: str | None = None) -> str | None:
    load_env()
    return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# Filesystem JSON helpers
# ---------------------------------------------------------------------------

def load_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log("json_load_error", path=str(p), error=str(e))
        return default


def save_json(path: Path | str, value: Any) -> None:
    """Atomically write `value` to `path`. Safe under concurrent writers:
    each writer uses a unique tmp file (pid + monotonic id) so that two
    concurrent calls cannot race on a shared `.tmp` name. Last writer wins
    (POSIX rename semantics), but neither writer raises FileNotFoundError."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Unique-per-writer tmp suffix; .tmp-<pid>-<nanos>-<rand>
    suffix = f".tmp-{os.getpid()}-{time.monotonic_ns()}-{os.urandom(2).hex()}"
    tmp = p.with_suffix(p.suffix + suffix)
    try:
        tmp.write_text(json.dumps(value, indent=2, default=str))
        os.replace(tmp, p)
    finally:
        # Belt + suspenders: if replace failed before unlinking, clean stragglers.
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def load_data(name: str) -> Any:
    """Load /data/<name>.json (with or without .json extension)."""
    if not name.endswith(".json"):
        name = name + ".json"
    return load_json(DATA_DIR / name, default={})


# ---------------------------------------------------------------------------
# Cache (file-based, TTL'd)
# ---------------------------------------------------------------------------

def _cache_path(key: str) -> Path:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def cache_get(key: str, ttl_seconds: int = 3600) -> Any:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        age = time.time() - p.stat().st_mtime
        if age > ttl_seconds:
            return None
        data = json.loads(p.read_text())
        log("cache_hit", key=key[:80], age_s=round(age, 1))
        return data
    except Exception as e:
        log("cache_read_error", key=key[:80], error=str(e))
        return None


def cache_set(key: str, value: Any) -> None:
    p = _cache_path(key)
    # Unique tmp per writer so concurrent cache_set() calls don't race the
    # same shared `.tmp` path (which would cause a spurious FileNotFoundError
    # on the second `replace`). Compute `tmp` BEFORE the try so the cleanup
    # block can reference it safely. Append the unique suffix to the full
    # filename (matching save_json's convention) — `Path.with_suffix(suffix)`
    # would otherwise strip the trailing `.json`.
    suffix = f".tmp-{os.getpid()}-{time.monotonic_ns()}-{os.urandom(2).hex()}"
    tmp = p.with_suffix(p.suffix + suffix)
    try:
        tmp.write_text(json.dumps(value, default=str))
        os.replace(tmp, p)
    except Exception as e:
        log("cache_write_error", key=key[:80], error=str(e))
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Safari/605.1.15"
)


def http_get(url: str, headers: dict | None = None, params: dict | None = None,
             timeout: int = 30) -> tuple[int, dict, bytes]:
    if params:
        url = url + ("&" if "?" in url else "?") + urlparse.urlencode(params)
    req_headers = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    req = urlrequest.Request(url, headers=req_headers, method="GET")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urlerror.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


def http_post(url: str, json_body: Any = None, headers: dict | None = None,
              timeout: int = 30) -> tuple[int, dict, bytes]:
    body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    req_headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = urlrequest.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urlerror.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


def retry_call(fn: Callable, attempts: int = 2, backoff: float = 2.0,
               retry_on: tuple = (urlerror.URLError, ConnectionError, TimeoutError)):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as e:
            last = e
            log("retry", attempt=i + 1, error=str(e))
            if i < attempts - 1:
                time.sleep(backoff)
    if last:
        raise last


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_iata(s: str) -> str:
    return (s or "").strip().upper()[:3]


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def date_range(start: str, end: str) -> list[str]:
    a = parse_date(start)
    b = parse_date(end)
    out = []
    cur = a
    while cur <= b:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Schema reference
# ---------------------------------------------------------------------------

# Canonical itinerary row — both cash and award producers emit this shape.
ITINERARY_SCHEMA = {
    "source": "google_flights|duffel|seats.aero",
    "kind": "cash|award",
    "origin": "IATA",
    "destination": "IATA",
    "depart_date": "YYYY-MM-DD",
    "return_date": "YYYY-MM-DD|null",
    "carrier": "2-letter IATA",
    "carriers_all": ["..."],
    "operating_carrier": "2-letter IATA|null",
    "cabin": "economy|premium_economy|business|first",
    "stops": 0,
    "duration_minutes": 0,
    # cash fields
    "price_usd": 0.0,
    "currency": "USD",
    "fare_brand": "Light|Standard|Flex|null",
    "baggage_included": True,
    "refundable": False,
    # award fields
    "program": "Issuing loyalty program for awards (e.g. United MileagePlus)",
    "miles": 0,
    "taxes_usd": 0.0,
    "available_seats": 0,
    # composed-route metadata (populated by compose.py)
    "composition": {
        "type": "direct|positioning|hidden_city|open_jaw|stopover",
        "legs": [],
        "extra_cost_usd": 0.0,
        "extra_time_minutes": 0,
        "risk": "LEGAL|GRAY|TOS-RISK",
        "notes": "",
    },
    # universal
    "segments": [
        {
            "carrier": "NH",
            "flight_no": "9",
            "from": "JFK",
            "to": "NRT",
            "depart": "2026-07-15T11:00",
            "arrive": "2026-07-16T15:00",
            "duration_minutes": 850,
            "aircraft": "B789",
        }
    ],
    "deep_link": "https://...",
    "raw": {"...": "source-specific payload"},
}


def make_empty_itinerary() -> dict:
    """Build a fresh itinerary dict pre-populated with safe defaults."""
    return {
        "source": "",
        "kind": "cash",
        "origin": "",
        "destination": "",
        "depart_date": "",
        "return_date": None,
        "carrier": "",
        "carriers_all": [],
        "operating_carrier": None,
        "cabin": "economy",
        "stops": 0,
        "duration_minutes": 0,
        "price_usd": 0.0,
        "currency": "USD",
        "fare_brand": None,
        "baggage_included": None,
        "refundable": None,
        "program": None,
        "miles": 0,
        "taxes_usd": 0.0,
        "available_seats": 0,
        "composition": {
            "type": "direct",
            "legs": [],
            "extra_cost_usd": 0.0,
            "extra_time_minutes": 0,
            "risk": "LEGAL",
            "notes": "",
        },
        "segments": [],
        "deep_link": None,
        "raw": {},
    }


# ---------------------------------------------------------------------------
# Points / valuation helpers
# ---------------------------------------------------------------------------

_VALUATIONS_CACHE: dict | None = None


def _valuations() -> dict:
    global _VALUATIONS_CACHE
    if _VALUATIONS_CACHE is None:
        _VALUATIONS_CACHE = load_data("points_valuations")
    return _VALUATIONS_CACHE or {}


def cpp_floor(program: str) -> float:
    """Return floor CPP for a program; 1.0 if unknown.
    Looks in data/points_valuations.json under any of:
      currencies (list)  |  valuations (list)  |  programs (list)  |  data (list)
    """
    if not program:
        return 1.0
    vals = _valuations()
    entries = (
        vals.get("currencies")
        or vals.get("valuations")
        or vals.get("programs")
        or vals.get("data")
        or []
    )
    if isinstance(entries, dict):
        entries = list(entries.values())
    p_norm = program.lower().strip()
    # Fuzzy match: program name appears in either direction; also try last-word match.
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("program") or entry.get("name") or "").lower()
        if not name:
            continue
        if name in p_norm or p_norm in name:
            f = entry.get("floor_cpp") or entry.get("floor") or entry.get("cpp_floor")
            if f:
                return float(f)
    # Last-word fallback: e.g. "Virgin Atlantic Flying Club" → match "Virgin Atlantic"
    tokens = [t for t in p_norm.replace(",", " ").split() if len(t) > 2]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("program") or entry.get("name") or "").lower()
        if not name:
            continue
        if any(t in name for t in tokens):
            f = entry.get("floor_cpp") or entry.get("floor") or entry.get("cpp_floor")
            if f:
                return float(f)
    return 1.0


# ---------------------------------------------------------------------------
# Transfer-partner graph + effective balances
# ---------------------------------------------------------------------------

_TRANSFER_CACHE: dict | None = None


def _transfer_partners() -> dict:
    global _TRANSFER_CACHE
    if _TRANSFER_CACHE is None:
        _TRANSFER_CACHE = load_data("transfer_partners")
    return _TRANSFER_CACHE or {}


def effective_balances(user_balances: dict) -> dict:
    """
    Compute effective balance per airline program:
      effective = direct + sum(card_balance * (out / in) for each partner)

    Ratio convention: "X:Y" means X source units yield Y destination units, so
    the multiplier is Y/X (NOT X/Y — that bug would inflate all transfers).
    Examples:
      "1:1"   → 1.0   (Chase 100 = United 100)
      "1:0.8" → 0.8   (Amex 100 = JetBlue 80)
      "5:3"   → 0.6   (Capital One 5 = JetBlue 3)
      "3:1"   → 0.333 (Marriott 3 = airline 1)
      "2:1"   → 0.5

    user_balances shape:
      {
        "currencies": {"Chase Ultimate Rewards": 145000, "Amex MR": 80000, ...},
        "programs":   {"United MileagePlus": 16000, "Aeroplan": 0, ...}
      }
    """
    out = dict(user_balances.get("programs", {}))
    tp = _transfer_partners().get("currencies", {})
    card_balances = user_balances.get("currencies", {})
    for card, bal in card_balances.items():
        if not bal:
            continue
        card_node = None
        for k, v in tp.items():
            if k.lower() == card.lower() or card.lower() in k.lower():
                card_node = v
                break
        if not card_node:
            continue
        for partner in card_node.get("transfer_partners", []):
            prog = partner.get("program")
            # Use "ratio" or "ratio_premium" (Citi) — premium ratio is the better
            # one available to mid-tier+ cardholders.
            ratio = (
                partner.get("ratio")
                or partner.get("ratio_premium")
                or partner.get("ratio_basic")
                or "1:1"
            )
            try:
                num, den = ratio.split(":")
                # X:Y means X source -> Y destination; multiplier = Y/X.
                multiplier = float(den) / float(num)
            except Exception:
                multiplier = 1.0
            out[prog] = out.get(prog, 0) + bal * multiplier
    return out


# ---------------------------------------------------------------------------
# Telegram notifications (optional)
# ---------------------------------------------------------------------------

def notify_telegram(text: str) -> bool:
    """Post a plaintext message to TELEGRAM_WEBHOOK_URL (HTML-escaped).
    Webhook should accept POST {"text": "..."}; alternative: Telegram Bot API
    URL of form https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=...
    Returns True on 2xx.
    """
    webhook = get_env("TELEGRAM_WEBHOOK_URL")
    if not webhook:
        return False
    try:
        if "api.telegram.org" in webhook and "sendMessage" in webhook:
            chat_id = get_env("TELEGRAM_CHAT_ID")
            payload = {"text": text}
            if chat_id and "chat_id=" not in webhook:
                payload["chat_id"] = chat_id
            status, _, _ = http_post(webhook, json_body=payload)
        else:
            status, _, _ = http_post(webhook, json_body={"text": text})
        return 200 <= status < 300
    except Exception as e:
        log("telegram_error", error=str(e))
        return False


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    load_env()
    log("smoke", skill_root=str(SKILL_ROOT), data_dir_exists=DATA_DIR.exists())
    sweet = load_data("sweet_spots")
    valuations = load_data("points_valuations")
    print(json.dumps({
        "skill_root": str(SKILL_ROOT),
        "data_files_loaded": {
            "sweet_spots": len(sweet.get("sweet_spots", sweet) if isinstance(sweet, dict) else []),
            "points_valuations": bool(valuations),
        },
        "env": {
            "SEATS_AERO_API_KEY": bool(get_env("SEATS_AERO_API_KEY")),
            "DUFFEL_API_TOKEN": bool(get_env("DUFFEL_API_TOKEN")),
            "TELEGRAM_WEBHOOK_URL": bool(get_env("TELEGRAM_WEBHOOK_URL")),
        },
        "cpp_floor_examples": {
            "United MileagePlus": cpp_floor("United MileagePlus"),
            "Aeroplan": cpp_floor("Aeroplan"),
            "Virgin Atlantic Flying Club": cpp_floor("Virgin Atlantic Flying Club"),
        },
    }, indent=2))
