"""Local Google Flights tracker for MAA -> DXB.

Default: fetch up to three cheapest displayed nonstop Economy itineraries for
2026-09-01, 02 and 03, then append observations to data/flight_prices.csv.

Google can change its page structure or block automated browsers. Every failed
date still produces an ERROR row and a diagnostic screenshot/log entry.
"""

from __future__ import annotations

import argparse
import base64
import csv
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ORIGIN = "MAA"
DESTINATION = "DXB"
DEPARTURE_DATES = ("2026-09-01", "2026-09-02", "2026-09-03")
SOURCE = "google_flights"
CURRENCY = "INR"
MAX_FLIGHTS_PER_DATE = 3

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_DIR / "data" / "flight_prices.csv"
LOG_FILE = PROJECT_DIR / "logs" / "flight_tracker.log"
SCREENSHOT_DIR = PROJECT_DIR / "logs" / "screenshots"

CSV_FIELDS = (
    "timestamp_iso",
    "source",
    "origin",
    "destination",
    "departure_date",
    "price",
    "currency",
    "status",
    "flight_key",
    "flight_number",
    "airline",
    "departure_time",
    "arrival_time",
    "stops",
    "cabin",
    "error",
)
PRICE_RE = re.compile(r"(?:₹|INR\s*)([\d,]+)", re.IGNORECASE)
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM)(?:\+\d+)?)\b", re.IGNORECASE)
DURATION_RE = re.compile(r"^\d+\s+hr(?:\s+\d+\s+min)?$|^\d+\s+min$", re.IGNORECASE)
FLIGHT_NUMBER_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{2})[\s\u00a0]+(\d{1,4})(?!\d)")


AIRLINE_IATA = {"Emirates": "EK", "IndiGo": "6E"}


def extract_flight_number(details_text: str, airline: str = "") -> str:
    """Return the exposed airline flight number, or blank if unavailable."""
    match = FLIGHT_NUMBER_RE.search(details_text)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    prefix = AIRLINE_IATA.get(airline)
    if prefix:
        match = re.search(rf"{re.escape(prefix)}[\s\u00a0]+(\d{{1,4}})(?!\d)", details_text)
        if match:
            return f"{prefix} {match.group(1)}"
    return ""


def apply_flight_number(flight: dict[str, object], flight_number: str) -> None:
    """Prefer a real flight number as the cross-run identifier."""
    flight["flight_number"] = flight_number
    if flight_number:
        flight["flight_key"] = flight_number


def configure_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def google_flights_url(departure_date: str) -> str:
    # This is Google's public Flights search-state encoding for a one-way,
    # one-adult, Economy search. Encoding locally avoids UI/date-picker fragility.
    raw = (
        b"\x08\x1c\x10\x02\x1a\x1e\x12\x0a"
        + departure_date.encode("ascii")
        + b"j\x07\x08\x01\x12\x03MAAr\x07\x08\x01\x12\x03DXB"
          b"@\x01H\x01p\x01\x82\x01\x0b\x08\xff\xff\xff\xff\xff\xff\xff\xff\xff\x01\x98\x01\x02"
    )
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"https://www.google.com/travel/flights?hl=en&curr={CURRENCY}&tfs={encoded}"


def append_rows(rows: list[dict[str, object]]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not DATA_FILE.exists() or DATA_FILE.stat().st_size == 0
    if not needs_header:
        with DATA_FILE.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            existing_fields = tuple(reader.fieldnames or ())
            existing_rows = list(reader)
        if existing_fields != CSV_FIELDS:
            unknown_fields = set(existing_fields) - set(CSV_FIELDS)
            if unknown_fields:
                raise RuntimeError(f"CSV has unknown columns: {sorted(unknown_fields)}")
            with DATA_FILE.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                for existing in existing_rows:
                    writer.writerow({field: existing.get(field, "") for field in CSV_FIELDS})
    with DATA_FILE.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)
        handle.flush()


def error_row(departure_date: str, message: str) -> dict[str, object]:
    return {
        "timestamp_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE,
        "origin": ORIGIN,
        "destination": DESTINATION,
        "departure_date": departure_date,
        "price": "",
        "currency": CURRENCY,
        "status": "ERROR",
        "flight_key": "",
        "flight_number": "",
        "airline": "",
        "departure_time": "",
        "arrival_time": "",
        "stops": "",
        "cabin": "Economy",
        "error": message[:500],
    }


def parse_card(text: str) -> dict[str, object] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = " | ".join(lines)
    if f"{ORIGIN}–{DESTINATION}" not in joined or "Nonstop" not in lines:
        return None
    if "Business Class" in lines or "First Class" in lines:
        return None

    price_matches = list(PRICE_RE.finditer(joined))
    prices = [
        match.group(1)
        for index, match in enumerate(price_matches)
        if not re.search(
            r"round[-\s]+trip",
            joined[match.end() : price_matches[index + 1].start() if index + 1 < len(price_matches) else len(joined)],
            re.IGNORECASE,
        )
    ]
    times = TIME_RE.findall(joined)
    if not prices or len(times) < 2:
        return None

    route_index = next((i for i, line in enumerate(lines) if f"{ORIGIN}–{DESTINATION}" in line), -1)
    airline = ""
    if route_index >= 2:
        # Typical card: departure, separator, arrival, airline, duration, route.
        candidates = lines[max(0, route_index - 4) : route_index]
        for candidate in reversed(candidates):
            if not TIME_RE.fullmatch(candidate) and not DURATION_RE.fullmatch(candidate) and "–" not in candidate:
                airline = candidate
                break

    departure_time, arrival_time = times[0].upper(), times[1].upper()
    price = int(prices[-1].replace(",", ""))
    flight_key = f"{airline}|{departure_time}|{arrival_time}|nonstop|Economy"
    return {
        "price": price,
        "flight_key": flight_key,
        "flight_number": "",
        "airline": airline,
        "departure_time": departure_time,
        "arrival_time": arrival_time,
        "stops": "Nonstop",
        "cabin": "Economy",
    }


def scrape_date(page, departure_date: str) -> list[dict[str, object]]:
    url = google_flights_url(departure_date)
    logging.info("Fetching %s", departure_date)
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)

    try:
        page.get_by_text("Search results", exact=True).wait_for(timeout=60_000)
        page.locator("li").filter(has_text="Nonstop").first.wait_for(timeout=30_000)
    except PlaywrightTimeoutError as exc:
        body = page.locator("body").inner_text(timeout=10_000)
        if "unusual traffic" in body.lower() or "not a robot" in body.lower():
            raise RuntimeError("Google blocked the automated request") from exc
        raise RuntimeError("Flight results did not load before timeout") from exc

    parsed: dict[str, dict[str, object]] = {}
    cards = page.locator("li")
    for index in range(min(cards.count(), 80)):
        item = parse_card(cards.nth(index).inner_text())
        if item is not None:
            parsed.setdefault(str(item["flight_key"]), item)

    selected = sorted(parsed.values(), key=lambda flight: int(flight["price"]))[:MAX_FLIGHTS_PER_DATE]
    if not selected:
        raise RuntimeError("No displayed nonstop Economy itinerary could be parsed")

    for flight in selected:
        original_key = str(flight["flight_key"])
        current_cards = page.locator("li")
        card = None
        for index in range(min(current_cards.count(), 80)):
            candidate_card = current_cards.nth(index)
            candidate = parse_card(candidate_card.inner_text())
            if candidate is not None and candidate["flight_key"] == original_key:
                card = candidate_card
                break
        if card is not None:
            details_buttons = card.locator("button")
            if details_buttons.count() >= 2:
                try:
                    details_buttons.nth(1).click(timeout=5_000)
                    flight_number = ""
                    # The expand panel renders asynchronously after the click;
                    # poll briefly instead of reading inner_text on the same tick.
                    for _ in range(6):
                        flight_number = extract_flight_number(card.inner_text(), str(flight["airline"]))
                        if flight_number:
                            break
                        page.wait_for_timeout(300)
                    apply_flight_number(flight, flight_number)
                    card.locator("button").nth(1).click(timeout=5_000)
                except Exception:
                    logging.warning("Could not expand flight details for %s", original_key)

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for flight in selected:
        rows.append(
            {
                "timestamp_iso": timestamp,
                "source": SOURCE,
                "origin": ORIGIN,
                "destination": DESTINATION,
                "departure_date": departure_date,
                "currency": CURRENCY,
                "status": "OK",
                "error": "",
                **flight,
            }
        )
    return rows


def run_once(headless: bool = True) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        channel = os.environ.get("PW_BROWSER_CHANNEL", "chrome") or None
        browser = playwright.chromium.launch(channel=channel, headless=headless)
        context = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata")
        page = context.new_page()
        page.set_default_timeout(30_000)
        try:
            for departure_date in DEPARTURE_DATES:
                try:
                    rows = scrape_date(page, departure_date)
                    append_rows(rows)
                    logging.info("%s: wrote %d OK row(s)", departure_date, len(rows))
                except Exception as exc:  # A failed date must not crash the full run.
                    message = f"{type(exc).__name__}: {exc}"
                    append_rows([error_row(departure_date, message)])
                    logging.exception("%s: %s", departure_date, message)
                    try:
                        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        page.screenshot(path=SCREENSHOT_DIR / f"{departure_date}_{stamp}.png", full_page=True)
                    except Exception:
                        logging.exception("Could not save diagnostic screenshot")
        finally:
            context.close()
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible", action="store_true", help="Show Chrome instead of running headless")
    parser.add_argument("--loop", action="store_true", help="Run hourly instead of once")
    parser.add_argument("--iterations", type=int, default=360, help="Loop runs; default 360 (15 days hourly)")
    parser.add_argument("--interval-seconds", type=int, default=3600, help="Seconds between loop starts")
    args = parser.parse_args()

    if args.iterations < 1 or args.interval_seconds < 1:
        parser.error("iterations and interval-seconds must both be positive")

    configure_logging()
    runs = args.iterations if args.loop else 1
    logging.info("Starting tracker: runs=%d, headless=%s", runs, not args.visible)
    for run_number in range(1, runs + 1):
        started = time.monotonic()
        logging.info("Run %d/%d", run_number, runs)
        try:
            run_once(headless=not args.visible)
        except Exception as exc:
            # Handles launch-level failures before individual dates can be visited.
            message = f"browser-level {type(exc).__name__}: {exc}"
            logging.exception(message)
            append_rows([error_row(date, message) for date in DEPARTURE_DATES])
        if run_number < runs:
            elapsed = time.monotonic() - started
            time.sleep(max(1, args.interval_seconds - elapsed))


if __name__ == "__main__":
    main()
