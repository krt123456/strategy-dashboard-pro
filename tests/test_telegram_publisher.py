from __future__ import annotations

import csv
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from telegram_publisher import (  # noqa: E402
    AuditStore,
    DeliveryError,
    InputDataError,
    Prediction,
    TelegramPublisher,
    TelegramBotTransport,
    _telegram_length,
    allocate_bankroll,
    calculate_portfolio,
    deduplicate_predictions,
    format_daily_messages,
    format_summary_messages,
    filter_to_recorded_selection,
    load_predictions,
    select_publishable_predictions,
)
import lock_daily_forecast  # noqa: E402


def prediction(**changes: object) -> Prediction:
    base = Prediction(
        business_date="2026-08-20",
        rank=1,
        sport="football",
        league="Premier League",
        home="Alpha",
        away="Beta",
        pick="Alpha",
        probability=Decimal("0.71"),
        odds=Decimal("2.00"),
        start_time="20:00",
        strategy="APPROVED_FOR_HUMAN_REVIEW",
    )
    return replace(base, **changes)


class FakeTransport:
    destination = "fake-destination-hash"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[str] = []

    def send_message(self, text: str, *, parse_mode: str = "HTML") -> str:
        self.messages.append(text)
        if self.fail:
            raise DeliveryError("synthetic failure")
        return str(1000 + len(self.messages))


class AllocationTests(unittest.TestCase):
    def test_allocation_uses_every_cent(self) -> None:
        stakes = allocate_bankroll(Decimal("100.00"), 3)
        self.assertEqual(stakes, (Decimal("33.34"), Decimal("33.33"), Decimal("33.33")))
        self.assertEqual(sum(stakes), Decimal("100.00"))

    def test_portfolio_handles_win_loss_refund_and_pending(self) -> None:
        rows = [
            prediction(rank=1, outcome="CORRECT", odds=Decimal("2.00")),
            prediction(rank=2, outcome="WRONG"),
            prediction(rank=3, outcome="VOID"),
            prediction(rank=4, outcome="PENDING"),
        ]
        summary = calculate_portfolio(rows)
        self.assertEqual([item.stake for item in summary.settlements], [Decimal("25"), Decimal("25"), Decimal("25"), Decimal("25")])
        self.assertEqual(summary.settled_profit_loss, Decimal("0.00"))
        self.assertEqual(summary.pending_stake, Decimal("25.00"))
        self.assertFalse(summary.accounting_complete)
        self.assertIsNone(summary.final_balance)

    def test_complete_portfolio_has_exact_final_balance(self) -> None:
        rows = [
            prediction(rank=1, outcome="CORRECT", odds=Decimal("2.00")),
            prediction(rank=2, outcome="WRONG"),
            prediction(rank=3, outcome="REFUNDED"),
        ]
        summary = calculate_portfolio(rows)
        self.assertEqual(summary.settled_profit_loss, Decimal("0.01"))
        self.assertEqual(summary.final_balance, Decimal("100.01"))


class SelectionTests(unittest.TestCase):
    def test_requires_fifteen_minutes_prediction_odds_and_deduplicates(self) -> None:
        now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        eligible = prediction(rank=1, start_utc=datetime(2026, 8, 20, 12, 15, tzinfo=timezone.utc))
        duplicate = prediction(rank=2, start_utc=datetime(2026, 8, 20, 12, 15, tzinfo=timezone.utc))
        close = prediction(rank=3, home="Close", start_utc=datetime(2026, 8, 20, 12, 14, 59, tzinfo=timezone.utc))
        missing_start = prediction(rank=4, home="No start", start_utc=None)
        missing_odds = prediction(rank=5, home="No odds", start_utc=datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc), odds=None)
        report = select_publishable_predictions(
            [eligible, duplicate, close, missing_start, missing_odds], as_of=now
        )
        self.assertEqual(report.selected, (eligible,))
        self.assertEqual(report.duplicates_removed, 1)
        self.assertEqual(report.too_close_or_started, 1)
        self.assertEqual(report.missing_start_utc, 1)
        self.assertEqual(report.missing_prediction_odds, 1)
        self.assertEqual(report.unsupported_pick_market, 0)

    def test_summary_dedup_uses_match_and_pick(self) -> None:
        first = prediction(rank=1)
        same = prediction(rank=2)
        other_pick = prediction(rank=3, pick="Beta")
        rows, removed = deduplicate_predictions([first, same, other_pick])
        self.assertEqual(rows, (first, other_pick))
        self.assertEqual(removed, 1)

    def test_doubleheader_with_different_start_times_is_not_deduplicated(self) -> None:
        first = prediction(rank=1, start_utc=datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc))
        second = prediction(rank=2, start_utc=datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc))
        rows, removed = deduplicate_predictions([first, second])
        self.assertEqual(rows, (first, second))
        self.assertEqual(removed, 0)

    def test_unsupported_total_market_is_not_published(self) -> None:
        row = prediction(
            pick="over 2.5",
            start_utc=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
        )
        report = select_publishable_predictions(
            [row], as_of=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(report.selected, ())
        self.assertEqual(report.unsupported_pick_market, 1)


class FormattingTests(unittest.TestCase):
    def test_daily_is_arabic_escaped_and_hypothetical(self) -> None:
        row = prediction(home="<Alpha & Co>", pick="over 2.5")
        message = format_daily_messages([row], business_date=row.business_date)[0]
        self.assertIn("توقعات اليوم", message)
        self.assertIn("أكثر من 2.5", message)
        self.assertIn("&lt;Alpha &amp; Co&gt;", message)
        self.assertNotIn("<Alpha & Co>", message)
        self.assertIn("افتراضية", message)
        self.assertIn("ليست ضمانًا للربح", message)

    def test_displayed_three_decimal_odds_match_accounting(self) -> None:
        row = prediction(odds=Decimal("1.854"), outcome="CORRECT")
        daily = format_daily_messages([row], business_date="2026-08-20")[0]
        summary = format_summary_messages([row], business_date="2026-08-20")[0]
        self.assertIn("1.854", daily)
        self.assertIn("+$85.40", summary)

    def test_daily_chunks_remain_under_requested_limit(self) -> None:
        rows = [prediction(rank=index, home=f"Home {index}", away=f"Away {index}") for index in range(1, 31)]
        messages = format_daily_messages(rows, business_date="2026-08-20", max_chars=800)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 800 for message in messages))
        self.assertTrue(all(_telegram_length(message) <= 800 for message in messages))

    def test_summary_lists_each_settlement_bucket(self) -> None:
        rows = [
            prediction(rank=1, outcome="CORRECT", score="2-1"),
            prediction(rank=2, outcome="WRONG"),
            prediction(rank=3, outcome="PUSH"),
            prediction(rank=4, outcome="PENDING"),
        ]
        text = "\n".join(format_summary_messages(rows, business_date="2026-08-20"))
        self.assertIn("المباريات الرابحة", text)
        self.assertIn("المباريات الخاسرة", text)
        self.assertIn("المباريات المستردة", text)
        self.assertIn("المباريات المعلقة", text)
        self.assertIn("حصة ما زالت معلقة: $25.00", text)
        self.assertIn("-$25.00", text)


class SchemaAdapterTests(unittest.TestCase):
    def test_missing_lock_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(InputDataError):
                load_predictions(Path(directory) / "missing.csv", business_date="2026-08-20")

    def test_loads_only_official_rows_and_merges_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "lock.csv"
            results = root / "results.csv"
            fields = [
                "ForecastDate", "Rank", "Sport", "League", "Home", "Away", "Pick",
                "Prob", "CurrentOdds", "OddsAtPrediction", "StartUtc", "OfficialEntry",
                "FinalDecision", "StrategyGate",
            ]
            with lock.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "ForecastDate": "2026-08-20", "Rank": "1", "Sport": "football",
                            "League": "L", "Home": "Alpha", "Away": "Beta", "Pick": "home",
                            "Prob": "0.7", "CurrentOdds": "1.80", "OddsAtPrediction": "1.80",
                            "StartUtc": "2026-08-20T18:00:00Z", "OfficialEntry": "yes",
                            "FinalDecision": "APPROVED_FOR_HUMAN_REVIEW", "StrategyGate": "PRIMARY",
                        },
                        {
                            "ForecastDate": "2026-08-20", "Rank": "2", "Sport": "football",
                            "League": "L", "Home": "Gamma", "Away": "Delta", "Pick": "away",
                            "Prob": "0.6", "CurrentOdds": "2.10", "OddsAtPrediction": "2.10",
                            "StartUtc": "2026-08-20T19:00:00+00:00", "OfficialEntry": "no",
                        },
                    ]
                )
            result_fields = [
                "Date", "Sport", "Home", "Away", "Pick", "PickOutcome", "ResultStatus",
                "HomeScore", "AwayScore", "CheckedAt", "StartUtc",
            ]
            with results.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=result_fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "Date": "2026-08-20", "Sport": "football", "Home": "Alpha", "Away": "Beta",
                        "Pick": "home", "PickOutcome": "CORRECT", "ResultStatus": "FINISHED",
                        "HomeScore": "2", "AwayScore": "1", "CheckedAt": "2026-08-20T23:00:00",
                        "StartUtc": "2026-08-20T18:00:00Z",
                    }
                )
            rows = load_predictions(lock, business_date="2026-08-20", results_csv=results)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].outcome, "CORRECT")
        self.assertEqual(rows[0].score, "2-1")
        self.assertEqual(rows[0].strategy, "PRIMARY")

    def test_rejects_malformed_official_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock.csv"
            with lock.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ForecastDate", "Home", "Away", "Pick", "OfficialEntry", "StartUtc", "OddsAtPrediction"])
                writer.writeheader()
                writer.writerow({"ForecastDate": "2026-08-20", "Home": "", "Away": "Beta", "Pick": "home", "OfficialEntry": "yes", "StartUtc": "2026-08-20T20:00:00Z", "OddsAtPrediction": "2"})
            with self.assertRaises(InputDataError):
                load_predictions(lock, business_date="2026-08-20")


class LockIntegrationTests(unittest.TestCase):
    def test_lock_freezes_prediction_odds_start_and_utc_timestamp(self) -> None:
        advisor = {
            "Rank": "1", "Sport": "football", "Date": "2026-08-20", "League": "L",
            "Home": "A", "Away": "B", "Pick": "home", "Prob": "0.7", "PickOdds": "1.85",
            "OneXBetStartUtc": "2026-08-20T20:00:00Z",
            "OneXBetEventId": "evt-123",
            "OneXBetManualCheckedAt": "2026-08-20T10:00:00+00:00",
        }
        guard = {
            "Sport": "football", "Date": "2026-08-20", "Home": "A", "Away": "B", "Pick": "home",
            "CurrentOdds": "1.85", "FinalDecision": "APPROVED_FOR_HUMAN_REVIEW",
        }

        def fake_read(path: Path) -> list[dict[str, str]]:
            name = Path(path).name
            if name.startswith("daily_1xbet_value_advisor_"):
                return [advisor]
            if name.startswith("final_decision_guard_"):
                return [guard]
            return []

        with patch.object(lock_daily_forecast, "_read_csv", side_effect=fake_read):
            rows = lock_daily_forecast._build(date(2026, 8, 20))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["OddsAtPrediction"], "1.85")
        self.assertEqual(rows[0]["StartUtc"], "2026-08-20T20:00:00Z")
        self.assertEqual(rows[0]["OddsCapturedAt"], "2026-08-20T10:00:00+00:00")
        self.assertEqual(rows[0]["EventId"], "evt-123")
        self.assertTrue(str(rows[0]["LockedAt"]).endswith("+00:00"))

    def test_cancelled_result_is_treated_as_refund(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "lock.csv"
            results = root / "results.csv"
            with lock.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ForecastDate", "Home", "Away", "Pick", "OfficialEntry", "CurrentOdds", "OddsAtPrediction", "StartUtc"])
                writer.writeheader()
                writer.writerow({"ForecastDate": "2026-08-20", "Home": "A", "Away": "B", "Pick": "home", "OfficialEntry": "yes", "CurrentOdds": "9", "OddsAtPrediction": "2", "StartUtc": "2026-08-20T20:00:00Z"})
            with results.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Date", "Home", "Away", "Pick", "PickOutcome", "EntryOutcome", "ResultStatus", "StartUtc"])
                writer.writeheader()
                writer.writerow({"Date": "2026-08-20", "Home": "A", "Away": "B", "Pick": "home", "PickOutcome": "PENDING", "EntryOutcome": "PENDING", "ResultStatus": "CANCELLED", "StartUtc": "2026-08-20T20:00:00Z"})
            rows = load_predictions(lock, business_date="2026-08-20", results_csv=results)
        self.assertEqual(rows[0].outcome, "CANCELLED")
        self.assertEqual(rows[0].odds, Decimal("2"))
        self.assertEqual(calculate_portfolio(rows).settlements[0].category, "refund")

    def test_result_for_different_pick_never_grades_locked_pick(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "lock.csv"
            results = root / "results.csv"
            with lock.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ForecastDate", "Sport", "Home", "Away", "Pick", "OfficialEntry", "OddsAtPrediction", "StartUtc"])
                writer.writeheader()
                writer.writerow({"ForecastDate": "2026-08-20", "Sport": "football", "Home": "A", "Away": "B", "Pick": "home", "OfficialEntry": "yes", "OddsAtPrediction": "2", "StartUtc": "2026-08-20T20:00:00Z"})
            with results.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Date", "Sport", "Home", "Away", "Pick", "PickOutcome"])
                writer.writeheader()
                writer.writerow({"Date": "2026-08-20", "Sport": "football", "Home": "A", "Away": "B", "Pick": "away", "PickOutcome": "CORRECT"})
            rows = load_predictions(lock, business_date="2026-08-20", results_csv=results)
        self.assertEqual(rows[0].outcome, "PENDING")

    def test_live_score_is_never_treated_as_final(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "lock.csv"
            results = root / "results.csv"
            with lock.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ForecastDate", "Sport", "Home", "Away", "Pick", "OfficialEntry", "OddsAtPrediction", "StartUtc"])
                writer.writeheader()
                writer.writerow({"ForecastDate": "2026-08-20", "Sport": "football", "Home": "A", "Away": "B", "Pick": "A", "OfficialEntry": "yes", "OddsAtPrediction": "2", "StartUtc": "2026-08-20T20:00:00Z"})
            with results.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Date", "Sport", "Home", "Away", "Pick", "PickOutcome", "ResultStatus", "StartUtc"])
                writer.writeheader()
                writer.writerow({"Date": "2026-08-20", "Sport": "football", "Home": "A", "Away": "B", "Pick": "A", "PickOutcome": "CORRECT", "ResultStatus": "FINISHED_OR_LIVE_SCORE", "StartUtc": "2026-08-20T20:00:00Z"})
            rows = load_predictions(lock, business_date="2026-08-20", results_csv=results)
        self.assertEqual(rows[0].outcome, "PENDING")

    def test_results_merge_uses_event_id_for_same_team_doubleheader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "lock.csv"
            results = root / "results.csv"
            lock_fields = [
                "ForecastDate", "Rank", "Sport", "Home", "Away", "Pick", "OfficialEntry",
                "OddsAtPrediction", "StartUtc", "EventId",
            ]
            with lock.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=lock_fields)
                writer.writeheader()
                writer.writerow({"ForecastDate": "2026-08-20", "Rank": "1", "Sport": "baseball", "Home": "A", "Away": "B", "Pick": "A", "OfficialEntry": "yes", "OddsAtPrediction": "2", "StartUtc": "2026-08-20T12:00:00Z", "EventId": "event-1"})
                writer.writerow({"ForecastDate": "2026-08-20", "Rank": "2", "Sport": "baseball", "Home": "A", "Away": "B", "Pick": "A", "OfficialEntry": "yes", "OddsAtPrediction": "2", "StartUtc": "2026-08-20T18:00:00Z", "EventId": "event-2"})
            result_fields = [
                "ForecastDate", "Sport", "Home", "Away", "Pick", "StartUtc", "EventId",
                "PickOutcome", "ResultStatus",
            ]
            with results.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=result_fields)
                writer.writeheader()
                writer.writerow({"ForecastDate": "2026-08-20", "Sport": "baseball", "Home": "A", "Away": "B", "Pick": "A", "StartUtc": "2026-08-20T18:00:00Z", "EventId": "event-2", "PickOutcome": "CORRECT", "ResultStatus": "FINISHED"})
            rows = load_predictions(lock, business_date="2026-08-20", results_csv=results)
        self.assertEqual([row.outcome for row in rows], ["PENDING", "CORRECT"])

    def test_blank_malformed_and_wrong_dates_fail_closed(self) -> None:
        for bad_date in ("", "2026-08-20junk", "2026-08-19"):
            with self.subTest(bad_date=bad_date), tempfile.TemporaryDirectory() as directory:
                lock = Path(directory) / "lock.csv"
                with lock.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["ForecastDate", "Home", "Away", "Pick", "OfficialEntry", "OddsAtPrediction", "StartUtc"])
                    writer.writeheader()
                    writer.writerow({"ForecastDate": bad_date, "Home": "A", "Away": "B", "Pick": "A", "OfficialEntry": "yes", "OddsAtPrediction": "2", "StartUtc": "2026-08-20T20:00:00Z"})
                with self.assertRaises(InputDataError):
                    load_predictions(lock, business_date="2026-08-20")

    def test_probability_above_one_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock.csv"
            with lock.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ForecastDate", "Home", "Away", "Pick", "OfficialEntry", "OddsAtPrediction", "StartUtc", "Prob"])
                writer.writeheader()
                writer.writerow({"ForecastDate": "2026-08-20", "Home": "A", "Away": "B", "Pick": "home", "OfficialEntry": "yes", "OddsAtPrediction": "2", "StartUtc": "2026-08-20T20:00:00Z", "Prob": "1.01"})
            with self.assertRaises(InputDataError):
                load_predictions(lock, business_date="2026-08-20")


class AuditAndPublishingTests(unittest.TestCase):
    def test_end_of_day_uses_only_the_recorded_daily_selection(self) -> None:
        as_of = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        published = prediction(
            rank=1,
            home="Published",
            pick="Published",
            start_utc=datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc),
        )
        excluded = prediction(
            rank=2,
            home="Too close",
            pick="Too close",
            start_utc=datetime(2026, 8, 20, 12, 5, tzinfo=timezone.utc),
        )
        selected = select_publishable_predictions([published, excluded], as_of=as_of).selected
        with tempfile.TemporaryDirectory() as directory:
            store = AuditStore(Path(directory) / "audit.sqlite3")
            store.record_daily_selection(
                destination_hash="channel", business_date="2026-08-20", predictions=selected
            )
            manifest = store.load_daily_selection(
                destination_hash="channel", business_date="2026-08-20"
            )
        self.assertIsNotNone(manifest)
        # A later lock price must not alter the recorded publication accounting.
        result_rows = [
            replace(published, outcome="CORRECT", odds=Decimal("9")),
            replace(excluded, outcome="WRONG"),
        ]
        assert manifest is not None
        summary_rows = filter_to_recorded_selection(result_rows, manifest.items)
        self.assertEqual(summary_rows[0].odds, Decimal("2.00"))
        recorded_stakes = [manifest.items[next(iter(manifest.items))].stake]
        summary = "\n".join(
            format_summary_messages(
                summary_rows,
                business_date="2026-08-20",
                bankroll=manifest.bankroll,
                recorded_stakes=recorded_stakes,
            )
        )
        self.assertIn("Published", summary)
        self.assertNotIn("Too close", summary)
        self.assertIn("رابحة: 1", summary)
        self.assertIn("خاسرة: 0", summary)

    def test_first_manifest_is_immutable_and_preserves_bankroll_and_stakes(self) -> None:
        first = prediction(
            rank=1, start_utc=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
        )
        second = prediction(
            rank=2,
            home="Gamma",
            away="Delta",
            pick="Gamma",
            start_utc=datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = AuditStore(Path(directory) / "audit.sqlite3")
            store.record_daily_selection(
                destination_hash="channel",
                business_date="2026-08-20",
                predictions=[first, second],
                bankroll=Decimal("50"),
            )
            with self.assertRaises(InputDataError):
                store.record_daily_selection(
                    destination_hash="channel",
                    business_date="2026-08-20",
                    predictions=[second],
                    bankroll=Decimal("50"),
                )
            with self.assertRaises(InputDataError):
                store.record_daily_selection(
                    destination_hash="channel",
                    business_date="2026-08-20",
                    predictions=[first, second],
                    bankroll=Decimal("100"),
                )
            manifest = store.load_daily_selection(
                destination_hash="channel", business_date="2026-08-20"
            )
        assert manifest is not None
        self.assertEqual(manifest.bankroll, Decimal("50.00"))
        self.assertEqual(sorted(item.stake for item in manifest.items.values()), [Decimal("25.00"), Decimal("25.00")])
        self.assertIn('"official_only":true', manifest.selection_config)

    def test_sent_message_is_not_sent_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "audit.sqlite3"
            transport = FakeTransport()
            publisher = TelegramPublisher(transport, AuditStore(db))
            first = publisher.publish(["رسالة"], publication_kind="daily", business_date="2026-08-20")
            second = publisher.publish(["رسالة"], publication_kind="daily", business_date="2026-08-20")
            self.assertEqual(first.sent, 1)
            self.assertEqual(second.skipped_duplicates, 1)
            self.assertEqual(len(transport.messages), 1)
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute("SELECT status, attempt_count FROM telegram_deliveries").fetchone()
            self.assertEqual(row, ("SENT", 1))

    def test_failed_delivery_can_be_retried_and_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "audit.sqlite3"
            failing = TelegramPublisher(FakeTransport(fail=True), AuditStore(db))
            first = failing.publish(["رسالة"], publication_kind="summary", business_date="2026-08-20")
            succeeding = TelegramPublisher(FakeTransport(), AuditStore(db))
            second = succeeding.publish(["رسالة"], publication_kind="summary", business_date="2026-08-20")
            self.assertEqual(first.failed, 1)
            self.assertEqual(second.sent, 1)
            with closing(sqlite3.connect(db)) as connection:
                delivery = connection.execute("SELECT status, attempt_count FROM telegram_deliveries").fetchone()
                attempts = connection.execute(
                    "SELECT status FROM telegram_delivery_attempts ORDER BY attempt_number"
                ).fetchall()
            self.assertEqual(delivery, ("SENT", 2))
            self.assertEqual(attempts, [("FAILED",), ("SENT",)])

    def test_stale_attempt_cannot_overwrite_newer_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "audit.sqlite3"
            store = AuditStore(db)
            claim_one, _ = store.claim(
                destination_hash="d", publication_kind="daily", business_date="2026-08-20",
                fingerprint="f", preview="p",
            )
            self.assertIsNotNone(claim_one)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("UPDATE telegram_deliveries SET updated_at='2000-01-01T00:00:00+00:00'")
            claim_two, _ = store.claim(
                destination_hash="d", publication_kind="daily", business_date="2026-08-20",
                fingerprint="f", preview="p",
            )
            self.assertIsNotNone(claim_two)
            store.mark_sent(claim_two, "200")
            store.mark_failed(claim_one, "late failure")
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    "SELECT status, attempt_count, telegram_message_id, last_error FROM telegram_deliveries"
                ).fetchone()
            self.assertEqual(row, ("SENT", 2, "200", None))

    def test_audit_failure_after_send_quarantines_and_never_resends(self) -> None:
        class FailingFinalizeStore(AuditStore):
            def __init__(self, path: Path) -> None:
                super().__init__(path)
                self.fail_once = True

            def mark_sent(self, claim, message_id):  # type: ignore[no-untyped-def]
                if self.fail_once:
                    self.fail_once = False
                    raise sqlite3.OperationalError("synthetic post-send failure")
                return super().mark_sent(claim, message_id)

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "audit.sqlite3"
            store = FailingFinalizeStore(db)
            transport = FakeTransport()
            publisher = TelegramPublisher(transport, store)
            first = publisher.publish(["رسالة"], publication_kind="daily", business_date="2026-08-20")
            second = publisher.publish(["رسالة"], publication_kind="daily", business_date="2026-08-20")
            with closing(sqlite3.connect(db)) as connection:
                row = connection.execute(
                    "SELECT reconciliation_required FROM telegram_deliveries"
                ).fetchone()
        self.assertEqual(first.reconciliation_required, 1)
        self.assertEqual(second.reconciliation_required, 1)
        self.assertEqual(len(transport.messages), 1)
        self.assertEqual(row, (1,))


class TelegramRetryTests(unittest.TestCase):
    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return False

        def read(self) -> bytes:
            return self.payload

    def test_http_429_honors_retry_after_then_succeeds(self) -> None:
        error = __import__("urllib.error").error.HTTPError(
            "https://api.telegram.org/redacted",
            429,
            "rate limited",
            {},
            io.BytesIO(b'{"ok":false,"parameters":{"retry_after":2}}'),
        )
        delays: list[float] = []
        transport = TelegramBotTransport(
            "not-a-real-token", "not-a-real-chat", max_attempts=2, sleeper=delays.append
        )
        response = self.Response(b'{"ok":true,"result":{"message_id":77}}')
        with patch("telegram_publisher.urllib.request.urlopen", side_effect=[error, response]) as mocked:
            message_id = transport.send_message("test")
        self.assertEqual(message_id, "77")
        self.assertEqual(delays, [2.0])
        self.assertEqual(mocked.call_count, 2)

    def test_http_500_retries_only_bounded_number(self) -> None:
        def server_error():
            return __import__("urllib.error").error.HTTPError(
                "https://api.telegram.org/redacted", 500, "server error", {}, io.BytesIO(b"{}")
            )

        delays: list[float] = []
        transport = TelegramBotTransport(
            "not-a-real-token", "not-a-real-chat", max_attempts=3, sleeper=delays.append
        )
        with patch(
            "telegram_publisher.urllib.request.urlopen",
            side_effect=[server_error(), server_error(), server_error()],
        ) as mocked:
            with self.assertRaises(Exception):
                transport.send_message("test")
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(delays, [1.0, 2.0])


class CliEncodingTests(unittest.TestCase):
    def test_non_official_rows_can_never_be_sent(self) -> None:
        self.assertEqual(
            __import__("telegram_publisher").main(
                ["daily", "--date", "2026-08-20", "--send", "--include-non-official"]
            ),
            2,
        )

    def test_preview_forces_utf8_even_when_environment_requests_cp1252(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock.csv"
            with lock.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ForecastDate", "Sport", "Home", "Away", "Pick", "OfficialEntry", "OddsAtPrediction", "StartUtc", "Prob"])
                writer.writeheader()
                writer.writerow({"ForecastDate": "2099-01-01", "Sport": "football", "Home": "A", "Away": "B", "Pick": "A", "OfficialEntry": "yes", "OddsAtPrediction": "2", "StartUtc": "2099-01-01T20:00:00Z", "Prob": "0.7"})
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "cp1252"
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "telegram_publisher.py"), "daily", "--date", "2099-01-01", "--lock-csv", str(lock)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        self.assertIn("توقعات اليوم", completed.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
