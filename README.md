# flight-tracker-maa-dxb

Hourly Google Flights fare tracker for MAA -> DXB, run entirely on GitHub Actions.

<!-- DASHBOARD:START -->

### MAA → DXB &middot; nonstop economy &middot; status: **live (0 min since last run)**

[**Open the dashboard**](https://subuuuus.github.io/flight-tracker-maa-dxb/) &middot; hourly samples of the cheapest displayed fare for 01-Sep-26&ndash;03-Sep-26.

| Departure | Cheapest now | 24h change | Low | High |
|---|---|---|---|---|
| 01-Sep-26 | **₹40,334** | no change | ₹40,334 | ₹40,334 |
| 02-Sep-26 | **₹37,289** | no change | ₹37,289 | ₹37,289 |
| 03-Sep-26 | **₹34,507** | no change | ₹34,507 | ₹34,507 |

_Updated 14-Aug-26 16:05 UTC by the hourly workflow. Figures are displayed fares, not booking quotes._

<!-- DASHBOARD:END -->

## How it works

- `.github/workflows/flight-tracker.yml` runs hourly at minute 17 UTC and self-disables after 2026-08-28.
- `src/scrape_google_flights.py` reads the cheapest displayed nonstop economy itineraries via Playwright.
- `tools/audit.py` grades data quality; `tools/build_dashboard.py` renders this summary and `docs/index.html`.
- Every observation is appended to `data/flight_prices.csv`; nothing is overwritten.
