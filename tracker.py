#!/usr/bin/env python3
import io
import json
import os
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SERPAPI_KEY    = os.getenv("SERPAPI_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")

CONFIG_FILE  = Path(__file__).parent / "config.json"
LOWEST_FILE  = Path(__file__).parent / "lowest_prices.json"
HISTORY_FILE = Path(__file__).parent / "price_history.json"

MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

BLOCKED_AIRPORTS = {
    "DOH", "DXB", "AUH", "SHJ", "RUH", "JED", "BAH", "KWI", "MCT", "CAI", "AMM",
}

DISCOUNT_AIRLINES = {
    "Qatar Airways", "Singapore Airlines", "Cathay Pacific",
    "Emirates", "Malaysia Airlines", "Virgin Australia",
}


def has_blocked_stopover(legs):
    for leg in legs[:-1]:
        if leg.get("arrival_airport", {}).get("id", "").upper() in BLOCKED_AIRPORTS:
            return True
    return False


def search_flights(origin, destination, departure_date, return_date=None, adults=1, currency="USD"):
    params = {
        "engine":        "google_flights",
        "departure_id":  origin,
        "arrival_id":    destination,
        "outbound_date": departure_date,
        "adults":        adults,
        "currency":      currency,
        "hl":            "en",
        "api_key":       SERPAPI_KEY,
        "type":          1 if return_date else 2,
    }
    if return_date:
        params["return_date"] = return_date

    resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    all_offers = data.get("best_flights", []) + data.get("other_flights", [])
    if not all_offers:
        return [], None

    all_offers = [o for o in all_offers if o.get("price") is not None]
    all_offers.sort(key=lambda o: o.get("price", float("inf")))

    direct_alert = None
    results      = []

    for offer in all_offers:
        legs = offer.get("flights", [])

        # Capture the cheapest direct flight seen, regardless of stopover filter
        if len(legs) == 1 and direct_alert is None:
            fl = legs[0]
            direct_alert = {
                "price":        offer.get("price"),
                "airline":      fl.get("airline", "N/A"),
                "duration_min": offer.get("total_duration"),
                "departure_at": fl.get("departure_airport", {}).get("time", departure_date),
                "arrival_at":   fl.get("arrival_airport", {}).get("time", ""),
            }

        if has_blocked_stopover(legs):
            continue

        first_leg = legs[0] if legs else {}
        last_leg  = legs[-1] if legs else {}
        layovers  = offer.get("layovers", [])

        segments = []
        for i, leg in enumerate(legs):
            dep = leg.get("departure_airport", {})
            arr = leg.get("arrival_airport", {})
            segments.append({
                "from_code":    dep.get("id", ""),
                "to_code":      arr.get("id", ""),
                "duration_min": leg.get("duration"),
                "layover_min":  layovers[i].get("duration") if i < len(layovers) else None,
            })

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

    return results, direct_alert


def search_best_price(origin, destination, departure_date, adults=1, currency="USD"):
    """Returns cheapest non-blocked price for a single date, or None."""
    params = {
        "engine":        "google_flights",
        "departure_id":  origin,
        "arrival_id":    destination,
        "outbound_date": departure_date,
        "adults":        adults,
        "currency":      currency,
        "hl":            "en",
        "api_key":       SERPAPI_KEY,
        "type":          2,
    }
    resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    all_offers = data.get("best_flights", []) + data.get("other_flights", [])
    all_offers = [o for o in all_offers if o.get("price") is not None]
    all_offers.sort(key=lambda o: o.get("price", float("inf")))

    for offer in all_offers:
        if not has_blocked_stopover(offer.get("flights", [])):
            return offer.get("price")
    return None


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )
    resp.raise_for_status()


def send_telegram_photo(image_bytes, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("chart.png", image_bytes, "image/png")},
        timeout=30,
    )
    resp.raise_for_status()


def load_json(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def route_key(route):
    return f"{route['origin'].upper()}-{route['destination'].upper()}-{route['departure_date']}"


def fmt_duration(minutes):
    if not minutes:
        return "N/A"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m"


def days_until(date_str):
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (target - datetime.now().date()).days


def generate_chart(entries, label, currency):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    dates  = [datetime.strptime(e["date"], "%Y-%m-%d") for e in entries]
    prices = [e["price"] for e in entries]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(dates, prices, marker="o", linewidth=2, color="#4A90D9", markersize=5)
    ax.fill_between(dates, prices, alpha=0.12, color="#4A90D9")

    min_price = min(prices)
    min_idx   = prices.index(min_price)
    ax.annotate(
        f"Low: {currency} {min_price}",
        xy=(dates[min_idx], min_price),
        xytext=(12, 12), textcoords="offset points",
        fontsize=9, color="green",
        arrowprops=dict(arrowstyle="->", color="green"),
    )

    ax.set_title(f"{label} — Price History", fontsize=13, fontweight="bold")
    ax.set_ylabel(f"Price ({currency})")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def day_of_week_insight(entries):
    if len(entries) < 14:
        return None
    by_day = {}
    for e in entries:
        dow = datetime.strptime(e["date"], "%Y-%m-%d").strftime("%a")
        by_day.setdefault(dow, []).append(e["price"])
    avgs     = {d: sum(p) / len(p) for d, p in by_day.items()}
    best_day = min(avgs, key=avgs.get)
    order    = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    row      = "  ".join(f"{d}:{avgs[d]:.0f}" for d in order if d in avgs)
    return f"📅 Cheapest day: <b>{best_day}</b>\n<code>{row}</code>"


def main():
    missing = [k for k, v in {
        "SERPAPI_KEY":        SERPAPI_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT,
    }.items() if not v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}")

    if not CONFIG_FILE.exists():
        sys.exit("config.json not found")

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    routes = config.get("routes", [])
    if not routes:
        sys.exit("No routes defined")

    lowest  = load_json(LOWEST_FILE)
    history = load_json(HISTORY_FILE)
    now     = datetime.now()
    now_str = now.strftime("%d %b %Y, %I:%M %p")
    today   = now.strftime("%Y-%m-%d")

    print(f"\nFlight tracker — {now_str}")

    for route in routes:
        origin      = route["origin"].upper()
        destination = route["destination"].upper()
        dep_date    = route["departure_date"]
        ret_date    = route.get("return_date")
        adults      = int(route.get("adults", 1))
        currency    = route.get("currency", "USD")
        label       = route.get("label", f"{origin}→{destination}")
        key         = route_key(route)
        days_left   = days_until(dep_date)

        print(f"  Checking {label} ({days_left} days away)...")

        try:
            offers, direct_alert = search_flights(
                origin, destination, dep_date, ret_date, adults, currency
            )
        except Exception as e:
            print(f"    Error: {e}")
            try:
                send_telegram(f"⚠️ {label}: error — {e}")
            except Exception:
                pass
            continue

        # Direct flight — shout loudly
        if direct_alert:
            try:
                send_telegram(
                    f"🚨🚨 <b>DIRECT FLIGHT FOUND!</b> 🚨🚨\n\n"
                    f"<b>{origin} → {destination}</b>\n"
                    f"💰 <b>{currency} {direct_alert['price']}</b> — {direct_alert['airline']}\n"
                    f"🛫 Departs: {direct_alert['departure_at']}\n"
                    f"🛬 Arrives: {direct_alert['arrival_at']}\n"
                    f"⏱ {fmt_duration(direct_alert['duration_min'])}\n\n"
                    f"<b>Book immediately!</b>"
                )
            except Exception as e:
                print(f"    Direct flight alert failed: {e}")

        if not offers:
            print("    No qualifying flights found.")
            try:
                send_telegram(f"❓ {label}: No flights found (all routes via blocked airports)")
            except Exception:
                pass
            continue

        best_price   = offers[0]["price"]
        prev_low     = lowest.get(key, {}).get("price")
        prev_seen_at = lowest.get(key, {}).get("seen_at")
        is_new_low   = prev_low is None or best_price < prev_low

        if is_new_low:
            lowest[key] = {"price": best_price, "seen_at": now.isoformat()}
            save_json(LOWEST_FILE, lowest)

        # Append to price history (one entry per day)
        route_history = history.setdefault(key, [])
        if not route_history or route_history[-1]["date"] != today:
            route_history.append({"date": today, "price": best_price})
            save_json(HISTORY_FILE, history)

        # ── Build message ────────────────────────────────────────────────────
        if is_new_low and prev_low is not None:
            drop   = prev_low - best_price
            header = f"🔥 <b>NEW LOW — -{currency} {drop:.0f}!</b>\n"
        elif is_new_low:
            header = f"📊 <b>First check</b>\n"
        else:
            header = f"✈️ <b>{origin}→{destination}</b>\n"

        seen_date = (
            datetime.fromisoformat(prev_seen_at).strftime("%d %b %Y")
            if prev_seen_at else None
        )

        msg  = "――――――――――――――――――\n"
        msg += header
        msg += f"🕐 {now_str} | Depart {dep_date}\n\n"

        if is_new_low:
            msg += f"📉 Lowest ever: <b>{currency} {best_price:.0f}</b> (today)\n"
        else:
            above = best_price - prev_low
            msg += f"📉 Lowest ever: <b>{currency} {prev_low:.0f}</b> ({seen_date}) · +{currency} {above:.0f} now\n"

        # Booking window warnings
        if days_left <= 30:
            msg += f"🚨 {days_left} days to departure — book soon!\n"
        elif days_left <= 60:
            msg += f"⏳ {days_left} days to departure — prices may start rising\n"

        # Day-of-week insight (shows once enough data exists)
        dow = day_of_week_insight(route_history)
        if dow:
            msg += f"{dow}\n"

        msg += "\n<b>Top 5 flights:</b>\n"

        for i, offer in enumerate(offers):
            medal      = MEDALS[i] if i < len(MEDALS) else f"{i+1}."
            airline    = offer["airline"]
            full_price = offer["price"]
            has_disc   = any(d.lower() in airline.lower() for d in DISCOUNT_AIRLINES)

            msg += f"\n{medal} <b>{currency} {full_price}</b> — {airline}\n"
            if has_disc:
                msg += f"   🎓 With 10% discount: <b>{currency} {round(full_price * 0.9)}</b>\n"
            msg += f"   🛫 Departs: {offer['departure_at']}\n"

            for seg in offer["segments"]:
                msg += f"   ✈️  {seg['from_code']}→{seg['to_code']}: {fmt_duration(seg['duration_min'])}\n"
                if seg.get("layover_min") is not None:
                    msg += f"   🔁 Layover: {fmt_duration(seg['layover_min'])}\n"

            msg += f"   🛬 {offer['arrival_at']}\n"
            msg += f"   ⏱ Total: {fmt_duration(offer['duration_min'])}\n"

        gf_url = f"https://www.google.com/flights?hl=en#flt={origin}.{destination}.{dep_date}"
        msg += f"\n<a href='{gf_url}'>Google Flights →</a>"

        try:
            send_telegram(msg)
            print("    Message sent!")
        except Exception as e:
            print(f"    Telegram error: {e}")

        # ── Flexible dates + cheapest week (Sundays only) ────────────────────
        is_sunday      = now.weekday() == 6
        last_chart_key = f"{key}_last_chart"
        already_sent   = lowest.get(last_chart_key) == today

        if is_sunday and not already_sent:
            base     = datetime.strptime(dep_date, "%Y-%m-%d")
            flex_dates = [
                (base + timedelta(days=d)).strftime("%Y-%m-%d")
                for d in range(-3, 4)
            ]
            flex_prices = {}
            for fd in flex_dates:
                if fd == dep_date:
                    flex_prices[fd] = best_price   # already fetched
                else:
                    try:
                        flex_prices[fd] = search_best_price(origin, destination, fd, adults, currency)
                    except Exception:
                        flex_prices[fd] = None

            valid_flex   = {d: p for d, p in flex_prices.items() if p is not None}
            cheapest_fd  = min(valid_flex, key=valid_flex.get) if valid_flex else None

            flex_msg  = "――――――――――――――――――\n"
            flex_msg += f"📅 <b>Flexible Dates (±3 days)</b>\n\n"
            for fd in flex_dates:
                price     = flex_prices.get(fd)
                d_label   = datetime.strptime(fd, "%Y-%m-%d").strftime("%a %d %b")
                is_orig   = fd == dep_date
                is_cheap  = fd == cheapest_fd and not is_orig
                tag       = " ← cheapest" if is_cheap else ""
                orig_tag  = " (your date)" if is_orig else ""
                if price is None:
                    flex_msg += f"   {d_label}: N/A{orig_tag}\n"
                elif is_cheap:
                    flex_msg += f"   {d_label}: <b>{currency} {price}</b>{tag}{orig_tag}\n"
                else:
                    flex_msg += f"   {d_label}: {currency} {price}{orig_tag}\n"

            try:
                send_telegram(flex_msg)
                print("    Flexible dates sent!")
            except Exception as e:
                print(f"    Flex dates error: {e}")

        # ── Cheapest week (Sundays only) ─────────────────────────────────────
        last_chart_key = f"{key}_last_chart"
        already_sent   = lowest.get(last_chart_key) == today

        if is_sunday and not already_sent and len(route_history) >= 2:
            try:
                # Price history chart
                chart_bytes = generate_chart(route_history, label, currency)
                week_prices = [
                    e["price"] for e in route_history
                    if (now - datetime.strptime(e["date"], "%Y-%m-%d")).days <= 7
                ]
                prev_prices = [
                    e["price"] for e in route_history
                    if 7 < (now - datetime.strptime(e["date"], "%Y-%m-%d")).days <= 14
                ]
                caption = f"<b>📊 Weekly Summary — {label}</b>\n"
                if week_prices:
                    caption += f"This week's low: {currency} {min(week_prices)}\n"
                if prev_prices:
                    diff  = min(week_prices) - min(prev_prices)
                    trend = f"▲ +{currency} {abs(diff):.0f} vs last week" if diff > 0 else f"▼ -{currency} {abs(diff):.0f} vs last week"
                    caption += f"{trend}\n"
                caption += f"All-time low: {currency} {min(e['price'] for e in route_history):.0f}"
                send_telegram_photo(chart_bytes, caption)
                lowest[last_chart_key] = today
                save_json(LOWEST_FILE, lowest)
                print("    Weekly chart sent!")

                # Cheapest week — scan Sep 1 through departure date
                dep_dt      = datetime.strptime(dep_date, "%Y-%m-%d")
                scan_start  = dep_dt.replace(day=1)
                scan_days   = (dep_dt - scan_start).days + 1
                week_scan   = {}

                for d in range(scan_days):
                    scan_date = (scan_start + timedelta(days=d)).strftime("%Y-%m-%d")
                    if scan_date == dep_date:
                        week_scan[scan_date] = best_price
                    else:
                        try:
                            week_scan[scan_date] = search_best_price(
                                origin, destination, scan_date, adults, currency
                            )
                        except Exception:
                            week_scan[scan_date] = None

                valid_scan   = {d: p for d, p in week_scan.items() if p is not None}
                cheapest_day = min(valid_scan, key=valid_scan.get) if valid_scan else None

                cw_msg  = "――――――――――――――――――\n"
                cw_msg += f"📊 <b>Cheapest days in {dep_dt.strftime('%B')}</b>\n\n"
                for scan_date in sorted(week_scan):
                    price    = week_scan[scan_date]
                    d_label  = datetime.strptime(scan_date, "%Y-%m-%d").strftime("%a %d %b")
                    is_orig  = scan_date == dep_date
                    is_cheap = scan_date == cheapest_day
                    orig_tag = " (your date)" if is_orig else ""
                    if price is None:
                        cw_msg += f"   {d_label}: N/A{orig_tag}\n"
                    elif is_cheap:
                        cw_msg += f"   {d_label}: <b>{currency} {price}</b> ← cheapest{orig_tag}\n"
                    else:
                        cw_msg += f"   {d_label}: {currency} {price}{orig_tag}\n"

                send_telegram(cw_msg)
                print("    Cheapest week sent!")

            except Exception as e:
                print(f"    Sunday summary error: {e}")

    print("Done.\n")


if __name__ == "__main__":
    main()
