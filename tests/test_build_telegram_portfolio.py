from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import build_telegram_portfolio as portfolio
import telegram_publisher


class TelegramPortfolioBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = self.root / "journal.db"
        self.lock = self.root / "forecast_lock_2026-08-20.csv"
        self.results = self.root / "prediction_results_2026-08-20.csv"
        self.linefeed = self.root / "linefeed.csv"
        connection = sqlite3.connect(self.db)
        connection.executescript(
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
            );
            CREATE TABLE results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                home_score INTEGER,
                away_score INTEGER,
                pick_won INTEGER,
                outcome TEXT,
                result_source TEXT
            );
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def insert_prediction(
        self,
        *,
        home: str,
        away: str | None = None,
        pick: str | None = None,
        odds: object = 3.0,
        created: str = "2026-08-20T06:00:00+00:00",
        start: str = "2026-08-20T10:00:00Z",
        sport: str = "tennis",
        league: str = "Test League",
        source: str = portfolio.DB_STORED_SOURCE,
        strategy: str = portfolio.LEGACY_STRATEGY,
        probability: object = 0.3333,
        match_date: str = "2026-08-20",
    ) -> int:
        away = away or f"{home} Away"
        pick = pick or away
        connection = sqlite3.connect(self.db)
        cursor = connection.execute(
            """
            INSERT INTO predictions (
                created_at, match_date, sport, league, home, away, pick, source,
                model_prob, odds_at_prediction, strategy, start_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created, match_date, sport, league, home, away, pick, source, probability, odds, strategy, start),
        )
        prediction_id = int(cursor.lastrowid)
        connection.commit()
        connection.close()
        return prediction_id

    def insert_result(
        self,
        prediction_id: int,
        *,
        outcome: str,
        pick_won: object,
        home_score: object = 1,
        away_score: object = 2,
        checked: str = "2026-08-20T22:00:00+00:00",
        source: str = "betexplorer",
    ) -> None:
        connection = sqlite3.connect(self.db)
        connection.execute(
            """
            INSERT INTO results (
                prediction_id, checked_at, home_score, away_score, pick_won, outcome, result_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (prediction_id, checked, home_score, away_score, pick_won, outcome, source),
        )
        connection.commit()
        connection.close()

    def write_linefeed(self, rows: list[dict[str, str]]) -> None:
        fields = ["Date", "Sport", "Home", "Away", "EventId", "StartUtc", "Source"]
        with self.linefeed.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def read_rows(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def build_daily(self, **kwargs: object) -> portfolio.DailyBuildReport:
        return portfolio.build_daily_lock(
            self.db,
            date(2026, 8, 20),
            self.lock,
            linefeed_csv=kwargs.pop("linefeed_csv", self.linefeed),
            now_utc=kwargs.pop("now_utc", datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc)),
            **kwargs,
        )

    def test_daily_uses_first_prediction_then_lowest_five_and_event_ids(self) -> None:
        odds = [4.0, 2.5, 3.1, 5.5, 2.9, 3.8]
        prediction_ids: dict[str, int] = {}
        linefeed_rows: list[dict[str, str]] = []
        for index, price in enumerate(odds, start=1):
            home = f"Home {index}"
            away = f"Away {index}"
            start = f"2026-08-20T{10 + index:02d}:00:00Z"
            prediction_ids[home] = self.insert_prediction(
                home=home,
                away=away,
                pick=away,
                odds=price,
                start=start,
            )
            linefeed_rows.append(
                {
                    "Date": "2026-08-20",
                    "Sport": "tennis",
                    "Home": home,
                    "Away": away,
                    "EventId": f"evt-{index}",
                    "StartUtc": start,
                    "Source": "1XBET_PUBLIC_LINEFEED",
                }
            )
        later_duplicate = self.insert_prediction(
            home="Home 1",
            away="Away 1",
            pick="Away 1",
            odds=2.6,
            created="2026-08-20T07:00:00+00:00",
            start="2026-08-20T11:00:00Z",
        )
        self.write_linefeed(linefeed_rows)

        report = self.build_daily()
        rows = self.read_rows(self.lock)

        self.assertEqual(report.selected, 5)
        self.assertEqual(report.eligible_before_limit, 6)
        self.assertEqual(report.duplicates_removed, 1)
        self.assertEqual([row["OddsAtPrediction"] for row in rows], ["2.5", "2.9", "3.1", "3.8", "4"])
        first_row = next(row for row in rows if row["Home"] == "Home 1")
        self.assertEqual(first_row["PredictionId"], str(prediction_ids["Home 1"]))
        self.assertNotEqual(first_row["PredictionId"], str(later_duplicate))
        self.assertEqual(first_row["EventId"], "evt-1")
        self.assertEqual([row["Rank"] for row in rows], ["1", "2", "3", "4", "5"])
        self.assertTrue(all(row["OfficialEntry"] == "yes" for row in rows))

    def test_daily_filters_exact_strategy_source_date_odds_and_both_leads(self) -> None:
        valid = self.insert_prediction(
            home="Boundary",
            odds=2.5,
            created="2026-08-19T21:00:00Z",
            start="2026-08-19T22:30:00Z",  # 00:30 on Aug 20 in Berlin.
        )
        self.insert_prediction(home="Wrong source", source="synthetic_model", start="2026-08-20T12:00:00Z")
        self.insert_prediction(home="Wrong strategy", strategy="nova_fade_fav_v2__xbet_linefeed", start="2026-08-20T12:30:00Z")
        self.insert_prediction(home="Odds low", odds=2.49, start="2026-08-20T13:00:00Z")
        self.insert_prediction(home="Odds high", odds=5.51, start="2026-08-20T13:30:00Z")
        self.insert_prediction(home="Unsupported pick", pick="over 2.5", start="2026-08-20T14:00:00Z")
        self.insert_prediction(home="Wrong local day", start="2026-08-20T22:30:00Z")  # Aug 21 Berlin.
        self.insert_prediction(
            home="Late prediction",
            created="2026-08-20T11:15:00Z",
            start="2026-08-20T12:00:00Z",
        )
        report = self.build_daily(
            linefeed_csv=self.root / "does-not-exist.csv",
            now_utc=datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc),
        )
        rows = self.read_rows(self.lock)
        self.assertEqual(report.selected, 1)
        self.assertEqual(rows[0]["PredictionId"], str(valid))
        self.assertEqual(rows[0]["Source"], portfolio.XBET_SOURCE)
        self.assertEqual(rows[0]["EventId"], "")
        self.assertEqual(rows[0]["StartUtc"], "2026-08-19T22:30:00Z")

    def test_publication_time_lead_is_enforced_even_when_original_lead_was_safe(self) -> None:
        self.insert_prediction(
            home="Too close now",
            created="2026-08-20T04:00:00Z",
            start="2026-08-20T05:59:59Z",
        )
        report = self.build_daily(
            linefeed_csv=None,
            now_utc=datetime(2026, 8, 20, 5, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(report.selected, 0)

    def test_naive_legacy_created_at_is_localised_as_berlin(self) -> None:
        # 10:00 Berlin in August is 08:00 UTC, exactly 60 minutes before start.
        prediction_id = self.insert_prediction(
            home="DST",
            created="2026-08-20T10:00:00",
            start="2026-08-20T09:00:00Z",
            odds=3.0,
        )
        report = self.build_daily(
            linefeed_csv=None,
            now_utc=datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(report.selected, 1)
        self.assertEqual(self.read_rows(self.lock)[0]["PredictionId"], str(prediction_id))

    def test_ambiguous_legacy_time_fails_closed_instead_of_using_later_duplicate(self) -> None:
        self.insert_prediction(
            home="Ambiguous",
            created="2026-10-25T02:30:00",
            start="2026-10-25T04:00:00Z",
            match_date="2026-10-25",
        )
        self.insert_prediction(
            home="Ambiguous",
            created="2026-10-25T02:40:00+00:00",
            start="2026-10-25T04:00:00Z",
            match_date="2026-10-25",
        )
        report = portfolio.build_daily_lock(
            self.db,
            date(2026, 10, 25),
            self.root / "dst-lock.csv",
            linefeed_csv=None,
            now_utc=datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(report.selected, 0)

    def test_out_of_range_first_prediction_is_not_replaced_by_later_price(self) -> None:
        first_id = self.insert_prediction(home="First price", odds=2.4, start="2026-08-20T12:00:00Z")
        later_id = self.insert_prediction(
            home="First price",
            odds=3.0,
            created="2026-08-20T07:00:00Z",
            start="2026-08-20T12:00:00Z",
        )
        report = self.build_daily(linefeed_csv=None)
        self.assertEqual(report.selected, 0)
        self.assertNotEqual(first_id, later_id)

    def test_daily_lock_is_immutable_unless_force_is_explicit(self) -> None:
        self.insert_prediction(home="Original", odds=3.0)
        self.build_daily(linefeed_csv=None)
        original = self.lock.read_bytes()
        self.insert_prediction(home="New", odds=2.5, start="2026-08-20T11:00:00Z")
        with self.assertRaises(portfolio.PortfolioBuildError):
            self.build_daily(linefeed_csv=None)
        self.assertEqual(self.lock.read_bytes(), original)

        report = self.build_daily(linefeed_csv=None, force=True)
        self.assertEqual(report.selected, 2)
        self.assertNotEqual(self.lock.read_bytes(), original)

    def test_minimum_lead_and_pick_cap_are_hard_limits(self) -> None:
        self.insert_prediction(home="Limits")
        with self.assertRaises(portfolio.PortfolioBuildError):
            self.build_daily(linefeed_csv=None, min_lead_minutes=14)
        with self.assertRaises(portfolio.PortfolioBuildError):
            self.build_daily(linefeed_csv=None, max_picks=6)

    def test_conflicting_linefeed_ids_are_not_attached(self) -> None:
        self.insert_prediction(home="A", away="B", pick="B")
        row = {
            "Date": "2026-08-20",
            "Sport": "tennis",
            "Home": "A",
            "Away": "B",
            "StartUtc": "2026-08-20T10:00:00Z",
            "Source": "1XBET_PUBLIC_LINEFEED",
        }
        self.write_linefeed([{**row, "EventId": "one"}, {**row, "EventId": "two"}])
        self.build_daily()
        self.assertEqual(self.read_rows(self.lock)[0]["EventId"], "")

    def test_results_settle_only_complete_consistent_records(self) -> None:
        win_id = self.insert_prediction(home="Win", odds=2.5, start="2026-08-20T10:00:00Z")
        loss_id = self.insert_prediction(home="Loss", odds=3.0, start="2026-08-20T11:00:00Z")
        incomplete_id = self.insert_prediction(home="Incomplete", odds=3.5, start="2026-08-20T12:00:00Z")
        self.build_daily(linefeed_csv=None)
        self.insert_result(win_id, outcome="WON", pick_won=1, home_score=0, away_score=2)
        self.insert_result(loss_id, outcome="LOST", pick_won=0, home_score=2, away_score=0)
        self.insert_result(incomplete_id, outcome="WON", pick_won=1, home_score=None, away_score=None)

        report = portfolio.build_results_csv(self.db, date(2026, 8, 20), self.lock, self.results)
        rows = {row["Home"]: row for row in self.read_rows(self.results)}
        self.assertEqual(report.total, 3)
        self.assertEqual(report.finished, 2)
        self.assertEqual(report.pending, 1)
        self.assertEqual(rows["Win"]["PickOutcome"], "CORRECT")
        self.assertEqual(rows["Win"]["ResultStatus"], "FINISHED")
        self.assertEqual(rows["Loss"]["PickOutcome"], "WRONG")
        self.assertEqual(rows["Incomplete"]["PickOutcome"], "PENDING")
        self.assertIn("no complete final", rows["Incomplete"]["SettlementNote"])

    def test_results_reject_conflicts_and_lock_tampering(self) -> None:
        conflict_id = self.insert_prediction(home="Conflict", odds=3.0, start="2026-08-20T10:00:00Z")
        tampered_id = self.insert_prediction(home="Tampered", odds=3.5, start="2026-08-20T11:00:00Z")
        self.build_daily(linefeed_csv=None)
        self.insert_result(conflict_id, outcome="WON", pick_won=1, home_score=0, away_score=2)
        self.insert_result(conflict_id, outcome="LOST", pick_won=0, home_score=2, away_score=0)
        self.insert_result(tampered_id, outcome="WON", pick_won=1, home_score=0, away_score=2)

        rows = self.read_rows(self.lock)
        for row in rows:
            if row["PredictionId"] == str(tampered_id):
                row["OddsAtPrediction"] = "4.5"
        with self.lock.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=portfolio.LOCK_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        report = portfolio.build_results_csv(self.db, date(2026, 8, 20), self.lock, self.results)
        result_rows = {row["Home"]: row for row in self.read_rows(self.results)}
        self.assertEqual(report.finished, 0)
        self.assertEqual(report.conflicts, 1)
        self.assertEqual(result_rows["Conflict"]["ResultStatus"], "PENDING")
        self.assertIn("conflicting", result_rows["Conflict"]["SettlementNote"])
        self.assertEqual(result_rows["Tampered"]["ResultStatus"], "PENDING")
        self.assertIn("does not match", result_rows["Tampered"]["SettlementNote"])

    def test_results_recompute_pick_from_final_score(self) -> None:
        prediction_id = self.insert_prediction(home="Score check", odds=3.0)
        self.build_daily(linefeed_csv=None)
        # The selected side is the away participant, but the stored flag claims
        # a win despite a 2-0 home score.  Do not propagate that bad settlement.
        self.insert_result(prediction_id, outcome="WON", pick_won=1, home_score=2, away_score=0)
        report = portfolio.build_results_csv(self.db, date(2026, 8, 20), self.lock, self.results)
        row = self.read_rows(self.results)[0]
        self.assertEqual(report.finished, 0)
        self.assertEqual(row["ResultStatus"], "PENDING")
        self.assertIn("no complete final", row["SettlementNote"])

    def test_results_snapshot_is_refreshable_but_lock_remains_unchanged(self) -> None:
        prediction_id = self.insert_prediction(home="Later", odds=3.0)
        self.build_daily(linefeed_csv=None)
        locked = self.lock.read_bytes()
        first = portfolio.build_results_csv(self.db, date(2026, 8, 20), self.lock, self.results)
        self.assertEqual(first.pending, 1)
        self.insert_result(prediction_id, outcome="WON", pick_won=1, home_score=0, away_score=1)
        second = portfolio.build_results_csv(self.db, date(2026, 8, 20), self.lock, self.results)
        self.assertEqual(second.finished, 1)
        self.assertEqual(self.lock.read_bytes(), locked)

    def test_generated_files_are_accepted_by_telegram_publisher(self) -> None:
        prediction_id = self.insert_prediction(home="Publisher", odds=3.125)
        self.build_daily(linefeed_csv=None)
        self.insert_result(prediction_id, outcome="WON", pick_won=1, home_score=0, away_score=1)
        portfolio.build_results_csv(self.db, date(2026, 8, 20), self.lock, self.results)

        predictions = telegram_publisher.load_predictions(
            self.lock,
            business_date="2026-08-20",
            results_csv=self.results,
            official_only=True,
        )
        self.assertEqual(len(predictions), 1)
        self.assertEqual(str(predictions[0].odds), "3.125")
        self.assertEqual(predictions[0].outcome, "CORRECT")

    def test_header_only_lock_is_valid_when_there_are_no_picks(self) -> None:
        report = self.build_daily(linefeed_csv=None)
        self.assertEqual(report.selected, 0)
        with self.lock.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, portfolio.LOCK_FIELDS)
            self.assertEqual(list(reader), [])
        results = portfolio.build_results_csv(self.db, date(2026, 8, 20), self.lock, self.results)
        self.assertEqual(results.total, 0)


if __name__ == "__main__":
    unittest.main()
