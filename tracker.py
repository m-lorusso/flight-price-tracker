#!/usr/bin/env python3
import json
import os
import sys
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SERPAPI_KEY    = os.getenv("SERPAPI_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")

CONFIG_FILE  = Path(__file__).parent / "config.json"
LOWEST_FILE  = Path(__file__).parent / "lowest_prices.json"


def search_flights(origin, destination, departure_date,
                   return_date=None, adults=1, currency="USD"):
    params = {
        "engine":        "google_flights",
        "departure_id":  origin,
        "arrival_id":    destination,
        "outbound_date": departure_date,
        "adults":        adults,
        "currency":      currency,
        "hl":            "en",
        "api_key":       SERPAPI_KEY,
    }
    if return_date:
        params["return_date"] = return_date
        params["type"] = 1
    else:
        params["type"] = 2

    resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    all_offers = data.get("best_flights", []) + data.get("other_flights", [])
    if not all_offers:
        return None

    all_offers.sort(key=lambda o: o.get("price", float("inf")))
    best = all_offers[0]
    first_leg = best.get("flights", [{}])[0]

    return {
        "price":        best.get("price"),
        "currency":     currency,
        "airline":      first_leg.get("airline", "N/A"),
        "duration_min": best.get("total_duration"),
        "departure_at": first_leg.get("departure_airport", {}).get("time", departure_date),
        "arrival_at":   first_leg.get("arrival_airport", {}).get("time", ""),
        "stops":        len(best.get("flights", [])) - 1,
    }


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()


def load_lowest() -> dict:
    if LOWEST_FILE.exists():
        with open(LOWEST_FILE) as f:
            return json.load(f)
    return {}


def save_lowest(data: dict):
    with open(LOWEST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def route_key(route: dict) -> str:
    return f"{route['origin'].upper()}-{route['destination'].upper()}-{route['departure_date']}"


def fmt_duration(minutes):
    if not minutes:
        return "N/A"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m"


def main():
    missing = [k for k, v in {
        "SERPAPI_KEY":        SERPAPI_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT,
    }.items() if not v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}")

    if not CONFIG_FILE.exists():
        sys.exit(f"config.json not found at {CONFIG_FILE}")

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    routes = config.get("routes", [])
    if not routes:
        sys.exit("No routes defined in config.json")

    print(f"\nFlight tracker started — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    lowest  = load_lowest()
    summary = []

    for route in routes:
        origin      = route["origin"].upper()
        destination = route["destination"].upper()
        dep_date    = route["departure_date"]
        ret_date    = route.get("return_date")
        adults      = int(route.get("adults", 1))
        currency    = route.get("currency", "USD")
        label       = route.get("label", f"{origin}→{destination}")
        key         = route_key(route)

        print(f"  Checking {label}...")

        try:
            offer = search_flights(origin, destination, dep_date, ret_date, adults, currency)
        except Exception as e:
            print(f"    Error: {e}")
            summary.append(f"⚠️ {label}: error — {e}")
            continue

        if offer is None:
            print(f"    No flights found.")
            summary.append(f"❓ {label}: No flights found")
            continue

        price     = offer["price"]
        stops_str = "Direct" if offer["stops"] == 0 else f"{offer['stops']} stop(s)"
        prev_low  = lowest.get(key, {}).get("price")

        print(f"    Price: {currency} {price}  |  {offer['airline']}  |  {stops_str}  |  {fmt_duration(offer['duration_min'])}")

        is_new_low = prev_low is None or price < prev_low
        gf_url = f"https://www.google.com/flights?hl=en#flt={origin}.{destination}.{dep_date}"
        trip_type = "Round-trip" if ret_date else "One-way"

        if is_new_low:
            lowest[key] = {"price": price, "seen_at": datetime.now().isoformat()}
            save_lowest(lowest)

            if prev_low is None:
                headline = "First price recorded"
                badge = "📊"
            else:
                drop = prev_low - price
                headline = f"LOWEST PRICE SEEN — dropped {currency} {drop:.0f} from {currency} {prev_low:.0f}"
                badge = "🔥"

            msg = (
                f"<b>{badge} {headline}</b>\n\n"
                f"✈️ {trip_type}: <b>{origin} → {destination}</b>\n"
                f"💰 Price: <b>{currency} {price}</b>\n"
                f"📅 Depart: {offer['departure_at']}\n"
                f"🛫 Airline: {offer['airline']}\n"
                f"🛑 Stops: {stops_str}\n"
                f"⏱ Duration: {fmt_duration(offer['duration_min'])}\n\n"
                f"<a href='{gf_url}'>Search on Google Flights →</a>"
            )
            try:
                send_telegram(msg)
                print(f"    Alert sent — new low!")
            except Exception as e:
                print(f"    Telegram error: {e}")

            summary.append(f"{badge} {label}: {currency} {price} — {headline}")
        else:
            diff = price - prev_low
            summary.append(
                f"😴 {label}: {currency} {price}  ({offer['airline']}, {stops_str}, {fmt_duration(offer['duration_min'])})\n"
                f"   ↳ Lowest seen: {currency} {prev_low:.0f}  (currently {currency} {diff:.0f} above)"
            )

    # Send status update every run
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    status_msg = f"<b>✈️ Flight Tracker Update</b> — {now_str}\n\n"
    status_msg += "\n\n".join(summary) if summary else "No routes checked."
    status_msg += "\n\n<i>Checking again in 6 hours.</i>"

    try:
        send_telegram(status_msg)
    except Exception as e:
        print(f"    Could not send status update: {e}")

    print("Done.\n")


if __name__ == "__main__":
    main()
