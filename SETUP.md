# Flight Price Tracker — Setup

Tracks one-way **SYD → AMS** airfare (2 adults, AUD) and sends a daily Telegram
update with the cheapest options, lowest-ever price, and per-airline history.

## What it tracks right now

`config.json` holds the routes. Today it has one:

| Label | Route | Date | Pax | Currency |
|-------|-------|------|-----|----------|
| SYD→AMS — leave 9 Sep 2026 | SYD → AMS (one-way) | 2026-09-09 | 2 | AUD |

### Adding more departure dates to compare (7th / 14th, etc.)

Just add more objects to the `routes` array — **no code change needed**. Each
date gets its own history series because the storage key includes the departure
date (`SYD-AMS-2026-09-09`, `SYD-AMS-2026-09-14`, …), so they never collide.

```json
{
  "routes": [
    { "label": "SYD→AMS — leave 7 Sep 2026",  "origin": "SYD", "destination": "AMS", "departure_date": "2026-09-07", "adults": 2, "currency": "AUD" },
    { "label": "SYD→AMS — leave 9 Sep 2026",  "origin": "SYD", "destination": "AMS", "departure_date": "2026-09-09", "adults": 2, "currency": "AUD" },
    { "label": "SYD→AMS — leave 14 Sep 2026", "origin": "SYD", "destination": "AMS", "departure_date": "2026-09-14", "adults": 2, "currency": "AUD" }
  ]
}
```

> Heads-up on quota if you scale to 3 dates — see the call-count math below.

## Secrets (required)

The tracker needs three secrets. Set them **both** locally (for test runs) and in
GitHub Actions (for the schedule).

| Secret | What it is |
|--------|------------|
| `SERPAPI_KEY` | API key from https://serpapi.com (the Google Flights data source) |
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather when you create the bot |
| `TELEGRAM_CHAT_ID` | Your chat ID (message the bot, then read it from `getUpdates`) |

### Local (for `python tracker.py`)

Create a file named `.env` next to `tracker.py`:

```
SERPAPI_KEY=your_serpapi_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

`.env` is read automatically (`load_dotenv` at the top of `tracker.py`). Don't
commit it.

### GitHub Actions

Repo → **Settings → Secrets and variables → Actions → New repository secret**, and
add all three names above. The workflow (`.github/workflows/main.yml`) already
wires them into the run step. Missing secrets is the #1 reason the job silently
does nothing.

## Schedule

`.github/workflows/main.yml` runs **once a day** at ~10am Sydney (`0 0 * * *`).
You can also trigger it manually from the Actions tab (**Run workflow** /
`workflow_dispatch`). The Sunday run additionally sends the weekly chart,
flexible-date (±3 days) scan, and — once a month — a cheapest-days-of-the-month
scan.

## SerpApi monthly call count

Free tier is ~100 searches/month. With **1 route, 1 scan/day**:

| Item | Calls |
|------|-------|
| Daily cheapest-price check | ~30 / month |
| Sunday flexible dates (±3 days = 6 extra calls × ~4.3 Sundays) | ~26 / month |
| Monthly "cheapest days" scan (1st → departure date, once/month) | ~8 / month |
| **Total** | **~64 / month** ✅ under the free 100 |

**If you add the 7th and 14th (3 routes total),** it roughly triples to **~190
calls/month**, which exceeds the free tier. Options then: drop to every-other-day,
gate the Sunday flexible/cheapest scans, or move to a paid SerpApi plan.

## Test run

```
python tracker.py
```

A successful run prints progress to stdout and sends one Telegram message for the
route. If you see `Missing env vars`, your secrets aren't set.
