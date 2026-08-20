from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cross_source_strategy_runner import _is_actionable_fixture, _parse_start_utc


class ActionableFixtureTests(unittest.TestCase):
    now = datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc)

    def test_parse_start_utc_normalizes_z_timestamp(self) -> None:
        self.assertEqual(
            _parse_start_utc("2026-08-20T06:00:00Z"),
            datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc),
        )

    def test_missing_or_invalid_start_is_not_actionable(self) -> None:
        self.assertFalse(_is_actionable_fixture({}, self.now, 15))
        self.assertFalse(
            _is_actionable_fixture({"start_utc": "not-a-date"}, self.now, 15)
        )

    def test_fixture_must_clear_lead_time(self) -> None:
        self.assertFalse(
            _is_actionable_fixture(
                {"start_utc": "2026-08-20T05:14:59Z"}, self.now, 15
            )
        )
        self.assertTrue(
            _is_actionable_fixture(
                {"start_utc": "2026-08-20T05:15:00Z"}, self.now, 15
            )
        )

    def test_offset_timestamp_is_compared_in_utc(self) -> None:
        self.assertTrue(
            _is_actionable_fixture(
                {"start_utc": "2026-08-20T07:15:00+02:00"}, self.now, 15
            )
        )


if __name__ == "__main__":
    unittest.main()
