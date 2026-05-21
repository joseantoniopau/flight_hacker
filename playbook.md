# FLIGHT-HACKER PLAYBOOK — Strategies by Trip Archetype

Loaded after `lessons.md` on every search. This document is a strategy index, not exhaustive prose. Each row tells you what to try and where to look it up.

---

## A. CASH ECONOMY DOMESTIC SHORT-HAUL (< 1500 mi)

Best moves:
- **Positioning** — nearby hub via cheap LCC. Check `data/airport_hubs.json[origin].nearby_hubs`.
- Compare WN (Southwest) directly — not in fast-flights. Check southwest.com manually.
- Mistake-fare ingest hits for sub-$50 fares occasionally; check `cache/mistakes_feed.json`.
- One-way arbitrage: sometimes 2× OW < RT (gflights pattern).

Avoid: award redemption (CPP is awful, ~1.0¢ at best). Spirit/Frontier mileage runs only worth it at signup bonus levels.

---

## B. CASH ECONOMY DOMESTIC LONG-HAUL (transcon)

Best moves:
- Direct from a hub airport (JFK/EWR/SFO/LAX/SEA) — lowest baseline.
- Positioning: if user is in mid-tier hub, often $50-100 cheaper to position via the closer big hub.
- Award option becomes interesting when cash > $500: Alaska Mileage Plan saver (25K ow), Delta SkyMiles sales, AAdvantage web specials.

---

## C. TRANSATLANTIC ECONOMY

Best moves:
- **Mistake fares** are common transatlantic. Run ingest_mistakes.py first; check for active mistakes ex-US to Europe.
- **Positioning** via BOS/JFK/EWR/PHL/IAD/MIA is often $200-400 cheaper than from interior US cities.
- **Open-jaw** is canonical here: fly into LHR, out of CDG; the cash market prices this similarly to RT but enables exploring two cities.
- Norse Atlantic, Air Asia X (defunct again 2025), French Bee — LCC long-haul; cheap but tight bag/seat rules.

Award path: rare to be worth it for econ. ANA round-trip via Virgin Atlantic (60K) is the standout sweet spot for econ awards.

---

## D. TRANSATLANTIC BUSINESS+

Best moves (in order of value):
1. **Iberia off-peak biz**: 34K ow from BOS/JFK/IAD/ORD to MAD (post-Sep 2025 bump, others 40.5K). Avios. Transferable from Chase/Amex/Citi/Bilt.
2. **Aeroplan to LH F**: 90K ow JFK/EWR-FRA/MUC, no fuel surcharges. Cheapest LH F path alive.
3. **LifeMiles to LH/Lux** (still works post-devaluation, but check current chart — 2026 spec was 92.4K ow JFK-FRA biz).
4. **TAP Miles&Go**: free stopover LIS, 1-10 nights. Reasonable Avios-like rates.
5. **Turkish Miles&Smiles**: free stopover IST. Pricing competitive post-Jun 2026 bump.

Positioning still relevant: positioning to BOS for Iberia 34K ow saves vs flying out of secondary US city.

---

## E. TRANSPACIFIC BUSINESS+ (US-Asia)

Best moves:
1. **ANA F via Virgin Atlantic**: 110K LAX/SFO/EC, 100K others (post-Oct 2025 deval). Phone-only. 8 F seats/plane. Book 355 days out.
2. **ANA F via Aeroplan**: 90K ow JFK/EWR-NRT/HND, no fuel surcharges. Online bookable when seat shows on Air Canada.
3. **Cathay F via Alaska Mileage Plan (now Atmos)**: 70K ow US-HKG. Limited release; usually 1-3 days out.
4. **JAL F via AA**: 80K ow flat. AA holds 24h. Saver space sparse.
5. **JAL F via BA Avios**: 130K-ish ow. Avios easy to acquire.
6. **EVA J via Avianca LifeMiles**: 75K ow US-TPE (subject to deval cycle).
7. **Korean F via Korean Skypass**: 80K ow LAX-ICN; partner via Capital One.

Cash baseline: $4000-7000 ow on premium carriers. Worth it only on mileage runs / status maintenance.

---

## F. INTRA-EUROPE / INTRA-ASIA SHORT-HAUL

Best moves:
- LCC direct (Ryanair, Wizz, Vueling, easyJet in EU; AirAsia, Cebu, VietJet, Scoot, Peach in APAC).
- Avios sweet spot: ~5K-9K Avios per OW intra-Europe. Iberia/BA/Aer Lingus/Vueling redemption. Transferable from Chase/Amex/Citi/Bilt.
- ANA short-haul intra-Asia: 12-15K via Virgin Atlantic or Aeroplan.
- Hidden-city interesting on hub-routed pricing (e.g. LHR-DUB ticketed onward to JFK can be cheaper than LHR-DUB direct). Risk badge TOS-RISK.

---

## G. AROUND-THE-WORLD / MULTI-CONTINENT

Best moves:
- **RTW awards** (see `data/rtw-awards.json` if present, or sweet_spots.json):
  - Star Alliance via United (cap 16 segments; one Star carrier per leg)
  - Star Alliance via ANA (cap 8 segments; some restrictions)
  - oneworld Explorer via JAL or BA
  - Aeroplan multi-stop construction (often more flexible than formal RTW)
- **Stopover gaming** + multiple awards stitched:
  - Aeroplan: 1 free stopover RT, can effectively visit 2 cities.
  - Singapore KrisFlyer: $100 stopover on saver, free on advantage; multi-stop possible.
  - Emirates Skywards: free stopover DXB on certain tiers.
  - JAL: multi-stopovers permitted on intercontinental OW.

---

## H. LAST-MINUTE (<14 days out)

Best moves:
- **Award only** — saver space often drops as airlines re-release inventory.
- Seats.aero cached search is great here; refreshes frequently.
- Cash typically peaks 7-21 days out, often falls 0-7 days for unsold inventory but not always.
- Mistake fares mostly fire 30-90 days out; rare last-minute.

---

## I. POSITIONING FLIGHT CHEAT SHEET

Sample value plays:
- BOS-Europe in biz: usually $400-800 cheaper than from interior US. Add JetBlue Mint OW from JFK ($299-499) and total < direct from interior city.
- LIS as positioning point for African continent (TAP free stopover doubles as a vacation).
- DXB positioning to South Africa/India via Emirates First flat-bed.
- BCN/MAD positioning to LATAM via LATAM/Iberia.
- HKG positioning to SE Asia via Cathay.

Use `data/airport_hubs.json[hub].nearby_hubs` for ground-transport options. Always include hotel-overnight cost if positioning flight arrives < 4h before long-haul departure or in the wee hours.

---

## J. WHEN A PROGRAM "MISSING" IS A RED FLAG

If the user mentions a sweet spot that should exist but Seats.aero shows nothing, check:
- Was the saver space released and grabbed within hours? (ANA F is notorious — 8 seats per plane, often grabbed in 60 minutes.)
- Did the program just devalue? Cross-check `sweet_spots.json[id].status` and `recently_changed`.
- Is the airline's award engine offline? (LH M&M has multi-day outages occasionally.)

Default: don't tell the user "no availability" — say "no saver space in cache; phone agent might find more, or wait for next release cycle."

---

## K. SEARCH-FANOUT TEMPLATE (for subagents)

When fanning out to subagents per origin, use this template:

```
Origin: <IATA>
Destinations: <list>
Dates: <range>
Cabins: <list>
Pax: <n>

Run in this order:
1. search_cash.py for each (dest, date, cabin) — parallel
2. search_award.py for each (dest, date, cabin) — parallel
3. If extra time budget, compose.py with techniques=positioning,open_jaw

Aggregate JSON into one list. Pipe to rank.py.
Return top-5 rows in compact ASCII table. No raw JSON.
```

---

## L. WHEN TO PRESENT MISTAKE FARES INLINE

If `cache/mistakes_feed.json` has a hit within ±30 days of the user's date window and the route matches by city or region (use airport_hubs.json regions), surface it explicitly in a "PROACTIVE: mistake fare alert" section above the normal results table. Include source + posted-at + confidence + cancellation-risk disclaimer.

---

## M. WHEN TO TELL THE USER TO STOP AND CALL

Phone an agent when:
- Aeroplan partner saver space shows on Air Canada but Aeroplan.com errors out (common with LH F).
- Virgin Atlantic ANA F is the play (phone-only since 2022).
- Multi-partner award construction not bookable online (e.g., Asia Miles 3-segment intra-Asia + transpacific).
- Cathay First saver via Alaska Atmos — phone is faster than online.

For everything else, online is the move.
