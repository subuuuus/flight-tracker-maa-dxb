import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "scrape_google_flights.py"
SPEC = importlib.util.spec_from_file_location("tracker", SCRIPT)
tracker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tracker)


class FlightNumberParsingTests(unittest.TestCase):
    def test_extracts_indigo_flight_number_from_expanded_details(self):
        text = (
            "IndiGo\nEconomy\nAirbus A320neo\n6E 1471\n"
            "Below average legroom (28 in)"
        )
        self.assertEqual(tracker.extract_flight_number(text), "6E 1471")

    def test_extracts_emirates_flight_number_from_expanded_details(self):
        text = "Emirates\nEconomy\nBoeing 777\nEK 543\nWi-Fi for a fee"
        self.assertEqual(tracker.extract_flight_number(text), "EK 543")

    def test_extracts_flight_number_from_concatenated_google_text(self):
        text = "IndiGoEconomyAirbus A320neo6E\u00a01471Below average legroom"
        self.assertEqual(tracker.extract_flight_number(text), "6E 1471")

    def test_extracts_emirates_number_from_concatenated_google_text(self):
        text = "EmiratesEconomyBoeing 777EK\u00a0543Above average legroom"
        self.assertEqual(tracker.extract_flight_number(text, "Emirates"), "EK 543")

    def test_returns_blank_when_number_is_not_exposed(self):
        text = "Emirates\nEconomy\nBoeing 777\nAbove average legroom"
        self.assertEqual(tracker.extract_flight_number(text), "")

    def test_flight_number_becomes_unique_flight_key(self):
        flight = {"flight_key": "IndiGo|6:20 AM|8:55 AM|nonstop|Economy", "flight_number": ""}
        tracker.apply_flight_number(flight, "6E 1471")
        self.assertEqual(flight["flight_number"], "6E 1471")
        self.assertEqual(flight["flight_key"], "6E 1471")

    def test_existing_key_remains_as_fallback(self):
        flight = {"flight_key": "IndiGo|6:20 AM|8:55 AM|nonstop|Economy", "flight_number": ""}
        tracker.apply_flight_number(flight, "")
        self.assertEqual(flight["flight_key"], "IndiGo|6:20 AM|8:55 AM|nonstop|Economy")

    def test_append_rows_migrates_legacy_csv_header(self):
        import csv
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tracker.DATA_FILE = Path(directory) / "flight_prices.csv"
            legacy_fields = [field for field in tracker.CSV_FIELDS if field != "flight_number"]
            legacy = {field: "" for field in legacy_fields}
            legacy.update({"timestamp_iso": "2026-08-13T00:00:00+00:00", "status": "OK", "flight_key": "legacy-key"})
            with tracker.DATA_FILE.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=legacy_fields)
                writer.writeheader()
                writer.writerow(legacy)

            new_row = {field: "" for field in tracker.CSV_FIELDS}
            new_row.update({"timestamp_iso": "2026-08-13T01:00:00+00:00", "status": "OK", "flight_key": "6E 1471", "flight_number": "6E 1471"})
            tracker.append_rows([new_row])

            with tracker.DATA_FILE.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["flight_key"], "legacy-key")
            self.assertEqual(rows[0]["flight_number"], "")
            self.assertEqual(rows[1]["flight_number"], "6E 1471")


if __name__ == "__main__":
    unittest.main()
