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
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_DIR / "data" / "flight_prices.csv"
HTML_OUT = PROJECT_DIR / "docs" / "index.html"
README_OUT = PROJECT_DIR / "README.md"

ROUTE = "MAA -> DXB"
EXPECTED_DATES = ("2026-09-01", "2026-09-02", "2026-09-03")
SERIES_COLOURS = {
    "2026-09-01": "var(--s1)",
    "2026-09-02": "var(--s2)",
    "2026-09-03": "var(--s3)",
}
STALE_MIN, DEAD_MIN = 90, 180
RUN_STRIP_HOURS = 72
IST = timedelta(hours=5, minutes=30)

README_START = "<!-- DASHBOARD:START -->"
README_END = "<!-- DASHBOARD:END -->"


def parse_ts(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def hour_bucket(stamp: datetime) -> datetime:
    return stamp.replace(minute=0, second=0, microsecond=0)


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


def svg_chart(series: dict[str, list[tuple[datetime, int]]], width: int = 920, height: int = 340) -> str:
    points = [(t, p) for pts in series.values() for t, p in pts]
    if len(points) < 2:
        return ('<p class="empty">Not enough observations yet to plot a trend. '
                'The chart appears once two hourly runs have completed.</p>')

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
        label = (stamp + IST).strftime("%d %b %H:%M")
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

    cells, landed, failed = [], 0, 0
    start = hour_bucket(now) - timedelta(hours=RUN_STRIP_HOURS - 1)
    for i in range(RUN_STRIP_HOURS):
        bucket = start + timedelta(hours=i)
        statuses = by_bucket.get(bucket)
        label = (bucket + IST).strftime("%d %b %H:%M IST")
        if not statuses:
            state, title = ("future", f"{label} - not due yet") if bucket > hour_bucket(now) \
                else ("missing", f"{label} - no run recorded")
        elif "ERROR" in statuses and "OK" in statuses:
            state, title, landed = "partial", f"{label} - partial ({statuses.count('OK')} ok)", landed + 1
        elif "ERROR" in statuses:
            state, title, landed, failed = "bad", f"{label} - all rows ERROR", landed + 1, failed + 1
        else:
            state, title, landed = "good", f"{label} - {len(statuses)} rows OK", landed + 1
        cells.append(f'<span class="cell {state}" title="{html.escape(title)}"></span>')
    return "".join(cells), landed, failed


def build_cards(series, observations) -> str:
    latest_bucket = max((hour_bucket(t) for t, _ in observations), default=None)
    cards = []
    for dep_date in EXPECTED_DATES:
        pts = series.get(dep_date, [])
        if not pts:
            cards.append(f'<article class="card"><h3>{dep_date}</h3>'
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
        change = "no change" if delta == 0 else f"{arrow} {rupees(abs(delta))}"
        basis = "vs 24h ago" if earlier else "since tracking began"

        stale = " stale" if latest_bucket and stamp < latest_bucket else ""
        cards.append(
            f'<article class="card{stale}">'
            f'<h3>{dep_date}</h3>'
            f'<p class="price">{rupees(current)}</p>'
            f'<p class="delta {trend}">{change} <span class="muted">{basis}</span></p>'
            f'<p class="range muted">low {rupees(window_min)} &middot; high {rupees(window_max)}</p>'
            f"</article>"
        )
    return "".join(cards)


def recent_table(observations, limit: int = 12) -> str:
    buckets = sorted({hour_bucket(t) for t, _ in observations}, reverse=True)[:limit]
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
        note = html.escape(errors[0]["error"][:70]) if errors else ""
        rows.append(
            f"<tr><td>{(bucket + IST):%d %b %H:%M}</td><td>{badge}</td>"
            f"<td>{len(ok)}</td><td>{rupees(cheapest) if cheapest else '&mdash;'}</td>"
            f'<td>{flight}</td><td class="muted">{note}</td></tr>'
        )
    return "".join(rows) or '<tr><td colspan="6" class="muted">No runs recorded yet.</td></tr>'


def render_html(observations, now: datetime, repo: str) -> str:
    series = cheapest_series(observations)
    strip, landed, failed = run_strip(observations, now)
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
        f'<span class="key"><i style="background:{SERIES_COLOURS[d]}"></i>{d}</span>'
        for d in EXPECTED_DATES
    )

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
  .price {{ margin:0; font-size:27px; font-weight:680; letter-spacing:-.02em; }}
  .delta {{ margin:6px 0 2px; font-size:13px; font-weight:600; }}
  .delta.up {{ color:var(--bad); }} .delta.down {{ color:var(--good); }} .delta.flat {{ color:var(--muted); }}
  .range {{ margin:0; font-size:12px; }}
  .muted {{ color:var(--muted); font-weight:400; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:18px; }}
  svg {{ width:100%; height:auto; display:block; }}
  .grid {{ stroke:var(--line); stroke-width:1; }}
  .tick {{ fill:var(--muted); font-size:11px; }}
  .line {{ fill:none; stroke-width:2.2; stroke-linejoin:round; stroke-linecap:round; }}
  .legend {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:12px; font-size:12px; color:var(--muted); }}
  .key {{ display:inline-flex; align-items:center; gap:7px; }}
  .key i {{ width:14px; height:3px; border-radius:2px; display:inline-block; }}
  .strip {{ display:flex; gap:3px; flex-wrap:wrap; }}
  .cell {{ width:12px; height:22px; border-radius:3px; background:var(--line); }}
  .cell.good {{ background:var(--good); }} .cell.partial {{ background:var(--warn); }}
  .cell.bad {{ background:var(--bad); }} .cell.future {{ background:transparent; border:1px dashed var(--line); }}
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
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>{ROUTE} &middot; nonstop economy</h1>
      <p class="sub">Cheapest displayed fares for 1&ndash;3 September 2026, sampled hourly by GitHub Actions.</p>
    </div>
    <span class="status {state}">{headline}</span>
  </header>

  <h2>Cheapest fare by departure date</h2>
  <div class="cards">{build_cards(series, observations)}</div>

  <h2>Price movement</h2>
  <div class="panel">
    {svg_chart(series)}
    <div class="legend">{legend}</div>
  </div>

  <h2>Hourly job health &middot; last {RUN_STRIP_HOURS} hours</h2>
  <div class="panel">
    <div class="strip">{strip}</div>
    <p class="sub" style="margin:14px 0 0">
      {landed} run(s) recorded, {failed} all-error. Hover a block for its timestamp.
      Gaps are normal: GitHub delays scheduled jobs by 5&ndash;20 minutes and occasionally skips one.
    </p>
  </div>

  <h2>Recent runs</h2>
  <div class="panel scroll">
    <table>
      <thead><tr><th>Run (IST)</th><th>Status</th><th>Rows</th><th>Cheapest</th><th>Flight</th><th>Note</th></tr></thead>
      <tbody>{recent_table(observations)}</tbody>
    </table>
  </div>

  <footer>
    Generated {now:%Y-%m-%d %H:%M} UTC / {(now + IST):%H:%M} IST &middot;
    {ok_rows} OK observations &middot;
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
        "&middot; hourly samples of the cheapest displayed fare for 1&ndash;3 Sep 2026.",
        "",
        "| Departure | Cheapest now | 24h change | Low | High |",
        "|---|---|---|---|---|",
    ]
    for dep_date in EXPECTED_DATES:
        pts = series.get(dep_date, [])
        if not pts:
            lines.append(f"| {dep_date} | – | – | – | – |")
            continue
        stamp, current = pts[-1]
        earlier = [p for t, p in pts if t <= stamp - timedelta(hours=24)]
        baseline = earlier[-1] if earlier else pts[0][1]
        delta = current - baseline
        change = "no change" if delta == 0 else f"{'▲' if delta > 0 else '▼'} {rupees(abs(delta))}"
        lines += [f"| {dep_date} | **{rupees(current)}** | {change} | "
                  f"{rupees(min(p for _, p in pts))} | {rupees(max(p for _, p in pts))} |"]

    lines += [
        "",
        f"_Updated {now:%Y-%m-%d %H:%M} UTC by the hourly workflow. "
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
