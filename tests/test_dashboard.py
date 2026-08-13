"""Regression tests for dashboard audit findings."""

from __future__ import annotations

import importlib.util
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dashboard", ROOT / "tools" / "build_dashboard.py")
dashboard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(dashboard)
UTC = timezone.utc


def observation(
    stamp: datetime,
    departure_date: str,
    price: int = 100,
    *,
    status: str = "OK",
    flight_number: str = "AA 101",
    airline: str = "Example Air",
    departure_time: str = "6:20 AM",
    arrival_time: str = "8:55 AM",
    error: str = "",
):
    return stamp, {
        "timestamp_iso": stamp.isoformat(),
        "source": "synthetic",
        "origin": "MAA",
        "destination": "DXB",
        "departure_date": departure_date,
        "price": str(price) if status == "OK" else "",
        "currency": "INR",
        "status": status,
        "flight_key": "",
        "flight_number": flight_number,
        "airline": airline,
        "departure_time": departure_time,
        "arrival_time": arrival_time,
        "stops": "Nonstop",
        "cabin": "Economy",
        "error": error,
    }


class DashboardAuditRegressionTests(unittest.TestCase):
    def test_partial_run_is_counted_as_run_with_errors(self):
        now = datetime(2026, 8, 13, 12, 30, tzinfo=UTC)
        observations = [
            observation(datetime(2026, 8, 13, 12, 1, tzinfo=UTC), "2026-09-01"),
            observation(
                datetime(2026, 8, 13, 12, 2, tzinfo=UTC),
                "2026-09-02",
                status="ERROR",
                error="synthetic failure",
            ),
        ]

        rendered = dashboard.render_html(observations, now, "example/repo")

        self.assertIn("1 run, 1 with errors.", rendered)
        self.assertNotIn("all successful", rendered)

    def test_lowest_seen_age_is_measured_from_generation_time(self):
        low_stamp = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
        latest_stamp = low_stamp + timedelta(hours=2)
        now = low_stamp + timedelta(hours=12)
        observations = [
            observation(low_stamp, "2026-09-01", 90),
            observation(latest_stamp, "2026-09-01", 100),
        ]
        series = dashboard.cheapest_series(observations)

        rendered = dashboard.build_cards(series, observations, now)

        self.assertIn("lowest yet ₹90 &middot; 12 h ago", rendered)
        self.assertNotIn("lowest yet ₹90 &middot; 2 h ago", rendered)

    def test_daily_summary_includes_missing_ist_days(self):
        observations = [
            observation(datetime(2026, 8, 13, 6, 0, tzinfo=UTC), "2026-09-01", 100),
            observation(datetime(2026, 8, 15, 6, 0, tzinfo=UTC), "2026-09-01", 90),
        ]
        series = dashboard.cheapest_series(observations)

        rendered = dashboard.daily_summary(series)

        self.assertIn("13-Aug-26", rendered)
        self.assertRegex(rendered, r"<tr><td>14-Aug-26</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td></tr>")
        self.assertIn("15-Aug-26", rendered)

    def test_hourly_chart_requires_two_distinct_runs(self):
        stamp = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        series = {
            dep: [(stamp, price)]
            for dep, price in zip(dashboard.EXPECTED_DATES, (100, 110, 120))
        }

        rendered = dashboard.svg_chart(series)

        self.assertIn("two hourly runs", rendered)
        self.assertNotIn("<svg", rendered)

    def test_flight_reconciliation_is_scoped_to_departure_date(self):
        stamp = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        first = "2026-09-01"
        second = "2026-09-02"
        observations = [
            observation(stamp, first, flight_number=""),
            observation(stamp + timedelta(hours=1), first, flight_number="AA 101"),
            observation(stamp, second, flight_number=""),
            observation(stamp + timedelta(hours=1), second, flight_number="AA 102"),
        ]

        counts = dashboard.unique_flight_counts(observations)

        self.assertEqual(1, counts[first])
        self.assertEqual(1, counts[second])

    def test_health_dates_have_non_wrapping_wide_css_column(self):
        rendered = dashboard.render_html([], datetime(2026, 8, 13, 12, 0, tzinfo=UTC), "example/repo")
        columns = re.findall(r"\.health-row\s*\{[^}]*grid-template-columns:\s*(\d+)px", rendered)

        self.assertTrue(columns, "health-grid column width declarations were not found")
        self.assertGreaterEqual(min(map(int, columns)), 70, 'date column must fit "13-Aug-26"')
        self.assertRegex(rendered, r"\.health-date\s*\{[^}]*white-space:\s*nowrap")


if __name__ == "__main__":
    unittest.main()
