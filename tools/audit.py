"""Daily quality audit for the MAA -> DXB fare tracker.

Runs a fixed set of deterministic checks over data/flight_prices.csv and prints
a graded report. Intended to be run by hand (or by Hermes) once or twice a day
while the 15-day collection window is open.

The point of this script is that it decides, not the reader: every check has a
hard threshold, so two runs on the same data always produce the same verdict.

Exit codes: 0 = all clear, 1 = at least one WARN, 2 = at least one FAIL.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_DIR / "data" / "flight_prices.csv"

EXPECTED_HEADER = [
    "timestamp_iso", "source", "origin", "destination", "departure_date",
    "price", "currency", "status", "flight_key", "flight_number", "airline",
    "departure_time", "arrival_time", "stops", "cabin", "error",
]
EXPECTED_DATES = ("2026-09-01", "2026-09-02", "2026-09-03")
WINDOW_END = datetime(2026, 8, 28, 23, 59, tzinfo=timezone.utc)

# Thresholds. Hourly cron plus GitHub's normal 5-20 min jitter means a healthy
# gap sits well under 90 minutes; beyond three hours the schedule has stalled.
FRESH_WARN_MIN, FRESH_FAIL_MIN = 90, 180
COVERAGE_MIN_SAMPLE = 6                    # hours of history before coverage is graded
COVERAGE_WARN, COVERAGE_FAIL = 0.85, 0.60  # fraction of expected hourly runs that landed
ERROR_RATE_WARN, ERROR_RATE_FAIL = 0.05, 0.20
PRICE_FLOOR, PRICE_CEILING = 5_000, 500_000
PRICE_JUMP_WARN = 0.40                     # fractional move vs previous observation

SEVERITY_ORDER = {"OK": 0, "INFO": 0, "WARN": 1, "FAIL": 2}
GLYPH = {"OK": "[ OK ]", "INFO": "[INFO]", "WARN": "[WARN]", "FAIL": "[FAIL]"}


def load_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    # utf-8-sig: the scraper writes a BOM, and csv would otherwise fold it into
    # the first field name.
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def parse_ts(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def hour_bucket(stamp: datetime) -> datetime:
    return stamp.replace(minute=0, second=0, microsecond=0)


def humanise(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f} min"
    return f"{minutes / 60:.1f} h"


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, str]] = []

    def add(self, severity: str, name: str, detail: str) -> None:
        self.checks.append({"severity": severity, "check": name, "detail": detail})

    @property
    def verdict(self) -> str:
        worst = max((SEVERITY_ORDER[c["severity"]] for c in self.checks), default=0)
        return {0: "HEALTHY", 1: "DEGRADED", 2: "BROKEN"}[worst]

    @property
    def exit_code(self) -> int:
        return max((SEVERITY_ORDER[c["severity"]] for c in self.checks), default=0)


def audit(rows: list[dict[str, str]], header: list[str], now: datetime, hours: int) -> Report:
    report = Report()

    if header != EXPECTED_HEADER:
        missing = [c for c in EXPECTED_HEADER if c not in header]
        extra = [c for c in header if c not in EXPECTED_HEADER]
        report.add("FAIL", "schema", f"header mismatch; missing={missing} unexpected={extra}")
        return report
    report.add("OK", "schema", f"{len(EXPECTED_HEADER)} columns as expected")

    if not rows:
        report.add("FAIL", "data", "CSV contains a header but no observations")
        return report

    stamped = [(parse_ts(r["timestamp_iso"]), r) for r in rows]
    unparsable = [r for ts, r in stamped if ts is None]
    if unparsable:
        report.add("FAIL", "timestamps", f"{len(unparsable)} row(s) have an unparsable timestamp")
    stamped = [(ts, r) for ts, r in stamped if ts is not None]
    if not stamped:
        return report

    future = [ts for ts, _ in stamped if ts > now + timedelta(minutes=5)]
    if future:
        report.add("FAIL", "timestamps", f"{len(future)} row(s) are dated in the future")

    latest = max(ts for ts, _ in stamped)
    earliest = min(ts for ts, _ in stamped)
    age_min = (now - latest).total_seconds() / 60

    # --- freshness -----------------------------------------------------------
    if now > WINDOW_END:
        report.add("INFO", "freshness",
                   f"collection window closed {WINDOW_END:%Y-%m-%d}; last row {humanise(age_min)} old")
    elif age_min > FRESH_FAIL_MIN:
        report.add("FAIL", "freshness",
                   f"last observation {humanise(age_min)} ago - schedule has stalled")
    elif age_min > FRESH_WARN_MIN:
        report.add("WARN", "freshness",
                   f"last observation {humanise(age_min)} ago - one or more runs missed")
    else:
        report.add("OK", "freshness", f"last observation {humanise(age_min)} ago")

    # --- run coverage over the recent window ---------------------------------
    cutoff = now - timedelta(hours=hours)
    recent = [(ts, r) for ts, r in stamped if ts >= cutoff]
    buckets = {hour_bucket(ts) for ts, _ in recent}
    expected_buckets = min(hours, int((now - earliest).total_seconds() // 3600) + 1)
    filled = len(buckets)
    ratio = filled / expected_buckets if expected_buckets else 1.0
    detail = f"{filled}/{expected_buckets} hourly runs landed in the last {hours}h ({ratio:.0%})"

    # Grade a proportion rather than a raw count, so the ramp-up hours after
    # setup are not scored as if a full day were already due.
    if expected_buckets < COVERAGE_MIN_SAMPLE:
        report.add("INFO", "coverage", detail + " - too early to grade")
    elif ratio < COVERAGE_FAIL:
        report.add("FAIL", "coverage", detail)
    elif ratio < COVERAGE_WARN:
        report.add("WARN", "coverage", detail)
    else:
        report.add("OK", "coverage", detail)

    # --- error rate ----------------------------------------------------------
    statuses = Counter(r["status"] for _, r in recent)
    total = sum(statuses.values())
    errors = statuses.get("ERROR", 0)
    rate = errors / total if total else 0.0
    detail = f"{errors}/{total} rows ERROR in last {hours}h ({rate:.1%})"
    if rate >= ERROR_RATE_FAIL:
        report.add("FAIL", "error_rate", detail)
    elif rate > ERROR_RATE_WARN:
        report.add("WARN", "error_rate", detail)
    else:
        report.add("OK", "error_rate", detail)

    if errors:
        common = Counter(r["error"][:110] for _, r in recent if r["status"] == "ERROR")
        for message, count in common.most_common(3):
            report.add("INFO", "error_sample", f"x{count}: {message}")

    # --- per-date coverage in the most recent run ----------------------------
    last_bucket = hour_bucket(latest)
    last_run = [r for ts, r in stamped if hour_bucket(ts) == last_bucket]
    seen_dates = {r["departure_date"] for r in last_run if r["status"] == "OK"}
    absent = [d for d in EXPECTED_DATES if d not in seen_dates]
    if absent:
        report.add("WARN", "date_coverage", f"latest run returned no OK rows for {', '.join(absent)}")
    else:
        report.add("OK", "date_coverage", f"latest run covered all {len(EXPECTED_DATES)} departure dates")

    # --- price sanity --------------------------------------------------------
    ok_rows = [(ts, r) for ts, r in stamped if r["status"] == "OK"]
    prices: list[tuple[datetime, dict[str, str], int]] = []
    malformed = 0
    for ts, row in ok_rows:
        try:
            prices.append((ts, row, int(row["price"])))
        except (TypeError, ValueError):
            malformed += 1
    if malformed:
        report.add("FAIL", "price_parse", f"{malformed} OK row(s) have a non-numeric price")

    outliers = [(r["departure_date"], p) for _, r, p in prices
                if not PRICE_FLOOR <= p <= PRICE_CEILING]
    if outliers:
        report.add("WARN", "price_range",
                   f"{len(outliers)} price(s) outside Rs{PRICE_FLOOR:,}-Rs{PRICE_CEILING:,}: {outliers[:3]}")
    elif prices:
        report.add("OK", "price_range", f"all {len(prices)} prices within plausible bounds")

    # --- sudden moves --------------------------------------------------------
    by_flight: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    for ts, row, price in prices:
        by_flight[(row["departure_date"], row["flight_key"])].append((ts, price))

    jumps = []
    for (dep_date, key), series in by_flight.items():
        series.sort()
        for (_, before), (after_ts, after) in zip(series, series[1:]):
            if before and abs(after - before) / before >= PRICE_JUMP_WARN and after_ts >= cutoff:
                jumps.append(f"{dep_date} {key.split('|')[0]}: Rs{before:,} -> Rs{after:,}")
    if jumps:
        report.add("WARN", "price_jump",
                   f"{len(jumps)} move(s) >={PRICE_JUMP_WARN:.0%} in last {hours}h: {'; '.join(jumps[:3])}")
    else:
        report.add("OK", "price_jump", f"no move >={PRICE_JUMP_WARN:.0%} between consecutive observations")

    # --- duplicates ----------------------------------------------------------
    seen = Counter((r["timestamp_iso"], r["departure_date"], r["flight_key"]) for _, r in stamped)
    dupes = [k for k, n in seen.items() if n > 1]
    if dupes:
        report.add("WARN", "duplicates", f"{len(dupes)} duplicate (timestamp, date, flight) key(s)")
    else:
        report.add("OK", "duplicates", "no duplicate observations")

    # --- flight-number enrichment (informational by design) ------------------
    if ok_rows:
        blank = sum(1 for _, r in ok_rows if not r["flight_number"].strip())
        share = blank / len(ok_rows)
        report.add("INFO", "flight_number",
                   f"{blank}/{len(ok_rows)} OK rows fall back to airline+time key ({share:.0%})")

    # --- window progress -----------------------------------------------------
    remaining = (WINDOW_END - now).total_seconds() / 86400
    if remaining > 0:
        report.add("INFO", "window",
                   f"{remaining:.1f} day(s) left; {len(stamped)} rows collected since {earliest:%Y-%m-%d %H:%M} UTC")
    else:
        report.add("INFO", "window", f"window closed; {len(stamped)} rows total")

    return report


def render(report: Report, now: datetime) -> str:
    ist = now + timedelta(hours=5, minutes=30)
    lines = [
        "=" * 68,
        f"  FLIGHT TRACKER QUALITY AUDIT - {report.verdict}",
        f"  {now:%Y-%m-%d %H:%M} UTC / {ist:%H:%M} IST",
        "=" * 68,
    ]
    for check in report.checks:
        lines.append(f"{GLYPH[check['severity']]} {check['check']:<14} {check['detail']}")
    lines.append("-" * 68)
    counts = Counter(c["severity"] for c in report.checks)
    lines.append(
        f"  verdict={report.verdict}  fail={counts['FAIL']} warn={counts['WARN']} ok={counts['OK']}"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DATA_FILE)
    parser.add_argument("--hours", type=int, default=24, help="Window for rate checks; default 24")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"FAIL: {args.csv} does not exist", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    header, rows = load_rows(args.csv)
    report = audit(rows, header, now, args.hours)

    if args.json:
        print(json.dumps({
            "generated_utc": now.isoformat(timespec="seconds"),
            "verdict": report.verdict,
            "exit_code": report.exit_code,
            "checks": report.checks,
        }, indent=2))
    else:
        print(render(report, now))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
