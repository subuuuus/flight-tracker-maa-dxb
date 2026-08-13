"""Render the fare tracker's status page from data/flight_prices.csv.

Writes two things, both committed by the hourly workflow:

  docs/index.html  - a self-contained status dashboard served by GitHub Pages
  README.md        - a short summary block, so the repo landing page is useful
                     even to someone who never opens Pages

No third-party dependencies and no CDN calls: the chart is inline SVG generated
here, so the page renders identically offline, in a fork, or behind Pages.
"""

from __future__ import annotations

import argparse
import csv
import html
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_DIR / "data" / "flight_prices.csv"
HTML_OUT = PROJECT_DIR / "docs" / "index.html"
README_OUT = PROJECT_DIR / "README.md"

ROUTE = "MAA → DXB"
EXPECTED_DATES = ("2026-09-01", "2026-09-02", "2026-09-03")
SERIES_COLOURS = {
    "2026-09-01": "var(--s1)",
    "2026-09-02": "var(--s2)",
    "2026-09-03": "var(--s3)",
}
STALE_MIN, DEAD_MIN = 90, 180
RUN_STRIP_DAYS = 15
IST = timezone(timedelta(hours=5, minutes=30))

README_START = "<!-- DASHBOARD:START -->"
README_END = "<!-- DASHBOARD:END -->"


def parse_ts(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def hour_bucket(stamp: datetime) -> datetime:
    """Floor a timestamp to its IST wall-clock hour."""
    return stamp.astimezone(IST).replace(minute=0, second=0, microsecond=0)


def display_date(value: str | date | datetime) -> str:
    """Format a date for dashboard display as dd-mmm-yy."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return value.strftime("%d-%b-%y")


def display_datetime(stamp: datetime) -> str:
    """Format an IST date-time label for dashboard display."""
    return f"{display_date(stamp)} {stamp:%H:%M}"


def rupees(value: float) -> str:
    return f"₹{int(round(value)):,}"


def load(path: Path) -> list[tuple[datetime, dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    stamped = [(parse_ts(r["timestamp_iso"]), r) for r in rows]
    return sorted(((ts, r) for ts, r in stamped if ts), key=lambda pair: pair[0])


def cheapest_series(observations) -> dict[str, list[tuple[datetime, int]]]:
    """Cheapest OK fare per departure date per hourly run."""
    grouped: dict[tuple[str, datetime], int] = {}
    for stamp, row in observations:
        if row["status"] != "OK":
            continue
        try:
            price = int(row["price"])
        except (TypeError, ValueError):
            continue
        key = (row["departure_date"], hour_bucket(stamp))
        grouped[key] = min(grouped.get(key, price), price)

    series: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for (dep_date, bucket), price in sorted(grouped.items(), key=lambda kv: kv[0][1]):
        series[dep_date].append((bucket, price))
    return series


def flight_fingerprint(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    """Stable fallback identity for a flight before its number is enriched."""
    return tuple(row.get(field, "").strip().casefold() for field in
                 ("airline", "departure_time", "arrival_time", "stops", "cabin"))


def unique_flight_counts(observations) -> dict[str, int]:
    """Count physical OK flight options, merging legacy and enriched rows."""
    numbers_by_fingerprint: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    for _, row in observations:
        if row["status"] == "OK" and row.get("flight_number", "").strip():
            numbers_by_fingerprint[flight_fingerprint(row)].add(row["flight_number"].strip())

    identities: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for _, row in observations:
        if row["status"] != "OK":
            continue
        fingerprint = flight_fingerprint(row)
        number = row.get("flight_number", "").strip()
        known_numbers = numbers_by_fingerprint[fingerprint]
        if number:
            identity = ("number", number)
        elif len(known_numbers) == 1:
            identity = ("number", next(iter(known_numbers)))
        else:
            identity = ("fallback", *fingerprint)
        identities[row["departure_date"]].add(identity)
    return {dep_date: len(identities[dep_date]) for dep_date in EXPECTED_DATES}


def daily_series(hourly: dict[str, list[tuple[datetime, int]]]) -> dict[str, list[tuple[datetime, int]]]:
    """Lowest fare per IST calendar day for each departure date."""
    result: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for dep_date, points in hourly.items():
        lows: dict[datetime, int] = {}
        for stamp, price in points:
            day = stamp.replace(hour=0)
            lows[day] = min(lows.get(day, price), price)
        result[dep_date] = sorted(lows.items())
    return result


def svg_chart(series: dict[str, list[tuple[datetime, int]]], width: int = 920, height: int = 340,
              empty_message: str | None = None) -> str:
    points = [(t, p) for pts in series.values() for t, p in pts]
    if len(points) < 2:
        message = empty_message or ('Not enough observations yet to plot a trend. '
                                    'The chart appears once two hourly runs have completed.')
        return f'<p class="empty">{message}</p>'

    pad_l, pad_r, pad_t, pad_b = 74, 18, 18, 40
    t_min = min(t for t, _ in points)
    t_max = max(t for t, _ in points)
    p_min = min(p for _, p in points)
    p_max = max(p for _, p in points)
    span = (t_max - t_min).total_seconds() or 1
    # Pad the value axis by 5% so lines never sit flush against the frame.
    head = (p_max - p_min) * 0.05 or max(p_max * 0.02, 1)
    p_lo, p_hi = p_min - head, p_max + head

    def x_of(stamp: datetime) -> float:
        return pad_l + (stamp - t_min).total_seconds() / span * (width - pad_l - pad_r)

    def y_of(price: float) -> float:
        return pad_t + (p_hi - price) / (p_hi - p_lo) * (height - pad_t - pad_b)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Cheapest nonstop economy fare over time" preserveAspectRatio="xMidYMid meet">']

    for frac in (0, 0.25, 0.5, 0.75, 1):
        value = p_hi - frac * (p_hi - p_lo)
        y = y_of(value)
        parts.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{pad_l - 10}" y="{y + 4:.1f}" text-anchor="end">{rupees(value)}</text>')

    ticks = 4
    for i in range(ticks + 1):
        stamp = t_min + timedelta(seconds=span * i / ticks)
        x = x_of(stamp)
        label = display_date(stamp) if span > 48 * 3600 else display_datetime(stamp)
        parts.append(f'<text class="tick" x="{x:.1f}" y="{height - pad_b + 22}" text-anchor="middle">{label}</text>')

    for dep_date in EXPECTED_DATES:
        pts = series.get(dep_date, [])
        if not pts:
            continue
        colour = SERIES_COLOURS[dep_date]
        coords = " ".join(f"{x_of(t):.1f},{y_of(p):.1f}" for t, p in pts)
        parts.append(f'<polyline class="line" points="{coords}" style="stroke:{colour}"/>')
        last_t, last_p = pts[-1]
        parts.append(f'<circle cx="{x_of(last_t):.1f}" cy="{y_of(last_p):.1f}" r="4.5" style="fill:{colour}"/>')

    parts.append("</svg>")
    return "".join(parts)


def run_strip(observations, now: datetime) -> tuple[str, int, int]:
    by_bucket: dict[datetime, list[str]] = defaultdict(list)
    for stamp, row in observations:
        by_bucket[hour_bucket(stamp)].append(row["status"])

    if not by_bucket:
        return '<p class="empty">No runs recorded yet.</p>', 0, 0

    current = hour_bucket(now)
    first = min(by_bucket)
    first_day = first.replace(hour=0)
    last_day = current.replace(hour=0)
    start_day = max(first_day, last_day - timedelta(days=RUN_STRIP_DAYS - 1))
    rows, landed, failed = [], 0, 0
    day = start_day
    while day <= last_day:
        cells = []
        for hour in range(24):
            bucket = day.replace(hour=hour)
            statuses = by_bucket.get(bucket)
            label = f"{display_datetime(bucket)} IST"
            if bucket < first:
                state, title = "before", f"{label} - tracking not started"
            elif statuses and "ERROR" in statuses and "OK" in statuses:
                state, title, landed = "partial", f"{label} - partial ({statuses.count('OK')} ok)", landed + 1
            elif statuses and "ERROR" in statuses:
                state, title, landed, failed = "error", f"{label} - all rows ERROR", landed + 1, failed + 1
            elif statuses:
                state, title, landed = "ok", f"{label} - {len(statuses)} rows OK", landed + 1
            elif bucket >= current:
                state, title = "not-due", f"{label} - not due yet"
            else:
                state, title = "missed", f"{label} - no run recorded"
            cells.append(f'<span class="cell {state}" title="{html.escape(title)}"></span>')
        rows.append(f'<div class="health-row"><span class="health-date">{display_date(day)}</span>'
                    f'<div class="health-cells">{"".join(cells)}</div></div>')
        day += timedelta(days=1)
    return "".join(rows), landed, failed


def duration_text(delta: timedelta) -> str:
    hours = max(0, int(delta.total_seconds() // 3600))
    return f"{hours // 24} d" if hours >= 48 else f"{hours} h"


def build_cards(series, observations, now: datetime) -> str:
    latest_bucket = max((hour_bucket(t) for t, _ in observations), default=None)
    today = now.astimezone(IST).date()
    counts = unique_flight_counts(observations)
    cards = []
    for dep_date in EXPECTED_DATES:
        pts = series.get(dep_date, [])
        if not pts:
            cards.append(f'<article class="card"><h3>{display_date(dep_date)}</h3>'
                         f'<p class="flight-count">0 flights</p>'
                         f'<p class="price muted">no data</p></article>')
            continue

        stamp, current = pts[-1]
        window_min = min(p for _, p in pts)
        window_max = max(p for _, p in pts)

        day_ago = stamp - timedelta(hours=24)
        earlier = [p for t, p in pts if t <= day_ago]
        baseline = earlier[-1] if earlier else pts[0][1]
        delta = current - baseline
        if delta > 0:
            trend, arrow = "up", "&#9650;"
        elif delta < 0:
            trend, arrow = "down", "&#9660;"
        else:
            trend, arrow = "flat", "&#8212;"
        if delta == 0:
            changed_at = next((t for (t, p), (_, previous) in zip(reversed(pts), reversed(pts[:-1]))
                               if p != previous), pts[0][0])
            change = f"unchanged for {duration_text(stamp - changed_at)}"
            basis = ""
        else:
            change = f"{arrow} {rupees(abs(delta))}"
            basis = "vs 24h ago" if earlier else "since tracking began"

        low_stamp = next(t for t, p in pts if p == window_min)
        if current == window_min:
            best_seen = '<span class="best-badge">lowest so far</span>'
        else:
            best_seen = f'<span class="best-seen">lowest yet {rupees(window_min)} &middot; {duration_text(stamp - low_stamp)} ago</span>'
        days_out = (datetime.fromisoformat(dep_date).date() - today).days
        flight_count = counts[dep_date]
        flight_copy = f"{flight_count} {'flight' if flight_count == 1 else 'flights'}"

        stale = " stale" if latest_bucket and stamp < latest_bucket else ""
        cards.append(
            f'<article class="card{stale}">'
            f'<h3>{display_date(dep_date)}</h3>'
            f'<p class="flight-count">{flight_copy}</p>'
            f'<p class="days-out">{days_out} days out</p>'
            f'<p class="price">{rupees(current)}</p>'
            f'<p class="delta {trend}">{change} <span class="muted">{basis}</span></p>'
            f'<p class="best">{best_seen}</p>'
            f'<p class="range muted">low {rupees(window_min)} &middot; high {rupees(window_max)}</p>'
            f"</article>"
        )
    return "".join(cards)


def recent_table(observations, limit: int = 12) -> tuple[str, bool]:
    all_buckets = sorted({hour_bucket(t) for t, _ in observations})
    buckets = list(reversed(all_buckets[-limit:]))
    cheapest_by_bucket = {}
    for bucket in all_buckets:
        prices = [int(r["price"]) for t, r in observations
                  if hour_bucket(t) == bucket and r["status"] == "OK" and r["price"].isdigit()]
        cheapest_by_bucket[bucket] = min(prices, default=None)
    has_errors = any(r["status"] == "ERROR" for bucket in buckets
                     for t, r in observations if hour_bucket(t) == bucket)
    rows = []
    for bucket in buckets:
        batch = [r for t, r in observations if hour_bucket(t) == bucket]
        ok = [r for r in batch if r["status"] == "OK"]
        errors = [r for r in batch if r["status"] == "ERROR"]
        if errors and ok:
            badge = '<span class="pill partial">partial</span>'
        elif errors:
            badge = '<span class="pill bad">error</span>'
        else:
            badge = '<span class="pill good">ok</span>'

        cheapest = min((int(r["price"]) for r in ok if r["price"].isdigit()), default=None)
        best = next((r for r in ok if r["price"].isdigit() and int(r["price"]) == cheapest), None)
        flight = html.escape(best["flight_number"] or best["airline"]) if best else "&mdash;"
        index = all_buckets.index(bucket)
        previous = cheapest_by_bucket.get(all_buckets[index - 1]) if index else None
        if cheapest is None or previous is None or cheapest == previous:
            change = '<span class="delta flat">&mdash;</span>'
        elif cheapest > previous:
            change = f'<span class="delta up">&#9650; {rupees(cheapest - previous)}</span>'
        else:
            change = f'<span class="delta down">&#9660; {rupees(previous - cheapest)}</span>'
        note = f'<td class="muted">{html.escape(errors[0]["error"][:70]) if errors else ""}</td>' if has_errors else ""
        rows.append(
            f"<tr><td>{display_datetime(bucket)}</td><td>{badge}</td>"
            f"<td>{len(ok)}</td><td>{change}</td><td>{flight}</td>{note}</tr>"
        )
    colspan = 6 if has_errors else 5
    return ("".join(rows) or f'<tr><td colspan="{colspan}" class="muted">No runs recorded yet.</td></tr>',
            has_errors)


def daily_summary(series) -> str:
    daily = daily_series(series)
    days = sorted({stamp.date() for points in daily.values() for stamp, _ in points})[-15:]
    if len(days) < 2:
        return '<p class="empty">Daily comparison appears after the first full day of tracking.</p>'
    maps = {dep: {stamp.date(): price for stamp, price in daily.get(dep, [])} for dep in EXPECTED_DATES}
    rows = []
    for i, day in enumerate(days):
        cells = []
        for dep in EXPECTED_DATES:
            price = maps[dep].get(day)
            previous = maps[dep].get(days[i - 1]) if i else None
            if price is None:
                value = "&mdash;"
            elif previous is None or price == previous:
                value = f'{rupees(price)} <span class="delta flat">&mdash;</span>'
            elif price > previous:
                value = f'{rupees(price)} <span class="delta up">&#9650; {rupees(price - previous)}</span>'
            else:
                value = f'{rupees(price)} <span class="delta down">&#9660; {rupees(previous - price)}</span>'
            cells.append(f"<td>{value}</td>")
        rows.append(f'<tr><td>{display_date(day)}</td>{"".join(cells)}</tr>')
    heads = "".join(f"<th>{display_date(d)}</th>" for d in EXPECTED_DATES)
    return f'<div class="scroll"><table><thead><tr><th>IST date</th>{heads}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def render_html(observations, now: datetime, repo: str) -> str:
    series = cheapest_series(observations)
    daily = daily_series(series)
    strip, landed, failed = run_strip(observations, now)
    run_rows, has_run_errors = recent_table(observations)
    latest = max((t for t, _ in observations), default=None)
    age_min = (now - latest).total_seconds() / 60 if latest else 1e9

    if not observations:
        state, headline = "bad", "No data yet"
    elif age_min > DEAD_MIN:
        state, headline = "bad", f"Stalled &mdash; last run {age_min / 60:.1f} h ago"
    elif age_min > STALE_MIN:
        state, headline = "warn", f"Late &mdash; last run {age_min:.0f} min ago"
    else:
        state, headline = "good", f"Live &mdash; updated {age_min:.0f} min ago"

    ok_rows = sum(1 for _, r in observations if r["status"] == "OK")
    legend = "".join(
        f'<span class="key"><i style="background:{SERIES_COLOURS[d]}"></i>{display_date(d)}</span>'
        for d in EXPECTED_DATES
    )
    runs_copy = (f"{landed} {'run' if landed == 1 else 'runs'}, " +
                 (f"{failed} with errors." if failed else "all successful."))
    note_head = "<th>Note</th>" if has_run_errors else ""
    daily_days = {stamp.date() for points in daily.values() for stamp, _ in points}
    daily_chart = (svg_chart(daily) if len(daily_days) >= 2 else
                   '<p class="empty">Daily view appears after two days of data.</p>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="900">
<title>{ROUTE} fare tracker</title>
<style>
  :root {{
    --bg:#f6f7f9; --panel:#ffffff; --ink:#12151a; --muted:#5b6572; --line:#e3e7ec;
    --good:#0f8a4b; --warn:#b06f00; --bad:#c0392b; --accent:#2563eb;
    --s1:#2563eb; --s2:#d97706; --s3:#0f8a4b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg:#0f1216; --panel:#171b21; --ink:#e8ecf1; --muted:#98a2b0; --line:#262c35;
      --good:#3ddc84; --warn:#f0b429; --bad:#ff6b5e; --accent:#5b9dff;
      --s1:#5b9dff; --s2:#f0b429; --s3:#3ddc84;
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:28px 20px 60px; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  .wrap {{ max-width:980px; margin:0 auto; }}
  header {{ display:flex; flex-wrap:wrap; gap:12px; align-items:baseline; justify-content:space-between; }}
  h1 {{ font-size:22px; margin:0; letter-spacing:-.01em; }}
  h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
    margin:34px 0 12px; font-weight:600; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  .status {{ display:inline-flex; align-items:center; gap:8px; padding:7px 14px; border-radius:999px;
    font-weight:600; font-size:13px; border:1px solid var(--line); background:var(--panel); }}
  .status::before {{ content:""; width:9px; height:9px; border-radius:50%; background:currentColor; }}
  .status.good {{ color:var(--good); }} .status.warn {{ color:var(--warn); }} .status.bad {{ color:var(--bad); }}
  .cards {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); margin-top:18px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
  .card.stale {{ opacity:.6; }}
  .card h3 {{ margin:0 0 6px; font-size:13px; color:var(--muted); font-weight:600; }}
  .flight-count {{ margin:-4px 0 1px; color:var(--muted); font-size:12px; }}
  .days-out {{ margin:-4px 0 6px; color:var(--muted); font-size:12px; }}
  .price {{ margin:0; font-size:27px; font-weight:680; letter-spacing:-.02em; }}
  .delta {{ margin:6px 0 2px; font-size:13px; font-weight:600; }}
  .delta.up {{ color:var(--bad); }} .delta.down {{ color:var(--good); }} .delta.flat {{ color:var(--muted); }}
  .range {{ margin:0; font-size:12px; }}
  .best {{ margin:7px 0 3px; font-size:12px; color:var(--muted); }}
  .best-badge {{ display:inline-block; padding:2px 8px; border-radius:999px; color:var(--good);
    border:1px solid var(--good); font-weight:600; }}
  .muted {{ color:var(--muted); font-weight:400; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px; }}
  svg {{ width:100%; height:auto; display:block; }}
  .grid {{ stroke:var(--line); stroke-width:1; }}
  .tick {{ fill:var(--muted); font-size:11px; }}
  .line {{ fill:none; stroke-width:2.2; stroke-linejoin:round; stroke-linecap:round; }}
  .legend {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:12px; font-size:12px; color:var(--muted); }}
  .key {{ display:inline-flex; align-items:center; gap:7px; }}
  .key i {{ width:14px; height:3px; border-radius:2px; display:inline-block; }}
  .chartbox {{ position:relative; }}
  .chartbox input {{ position:absolute; opacity:0; }}
  .chartbox label {{ display:inline-block; cursor:pointer; padding:5px 12px; margin:0 5px 12px 0;
    border:1px solid var(--line); border-radius:7px; color:var(--muted); font-size:12px; font-weight:600; }}
  #view-hourly:checked + label, #view-daily:checked + label {{ color:var(--accent); border-color:var(--accent); }}
  .chartbox input:focus-visible + label {{ outline:2px solid var(--accent); outline-offset:2px; }}
  .chart-daily {{ display:none; }}
  #view-daily:checked ~ .chart-daily {{ display:block; }}
  #view-daily:checked ~ .chart-hourly {{ display:none; }}
  .chart-caption {{ margin:8px 0 0; color:var(--muted); font-size:12px; }}
  .health-row {{ display:grid; grid-template-columns:52px minmax(288px,1fr); gap:9px; align-items:center; margin:4px 0; }}
  .health-date {{ color:var(--muted); font-size:11px; }}
  .health-cells {{ display:grid; grid-template-columns:repeat(24,minmax(7px,1fr)); gap:3px; }}
  .cell {{ height:18px; border-radius:3px; background:var(--line); }}
  .cell.ok {{ background:var(--good); }} .cell.partial {{ background:var(--warn); }}
  .cell.error {{ background:var(--bad); }} .cell.not-due {{ background:transparent; border:1px dashed var(--line); }}
  .cell.before {{ visibility:hidden; }}
  .health-legend {{ display:flex; gap:14px; flex-wrap:wrap; margin:12px 0 0; color:var(--muted); font-size:11px; }}
  .health-legend .cell {{ display:inline-block; width:11px; height:11px; margin-right:5px; vertical-align:-1px; }}
  .scroll {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; min-width:620px; }}
  th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.06em;
    color:var(--muted); padding:0 10px 8px 0; }}
  td {{ padding:9px 10px 9px 0; border-top:1px solid var(--line); }}
  .pill {{ font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600; }}
  .pill.good {{ background:color-mix(in srgb,var(--good) 16%,transparent); color:var(--good); }}
  .pill.partial {{ background:color-mix(in srgb,var(--warn) 18%,transparent); color:var(--warn); }}
  .pill.bad {{ background:color-mix(in srgb,var(--bad) 16%,transparent); color:var(--bad); }}
  .empty {{ color:var(--muted); margin:0; padding:26px 0; text-align:center; }}
  footer {{ margin-top:34px; font-size:12px; color:var(--muted); }}
  a {{ color:var(--accent); }}
  @media (max-width:480px) {{
    body {{ padding:20px 12px 40px; }}
    .panel {{ padding:13px; }}
    .health-row {{ grid-template-columns:45px minmax(288px,1fr); }}
    .health-grid {{ overflow-x:auto; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>{ROUTE} &middot; nonstop economy</h1>
      <p class="sub">Cheapest displayed fares for {display_date(EXPECTED_DATES[0])}&ndash;{display_date(EXPECTED_DATES[-1])}, sampled hourly by GitHub Actions.</p>
    </div>
    <span class="status {state}">{headline}</span>
  </header>

  <h2>Cheapest fare by departure date</h2>
  <div class="cards">{build_cards(series, observations, now)}</div>

  <h2>Price movement</h2>
  <div class="panel chartbox">
    <input type="radio" name="chartview" id="view-hourly" checked>
    <label for="view-hourly">Hourly</label>
    <input type="radio" name="chartview" id="view-daily">
    <label for="view-daily">Daily</label>
    <div class="chart chart-hourly">{svg_chart(series)}</div>
    <div class="chart chart-daily">{daily_chart}
      <p class="chart-caption">Daily view plots each day's lowest fare.</p>
    </div>
    <div class="legend">{legend}</div>
  </div>

  <h2>Daily summary</h2>
  <div class="panel">{daily_summary(series)}</div>

  <h2>Collection health</h2>
  <div class="panel">
    <div class="health-grid">{strip}</div>
    <div class="health-legend">
      <span><i class="cell ok"></i>ok</span><span><i class="cell partial"></i>partial</span>
      <span><i class="cell error"></i>error</span><span><i class="cell missed"></i>missed</span>
      <span><i class="cell not-due"></i>not due</span>
    </div>
    <p class="sub" style="margin:14px 0 0">
      {runs_copy} Hover a block for its timestamp.
      Gaps are normal: GitHub delays scheduled jobs by 5&ndash;20 minutes and occasionally skips one.
    </p>
  </div>

  <h2>Recent runs</h2>
  <div class="panel scroll">
    <table>
      <thead><tr><th>Run (IST)</th><th>Status</th><th>Rows</th><th>Change</th><th>Flight</th>{note_head}</tr></thead>
      <tbody>{run_rows}</tbody>
    </table>
  </div>

  <footer>
    Generated {display_datetime(now)} UTC / {now.astimezone(IST):%H:%M} IST &middot;
    {ok_rows} fare checks &middot;
    <a href="https://github.com/{repo}">source</a> &middot;
    <a href="https://github.com/{repo}/blob/main/data/flight_prices.csv">raw CSV</a>.
    Page refreshes every 15 minutes; fares are what Google Flights displayed, not a booking quote.
  </footer>
</div>
</body>
</html>
"""


def render_readme_block(observations, now: datetime, repo: str) -> str:
    series = cheapest_series(observations)
    latest = max((t for t, _ in observations), default=None)
    age_min = (now - latest).total_seconds() / 60 if latest else None
    if age_min is None:
        badge = "no data"
    elif age_min > DEAD_MIN:
        badge = f"stalled ({age_min / 60:.1f} h since last run)"
    elif age_min > STALE_MIN:
        badge = f"late ({age_min:.0f} min since last run)"
    else:
        badge = f"live ({age_min:.0f} min since last run)"

    lines = [
        README_START,
        "",
        f"### {ROUTE} &middot; nonstop economy &middot; status: **{badge}**",
        "",
        f"[**Open the dashboard**](https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/) "
        f"&middot; hourly samples of the cheapest displayed fare for {display_date(EXPECTED_DATES[0])}&ndash;{display_date(EXPECTED_DATES[-1])}.",
        "",
        "| Departure | Cheapest now | 24h change | Low | High |",
        "|---|---|---|---|---|",
    ]
    for dep_date in EXPECTED_DATES:
        pts = series.get(dep_date, [])
        if not pts:
            lines.append(f"| {display_date(dep_date)} | – | – | – | – |")
            continue
        stamp, current = pts[-1]
        earlier = [p for t, p in pts if t <= stamp - timedelta(hours=24)]
        baseline = earlier[-1] if earlier else pts[0][1]
        delta = current - baseline
        change = "no change" if delta == 0 else f"{'▲' if delta > 0 else '▼'} {rupees(abs(delta))}"
        lines += [f"| {display_date(dep_date)} | **{rupees(current)}** | {change} | "
                  f"{rupees(min(p for _, p in pts))} | {rupees(max(p for _, p in pts))} |"]

    lines += [
        "",
        f"_Updated {display_datetime(now)} UTC by the hourly workflow. "
        "Figures are displayed fares, not booking quotes._",
        "",
        README_END,
    ]
    return "\n".join(lines)


def write_readme(block: str) -> None:
    if README_OUT.exists():
        current = README_OUT.read_text(encoding="utf-8")
        if README_START in current and README_END in current:
            head, _, rest = current.partition(README_START)
            _, _, tail = rest.partition(README_END)
            README_OUT.write_text(head + block + tail, encoding="utf-8")
            return
        README_OUT.write_text(current.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
        return

    README_OUT.write_text(
        f"# flight-tracker-maa-dxb\n\n"
        f"Hourly Google Flights fare tracker for {ROUTE}, run entirely on GitHub Actions.\n\n"
        f"{block}\n\n"
        "## How it works\n\n"
        "- `.github/workflows/flight-tracker.yml` runs hourly at minute 17 UTC and self-disables after 2026-08-28.\n"
        "- `src/scrape_google_flights.py` reads the cheapest displayed nonstop economy itineraries via Playwright.\n"
        "- `tools/audit.py` grades data quality; `tools/build_dashboard.py` renders this summary and `docs/index.html`.\n"
        "- Every observation is appended to `data/flight_prices.csv`; nothing is overwritten.\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DATA_FILE)
    parser.add_argument("--repo", default="subuuuus/flight-tracker-maa-dxb")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    observations = load(args.csv) if args.csv.exists() else []

    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(render_html(observations, now, args.repo), encoding="utf-8")
    write_readme(render_readme_block(observations, now, args.repo))
    print(f"Wrote {HTML_OUT.relative_to(PROJECT_DIR)} and README.md from {len(observations)} observation(s)")


if __name__ == "__main__":
    main()
