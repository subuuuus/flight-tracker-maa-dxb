# flight-tracker-maa-dxb

Hourly Google Flights fare tracker for MAA -> DXB, run entirely on GitHub Actions.

<!-- DASHBOARD:START -->

### MAA → DXB &middot; nonstop economy &middot; status: **live (0 min since last run)**

[**Open the dashboard**](https://subuuuus.github.io/flight-tracker-maa-dxb/) &middot; hourly samples of the cheapest displayed fare for 01-Sep-26&ndash;03-Sep-26.

| Departure | Cheapest now | 24h change | Low | High |
|---|---|---|---|---|
| 01-Sep-26 | **₹40,140** | no change | ₹37,095 | ₹47,212 |
| 02-Sep-26 | **₹43,447** | ▲ ₹6,352 | ₹37,095 | ₹43,447 |
| 03-Sep-26 | **₹37,095** | ▲ ₹2,783 | ₹34,312 | ₹40,140 |

_Updated 22-Aug-26 10:39 UTC by the hourly workflow. Figures are displayed fares, not booking quotes._

<!-- DASHBOARD:END -->

## How it works

- `.github/workflows/flight-tracker.yml` runs hourly at minute 17 UTC and self-disables after 2026-08-28.
- `src/scrape_google_flights.py` reads the cheapest displayed nonstop economy itineraries via Playwright.
- `tools/audit.py` grades data quality; `tools/build_dashboard.py` renders this summary and `docs/index.html`.
- Every observation is appended to `data/flight_prices.csv`; nothing is overwritten.
