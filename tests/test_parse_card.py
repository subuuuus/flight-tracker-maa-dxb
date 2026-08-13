"""Fixture-based coverage for Google Flights card parsing."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures")
SPEC = importlib.util.spec_from_file_location(
    "scrape_google_flights", ROOT / "src" / "scrape_google_flights.py"
)
scraper = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scraper)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode("utf-8")


class ParseCardTests(unittest.TestCase):
    def test_normal_single_price_cards_return_all_recorded_fields(self):
        cases = (
            (
                "real_indigo_nonstop_economy.txt",
                {
                    "price": 40334,
                    "airline": "IndiGo",
                    "departure_time": "6:20 AM",
                    "arrival_time": "8:55 AM",
                    "stops": "Nonstop",
                    "cabin": "Economy",
                },
            ),
            (
                "real_emirates_nonstop_economy.txt",
                {
                    "price": 75542,
                    "airline": "Emirates",
                    "departure_time": "4:05 AM",
                    "arrival_time": "6:40 AM",
                    "stops": "Nonstop",
                    "cabin": "Economy",
                },
            ),
        )
        for filename, expected in cases:
            with self.subTest(fixture=filename):
                parsed = scraper.parse_card(fixture(filename))
                self.assertIsNotNone(parsed)
                assert parsed is not None
                for field, value in expected.items():
                    self.assertEqual(value, parsed[field])

    def test_two_price_card_uses_one_way_fare_not_round_trip_note(self):
        parsed = scraper.parse_card(fixture("synthetic_two_price_round_trip_note.txt"))

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(40334, parsed["price"])

    def test_business_or_first_class_card_returns_none(self):
        parsed = scraper.parse_card(fixture("real_emirates_business_class.txt"))

        self.assertIsNone(parsed)

    def test_missing_price_or_second_time_returns_none(self):
        for filename in ("synthetic_no_price.txt", "synthetic_one_time.txt"):
            with self.subTest(fixture=filename):
                self.assertIsNone(scraper.parse_card(fixture(filename)))

    def test_different_route_returns_none(self):
        parsed = scraper.parse_card(fixture("synthetic_different_route.txt"))

        self.assertIsNone(parsed)

    def test_round_trip_only_real_card_is_not_recorded_as_one_way_fare(self):
        parsed = scraper.parse_card(fixture("real_emirates_round_trip_price_note.txt"))

        self.assertIsNone(parsed)


if __name__ == "__main__":
    unittest.main()
