#!/usr/bin/env python3
"""
Flight Price Tracker — powered by SerpApi (Google Flights)
Checks prices every 6 hours and fires a Telegram alert when below your limit.
Edit config.json to set routes, dates, and price limits.
"""

import json
import os
import sys
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

SERPAPI_KEY    = os.getenv("SERPAPI_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")

CONFIG_FILE   = Path(__file__).parent / "config.json"
NOTIFIED_FILE = Path(__file__).parent / "notified.json"


# ── Flight search via SerpApi ─────────────────────────────────────────────────
def search_flights(origin, destination, departure_date,
                   return_date=None, adults=1, currency="USD"):
    """
    Returns cheapest offer dict, or None if no results.
    """
    params = {
        "engine":         "google_flights",
        "departure_id":   origin,
        "arrival_id":     destination,
        "outbound_date":  departure_date,
        "adults":         adults,
        "currency":       currency,
        "hl":             "en",
        "api_key":        SERPAPI_KEY,
    }

    if return_date:
        params["return_date"] = return_date
        params["type"] = 1      # round-trip
    else:
        params["type"] = 2      # one-way

    resp = requests.get(
        "https://serpapi.com/search",
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # SerpApi returns best_flights and other_flights
    all_offers = data.get("best_flights", []) + data.get("other_flights", [])
    if not all_offers:
        return None

    # Sort by price and take the cheapest
    all_offers.sort(key=lambda o: o.get("price", float("inf")))
    best = all_offers[0]

    # Pull first flight leg details
    first_leg = best.get("flights", [{}])[0]
    departure_info = first_leg.get("departure_airport", {})
    arrival_info   = first_leg.get("arrival_airport", {})

    return {
        "price":        best.get("price"),
        "currency":     currency,
        "airline":      first_leg.get("airline", "N/A"),
        "duration_min": best.get("total_duration"),          # minutes
        "departure_at": departure_info.get("time", departure_date),
        "arrival_at":   arrival_info.get("time", ""),
        "stops":        len(best.get("flights", [])) - 1,   # 0 = direct
        "return_date":  return_date,
    }


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()


# ── Notified cache (avoids duplicate alerts for same price) ───────────────────
def load_notified() -> dict:
    if NOTIFIED_FILE.exists():
        with open(NOTIFIED_FILE) as f:
            return json.load(f)
    return {}


def save_notified(data: dict):
    with open(NOTIFIED_FILE, "w") as f:
        json.dump(data, f, indent=2)


def notified_key(route: dict, price: float) -> str:
    """Re-alerts on every new $1 price drop."""
    return f"{route['origin']}-{route['destination']}-{route['departure_date']}-{int(price)}"


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_duration(minutes):
    if not minutes:
        return "N/A"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Validate env
    missing = [k for k, v in {
        "SERPAPI_KEY":        SERPAPI_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT,
    }.items() if not v]
    if missing:
        sys.exit(f"❌ Missing env vars: {', '.join(missing)}\nEdit your .env file.")

    if not CONFIG_FILE.exists():
        sys.exit(f"❌ config.json not found at {CONFIG_FILE}")

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    routes = config.get("routes", [])
    if not routes:
        sys.exit("❌ No routes defined in config.json")

    print(f"\n🛫  Flight tracker started — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   Checking {len(routes)} route(s)...\n")

    notified    = load_notified()
    any_alert   = False
    summary_lines = []   # collect one line per route for the status update

    for route in routes:
        origin      = route["origin"].upper()
        destination = route["destination"].upper()
        dep_date    = route["departure_date"]
        ret_date    = route.get("return_date")
        limit       = float(route["price_limit"])
        adults      = int(route.get("adults", 1))
        currency    = route.get("currency", "USD")
        label       = route.get("label", f"{origin}→{destination}")

        trip_str = f"{dep_date}" + (f" / return {ret_date}" if ret_date else " (one-way)")
        print(f"  Checking {label}  [{trip_str}]  — limit {currency} {limit:.0f}")

        try:
            offer = search_flights(origin, destination, dep_date,
                                   ret_date, adults, currency)
        except requests.HTTPError as e:
            print(f"    ⚠️  API error: {e.response.status_code} — {e.response.text[:200]}")
            summary_lines.append(f"⚠️ {label}: API error")
            continue
        except Exception as e:
            print(f"    ⚠️  Unexpected error: {e}")
            summary_lines.append(f"⚠️ {label}: {e}")
            continue

        if offer is None:
            print(f"    No flights found.")
            summary_lines.append(f"❓ {label}: No flights found")
            continue

        price = offer["price"]
        stops_str = "Direct" if offer["stops"] == 0 else f"{offer['stops']} stop(s)"
        print(f"    Best price: {currency} {price}  |  {offer['airline']}  |  {stops_str}  |  {fmt_duration(offer['duration_min'])}")

        if price <= limit:
            key = notified_key(route, price)
            if key in notified:
                print(f"    Already notified at this price level — skipping.")
                summary_lines.append(f"✅ {label}: {currency} {price} — DEAL (already alerted)")
                continue

            # Build deal alert message
            trip_type = "✈️ Round-trip" if ret_date else "✈️ One-way"
            gf_url = f"https://www.google.com/flights?hl=en#flt={origin}.{destination}.{dep_date}"

            msg = (
                f"<b>🚨 Flight Deal Alert!</b>\n\n"
                f"{trip_type}: <b>{origin} → {destination}</b>\n"
                f"💰 Price: <b>{currency} {price}</b>  (your limit: {currency} {int(limit)})\n"
                f"📅 Depart: {offer['departure_at']}\n"
            )
            if ret_date:
                msg += f"🔙 Return: {ret_date}\n"
            msg += (
                f"✈️  Airline: {offer['airline']}\n"
                f"🛑 Stops: {stops_str}\n"
                f"⏱ Duration: {fmt_duration(offer['duration_min'])}\n\n"
                f"<a href='{gf_url}'>🔍 Search on Google Flights →</a>"
            )

            try:
                send_telegram(msg)
                notified[key] = {
                    "price":      price,
                    "alerted_at": datetime.now().isoformat(),
                }
                save_notified(notified)
                print(f"    ✅ Telegram alert sent!")
                any_alert = True
                summary_lines.append(f"🚨 {label}: {currency} {price} — DEAL FOUND!")
            except requests.HTTPError as e:
                print(f"    ⚠️  Telegram error: {e.response.status_code} — {e.response.text[:200]}")
                summary_lines.append(f"⚠️ {label}: {currency} {price} — deal found but Telegram failed")
        else:
            diff = price - limit
            print(f"    {currency} {diff:.0f} above your limit — no alert.")
            summary_lines.append(
                f"😴 {label}: {currency} {price}  ({offer['airline']}, {stops_str}, {fmt_duration(offer['duration_min'])})\n"
                f"   ↳ {currency} {diff:.0f} above your limit of {currency} {int(limit)}"
            )

    # Always send a Telegram status update
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    status_msg = f"<b>✈️ Flight Tracker Update</b> — {now_str}\n\n"
    status_msg += "\n\n".join(summary_lines) if summary_lines else "No routes checked."
    if not any_alert:
        status_msg += "\n\n<i>No deals yet. I'll check again in 6 hours.</i>"

    try:
        send_telegram(status_msg)
    except Exception as e:
        print(f"    ⚠️  Could not send status update: {e}")

    print(f"\n{'🔔 Done — alert(s) sent!' if any_alert else '😴 No deals found this run.'}\n")


if __name__ == "__main__":
    main()
