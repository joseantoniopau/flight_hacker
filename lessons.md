# LESSONS LEARNED — flight-hacker skill

Read this FIRST on every flight query. Each entry is a hard-won correction.

---

## Data-source accuracy hierarchy

For cash fares, when sources disagree, trust in this order:
1. **Airline.com direct quote** (most accurate, includes all fees)
2. **Duffel API** (NDC + GDS, official)
3. **fast-flights / Google Flights** (good baseline, sometimes stale by minutes)
4. **OTA aggregators** (Kiwi, Skyscanner — may not include bag/seat fees in headline)
5. **Skiplagged-style hidden-city** (last; always book via airline-direct for the actual ticket)

For award space:
1. **Seats.aero cached search** (Pro tier) — pre-fetched, fast, accurate for partner saver awards
2. **Airline.com direct search** — final source of truth; cached can be stale by a few hours
3. **Phone agent** — for partner awards not shown online (some programs like ANA US, Aeroplan partner saver)

---

## Cabin enum mapping (must use exactly these strings)

Internal: `economy`, `premium_economy`, `business`, `first`

- fast-flights expects: `economy`, `premium-economy`, `business`, `first`
- Seats.aero expects (plural `cabins=`): `economy`, `premium`, `business`, `first`
- Duffel expects: `economy`, `premium_economy`, `business`, `first`

Get this wrong and you'll silently search the wrong cabin (most common silent failure mode).

---

## Seats.aero gotchas

- Auth header is `Partner-Authorization: <key>` — **NO `Bearer `** prefix.
- `*TotalTaxes` are in **CENTS** (minor units). Divide by 100.
- `MileageCost` is a STRING. Use `*MileageCostRaw` (int) for math.
- One CachedSearchData row has FOUR parallel cabin columns (Y/W/J/F). Explode it into 1–N records based on which cabins were requested AND `*Available=true`.
- **Live Search is NOT included in Pro tier.** Use `GET /search` (cached) only.
- Cabin param naming differs: `cabins=` (plural) on `/search`, `cabin=` (singular) on `/availability`.
- Rate limit: 1000/day. Burst 1 req/sec is safe. Header `X-RateLimit-Remaining` tells you what's left.

---

## fast-flights / Google Flights gotchas

- Currency defaults to USD when called from US IP. To get JPY/EUR pricing for the same route, set `currency` or call with a non-US-pinned proxy (rare — usually USD is fine).
- The `Result.flights[i]` object is flat — no per-segment breakdown. To get segments, search again with `only_direct=True` to confirm it's direct, or parse the layover strings.
- Some routes return "no flights found" even when flights exist — this is an anti-bot interstitial. Retry once with a small delay before falling back.
- Empty-result fallback ladder: strip bag filter → ±1/±2 day shift → present `search_dates` indicative price.

---

## CPP must default to FLOOR, not ceiling

When the user asks "is this a good redemption?", compute:
```
miles_value_usd = miles × cpp_floor / 100
total_cost_usd  = miles_value_usd + taxes_usd + positioning_cost
```

Use **floor** CPP from `points_valuations.json`. Most travel blogs quote ceiling — that's marketing math. Floor is honest: it's what *you* would conservatively realize in a typical redemption. Ceiling sets up disappointment.

---

## Effective balance — never dismiss a program for low direct balance

If the user has 0 United miles but 145K Chase UR, **United effective = 0 + 145K × 1.0 = 145K**.

Compute effective balance for every relevant program before saying "you don't have enough."

But: do NOT recommend transferring before booking unless the program allows holds (`award_holds.json.allowed`). For non-hold programs (United, Alaska, Delta, BA, JetBlue, Spirit, Frontier post-2024, Singapore, ANA, Qatar, Korean), find the seat in availability search first, hold-or-book in one motion.

---

## Negative-space rules (do not hallucinate)

- **United** does NOT allow stopovers (post-2025). The Excursionist Perk was discontinued Aug 2025.
- **AA** does NOT allow stopovers on awards. Hold reduced from 5d → 24h Apr 2025.
- **Delta** never allowed stopovers on awards. Dynamic pricing only.
- **British Airways Avios** — no stopovers on single-direction awards (multi-segment treats each as its own).
- **JetBlue, Spirit, Frontier, Allegiant** — no awards meaningfully redeemable for partner travel; treat as cash-only.
- **Singapore KrisFlyer Suites** to/from JFK ended; route now via FRA/MAN. PVG and HKG return May/June 2026.
- **ANA First** via Virgin Atlantic was devalued Oct 2025 (85K → 110K LAX/SFO/EC). Aeroplan still 90K ow JFK/EWR-NRT in F.
- **Iberia off-peak biz**: 34K ow only from BOS/JFK/IAD/ORD; 40.5K elsewhere from Sep 2025.
- **Avianca LifeMiles** US-Europe biz: 92.4K ow as of May 2026 (3rd devaluation in 15 months).
- **Etihad Guest** leaves Amex MR June 30, 2026 — flag aggressively.

---

## Source-accuracy receipts (kept here so I don't forget)

- Duffel beat SerpAPI on JFK-LHR economy: $271 vs $541 for same flights (Apr 2026 test).
- Seats.aero cached lagged airline.com by 2-6 hours during peak release windows; for time-critical hunts, hit airline.com after the cached hit.
- fast-flights occasionally double-counts long layovers as "+1 day" mid-segment; verify with the deep_link before booking.

---

## Output discipline

- IATA codes ONLY in tables. Never airport long names.
- Risk badge on every row. LEGAL / GRAY / TOS-RISK.
- For TOS-RISK rows always include: "No checked bags. No return segment. Do not enter FF#. May void status. Airline ToS violation."
- Cash + award + composed always shown side-by-side in one ranked table. Do NOT silo cash vs award — the user wants the best path, regardless of currency.

---

## Hidden-city specific rules

- Federal jury upheld Skiplagged's right to operate (May 2025) but awarded American Airlines $9.4M on copyright grounds. Individual flyers risk:
  - Voided return segment
  - Lost frequent-flyer status
  - Miles clawback
  - Future-booking ban from the carrier
- Hard rules for a recommended hidden-city booking:
  1. One-way only (or first segment of round-trip with discard return)
  2. No checked bags
  3. Do NOT enter frequent-flyer number
  4. Pay with a card not associated with the airline's loyalty program
  5. If you must use it once, never tell the airline

---

## Mistake-fare specific rules

- DOT 24-hour rule: US-ticketed itineraries can be cancelled by the passenger free within 24h. Use this window before booking non-refundable hotels.
- Airlines sometimes honor mistake fares, sometimes cancel. Recent trend: more cancellations under "obvious pricing error" defense.
- Best signal-to-noise sources: Secret Flying (error fares), Thrifty Traveler (paid; free preview), Going.com (paid). Free sources: VFTW, OMAAT, Flight Deal, GSTP.

---

## Operational lessons (the toolkit itself)

- Always run sources in PARALLEL via ThreadPoolExecutor; never sequential.
- 30s timeout per source. One retry on transient errors with 2s backoff.
- Cache aggressively: 60-min TTL on cash, 6h on Seats.aero cached, 24h on transfer-bonus refresh.
- Async safety: never call sync HTTP/scraping from an async def — wrap with `to_thread`. The UI server enforces this.
- Atomic file writes (.tmp → rename). The `save_json` helper in common.py does this.
- One source failing must not kill the search. Each branch wraps try/except and emits an error record sortable apart.

---

## When the user just says "find me flights to X"

Default behavior:
1. Expand origin via `data/airport_hubs.json` (top-3 hubs near user's home — check `data/user_balances.json` for home airport hint, else default to NYC area).
2. Expand destination same way if X is a city not an airport.
3. Default cabin = economy unless user has >50K balance in a premium-cabin-favorable program.
4. Date window: ±3 days around their stated date, or next 60 days if no date given.
5. Run cash + award + positioning + open_jaw in parallel.
6. Rank and present top 10.

Do NOT ask follow-up questions before running the search.
