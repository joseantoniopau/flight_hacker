"""
watch.py — Recurring watchlist runner with Telegram alerts.

Each watch is a JSON file in /watches/<id>.json:
{
  "id": "uuid",
  "label": "JFK-NRT biz under $3500",
  "origins": ["JFK", "EWR", "LGA"],
  "destinations": ["NRT", "HND"],
  "depart_window": {"from": "2026-09-01", "to": "2026-09-30"},
  "return_window": {"from": "2026-09-20", "to": "2026-10-15"},
  "cabin": ["business", "first"],
  "adults": 2, "children": 0, "infants": 0,
  "max_price_usd": 3500.0,
  "max_miles": 200000,
  "mode": "both",
  "composers": {"positioning": true, "hidden_city": false, "open_jaw": true, "stopover": true},
  "frequency_hours": 4,
  "alerts": {"telegram": true},
  "paused": false,
  "last_run": "2026-05-21T12:30:00Z",
  "last_results": [],          # top 3 by score from previous run
  "last_alert_at": null
}

Public API:
    run_one(watch_id) -> dict     — runs one watch now and writes back last_run/last_results
    run_due() -> list[dict]       — runs every watch whose last_run + frequency_hours <= now
    run_all() -> list[dict]       — force-run every watch regardless of frequency
    install_launchagent()         — writes ~/Library/LaunchAgents/com.fh.watcher.plist

CLI:
    python watch.py [--id <uuid>] [--due] [--all] [--install-launchagent]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    SKILL_ROOT,
    WATCHES_DIR,
    DATA_DIR,
    load_json,
    save_json,
    log,
    notify_telegram,
    load_env,
)

try:
    from search_cash import search as cash_search
except Exception as e:  # pragma: no cover - degrade gracefully
    log("watch_import_warning", module="search_cash", error=str(e))
    cash_search = None

try:
    from search_award import search as award_search
except Exception as e:  # pragma: no cover
    log("watch_import_warning", module="search_award", error=str(e))
    award_search = None

try:
    from compose import compose as compose_fn
except Exception as e:  # pragma: no cover
    log("watch_import_warning", module="compose", error=str(e))
    compose_fn = None

try:
    from rank import rank as rank_fn
except Exception as e:  # pragma: no cover
    log("watch_import_warning", module="rank", error=str(e))
    rank_fn = None


# ---------------------------------------------------------------------------
# Watch CRUD
# ---------------------------------------------------------------------------

def list_watches() -> list[dict]:
    out = []
    for p in WATCHES_DIR.glob("*.json"):
        w = load_json(p)
        if isinstance(w, dict):
            w.setdefault("id", p.stem)
            out.append(w)
    return out


def get_watch(watch_id: str) -> dict | None:
    p = WATCHES_DIR / f"{watch_id}.json"
    if not p.exists():
        return None
    return load_json(p)


def save_watch(watch: dict) -> dict:
    if not watch.get("id"):
        watch["id"] = uuid.uuid4().hex[:12]
    save_json(WATCHES_DIR / f"{watch['id']}.json", watch)
    return watch


def delete_watch(watch_id: str) -> bool:
    p = WATCHES_DIR / f"{watch_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------

def is_due(watch: dict, now: datetime | None = None) -> bool:
    if watch.get("paused"):
        return False
    freq_h = float(watch.get("frequency_hours") or 4)
    last = watch.get("last_run")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return True
    now_dt = now or datetime.now(timezone.utc)
    return (now_dt - last_dt) >= timedelta(hours=freq_h)


def _dates_for_window(window: dict | None) -> list[str]:
    """Return up to 7 sampled dates inside the window — start, end, and 5 evenly spaced midpoints."""
    if not window or not window.get("from") or not window.get("to"):
        return []
    a = datetime.fromisoformat(window["from"])
    b = datetime.fromisoformat(window["to"])
    if b < a:
        return [a.strftime("%Y-%m-%d")]
    span = (b - a).days
    if span == 0:
        return [a.strftime("%Y-%m-%d")]
    n = min(7, max(2, span // 3))
    step = max(1, span // (n - 1))
    out = []
    cur = a
    while cur <= b:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=step)
    if out[-1] != b.strftime("%Y-%m-%d"):
        out.append(b.strftime("%Y-%m-%d"))
    return out[:7]


# ---------------------------------------------------------------------------
# Run a single watch
# ---------------------------------------------------------------------------

def _balance_dict() -> dict:
    p = DATA_DIR / "user_balances.json"
    if not p.exists():
        p = DATA_DIR / "user_balances.example.json"
    return load_json(p, default={}) or {}


def run_one(watch_id: str) -> dict:
    watch = get_watch(watch_id)
    if not watch:
        return {"id": watch_id, "error": "watch not found"}

    log("watch_run_start", id=watch_id, label=watch.get("label"))
    t0 = time.time()

    origins = watch.get("origins") or [watch.get("origin")]
    destinations = watch.get("destinations") or [watch.get("destination")]
    origins = [o for o in origins if o]
    destinations = [d for d in destinations if d]
    cabins = watch.get("cabin") or watch.get("cabins") or ["economy"]
    if isinstance(cabins, str):
        cabins = [cabins]
    adults = int(watch.get("adults") or 1)
    mode = (watch.get("mode") or "both").lower()
    composers = watch.get("composers") or {}
    techniques = tuple(k for k, v in composers.items() if v)

    depart_dates = _dates_for_window(watch.get("depart_window"))
    return_dates = _dates_for_window(watch.get("return_window")) or [None]
    if not depart_dates:
        depart_dates = [(datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")]

    all_rows = []
    jobs = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for o in origins:
            for d in destinations:
                for cab in cabins:
                    for dep in depart_dates[:5]:
                        ret = (return_dates[0] if return_dates else None)
                        if mode in ("cash", "both") and cash_search:
                            jobs.append(ex.submit(_safe_cash, o, d, dep, ret, cab, adults))
                        if mode in ("award", "both") and award_search:
                            jobs.append(ex.submit(_safe_award, o, d, dep, ret, cab, adults))
        for fut in as_completed(jobs):
            try:
                rows = fut.result(timeout=120)
                if rows:
                    all_rows.extend(rows)
            except Exception as e:
                log("watch_subjob_error", id=watch_id, error=str(e))

    if compose_fn and techniques:
        try:
            composed = compose_fn(
                origins[0],
                destinations[0],
                depart_dates[0],
                return_dates[0],
                cabin=cabins[0],
                passengers=adults,
                techniques=techniques,
                base_results=all_rows,
            )
            if composed:
                all_rows.extend(composed)
        except Exception as e:
            log("watch_compose_error", id=watch_id, error=str(e))

    if rank_fn:
        ranked = rank_fn(all_rows, user_balances=_balance_dict())
    else:
        ranked = sorted(
            all_rows,
            key=lambda r: r.get("price_usd") or (r.get("miles") or 1e9) / 100.0,
        )

    elapsed = round(time.time() - t0, 1)
    top = [r for r in ranked if isinstance(r, dict) and not r.get("error")][:5]
    watch["last_run"] = datetime.now(timezone.utc).isoformat()
    watch["last_elapsed_s"] = elapsed
    watch["last_results"] = top
    watch["last_count"] = len(top)

    hits = _alert_threshold_hits(watch, top)
    if hits and watch.get("alerts", {}).get("telegram"):
        text = _format_alert(watch, hits)
        ok = notify_telegram(text)
        if ok:
            watch["last_alert_at"] = datetime.now(timezone.utc).isoformat()
            watch["last_alert_text"] = text[:400]
        log("watch_alert_sent", id=watch_id, ok=ok, hits=len(hits))

    save_watch(watch)
    log("watch_run_done", id=watch_id, count=len(top), elapsed_s=elapsed)
    return {"id": watch_id, "count": len(top), "elapsed_s": elapsed, "top": top}


def _safe_cash(o, d, dep, ret, cab, adults):
    try:
        return cash_search(o, d, dep, ret, cab, adults)
    except Exception as e:
        log("watch_cash_err", origin=o, dest=d, error=str(e))
        return []


def _safe_award(o, d, dep, ret, cab, adults):
    try:
        result = award_search(o, d, dep, ret, cabins=(cab,), passengers=adults)
    except Exception as e:
        log("watch_award_err", origin=o, dest=d, error=str(e))
        return []
    # award_search returns a dict {outbound, return} for round-trip and a list
    # for one-way. Flatten the dict shape with `direction` stamped so the
    # downstream rank/alert layer sees a uniform list either way.
    if isinstance(result, dict):
        out: list[dict] = []
        for direction in ("outbound", "return"):
            for row in (result.get(direction) or []):
                if isinstance(row, dict):
                    row.setdefault("direction", direction)
                    out.append(row)
        return out
    return result or []


def _alert_threshold_hits(watch: dict, results: list[dict]) -> list[dict]:
    """Return rows that beat the watch's max_price_usd / max_miles thresholds."""
    out = []
    max_price = watch.get("max_price_usd")
    max_miles = watch.get("max_miles")
    for r in results:
        if not isinstance(r, dict) or r.get("error"):
            continue
        hit = False
        if max_price is not None and r.get("price_usd"):
            if float(r["price_usd"]) <= float(max_price):
                hit = True
        if max_miles is not None and r.get("miles"):
            if float(r["miles"]) <= float(max_miles):
                hit = True
        if max_price is None and max_miles is None:
            # No thresholds: alert on top result only
            hit = (results.index(r) == 0)
        if hit:
            out.append(r)
    return out[:5]


def _format_alert(watch: dict, hits: list[dict]) -> str:
    lines = [f"FLIGHT-HACKER ALERT · {watch.get('label') or watch.get('id')}"]
    lines.append(
        f"{'/'.join(watch.get('origins') or [])} → {'/'.join(watch.get('destinations') or [])}"
    )
    for r in hits[:5]:
        risk = (r.get("composition") or {}).get("risk", "LEGAL")
        # Defensive numeric coercion — rows can carry None for either price or
        # miles (cash-only rows have miles=None; award rows have price=None).
        # Using f-string formatters like `{None:,}` crashes with TypeError, so
        # default to 0 when the field is missing.
        try:
            miles_i = int(r.get("miles") or 0)
        except (TypeError, ValueError):
            miles_i = 0
        try:
            price_f = float(r.get("price_usd") or 0)
        except (TypeError, ValueError):
            price_f = 0.0
        try:
            taxes_f = float(r.get("taxes_usd") or 0)
        except (TypeError, ValueError):
            taxes_f = 0.0
        depart = r.get("depart_date") or "-"
        carrier = r.get("carrier") or "-"
        cabin = r.get("cabin") or "-"
        if r.get("kind") == "award":
            lines.append(
                f"  {depart} {carrier} {cabin} "
                f"— {miles_i:,} mi + ${taxes_f:.0f} "
                f"({r.get('program') or '-'})  [{risk}]"
            )
        else:
            lines.append(
                f"  {depart} {carrier} {cabin} "
                f"— ${price_f:.0f} "
                f"{r.get('origin') or '?'}→{r.get('destination') or '?'}  "
                f"[{risk}]"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batch runners
# ---------------------------------------------------------------------------

def run_due() -> list[dict]:
    out = []
    for w in list_watches():
        if is_due(w):
            out.append(run_one(w["id"]))
    return out


def run_all() -> list[dict]:
    return [run_one(w["id"]) for w in list_watches()]


# ---------------------------------------------------------------------------
# LaunchAgent installer
# ---------------------------------------------------------------------------

LAUNCH_AGENT_LABEL = "com.fh.watcher"

LAUNCH_AGENT_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
        <string>--due</string>
    </array>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{logdir}/watcher.out.log</string>
    <key>StandardErrorPath</key>
    <string>{logdir}/watcher.err.log</string>
    <key>WorkingDirectory</key>
    <string>{cwd}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


def install_launchagent() -> str:
    home = Path.home()
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    logdir = SKILL_ROOT / "cache"
    plist = LAUNCH_AGENT_PLIST.format(
        label=LAUNCH_AGENT_LABEL,
        python=sys.executable,
        script=str(SKILL_ROOT / "scripts" / "watch.py"),
        logdir=str(logdir),
        cwd=str(SKILL_ROOT),
    )
    path = agents / f"{LAUNCH_AGENT_LABEL}.plist"
    path.write_text(plist)
    # Unload then load (ignore errors)
    try:
        subprocess.run(["launchctl", "unload", str(path)], check=False, capture_output=True)
    except Exception:
        pass
    subprocess.run(["launchctl", "load", str(path)], check=False, capture_output=True)
    log("launchagent_installed", path=str(path))
    return str(path)


def uninstall_launchagent() -> str:
    path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    if path.exists():
        try:
            subprocess.run(["launchctl", "unload", str(path)], check=False, capture_output=True)
        except Exception:
            pass
        path.unlink()
    return str(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default=None, help="Run a specific watch by id")
    ap.add_argument("--due", action="store_true", help="Run all watches that are due")
    ap.add_argument("--all", action="store_true", help="Force-run every watch")
    ap.add_argument("--list", action="store_true", help="List watches and exit")
    ap.add_argument("--install-launchagent", action="store_true")
    ap.add_argument("--uninstall-launchagent", action="store_true")
    args = ap.parse_args()

    if args.install_launchagent:
        print(install_launchagent())
        sys.exit(0)
    if args.uninstall_launchagent:
        print(uninstall_launchagent())
        sys.exit(0)
    if args.list:
        for w in list_watches():
            print(json.dumps({k: w.get(k) for k in ("id", "label", "paused", "last_run", "last_count")}))
        sys.exit(0)
    if args.id:
        print(json.dumps(run_one(args.id), indent=2, default=str))
    elif args.all:
        print(json.dumps(run_all(), indent=2, default=str))
    elif args.due:
        print(json.dumps(run_due(), indent=2, default=str))
    else:
        ap.print_help()
