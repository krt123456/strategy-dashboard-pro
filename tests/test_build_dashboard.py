from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import build_dashboard as dashboard
import build_telegram_portfolio as portfolio


class SafeDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = self.root / "journal.db"
        self.linefeed = self.root / "missing-linefeed.csv"
        self.lock = self.root / "forecast_lock_2026-08-20.csv"
        self.report = self.root / "publication_backtest.json"
        connection = sqlite3.connect(self.db)
        connection.execute(
            """
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                match_date TEXT NOT NULL,
                sport TEXT NOT NULL,
                league TEXT,
                home TEXT NOT NULL,
                away TEXT NOT NULL,
                pick TEXT NOT NULL,
                source TEXT NOT NULL,
                model_prob REAL,
                odds_at_prediction REAL,
                strategy TEXT,
                start_utc TEXT
            )
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def insert_prediction(
        self,
        name: str,
        odds: float,
        *,
        start: str = "2026-08-20T12:00:00Z",
        created: str = "2026-08-20T06:00:00Z",
        strategy: str = portfolio.LEGACY_STRATEGY,
        source: str = portfolio.DB_STORED_SOURCE,
    ) -> int:
        connection = sqlite3.connect(self.db)
        cursor = connection.execute(
            """
            INSERT INTO predictions (
                created_at, match_date, sport, league, home, away, pick, source,
                model_prob, odds_at_prediction, strategy, start_utc
            ) VALUES (?, '2026-08-20', 'tennis', 'Test League', ?, ?, ?, ?, 0.35, ?, ?, ?)
            """,
            (created, f"{name} Home", f"{name} Away", f"{name} Away", source, odds, strategy, start),
        )
        connection.commit()
        connection.close()
        return int(cursor.lastrowid)

    def write_backtest(self) -> None:
        payload = {
            "report_type": "publication_backtest_audited_summary",
            "decision": {
                "strategy": portfolio.LEGACY_STRATEGY,
                "strategy_status": "legacy_active",
                "strategy_promoted": False,
            },
            "methodology": {"automatic_cutoff": "2026-08-03"},
            "snapshot": {"match_date_min": "2026-06-18"},
            "legacy_strategy": {
                "complete_cutoff_metrics": {
                    "bets": 4281,
                    "wins": 1559,
                    "losses": 2722,
                    "win_rate_pct": 36.42,
                    "average_odds": 3.49,
                    "pnl_units": 939.57,
                    "roi_pct": 21.95,
                }
            },
        }
        self.report.write_text(json.dumps(payload), encoding="utf-8")

    def gather(self) -> dict:
        return dashboard.gather(
            "2026-08-20",
            now_utc=datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
            db_path=self.db,
            linefeed_path=self.linefeed,
            lock_path=self.lock,
            backtest_path=self.report,
        )

    def write_lock(self, rows: list[dict[str, str]]) -> None:
        with self.lock.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=portfolio.LOCK_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def lock_row(
        prediction_id: int,
        rank: int,
        name: str,
        odds: str,
        start: str,
    ) -> dict[str, str]:
        return {
            "ForecastDate": "2026-08-20",
            "LockedAt": "2026-08-20T01:00:00Z",
            "PredictionId": str(prediction_id),
            "Rank": str(rank),
            "Sport": "tennis",
            "League": "Official League",
            "Home": f"{name} Home",
            "Away": f"{name} Away",
            "Pick": f"{name} Away",
            "Prob": "0.35",
            "OddsAtPrediction": odds,
            "OddsCapturedAt": "2026-08-20T01:00:00Z",
            "EventId": f"event-{prediction_id}",
            "StartUtc": start,
            "StartTimeLocal": start,
            "Source": portfolio.XBET_SOURCE,
            "StrategyGate": portfolio.LEGACY_STRATEGY,
            "OfficialEntry": "yes",
            "FinalDecision": "APPROVED_FOR_PUBLICATION",
            "ForecastPurpose": "HYPOTHETICAL_PAPER_SIMULATION",
        }

    def test_missing_lock_uses_only_legacy_lowest_five_with_sixty_minute_lead(self) -> None:
        for index, odds in enumerate([4.0, 2.5, 3.1, 5.5, 2.9, 3.8], start=1):
            self.insert_prediction(f"Valid {index}", odds, start=f"2026-08-20T{10 + index:02d}:00:00Z")
        self.insert_prediction("Wrong strategy", 2.5, strategy="unapproved__xbet_linefeed")
        self.insert_prediction("Wrong source", 2.5, source="synthetic")
        self.insert_prediction("Too close", 2.5, start="2026-08-20T05:59:00Z")
        self.write_backtest()

        data = self.gather()

        self.assertEqual(data["selection_source"], "dynamic_legacy_policy")
        self.assertEqual([pick["odds"] for pick in data["picks"]], [2.5, 2.9, 3.1, 3.8, 4.0])
        self.assertTrue(all(pick["strategy"] == portfolio.LEGACY_STRATEGY for pick in data["picks"]))
        self.assertTrue(all(pick["approved"] for pick in data["picks"]))
        self.assertEqual(data["best"]["home"], data["picks"][0]["home"])
        self.assertEqual(len(data["strategies"]), 1)
        self.assertEqual(data["headline"]["bets"], 4281)
        self.assertTrue(data["strategies"][0]["backtest_no_guarantee"])

    def test_valid_official_lock_overrides_database_and_hides_started_fixture(self) -> None:
        dynamic_id = self.insert_prediction("Dynamic", 2.5)
        started_id = dynamic_id + 100
        future_id = dynamic_id + 101
        self.write_lock(
            [
                self.lock_row(started_id, 1, "Started", "2.6", "2026-08-20T04:00:00Z"),
                self.lock_row(future_id, 2, "Locked", "4.125", "2026-08-20T09:00:00Z"),
            ]
        )

        data = self.gather()

        self.assertEqual(data["selection_source"], "official_lock")
        self.assertEqual(len(data["picks"]), 1)
        self.assertEqual(data["picks"][0]["home"], "Locked Home")
        self.assertEqual(data["picks"][0]["odds"], 4.125)
        self.assertTrue(data["picks"][0]["official_lock"])
        self.assertEqual(data["best"]["home"], "Locked Home")

    def test_present_invalid_lock_fails_closed_instead_of_using_database(self) -> None:
        prediction_id = self.insert_prediction("Dynamic", 2.5)
        row = self.lock_row(prediction_id, 1, "Tampered", "2.6", "2026-08-20T09:00:00Z")
        row["FinalDecision"] = "DRAFT"
        self.write_lock([row])

        data = self.gather()

        self.assertEqual(data["selection_source"], "invalid_lock_fail_closed")
        self.assertEqual(data["picks"], [])
        self.assertIsNone(data["best"])

    def test_header_only_official_lock_is_valid_and_blocks_dynamic_fallback(self) -> None:
        self.insert_prediction("Dynamic", 2.5)
        self.write_lock([])

        data = self.gather()

        self.assertEqual(data["selection_source"], "official_lock")
        self.assertEqual(data["picks"], [])
        self.assertIsNone(data["best"])

    def test_missing_or_untrusted_backtest_hides_strategy_performance(self) -> None:
        self.insert_prediction("Dynamic", 2.5)
        self.report.write_text('{"report_type":"not-audited"}', encoding="utf-8")

        data = self.gather()

        self.assertEqual(data["strategies"], [])
        self.assertEqual(data["headline"]["strategies"], 0)
        self.assertEqual(data["headline"]["bets"], 0)
        self.assertEqual(data["performance_basis"], "unavailable")


if __name__ == "__main__":
    unittest.main()
