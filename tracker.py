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

CONFIG_FILE = Path(__file__).parent / "config.json"
LOWEST_FILE = Path(__file__).parent / "lowest_prices.json"

MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

DISCOUNT_AIRLINES = {
    "Qatar Airways",
    "Singapore Airlines",
    "Cathay Pacific",
    "Emirates",
    "Malaysia Airlines",
    "Virgin Australia",
}

# Airports to avoid — Middle East hubs
BLOCKED_AIRPORTS = {
    "DOH",  # Doha, Qatar
    "DXB",  # Dubai, UAE
    "AUH",  # Abu Dhabi, UAE
    "SHJ",  # Sharjah, UAE
    "RUH",  # Riyadh, Saudi Arabia
    "JED",  # Jeddah, Saudi Arabia
    "BAH",  # Bahrain
    "KWI",  # Kuwait
    "MCT",  # Muscat, Oman
    "CAI",  # Cairo, Egypt
    "AMM",  # Amman, Jordan
}


def has_blocked_stopover(legs: list) -> bool:
    for leg in legs[:-1]:
        code = leg.get("arrival_airport", {}).get("id", "")
        if code.upper() in BLOCKED_AIRPORTS:
            return True
    return False


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
        return []

    all_offers.sort(key=lambda o: o.get("price", float("inf")))

    results = []
    for offer in all_offers:
        legs = offer.get("flights", [])
        if has_blocked_stopover(legs):
            continue

        first_leg = legs[0] if legs else {}
        last_leg  = legs[-1] if legs else {}

        layovers = offer.get("layovers", [])

        segments = []
        for i, leg in enumerate(legs):
            dep = leg.get("departure_airport", {})
            arr = leg.get("arrival_airport", {})
            seg = {
                "from_code":    dep.get("id", ""),
                "to_code":      arr.get("id", ""),
                "to_name":      arr.get("name", ""),
                "duration_min": leg.get("duration"),
                "blocked":      arr.get("id", "").upper() in BLOCKED_AIRPORTS,
            }
            segments.append(seg)
            if i < len(layovers):
                seg["layover_min"] = layovers[i].get("duration")
            else:
                seg["layover_min"] = None

        results.append({
            "price":        offer.get("price"),
            "airline":      first_leg.get("airline", "N/A"),
            "duration_min": offer.get("total_duration"),
            "departure_at": first_leg.get("departure_airport", {}).get("time", departure_date),
            "arrival_at":   last_leg.get("arrival_airport", {}).get("time", ""),
            "stops":        len(legs) - 1,
            "segments":     segments,
        })

        if len(results) == 5:
            break

    return results


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

    lowest  = load_lowest()
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

    print(f"\nFlight tracker started — {now_str}")

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
            offers = search_flights(origin, destination, dep_date, ret_date, adults, currency)
        except Exception as e:
            print(f"    Error: {e}")
            try:
                send_telegram(f"⚠️ {label}: error fetching flights — {e}")
            except Exception:
                pass
            continue

        if not offers:
            print("    No flights found.")
            try:
                send_telegram(f"❓ {label}: No flights found")
            except Exception:
                pass
            continue

        best_price = offers[0]["price"]
        prev_low      = lowest.get(key, {}).get("price")
        prev_seen_at  = lowest.get(key, {}).get("seen_at")
        is_new_low    = prev_low is None or best_price < prev_low

        if is_new_low:
            lowest[key] = {"price": best_price, "seen_at": datetime.now().isoformat()}
            save_lowest(lowest)

        if is_new_low and prev_low is not None:
            drop   = prev_low - best_price
            header = f"🔥 <b>NEW LOW — -{currency} {drop:.0f}!</b>\n"
        elif is_new_low:
            header = f"📊 <b>First check</b>\n"
        else:
            header = f"✈️ <b>{origin}→{destination}</b>\n"

        if prev_seen_at:
            seen_date = datetime.fromisoformat(prev_seen_at).strftime("%d %b %Y")
        else:
            seen_date = None

        msg  = "――――――――――――――――――\n"
        msg += header
        msg += f"🕐 {now_str} | Depart {dep_date}\n\n"

        if is_new_low:
            msg += f"📉 Lowest ever: <b>{currency} {best_price:.0f}</b> (today)\n\n"
        else:
            above = best_price - prev_low
            msg += f"📉 Lowest ever: <b>{currency} {prev_low:.0f}</b> ({seen_date}) · +{currency} {above:.0f} now\n\n"

        for i, offer in enumerate(offers):
            medal    = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
            segments = offer["segments"]

            airline      = offer['airline']
            full_price   = offer['price']
            discounted   = round(full_price * 0.9)
            has_discount = any(d.lower() in airline.lower() for d in DISCOUNT_AIRLINES)

            msg += f"\n{medal} <b>{currency} {full_price}</b> — {airline}\n"
            if has_discount:
                msg += f"   🎓 With 10% discount: <b>{currency} {discounted}</b>\n"
            msg += f"   🛫 Departs: {offer['departure_at']}\n"

            for seg in segments:
                msg += f"   ✈️  {seg['from_code']}→{seg['to_code']}: {fmt_duration(seg['duration_min'])}\n"
                if seg.get("layover_min") is not None:
                    msg += f"   🔁 Layover: {fmt_duration(seg['layover_min'])}\n"

            msg += f"   🛬 {offer['arrival_at']}\n"
            msg += f"   ⏱ Total: {fmt_duration(offer['duration_min'])}\n"

        gf_url = f"https://www.google.com/flights?hl=en#flt={origin}.{destination}.{dep_date}"
        msg += f"<a href='{gf_url}'>Google Flights →</a>"

        print(msg)
        try:
            send_telegram(msg)
            print("    Message sent!")
        except Exception as e:
            print(f"    Telegram error: {e}")

    print("Done.\n")


if __name__ == "__main__":
    main()
