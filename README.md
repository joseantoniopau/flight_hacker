# flight-hacker

**Find the cheapest flights for any trip — including the ones Google Flights doesn't show you.**

This is a free tool that runs on your own computer. It searches Google Flights AND the world of airline miles redemptions at the same time, then tells you the cheapest way to book — whether that's paying cash or using points you already have.

```
                              ▄▄▄▄▄▄▄▄
                  ┏━━━━━━━━━━━┃ FLIGHT-HACKER
                  ┃ cash + award + composed │
                  ┃ ranked. risk-badged.    │
                  ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

---

## What it does (in plain English)

When you search for a flight on Google, you see cash prices. That's it. This tool adds **everything else**:

- 💸 **Award miles search**: Checks if you can book the same flight with miles from 22+ airline programs (United, Delta, American, Aeroplan, Virgin, etc.) and tells you the dollar value.
- 💳 **Knows your points balance**: If you have Chase, Amex, Citi, Capital One, or Bilt points, it figures out which airlines you can transfer them to and how many miles you'd have.
- ✈️ **Nearby airports**: Type "MIA" and it also searches Fort Lauderdale and Palm Beach automatically (because those flights are often cheaper).
- 🧠 **Travel-hacking moves**: Like "positioning flights" (fly to a nearby city first to catch a cheap long-haul) and "hidden city" tickets (with safety warnings).
- 📰 **Mistake fare alerts**: Watches 9 travel-deal websites and shows you when an airline accidentally publishes a $300 business class ticket.
- ⏰ **Set-and-forget watchlists**: Save a route, set your budget, and your computer will check hourly and text you when prices drop.
- 🎯 **Curated "sweet spots"**: A handpicked list of 25 amazing redemption deals (like "Tokyo first class on ANA for 110K miles" instead of the usual 200K).

**Real example:** New York → London business class
- Google Flights: $2,178 cheapest
- This tool: 88,000 Qatar miles + $0 taxes = **$1,232 effective cost** (saves you $946)

---

## Who is this for?

You'll get the most out of this if:

- ✅ You fly more than once or twice a year
- ✅ You have a credit card that earns points (Chase Sapphire, Amex Gold, etc.)
- ✅ You're willing to spend ~10 minutes on a one-time setup
- ✅ You're comfortable copy-pasting a few commands into your computer's Terminal app

You don't need to be a programmer. But you do need a Mac or Linux computer (Windows works with extra setup).

---

## What you need before you start

1. **A Mac or Linux computer** (Windows users can use WSL — see the bottom)
2. **About 10 minutes** for the one-time setup
3. **Seats.aero Pro subscription** — $99/year at https://seats.aero/ (this is what powers the award miles search; without it you only get cash prices). This is the only paid thing.
4. **(Optional)** A Telegram account if you want price-drop alerts on your phone

---

## How to install (step by step)

### Step 1: Open Terminal

On a Mac: press `Cmd + Space`, type "Terminal", press Enter. A black window opens. That's where you'll paste the commands below.

### Step 2: Check you have Python (most Macs do)

In Terminal, copy-paste this and press Enter:

```bash
python3 --version
```

If you see something like `Python 3.10.x` or higher, you're good. If not, install it from https://www.python.org/downloads/ first.

### Step 3: Download the tool

Copy-paste this into Terminal and press Enter:

```bash
git clone https://github.com/joseantoniopau/flight_hacker ~/Desktop/flight-hacker
cd ~/Desktop/flight-hacker
```

This downloads everything to a folder called `flight-hacker` on your Desktop.

### Step 4: Run the installer

```bash
./install.sh
```

This sets everything up automatically. Takes about 30 seconds.

### Step 5: Add your Seats.aero key

After you sign up at https://seats.aero/ (the $99/yr plan), they'll give you a key that looks like `pro_xxxxx...`. Run:

```bash
./setup-keys.sh
```

When it asks for the key, paste it in and press Enter. Skip everything else (just press Enter to skip).

### Step 6: Tell it about your points

This is what makes the tool know what miles you can use. Open this file in any text editor:

```
~/Desktop/flight-hacker/data/user_balances.json
```

You'll see a template like this:
```json
{
  "currencies": {
    "Chase Ultimate Rewards": 145000,
    "Amex Membership Rewards": 82000,
    "Citi ThankYou": 30000,
    "Capital One Miles": 0,
    "Bilt Rewards": 24000
  },
  "programs": {
    "United MileagePlus": 16000,
    "American AAdvantage": 8000
  }
}
```

Replace the numbers with your actual balances. If you don't know yours, log into your credit card and airline accounts. Save the file.

### Step 7: Launch the tool

```bash
python3 ui/server.py
```

You'll see a message like `Uvicorn running on http://127.0.0.1:8721`. Leave this window open.

Now open your web browser and go to: **http://127.0.0.1:8721**

That's it. The tool is running.

---

## How to use it

1. **Click SEARCH in the sidebar** (it's the first thing on the left).
2. Type your departure airport (e.g. "JFK" or "Miami"). Then your destination.
3. Pick your travel dates.
4. Click **Search flights**.
5. Wait ~10 seconds. You'll see a table showing the cheapest options, with both cash prices AND miles redemptions ranked together.

Look for the **"Total" column** — that's the true cost in dollars, whether you pay cash or use miles. Click **Book** on any row to go directly to the airline's website to book it.

### Other things to try

- **Watchlists**: After a search, click "Save as watch" — the tool will check that route every hour and alert you when prices drop.
- **Mistake Fares** (sidebar): Shows recently-published deals like "$400 business class to Europe".
- **Sweet Spots** (sidebar): Browse 25 known great miles deals. Click any row to see if your points can cover it.

---

## How to use it tomorrow (and every day after)

You only install it once. After that:

1. Open Terminal
2. Run:
   ```bash
   cd ~/Desktop/flight-hacker && python3 ui/server.py
   ```
3. Open http://127.0.0.1:8721 in your browser
4. Search away

To stop it: in the Terminal window, press `Ctrl + C`.

---

## Why this is better than searching on Google

Google Flights shows you cash prices. That's it.

This tool also shows you:
- 🎫 Whether you can book the same flight with miles (often saves $1,000–$3,000)
- 🛫 Nearby airports you'd never think to check (often saves $200–$500)
- 🕵️ Mistake fares right after they're published (sometimes saves 60-80%)
- 🛂 Travel-hacking tricks like positioning flights and free stopovers
- 💼 What's actually possible with the points already sitting in your accounts

A few examples from real searches:

| Trip | Google Flights | This tool | You save |
|------|----------------|-----------|----------|
| NYC → Tokyo business class | $4,200 | 87,500 Aeroplan miles + $230 = $1,055 effective | **$3,145** |
| Miami → Rome economy | $1,471 from MIA | $1,037 from FLL (auto-checks nearby airports) | **$434** |
| JFK → London business | $2,178 | 88,000 Qatar miles + $0 = $1,232 | **$946** |

---

## Troubleshooting

**"I get an error when I run ./install.sh"**
Make sure you have Python 3.10 or newer. Type `python3 --version` to check.

**"The website doesn't load when I go to localhost:8721"**
Make sure the Terminal window is still open and shows "Uvicorn running...". If you closed it, run `python3 ui/server.py` again.

**"My miles balances aren't being used"**
Open `~/Desktop/flight-hacker/data/user_balances.json` and check the numbers are saved.

**"Search is slow"**
First searches take 10-30 seconds because we're checking multiple airports and miles programs. After that, results are cached.

**"I'm on Windows"**
Use WSL (Windows Subsystem for Linux) — https://learn.microsoft.com/en-us/windows/wsl/install — then follow the Linux steps above.

---

## Privacy + safety

- ✅ Everything runs on **your own computer**. Nothing is uploaded.
- ✅ Your miles balances stay in a file on your computer (the `user_balances.json` you edited).
- ✅ No tracking, no ads, no Google account, no cookies tied to your travel searches.
- ✅ When you book, you book directly on the airline's website — this tool just shows you the cheapest path.

**Hidden-city tickets** (one of the optional travel-hacking moves): legal, but airlines don't like them. The tool always shows a clear `TOS-RISK` badge when one appears, with full warnings. You decide whether to use them.

---

## Advanced (for the curious)

The tool can also be used through:

- **Command line** for power users:
  ```bash
  python3 scripts/search_cash.py --origin JFK --dest LAX --depart 2026-10-15 --cabin economy --adults 1
  ```
- **Claude Code** (an AI coding assistant): The skill auto-loads when you mention flights. Just say "find me business class from JFK to Tokyo next month".
- **REST API** at `http://127.0.0.1:8721/api/search` for building your own front-end.

---

## What this does NOT do

- ❌ It doesn't book the ticket for you. It shows you the cheapest path and links you to the airline's site to book.
- ❌ It doesn't see your real credit card. You enter your **points balances** (totally separate from card numbers), and it uses that math to figure out which redemptions are reachable.
- ❌ It doesn't track you. Everything is local.
- ❌ It won't help with hotels, cars, or cruises — just flights.

---

## Technical architecture (for developers)

<details>
<summary>Click to expand</summary>

```
flight-hacker/
├── SKILL.md                  Claude orchestration prompt (PRE-OUTPUT GATE etc.)
├── lessons.md                Hard-won corrections. Loaded first every query.
├── playbook.md               Strategy table by trip archetype.
├── install.sh                One-shot installer
├── setup-keys.sh             Interactive secrets setup
├── .env                      Gitignored secrets
│
├── data/                     Reference data — all 2026-accurate
│   ├── sweet_spots.json      25 curated landmark redemptions
│   ├── transfer_partners.json   Chase/Amex/Citi/Cap1/Bilt/Marriott graph
│   ├── points_valuations.json   42 programs, TPG/UP/OMAAT/VFTW floor/ceiling
│   ├── stopovers.json        Which programs allow free stopovers
│   ├── award_holds.json      Which programs allow holds
│   ├── airport_hubs.json     130 hubs across 6 regions, nearby_hubs with ground transport
│   └── user_balances.example.json
│
├── scripts/
│   ├── common.py             Shared utilities: cache, log, schema, CPP, effective balance
│   ├── search_cash.py        fast-flights (Duffel optional, B2B-only)
│   ├── search_award.py       Seats.aero Pro client
│   ├── compose.py            positioning / hidden-city / open-jaw / stopover
│   ├── rank.py               Unified scoring (cash + award + composed)
│   ├── ingest_mistakes.py    9-source mistake-fare ingester
│   ├── watch.py              Watchlist runner + LaunchAgent installer
│   └── smoke_test.py         End-to-end golden + live tests
│
├── ui/
│   ├── index.html · styles.css · app.js     Brutalist dark-mode shell
│   └── server.py                            FastAPI backend
```

Design principles:
1. Cash + award in one ranked table. No silo.
2. Floor CPP, not ceiling. Honest math.
3. Effective balance reachable. Direct miles + transfer-partner closure.
4. Negative-space data. Encode which programs *don't* offer features.
5. Parallel by default.
6. Risk badges everywhere. LEGAL / GRAY / TOS-RISK.
7. PRE-OUTPUT GATE in the Claude skill. No "Would you like me to…"
8. Stateful. Watchlists persist, hourly cron, Telegram alerts.

**Note on Duffel API**: The skill has a Duffel integration in `scripts/search_cash.py`, but Duffel is B2B-only (for travel agencies). Personal users cannot realistically book through it. The code path stays for anyone with existing access; not part of personal setup.

</details>

---

## License

MIT. Free for any use, including commercial.

Data structure + PRE-OUTPUT GATE pattern credit: [borski/travel-hacking-toolkit](https://github.com/borski/travel-hacking-toolkit). Sub-agent topology credit: [van4oza/gflights-mcp-skill](https://github.com/van4oza/gflights-mcp-skill).
