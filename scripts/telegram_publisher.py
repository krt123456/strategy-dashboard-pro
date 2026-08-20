#!/usr/bin/env python3
"""Render and safely publish the approved daily forecast to Telegram.

The module deliberately keeps Telegram outside the prediction pipeline:

* Daily picks come from ``reports/locked_forecasts/forecast_lock_DATE.csv``.
* Only ``OfficialEntry=yes`` rows are used by default.
* End-of-day outcomes are merged from ``reports/prediction_results_DATE.csv``.
* Sending is opt-in (``--send``); the default is a local preview.
* Credentials are read only from ``TELEGRAM_BOT_TOKEN`` and
  ``TELEGRAM_CHAT_ID``. They are never written to the audit database.

Examples::

    python scripts/telegram_publisher.py daily --date 2026-08-20
    python scripts/telegram_publisher.py summary --date 2026-08-20
    python scripts/telegram_publisher.py daily --date 2026-08-20 --send

This is a presentation and delivery layer, not a betting strategy. Published
messages explicitly describe the $100 figures as a hypothetical simulation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
LOCK_DIR = REPORTS_DIR / "locked_forecasts"
DEFAULT_AUDIT_DB = BASE_DIR / "data" / "telegram_publication_audit.sqlite3"
try:
    LOCAL_TZ = ZoneInfo("Africa/Algiers")
except ZoneInfoNotFoundError:
    # Some minimal Windows/Python installations omit the IANA tz database.
    # Algeria has used UTC+01 without daylight-saving changes since 1981.
    LOCAL_TZ = timezone(timedelta(hours=1), name="Africa/Algiers")
TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_MESSAGE_LIMIT = 3600
DEFAULT_BANKROLL = Decimal("100.00")
MONEY_QUANTUM = Decimal("0.01")


class InputDataError(ValueError):
    """Raised when a source CSV cannot safely be adapted."""


class DeliveryError(RuntimeError):
    """Raised when Telegram rejects or cannot accept a message."""


class RetryableDeliveryError(DeliveryError):
    def __init__(self, message: str, retry_after: float = 1.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuditStateError(RuntimeError):
    """Raised when delivery succeeded but the audit claim can no longer be finalized."""


@dataclass(frozen=True)
class Prediction:
    business_date: str
    rank: int
    sport: str
    league: str
    home: str
    away: str
    pick: str
    probability: Optional[Decimal]
    odds: Optional[Decimal]
    start_utc: Optional[datetime] = None
    event_id: str = ""
    prediction_id: str = ""
    odds_captured_at: str = ""
    start_time: str = ""
    strategy: str = ""
    outcome: str = "PENDING"
    result_status: str = ""
    score: str = ""


@dataclass(frozen=True)
class Settlement:
    prediction: Prediction
    stake: Decimal
    category: str
    profit_loss: Optional[Decimal]
    return_amount: Optional[Decimal]


@dataclass(frozen=True)
class PortfolioSummary:
    bankroll: Decimal
    settlements: Tuple[Settlement, ...]
    settled_profit_loss: Decimal
    settled_return: Decimal
    pending_stake: Decimal
    accounting_complete: bool
    final_balance: Optional[Decimal]


@dataclass(frozen=True)
class SelectionReport:
    selected: Tuple[Prediction, ...]
    duplicates_removed: int
    missing_start_utc: int
    too_close_or_started: int
    missing_prediction_odds: int
    unsupported_pick_market: int


@dataclass(frozen=True)
class RecordedSelection:
    odds_at_prediction: Decimal
    start_utc: datetime
    stake: Decimal


@dataclass(frozen=True)
class RecordedDailyManifest:
    bankroll: Decimal
    items: Mapping[str, RecordedSelection]
    selection_config: str


@dataclass(frozen=True)
class DailySelectionReservation:
    manifest_fingerprint: str


@dataclass(frozen=True)
class DeliveryClaim:
    delivery_id: int
    attempt_number: int


@dataclass(frozen=True)
class PublishReport:
    sent: int
    skipped_duplicates: int
    in_progress: int
    reconciliation_required: int
    failed: int
    message_ids: Tuple[str, ...]
    errors: Tuple[str, ...]


class TelegramTransport(Protocol):
    destination: str

    def send_message(self, text: str, *, parse_mode: str = "HTML") -> str:
        """Send a single message and return the provider message id."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _target_date(raw: str) -> date:
    value = (raw or "today").strip().casefold()
    today = datetime.now(LOCAL_TZ).date()
    if value in {"today", "اليوم"}:
        return today
    if value in {"yesterday", "أمس", "امس"}:
        return today - timedelta(days=1)
    if value in {"tomorrow", "غداً", "غدا"}:
        return today + timedelta(days=1)
    return date.fromisoformat(value)


def _decimal(value: Any) -> Optional[Decimal]:
    text = str(value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _normalise_odds(value: Decimal) -> Decimal:
    if value.as_tuple().exponent < -3:
        return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return value


def _strict_iso_date(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise InputDataError(f"{label} must be a complete ISO date (YYYY-MM-DD)") from exc
    if text != parsed.isoformat():
        raise InputDataError(f"{label} must be a complete ISO date (YYYY-MM-DD)")
    return text


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # The input column is explicitly StartUtc; legacy producers sometimes
        # omitted the +00:00 suffix.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int(value: Any, default: int) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "نعم"}


def _is_official(row: Mapping[str, Any]) -> bool:
    explicit = str(row.get("OfficialEntry") or "").strip()
    if explicit:
        return _truthy(explicit)
    # Compatibility with locks produced before OfficialEntry was added.
    return str(row.get("FinalDecision") or "").strip() == "APPROVED_FOR_HUMAN_REVIEW"


def _normalise_key_part(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)


def _row_key(row: Mapping[str, Any]) -> Tuple[str, str, str, str, str]:
    day = str(row.get("ForecastDate") or row.get("Date") or "").strip()
    return (
        day,
        _normalise_key_part(row.get("Sport")),
        _normalise_key_part(row.get("Home")),
        _normalise_key_part(row.get("Away")),
        _normalise_key_part(row.get("Pick")),
    )


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _csv_headers(path: Path) -> set[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(field or "").strip() for field in (csv.DictReader(handle).fieldnames or [])}


def _require_csv(path: Path, label: str) -> set[str]:
    if not path.exists() or not path.is_file():
        raise InputDataError(f"Missing {label} CSV: {path}")
    if path.stat().st_size == 0:
        raise InputDataError(f"Empty {label} CSV: {path}")
    headers = _csv_headers(path)
    if not headers:
        raise InputDataError(f"Missing CSV header in {label}: {path}")
    return headers


def _result_match_keys(row: Mapping[str, Any]) -> Tuple[Tuple[str, ...], ...]:
    day = str(row.get("ForecastDate") or row.get("Date") or "").strip()
    pick = _normalise_key_part(row.get("Pick"))
    if not day or not pick:
        return ()
    keys: List[Tuple[str, ...]] = []
    prediction_id = _normalise_key_part(row.get("PredictionId"))
    event_id = _normalise_key_part(
        row.get("EventId")
        or row.get("OneXBetEventId")
        or row.get("OneXBetManualEventId")
        or row.get("OneXBetCanonicalId")
    )
    start_utc = _parse_utc_datetime(row.get("StartUtc") or row.get("OneXBetStartUtc"))
    if prediction_id:
        keys.append(("prediction", day, prediction_id, pick))
    if event_id:
        keys.append(("event", day, event_id, pick))
    if start_utc is not None and not event_id:
        keys.append(
            (
                "start",
                day,
                _normalise_key_part(row.get("Sport")),
                _normalise_key_part(row.get("Home")),
                _normalise_key_part(row.get("Away")),
                start_utc.isoformat(),
                pick,
            )
        )
    if not keys:
        keys.append(("legacy", *_row_key(row)))
    return tuple(keys)


def _result_signature(row: Mapping[str, Any]) -> Tuple[str, ...]:
    return (
        str(row.get("PickOutcome") or row.get("EntryOutcome") or row.get("Outcome") or "").upper(),
        str(row.get("ResultStatus") or "").upper(),
        str(row.get("HomeScore") or ""),
        str(row.get("AwayScore") or ""),
    )


def _latest_result_index(
    rows: Iterable[Mapping[str, Any]],
) -> Dict[Tuple[str, ...], Optional[Mapping[str, Any]]]:
    exact: Dict[Tuple[str, ...], Optional[Mapping[str, Any]]] = {}
    for row in sorted(rows, key=lambda item: str(item.get("CheckedAt") or "")):
        for key in _result_match_keys(row):
            previous = exact.get(key)
            if previous is not None and _result_signature(previous) != _result_signature(row):
                exact[key] = None
            elif key not in exact:
                exact[key] = row
    return exact


def _find_result(
    lock_row: Mapping[str, Any], index: Mapping[Tuple[str, ...], Optional[Mapping[str, Any]]]
) -> Mapping[str, Any]:
    matches: List[Mapping[str, Any]] = []
    for key in _result_match_keys(lock_row):
        row = index.get(key)
        if row is not None and all(row is not existing for existing in matches):
            matches.append(row)
    if not matches:
        return {}
    signatures = {_result_signature(row) for row in matches}
    if len(signatures) != 1:
        raise InputDataError("Conflicting result rows matched the same locked prediction")
    return matches[0]


def _outcome_from_row(row: Mapping[str, Any]) -> str:
    entry = str(row.get("EntryOutcome") or "").strip().upper()
    if entry and entry not in {"NOT_OFFICIAL_ENTRY", "PENDING"}:
        outcome = entry
    else:
        outcome = str(row.get("PickOutcome") or row.get("Outcome") or "PENDING").strip().upper() or "PENDING"
    result_status = str(row.get("ResultStatus") or "").strip().upper()
    if outcome in {"PENDING", ""} and result_status in {
        "PUSH", "VOID", "REFUND", "REFUNDED", "CANCELLED", "CANCELED", "POSTPONED"
    }:
        return result_status
    if outcome in {"CORRECT", "WRONG", "WIN", "WON", "LOSS", "LOST"} and result_status != "FINISHED":
        # FINISHED_OR_LIVE_SCORE is explicitly not final: the current result
        # producer uses it whenever a live score exists.
        return "PENDING"
    return outcome


def load_predictions(
    lock_csv: Path,
    *,
    business_date: str,
    results_csv: Optional[Path] = None,
    official_only: bool = True,
) -> List[Prediction]:
    """Adapt the current locked-forecast schema without changing its producer."""

    lock_headers = _require_csv(lock_csv, "locked forecast")
    required_lock_headers = {"Home", "Away", "Pick", "StartUtc", "OddsAtPrediction"}
    missing_headers = sorted(required_lock_headers - lock_headers)
    if missing_headers:
        raise InputDataError(f"Locked forecast is missing columns: {', '.join(missing_headers)}")
    if not ({"ForecastDate", "Date"} & lock_headers):
        raise InputDataError("Locked forecast needs ForecastDate or Date")
    if official_only and not ({"OfficialEntry", "FinalDecision"} & lock_headers):
        raise InputDataError("Locked forecast needs OfficialEntry or FinalDecision for safe official-only publishing")
    source_rows = _read_csv(lock_csv)
    result_exact: Dict[Tuple[str, ...], Optional[Mapping[str, Any]]] = {}
    if results_csv is not None:
        result_headers = _require_csv(results_csv, "prediction results")
        missing_result_headers = sorted({"Home", "Away"} - result_headers)
        if missing_result_headers:
            raise InputDataError(f"Prediction results are missing columns: {', '.join(missing_result_headers)}")
        if not ({"ForecastDate", "Date"} & result_headers):
            raise InputDataError("Prediction results need ForecastDate or Date")
        if not ({"PickOutcome", "EntryOutcome", "Outcome", "ResultStatus"} & result_headers):
            raise InputDataError("Prediction results need an outcome or result-status column")
        result_rows = _read_csv(results_csv)
        for row_number, result_row in enumerate(result_rows, start=2):
            result_day = _strict_iso_date(
                result_row.get("ForecastDate") or result_row.get("Date"),
                f"prediction-results row {row_number} date",
            )
            if result_day != business_date:
                raise InputDataError(
                    f"prediction-results row {row_number} belongs to {result_day}, expected {business_date}"
                )
            result_start_raw = result_row.get("StartUtc") or result_row.get("OneXBetStartUtc")
            if str(result_start_raw or "").strip() and _parse_utc_datetime(result_start_raw) is None:
                raise InputDataError(f"prediction-results row {row_number} has invalid StartUtc")
        result_exact = _latest_result_index(result_rows)

    predictions: List[Prediction] = []
    errors: List[str] = []
    for row_number, row in enumerate(source_rows, start=2):
        try:
            row_day = _strict_iso_date(
                row.get("ForecastDate") or row.get("Date"), f"locked-forecast row {row_number} date"
            )
        except InputDataError as exc:
            errors.append(str(exc))
            continue
        if row_day != business_date:
            errors.append(f"locked-forecast row {row_number} belongs to {row_day}, expected {business_date}")
            continue
        if official_only and not _is_official(row):
            continue

        missing = [name for name in ("Home", "Away", "Pick") if not str(row.get(name) or "").strip()]
        if missing:
            errors.append(f"row {row_number}: missing {', '.join(missing)}")
            continue

        try:
            result = _find_result(row, result_exact)
        except InputDataError as exc:
            errors.append(f"row {row_number}: {exc}")
            continue
        home_score = str(result.get("HomeScore") or "").strip()
        away_score = str(result.get("AwayScore") or "").strip()
        score = f"{home_score}-{away_score}" if home_score and away_score else ""
        probability_raw = row.get("Prob") or row.get("Probability")
        probability = _decimal(probability_raw)
        if str(probability_raw or "").strip() and probability is None:
            errors.append(f"row {row_number}: invalid probability")
            continue
        if probability is not None and not Decimal("0") <= probability <= Decimal("1"):
            errors.append(f"row {row_number}: Prob must be between 0 and 1")
            continue
        # Never fall back to a result-time/latest price. This field is frozen by
        # lock_daily_forecast.py at prediction time.
        odds_raw = row.get("OddsAtPrediction")
        odds = _decimal(odds_raw)
        if str(odds_raw or "").strip() and odds is None:
            errors.append(f"row {row_number}: invalid odds")
            continue
        if odds is not None and odds < 1:
            errors.append(f"row {row_number}: decimal odds must be at least 1")
            continue
        if odds is not None:
            odds = _normalise_odds(odds)
        start_raw = row.get("StartUtc")
        start_utc = _parse_utc_datetime(start_raw)
        if str(start_raw or "").strip() and start_utc is None:
            errors.append(f"row {row_number}: invalid StartUtc")
            continue
        predictions.append(
            Prediction(
                business_date=row_day or business_date,
                rank=_int(row.get("Rank"), len(predictions) + 1),
                sport=str(row.get("Sport") or "").strip(),
                league=str(row.get("League") or "").strip(),
                home=str(row.get("Home") or "").strip(),
                away=str(row.get("Away") or "").strip(),
                pick=str(row.get("Pick") or "").strip(),
                probability=probability,
                odds=odds,
                start_utc=start_utc,
                event_id=str(
                    row.get("EventId")
                    or row.get("OneXBetEventId")
                    or row.get("OneXBetManualEventId")
                    or row.get("OneXBetCanonicalId")
                    or ""
                ).strip(),
                prediction_id=str(row.get("PredictionId") or "").strip(),
                odds_captured_at=str(row.get("OddsCapturedAt") or row.get("LockedAt") or "").strip(),
                start_time=str(result.get("StartTimeLocal") or row.get("StartTimeLocal") or "").strip(),
                strategy=str(row.get("StrategyGate") or row.get("FinalDecision") or "").strip(),
                outcome=_outcome_from_row(result) if result else _outcome_from_row(row),
                result_status=str(result.get("ResultStatus") or row.get("ResultStatus") or "").strip(),
                score=score,
            )
        )

    if errors:
        preview = "; ".join(errors[:8])
        suffix = f"; and {len(errors) - 8} more" if len(errors) > 8 else ""
        raise InputDataError(f"Unsafe locked forecast data in {lock_csv}: {preview}{suffix}")
    predictions.sort(key=lambda item: (item.rank, item.start_time, item.home.casefold(), item.away.casefold()))
    return predictions


def prediction_identity(prediction: Prediction) -> Tuple[str, ...]:
    """Stable date/sport/match/pick key used by both daily and summary flows."""

    prediction_id = _normalise_key_part(prediction.prediction_id)
    event_id = _normalise_key_part(prediction.event_id)
    if event_id:
        match_identity = (f"event:{event_id}", "", "")
    elif prediction_id:
        match_identity = (f"prediction:{prediction_id}", "", "")
    else:
        start = prediction.start_utc.astimezone(timezone.utc).isoformat() if prediction.start_utc else ""
        match_identity = (
            _normalise_key_part(prediction.home),
            _normalise_key_part(prediction.away),
            start,
        )
    return (
        prediction.business_date,
        _normalise_key_part(prediction.sport),
        *match_identity,
        _normalise_key_part(prediction.pick),
    )


def prediction_fingerprint(prediction: Prediction) -> str:
    return hashlib.sha256("\0".join(prediction_identity(prediction)).encode("utf-8")).hexdigest()


def deduplicate_predictions(predictions: Sequence[Prediction]) -> Tuple[Tuple[Prediction, ...], int]:
    seen: set[Tuple[str, ...]] = set()
    selected: List[Prediction] = []
    duplicates = 0
    for prediction in sorted(
        predictions,
        key=lambda item: (item.rank, item.start_utc or datetime.max.replace(tzinfo=timezone.utc), item.home.casefold()),
    ):
        identity = prediction_identity(prediction)
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        selected.append(prediction)
    return tuple(selected), duplicates


def filter_to_recorded_selection(
    predictions: Sequence[Prediction], manifest: Mapping[str, RecordedSelection]
) -> Tuple[Prediction, ...]:
    """Rebuild the end-of-day portfolio from the immutable daily manifest."""

    deduplicated, _ = deduplicate_predictions(predictions)
    by_fingerprint = {prediction_fingerprint(item): item for item in deduplicated}
    missing = frozenset(manifest) - frozenset(by_fingerprint)
    if missing:
        raise InputDataError(
            f"{len(missing)} published prediction(s) are missing from the locked forecast/results merge"
        )
    selected: List[Prediction] = []
    for item in deduplicated:
        fingerprint = prediction_fingerprint(item)
        recorded = manifest.get(fingerprint)
        if recorded is None:
            continue
        # The audit snapshot, not a later regenerated lock/result price, is
        # authoritative for the hypothetical end-of-day accounting.
        selected.append(replace(item, odds=recorded.odds_at_prediction, start_utc=recorded.start_utc))
    return tuple(selected)


def _pick_supported_by_result_checker(prediction: Prediction) -> bool:
    """Mirror check_prediction_results._picked_side until other markets gain grading."""

    pick = _normalise_key_part(prediction.pick)
    home = _normalise_key_part(prediction.home)
    away = _normalise_key_part(prediction.away)
    if pick in {"draw", "x"}:
        return True
    return bool(
        pick
        and (
            pick == home
            or pick == away
            or (home and home in pick)
            or (away and away in pick)
        )
    )


def select_publishable_predictions(
    predictions: Sequence[Prediction],
    *,
    as_of: Optional[datetime] = None,
    min_lead_minutes: int = 15,
) -> SelectionReport:
    """Apply the final, publication-time pre-match gate and exact de-duplication."""

    if min_lead_minutes < 0:
        raise ValueError("min_lead_minutes cannot be negative")
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    cutoff = as_of.astimezone(timezone.utc) + timedelta(minutes=min_lead_minutes)
    eligible: List[Prediction] = []
    missing_start = 0
    too_close = 0
    missing_odds = 0
    unsupported_pick = 0
    for prediction in predictions:
        if prediction.start_utc is None:
            missing_start += 1
            continue
        if prediction.start_utc < cutoff:
            too_close += 1
            continue
        if prediction.odds is None:
            missing_odds += 1
            continue
        if not _pick_supported_by_result_checker(prediction):
            unsupported_pick += 1
            continue
        eligible.append(prediction)
    deduplicated, duplicates = deduplicate_predictions(eligible)
    return SelectionReport(
        selected=deduplicated,
        duplicates_removed=duplicates,
        missing_start_utc=missing_start,
        too_close_or_started=too_close,
        missing_prediction_odds=missing_odds,
        unsupported_pick_market=unsupported_pick,
    )


def allocate_bankroll(bankroll: Decimal, count: int) -> Tuple[Decimal, ...]:
    """Allocate every cent exactly, distributing remainder cents by rank order."""

    bankroll = bankroll.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if bankroll < 0:
        raise ValueError("bankroll cannot be negative")
    if count < 0:
        raise ValueError("count cannot be negative")
    if count == 0:
        return ()
    cents = int(bankroll * 100)
    base, remainder = divmod(cents, count)
    return tuple(Decimal(base + (1 if index < remainder else 0)) / 100 for index in range(count))


WIN_OUTCOMES = {"CORRECT", "WIN", "WON", "WINNER", "رابح"}
LOSS_OUTCOMES = {"WRONG", "LOSS", "LOST", "LOSER", "خاسر"}
REFUND_OUTCOMES = {
    "PUSH",
    "VOID",
    "REFUND",
    "REFUNDED",
    "CANCELLED",
    "CANCELED",
    "POSTPONED",
    "مسترد",
}


def settlement_category(outcome: str) -> str:
    value = str(outcome or "").strip().upper()
    if value in WIN_OUTCOMES:
        return "win"
    if value in LOSS_OUTCOMES:
        return "loss"
    if value in REFUND_OUTCOMES:
        return "refund"
    return "pending"


def calculate_portfolio(
    predictions: Sequence[Prediction],
    bankroll: Decimal = DEFAULT_BANKROLL,
    stakes: Optional[Sequence[Decimal]] = None,
) -> PortfolioSummary:
    if stakes is None:
        allocated_stakes = allocate_bankroll(bankroll, len(predictions))
    else:
        allocated_stakes = tuple(stake.quantize(MONEY_QUANTUM) for stake in stakes)
        if len(allocated_stakes) != len(predictions):
            raise ValueError("recorded stake count does not match prediction count")
        if allocated_stakes and sum(allocated_stakes) != bankroll.quantize(MONEY_QUANTUM):
            raise ValueError("recorded stakes do not add up to bankroll")
    settlements: List[Settlement] = []
    profit_loss = Decimal("0.00")
    settled_return = Decimal("0.00")
    pending_stake = Decimal("0.00")
    complete = True

    for prediction, stake in zip(predictions, allocated_stakes):
        category = settlement_category(prediction.outcome)
        item_profit: Optional[Decimal]
        item_return: Optional[Decimal]
        if category == "win" and prediction.odds is not None:
            item_return = (stake * _normalise_odds(prediction.odds)).quantize(
                MONEY_QUANTUM, rounding=ROUND_HALF_UP
            )
            item_profit = item_return - stake
        elif category == "win":
            item_profit = None
            item_return = None
            complete = False
        elif category == "loss":
            item_return = Decimal("0.00")
            item_profit = -stake
        elif category == "refund":
            item_return = stake
            item_profit = Decimal("0.00")
        else:
            item_return = None
            item_profit = None
            pending_stake += stake
            complete = False
        if item_profit is not None:
            profit_loss += item_profit
        if item_return is not None:
            settled_return += item_return
        settlements.append(Settlement(prediction, stake, category, item_profit, item_return))

    bankroll = bankroll.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    final_balance = (bankroll + profit_loss).quantize(MONEY_QUANTUM) if complete else None
    return PortfolioSummary(
        bankroll=bankroll,
        settlements=tuple(settlements),
        settled_profit_loss=profit_loss.quantize(MONEY_QUANTUM),
        settled_return=settled_return.quantize(MONEY_QUANTUM),
        pending_stake=pending_stake.quantize(MONEY_QUANTUM),
        accounting_complete=complete,
        final_balance=final_balance,
    )


ARABIC_WEEKDAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
ARABIC_MONTHS = (
    "يناير",
    "فبراير",
    "مارس",
    "أبريل",
    "مايو",
    "يونيو",
    "يوليو",
    "أغسطس",
    "سبتمبر",
    "أكتوبر",
    "نوفمبر",
    "ديسمبر",
)


def _arabic_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return _safe(value)
    return f"{ARABIC_WEEKDAYS[parsed.weekday()]} {parsed.day} {ARABIC_MONTHS[parsed.month - 1]} {parsed.year}"


def _safe(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return html.escape(text, quote=True)


SPORT_NAMES = {
    "football": "كرة القدم ⚽",
    "soccer": "كرة القدم ⚽",
    "basketball": "كرة السلة 🏀",
    "tennis": "التنس 🎾",
    "table tennis": "تنس الطاولة 🏓",
    "tabletennis": "تنس الطاولة 🏓",
    "handball": "كرة اليد 🤾",
    "hockey": "الهوكي 🏒",
    "ice hockey": "هوكي الجليد 🏒",
    "baseball": "البيسبول ⚾",
}


def _sport_arabic(value: str) -> str:
    return SPORT_NAMES.get(value.strip().casefold(), _safe(value) or "رياضة")


def _pick_arabic(value: str) -> str:
    raw = value.strip()
    lowered = re.sub(r"[_\-]+", " ", raw.casefold()).strip()
    direct = {
        "1": "فوز صاحب الأرض",
        "home": "فوز صاحب الأرض",
        "home win": "فوز صاحب الأرض",
        "x": "تعادل",
        "draw": "تعادل",
        "2": "فوز الضيف",
        "away": "فوز الضيف",
        "away win": "فوز الضيف",
        "btts yes": "الفريقان يسجلان: نعم",
        "btts no": "الفريقان يسجلان: لا",
    }
    if lowered in direct:
        return direct[lowered]
    over_under = re.fullmatch(r"(over|under)\s*([0-9]+(?:[.,][0-9]+)?)", lowered)
    if over_under:
        direction = "أكثر من" if over_under.group(1) == "over" else "أقل من"
        return f"{direction} {over_under.group(2).replace(',', '.')}"
    return _safe(raw)


def _probability(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    percent = value * 100
    return f"{percent.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}٪"


def _odds(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    value = _normalise_odds(value)
    decimals = min(3, max(2, -value.as_tuple().exponent))
    return f"{value:.{decimals}f}"


def _money(value: Decimal, *, signed: bool = False) -> str:
    value = value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if value < 0:
        return f"-${abs(value):,.2f}"
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}${value:,.2f}"


def _daily_block(index: int, prediction: Prediction, stake: Decimal) -> str:
    details = [
        f"<b>{index}) {_safe(prediction.home)} × {_safe(prediction.away)}</b>",
        f"🏟️ {_sport_arabic(prediction.sport)}" + (f" | {_safe(prediction.league)}" if prediction.league else ""),
    ]
    if prediction.start_time:
        details.append(f"⏰ {_safe(prediction.start_time)}")
    details.extend(
        [
            f"🎯 الاختيار: <b>{_pick_arabic(prediction.pick)}</b>",
            f"📈 الاحتمال: {_probability(prediction.probability)} | السعر: {_odds(prediction.odds)}",
            f"💵 الحصة الافتراضية: <b>{_money(stake)}</b>",
        ]
    )
    return "\n".join(details)


def _telegram_length(text: str) -> int:
    """Telegram's documented limit is measured in UTF-16 code units."""

    return len(text.encode("utf-16-le")) // 2


def _split_blocks(header: str, blocks: Sequence[str], footer: str, max_chars: int) -> List[str]:
    if not 500 <= max_chars <= TELEGRAM_MESSAGE_LIMIT:
        raise ValueError(f"max_chars must be between 500 and {TELEGRAM_MESSAGE_LIMIT}")
    if not blocks:
        message = "\n\n".join(part for part in (header, footer) if part)
        return [message]

    messages: List[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if _telegram_length(candidate) <= max_chars:
            current = candidate
            continue
        if current and current != header:
            messages.append(current)
            current = f"{header}\n\n{block}"
        else:
            raise ValueError("A formatted prediction block is too large for the configured Telegram limit")
        if _telegram_length(current) > max_chars:
            raise ValueError("A formatted prediction block is too large for the configured Telegram limit")
    if current:
        if footer and _telegram_length(f"{current}\n\n{footer}") <= max_chars:
            current = f"{current}\n\n{footer}"
        messages.append(current)
    if footer and messages and footer not in messages[-1]:
        if _telegram_length(footer) <= max_chars:
            messages.append(footer)
    return messages


DISCLAIMER = "⚠️ محاكاة تحليلية وليست ضمانًا للربح. راهن بمسؤولية، وللبالغين فقط."


def format_daily_messages(
    predictions: Sequence[Prediction],
    *,
    business_date: str,
    bankroll: Decimal = DEFAULT_BANKROLL,
    max_chars: int = DEFAULT_MESSAGE_LIMIT,
) -> List[str]:
    header = (
        f"🔥 <b>توقعات اليوم</b> | {_arabic_date(business_date)}\n"
        f"🎯 {len(predictions)} اختيارات اجتازت بوابة المراجعة"
    )
    if not predictions:
        footer = "لا توجد اختيارات رسمية مستوفية للشروط اليوم. الانضباط أهم من كثرة الاختيارات. 🛡️\n\n" + DISCLAIMER
        return _split_blocks(header, (), footer, max_chars)
    stakes = allocate_bankroll(bankroll, len(predictions))
    blocks = [_daily_block(index, prediction, stake) for index, (prediction, stake) in enumerate(zip(predictions, stakes), start=1)]
    footer = f"💼 المحاكاة: {_money(bankroll)} موزعة بالكامل بالتساوي على الاختيارات.\n{DISCLAIMER}"
    return _split_blocks(header, blocks, footer, max_chars)


def _settlement_line(item: Settlement) -> str:
    prediction = item.prediction
    score = f" | النتيجة: {_safe(prediction.score)}" if prediction.score else ""
    if item.profit_loss is None:
        pnl = "قيد الحساب"
    else:
        pnl = _money(item.profit_loss, signed=True)
    return (
        f"• <b>{_safe(prediction.home)} × {_safe(prediction.away)}</b>{score}\n"
        f"  الاختيار: {_pick_arabic(prediction.pick)} | الحصة: {_money(item.stake)} | الأثر: <b>{pnl}</b>"
    )


def format_summary_messages(
    predictions: Sequence[Prediction],
    *,
    business_date: str,
    bankroll: Decimal = DEFAULT_BANKROLL,
    recorded_stakes: Optional[Sequence[Decimal]] = None,
    max_chars: int = DEFAULT_MESSAGE_LIMIT,
) -> List[str]:
    portfolio = calculate_portfolio(predictions, bankroll, recorded_stakes)
    counts = {name: sum(1 for item in portfolio.settlements if item.category == name) for name in ("win", "loss", "refund", "pending")}
    header = (
        f"📊 <b>حصيلة اليوم</b> | {_arabic_date(business_date)}\n"
        f"✅ رابحة: {counts['win']} | ❌ خاسرة: {counts['loss']} | ↩️ مستردة: {counts['refund']} | ⏳ معلقة: {counts['pending']}"
    )
    labels = {
        "win": "✅ <b>المباريات الرابحة</b>",
        "loss": "❌ <b>المباريات الخاسرة</b>",
        "refund": "↩️ <b>المباريات المستردة</b>",
        "pending": "⏳ <b>المباريات المعلقة</b>",
    }
    blocks: List[str] = []
    for category in ("win", "loss", "refund", "pending"):
        items = [item for item in portfolio.settlements if item.category == category]
        if items:
            blocks.append(labels[category] + "\n" + _settlement_line(items[0]))
            blocks.extend(_settlement_line(item) for item in items[1:])

    accounting = [
        "💼 <b>محاكاة رأس مال افتراضي</b>",
        f"• رأس المال: {_money(portfolio.bankroll)}",
        f"• صافي نتائج المباريات المحسومة: <b>{_money(portfolio.settled_profit_loss, signed=True)}</b>",
    ]
    if portfolio.pending_stake:
        accounting.append(f"• حصة ما زالت معلقة: {_money(portfolio.pending_stake)}")
    if portfolio.accounting_complete and portfolio.final_balance is not None:
        accounting.append(f"• الرصيد الختامي الافتراضي: <b>{_money(portfolio.final_balance)}</b>")
    else:
        accounting.append("• الرصيد الختامي غير نهائي حتى تسوية كل المباريات وتوفر أسعارها.")
    blocks.append("\n".join(accounting))
    return _split_blocks(header, blocks, DISCLAIMER, max_chars)


class TelegramBotTransport:
    """Small stdlib Telegram Bot API client with no credential persistence."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: float = 15.0,
        api_base: str = "https://api.telegram.org",
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token.strip() or not chat_id.strip():
            raise ValueError("Telegram token and chat id are required")
        self._token = token.strip()
        self._chat_id = chat_id.strip()
        self._timeout = timeout
        self._api_base = api_base.rstrip("/")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._max_attempts = max_attempts
        self._sleeper = sleeper
        # Only a digest reaches logs/SQLite; the channel id itself is not stored.
        self.destination = hashlib.sha256(self._chat_id.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _retry_after(body: bytes, fallback: float) -> float:
        try:
            parsed = json.loads(body.decode("utf-8"))
            value = float(parsed.get("parameters", {}).get("retry_after", fallback))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            value = fallback
        return min(30.0, max(0.0, value))

    def _send_once(self, text: str, parse_mode: str, attempt: int) -> str:
        payload = urllib.parse.urlencode(
            {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._api_base}/bot{self._token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            except OSError:
                body = b""
            if exc.code == 429 or 500 <= exc.code <= 599:
                header_delay = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    fallback = float(header_delay) if header_delay is not None else float(2 ** (attempt - 1))
                except ValueError:
                    fallback = float(2 ** (attempt - 1))
                raise RetryableDeliveryError(
                    f"Telegram temporarily unavailable (HTTP {exc.code})",
                    self._retry_after(body, fallback),
                ) from exc
            raise DeliveryError(f"Telegram rejected the request (HTTP {exc.code})") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RetryableDeliveryError(
                f"Telegram request failed: {type(exc).__name__}",
                min(30.0, float(2 ** (attempt - 1))),
            ) from exc
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeliveryError("Telegram returned an invalid response") from exc
        if not parsed.get("ok"):
            description = str(parsed.get("description") or "request rejected")
            error_code = int(parsed.get("error_code") or 0)
            if error_code == 429 or 500 <= error_code <= 599:
                raise RetryableDeliveryError(
                    f"Telegram temporarily rejected the message ({error_code})",
                    self._retry_after(body, float(2 ** (attempt - 1))),
                )
            raise DeliveryError(f"Telegram rejected the message: {description[:200]}")
        message_id = parsed.get("result", {}).get("message_id")
        if message_id is None:
            raise DeliveryError("Telegram response did not contain a message id")
        return str(message_id)

    def send_message(self, text: str, *, parse_mode: str = "HTML") -> str:
        if _telegram_length(text) > TELEGRAM_MESSAGE_LIMIT:
            raise DeliveryError("Telegram message exceeds 4096 characters")
        last_error: Optional[RetryableDeliveryError] = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._send_once(text, parse_mode, attempt)
            except RetryableDeliveryError as exc:
                last_error = exc
                if attempt >= self._max_attempts:
                    break
                self._sleeper(exc.retry_after)
        assert last_error is not None
        raise last_error


class AuditStore:
    """SQLite delivery ledger used for de-duplication and retry auditing."""

    def __init__(self, path: Path, *, stale_after: timedelta = timedelta(minutes=15)) -> None:
        self.path = Path(path)
        self.stale_after = stale_after
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialise(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    destination_hash TEXT NOT NULL,
                    publication_kind TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    content_preview TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('SENDING', 'SENT', 'FAILED')),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    telegram_message_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    reconciliation_required INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(destination_hash, publication_kind, business_date, content_fingerprint)
                );
                CREATE TABLE IF NOT EXISTS telegram_delivery_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_id INTEGER NOT NULL REFERENCES telegram_deliveries(id),
                    attempt_number INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL CHECK (status IN ('SENDING', 'SENT', 'FAILED')),
                    error TEXT,
                    UNIQUE(delivery_id, attempt_number)
                );
                CREATE INDEX IF NOT EXISTS idx_telegram_deliveries_date
                    ON telegram_deliveries(business_date, publication_kind, status);
                CREATE TABLE IF NOT EXISTS telegram_daily_selection_runs (
                    destination_hash TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    selected_at TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    bankroll TEXT NOT NULL DEFAULT '100.00',
                    manifest_fingerprint TEXT NOT NULL DEFAULT '',
                    selection_config TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(destination_hash, business_date)
                );
                CREATE TABLE IF NOT EXISTS telegram_daily_selection_items (
                    destination_hash TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    prediction_fingerprint TEXT NOT NULL,
                    odds_at_prediction TEXT NOT NULL,
                    start_utc TEXT NOT NULL,
                    stake TEXT NOT NULL DEFAULT '0.00',
                    selected_at TEXT NOT NULL,
                    PRIMARY KEY(destination_hash, business_date, prediction_fingerprint)
                );
                CREATE TABLE IF NOT EXISTS telegram_daily_selection_reservations (
                    destination_hash TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    manifest_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('SENDING', 'SENT', 'FAILED')),
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    PRIMARY KEY(destination_hash, business_date)
                );
                """
            )
            # Forward-only migration for databases created by an earlier local
            # preview of this module.
            self._ensure_column(connection, "telegram_daily_selection_runs", "bankroll", "TEXT NOT NULL DEFAULT '100.00'")
            self._ensure_column(connection, "telegram_daily_selection_runs", "manifest_fingerprint", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "telegram_daily_selection_runs", "selection_config", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "telegram_daily_selection_items", "stake", "TEXT NOT NULL DEFAULT '0.00'")
            self._ensure_column(connection, "telegram_deliveries", "reconciliation_required", "INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def claim(
        self,
        *,
        destination_hash: str,
        publication_kind: str,
        business_date: str,
        fingerprint: str,
        preview: str,
    ) -> Tuple[Optional[DeliveryClaim], str]:
        now = _now_utc()
        stale_before = (datetime.now(timezone.utc) - self.stale_after).isoformat(timespec="seconds")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM telegram_deliveries
                WHERE destination_hash=? AND publication_kind=? AND business_date=? AND content_fingerprint=?
                """,
                (destination_hash, publication_kind, business_date, fingerprint),
            ).fetchone()
            if row is not None and int(row["reconciliation_required"] or 0) == 1:
                return None, "reconciliation_required"
            if row is not None and row["status"] == "SENT":
                return None, "already_sent"
            if row is not None and row["status"] == "SENDING" and row["updated_at"] >= stale_before:
                return None, "already_sending"

            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO telegram_deliveries (
                        destination_hash, publication_kind, business_date, content_fingerprint,
                        content_preview, status, attempt_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'SENDING', 1, ?, ?)
                    """,
                    (destination_hash, publication_kind, business_date, fingerprint, preview[:240], now, now),
                )
                delivery_id = int(cursor.lastrowid)
                attempt_number = 1
            else:
                delivery_id = int(row["id"])
                attempt_number = int(row["attempt_count"]) + 1
                connection.execute(
                    """
                    UPDATE telegram_deliveries
                    SET status='SENDING', attempt_count=?, updated_at=?, last_error=NULL
                    WHERE id=?
                    """,
                    (attempt_number, now, delivery_id),
                )
            connection.execute(
                """
                INSERT INTO telegram_delivery_attempts
                    (delivery_id, attempt_number, started_at, status)
                VALUES (?, ?, ?, 'SENDING')
                """,
                (delivery_id, attempt_number, now),
            )
            return DeliveryClaim(delivery_id, attempt_number), "claimed"

    def mark_sent(self, claim: DeliveryClaim, message_id: str) -> None:
        now = _now_utc()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE telegram_deliveries
                SET status='SENT', telegram_message_id=?, last_error=NULL, updated_at=?, sent_at=?,
                    reconciliation_required=0
                WHERE id=? AND status='SENDING' AND attempt_count=?
                """,
                (message_id, now, now, claim.delivery_id, claim.attempt_number),
            )
            if cursor.rowcount != 1:
                raise AuditStateError("delivery claim was no longer active after Telegram accepted the message")
            connection.execute(
                """
                UPDATE telegram_delivery_attempts
                SET status='SENT', finished_at=?, error=NULL
                WHERE delivery_id=? AND attempt_number=?
                """,
                (now, claim.delivery_id, claim.attempt_number),
            )

    def mark_failed(self, claim: DeliveryClaim, error: str) -> None:
        now = _now_utc()
        safe_error = str(error)[:500]
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE telegram_deliveries
                SET status='FAILED', last_error=?, updated_at=?
                WHERE id=? AND status='SENDING' AND attempt_count=?
                """,
                (safe_error, now, claim.delivery_id, claim.attempt_number),
            )
            connection.execute(
                """
                UPDATE telegram_delivery_attempts
                SET status='FAILED', finished_at=?, error=?
                WHERE delivery_id=? AND attempt_number=?
                """,
                (now, safe_error, claim.delivery_id, claim.attempt_number),
            )

    def mark_unknown(self, claim: DeliveryClaim, error: str) -> None:
        """Quarantine a sent-but-uncommitted delivery so automatic retry cannot duplicate it."""

        now = _now_utc()
        safe_error = f"RECONCILIATION_REQUIRED: {error}"[:500]
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE telegram_deliveries
                SET status='FAILED', last_error=?, updated_at=?, reconciliation_required=1
                WHERE id=? AND attempt_count=?
                """,
                (safe_error, now, claim.delivery_id, claim.attempt_number),
            )
            connection.execute(
                """
                UPDATE telegram_delivery_attempts
                SET status='FAILED', finished_at=?, error=?
                WHERE delivery_id=? AND attempt_number=?
                """,
                (now, safe_error, claim.delivery_id, claim.attempt_number),
            )

    @staticmethod
    def _daily_manifest_data(
        predictions: Sequence[Prediction],
        bankroll: Decimal,
        min_lead_minutes: int,
    ) -> Tuple[str, Tuple[Tuple[str, str, str, str], ...], str, Decimal]:
        deduplicated, _ = deduplicate_predictions(predictions)
        bankroll = bankroll.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        stakes = allocate_bankroll(bankroll, len(deduplicated))
        rows: List[Tuple[str, str, str, str]] = []
        for prediction, stake in zip(deduplicated, stakes):
            if prediction.odds is None or prediction.start_utc is None:
                raise InputDataError("A daily manifest item is missing OddsAtPrediction or StartUtc")
            rows.append(
                (
                    prediction_fingerprint(prediction),
                    format(_normalise_odds(prediction.odds), "f"),
                    prediction.start_utc.astimezone(timezone.utc).isoformat(),
                    format(stake, ".2f"),
                )
            )
        rows.sort(key=lambda item: item[0])
        selection_config = json.dumps(
            {
                "official_only": True,
                "min_lead_minutes": min_lead_minutes,
                "odds_field": "OddsAtPrediction",
                "start_field": "StartUtc",
                "dedup_version": 2,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = json.dumps(
            {"bankroll": format(bankroll, ".2f"), "config": selection_config, "items": rows},
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return fingerprint, tuple(rows), selection_config, bankroll

    def reserve_daily_selection(
        self,
        *,
        destination_hash: str,
        business_date: str,
        predictions: Sequence[Prediction],
        bankroll: Decimal,
        min_lead_minutes: int = 15,
    ) -> Tuple[Optional[DailySelectionReservation], str]:
        """Reserve the first manifest; changed reruns are rejected, not republished."""

        fingerprint, _, _, _ = self._daily_manifest_data(predictions, bankroll, min_lead_minutes)
        now = _now_utc()
        stale_before = (datetime.now(timezone.utc) - self.stale_after).isoformat(timespec="seconds")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            completed = connection.execute(
                """
                SELECT manifest_fingerprint FROM telegram_daily_selection_runs
                WHERE destination_hash=? AND business_date=?
                """,
                (destination_hash, business_date),
            ).fetchone()
            if completed is not None:
                if str(completed["manifest_fingerprint"]) == fingerprint:
                    return None, "already_completed"
                return None, "immutable_conflict"
            current = connection.execute(
                """
                SELECT * FROM telegram_daily_selection_reservations
                WHERE destination_hash=? AND business_date=?
                """,
                (destination_hash, business_date),
            ).fetchone()
            if current is None:
                connection.execute(
                    """
                    INSERT INTO telegram_daily_selection_reservations
                        (destination_hash, business_date, manifest_fingerprint, status, updated_at)
                    VALUES (?, ?, ?, 'SENDING', ?)
                    """,
                    (destination_hash, business_date, fingerprint, now),
                )
                return DailySelectionReservation(fingerprint), "claimed"
            if str(current["manifest_fingerprint"]) != fingerprint:
                return None, "immutable_conflict"
            if current["status"] == "SENT":
                return None, "already_completed"
            if current["status"] == "SENDING" and current["updated_at"] >= stale_before:
                return None, "already_sending"
            connection.execute(
                """
                UPDATE telegram_daily_selection_reservations
                SET status='SENDING', updated_at=?, last_error=NULL
                WHERE destination_hash=? AND business_date=? AND manifest_fingerprint=?
                """,
                (now, destination_hash, business_date, fingerprint),
            )
            return DailySelectionReservation(fingerprint), "claimed"

    def complete_daily_selection(
        self,
        reservation: DailySelectionReservation,
        *,
        destination_hash: str,
        business_date: str,
        predictions: Sequence[Prediction],
        bankroll: Decimal,
        min_lead_minutes: int = 15,
    ) -> None:
        fingerprint, rows, selection_config, bankroll = self._daily_manifest_data(
            predictions, bankroll, min_lead_minutes
        )
        if fingerprint != reservation.manifest_fingerprint:
            raise InputDataError("Daily selection changed after it was reserved")
        selected_at = _now_utc()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT manifest_fingerprint FROM telegram_daily_selection_runs
                WHERE destination_hash=? AND business_date=?
                """,
                (destination_hash, business_date),
            ).fetchone()
            if existing is not None:
                if str(existing["manifest_fingerprint"]) != fingerprint:
                    raise InputDataError("The completed daily selection is immutable")
                return
            active = connection.execute(
                """
                SELECT status, manifest_fingerprint FROM telegram_daily_selection_reservations
                WHERE destination_hash=? AND business_date=?
                """,
                (destination_hash, business_date),
            ).fetchone()
            if active is None or str(active["manifest_fingerprint"]) != fingerprint or active["status"] != "SENDING":
                raise InputDataError("Daily selection reservation is no longer active")
            for item_fingerprint, odds, start_utc, stake in rows:
                connection.execute(
                    """
                    INSERT INTO telegram_daily_selection_items (
                        destination_hash, business_date, prediction_fingerprint,
                        odds_at_prediction, start_utc, stake, selected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (destination_hash, business_date, item_fingerprint, odds, start_utc, stake, selected_at),
                )
            connection.execute(
                """
                INSERT INTO telegram_daily_selection_runs (
                    destination_hash, business_date, selected_at, item_count,
                    bankroll, manifest_fingerprint, selection_config
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    destination_hash,
                    business_date,
                    selected_at,
                    len(rows),
                    format(bankroll, ".2f"),
                    fingerprint,
                    selection_config,
                ),
            )
            connection.execute(
                """
                UPDATE telegram_daily_selection_reservations
                SET status='SENT', updated_at=?, last_error=NULL
                WHERE destination_hash=? AND business_date=? AND manifest_fingerprint=?
                """,
                (selected_at, destination_hash, business_date, fingerprint),
            )

    def fail_daily_selection(
        self,
        reservation: DailySelectionReservation,
        *,
        destination_hash: str,
        business_date: str,
        error: str,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE telegram_daily_selection_reservations
                SET status='FAILED', updated_at=?, last_error=?
                WHERE destination_hash=? AND business_date=?
                  AND manifest_fingerprint=? AND status='SENDING'
                """,
                (
                    _now_utc(),
                    str(error)[:500],
                    destination_hash,
                    business_date,
                    reservation.manifest_fingerprint,
                ),
            )

    def record_daily_selection(
        self,
        *,
        destination_hash: str,
        business_date: str,
        predictions: Sequence[Prediction],
        bankroll: Decimal = DEFAULT_BANKROLL,
        min_lead_minutes: int = 15,
    ) -> None:
        reservation, status = self.reserve_daily_selection(
            destination_hash=destination_hash,
            business_date=business_date,
            predictions=predictions,
            bankroll=bankroll,
            min_lead_minutes=min_lead_minutes,
        )
        if status == "already_completed":
            return
        if reservation is None:
            raise InputDataError(f"Cannot record daily selection: {status}")
        self.complete_daily_selection(
            reservation,
            destination_hash=destination_hash,
            business_date=business_date,
            predictions=predictions,
            bankroll=bankroll,
            min_lead_minutes=min_lead_minutes,
        )

    def load_daily_selection(
        self,
        *,
        business_date: str,
        destination_hash: Optional[str] = None,
    ) -> Optional[RecordedDailyManifest]:
        """Return the posted-pick fingerprints, or None when no daily post completed."""

        with closing(self._connect()) as connection:
            if destination_hash is None:
                runs = connection.execute(
                    """
                    SELECT destination_hash, item_count, bankroll, selection_config
                    FROM telegram_daily_selection_runs
                    WHERE business_date=? ORDER BY selected_at DESC
                    """,
                    (business_date,),
                ).fetchall()
                if not runs:
                    return None
                if len(runs) > 1:
                    raise InputDataError(
                        "Multiple channel manifests exist for this date; use --send so TELEGRAM_CHAT_ID selects one"
                    )
                run = runs[0]
                destination_hash = str(run["destination_hash"])
                expected_count = int(run["item_count"])
            else:
                run = connection.execute(
                    """
                    SELECT item_count, bankroll, selection_config
                    FROM telegram_daily_selection_runs
                    WHERE destination_hash=? AND business_date=?
                    """,
                    (destination_hash, business_date),
                ).fetchone()
                if run is None:
                    return None
                expected_count = int(run["item_count"])
            rows = connection.execute(
                """
                SELECT prediction_fingerprint, odds_at_prediction, start_utc, stake
                FROM telegram_daily_selection_items
                WHERE destination_hash=? AND business_date=?
                """,
                (destination_hash, business_date),
            ).fetchall()
        manifest: Dict[str, RecordedSelection] = {}
        for row in rows:
            odds = _decimal(row["odds_at_prediction"])
            start_utc = _parse_utc_datetime(row["start_utc"])
            stake = _decimal(row["stake"])
            if odds is None or odds < 1 or start_utc is None or stake is None or stake < 0:
                raise InputDataError("Telegram daily-selection audit contains invalid odds/start/stake data")
            manifest[str(row["prediction_fingerprint"])] = RecordedSelection(
                _normalise_odds(odds), start_utc, stake.quantize(MONEY_QUANTUM)
            )
        if len(manifest) != expected_count:
            raise InputDataError("Telegram daily-selection audit is incomplete or inconsistent")
        bankroll = _decimal(run["bankroll"])
        if bankroll is None or bankroll < 0:
            raise InputDataError("Telegram daily-selection audit contains an invalid bankroll")
        bankroll = bankroll.quantize(MONEY_QUANTUM)
        if manifest and sum((item.stake for item in manifest.values()), Decimal("0")) != bankroll:
            raise InputDataError("Telegram daily-selection stakes do not add up to the recorded bankroll")
        return RecordedDailyManifest(bankroll, manifest, str(run["selection_config"] or ""))


class TelegramPublisher:
    def __init__(self, transport: TelegramTransport, audit_store: AuditStore) -> None:
        self.transport = transport
        self.audit_store = audit_store

    def publish(self, messages: Sequence[str], *, publication_kind: str, business_date: str) -> PublishReport:
        sent = 0
        skipped = 0
        in_progress = 0
        reconciliation_required = 0
        failed = 0
        message_ids: List[str] = []
        errors: List[str] = []
        for part_number, message in enumerate(messages, start=1):
            if _telegram_length(message) > TELEGRAM_MESSAGE_LIMIT:
                raise ValueError(f"message part {part_number} exceeds Telegram's 4096-character limit")
            fingerprint = hashlib.sha256(
                f"v1\0{part_number}\0{len(messages)}\0{message}".encode("utf-8")
            ).hexdigest()
            try:
                claim, reason = self.audit_store.claim(
                    destination_hash=self.transport.destination,
                    publication_kind=publication_kind,
                    business_date=business_date,
                    fingerprint=fingerprint,
                    preview=re.sub(r"<[^>]+>", "", message),
                )
            except (OSError, sqlite3.Error, AuditStateError) as exc:
                failed += 1
                errors.append(f"part {part_number}: audit claim failed before send: {type(exc).__name__}")
                continue
            if claim is None:
                if reason == "already_sent":
                    skipped += 1
                elif reason == "reconciliation_required":
                    reconciliation_required += 1
                    errors.append(f"part {part_number}: delivery requires manual reconciliation; not resent")
                else:
                    in_progress += 1
                continue
            try:
                message_id = self.transport.send_message(message, parse_mode="HTML")
            except Exception as exc:
                failed += 1
                error = f"part {part_number}: {type(exc).__name__}: {exc}"
                errors.append(error)
                try:
                    self.audit_store.mark_failed(claim, error)
                except (OSError, sqlite3.Error, AuditStateError) as audit_exc:
                    errors.append(
                        f"part {part_number}: could not audit the pre-delivery failure: {type(audit_exc).__name__}"
                    )
                continue
            try:
                self.audit_store.mark_sent(claim, message_id)
            except (OSError, sqlite3.Error, AuditStateError) as exc:
                reconciliation_required += 1
                error = (
                    f"part {part_number}: Telegram accepted message_id={message_id}, "
                    f"but audit finalization failed ({type(exc).__name__}); automatic resend disabled"
                )
                errors.append(error)
                try:
                    self.audit_store.mark_unknown(claim, error)
                except (OSError, sqlite3.Error, AuditStateError) as quarantine_exc:
                    errors.append(
                        f"part {part_number}: reconciliation quarantine also failed: {type(quarantine_exc).__name__}"
                    )
                message_ids.append(message_id)
                continue
            sent += 1
            message_ids.append(message_id)
        return PublishReport(
            sent,
            skipped,
            in_progress,
            reconciliation_required,
            failed,
            tuple(message_ids),
            tuple(errors),
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview or publish approved sports forecasts in Arabic.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("daily", "summary"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--date", default="today")
        subparser.add_argument("--lock-csv", default="")
        subparser.add_argument("--results-csv", default="")
        subparser.add_argument("--bankroll", default="100.00")
        subparser.add_argument("--include-non-official", action="store_true")
        subparser.add_argument(
            "--min-lead-minutes",
            type=int,
            default=15,
            help="Daily publication lead-time gate; values below 15 are rejected.",
        )
        subparser.add_argument("--max-chars", type=int, default=DEFAULT_MESSAGE_LIMIT)
        subparser.add_argument("--send", action="store_true", help="Actually call Telegram; otherwise print a local preview.")
        subparser.add_argument("--audit-db", default=str(DEFAULT_AUDIT_DB))
        subparser.add_argument("--timeout", type=float, default=15.0)
        subparser.add_argument(
            "--preview-unpublished-summary",
            action="store_true",
            help="Preview all official locked rows when no sent daily-selection manifest is available; never allowed with --send.",
        )
    return parser


def _preview(messages: Sequence[str]) -> None:
    for index, message in enumerate(messages, start=1):
        print(f"\n--- Telegram preview {index}/{len(messages)} ---\n")
        print(message)


def _configure_console_utf8() -> None:
    """Prevent Arabic preview/log crashes on legacy Windows code pages."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_console_utf8()
    args = _build_parser().parse_args(argv)
    try:
        target = _target_date(args.date)
    except ValueError:
        print("Invalid --date value; expected YYYY-MM-DD, today, yesterday, or tomorrow", file=sys.stderr)
        return 2
    day = target.isoformat()
    lock_csv = Path(args.lock_csv) if args.lock_csv else LOCK_DIR / f"forecast_lock_{day}.csv"
    results_csv = Path(args.results_csv) if args.results_csv else REPORTS_DIR / f"prediction_results_{day}.csv"
    try:
        bankroll = Decimal(str(args.bankroll)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        print("Invalid --bankroll value", file=sys.stderr)
        return 2
    if not bankroll.is_finite() or bankroll < 0:
        print("--bankroll cannot be negative", file=sys.stderr)
        return 2
    if args.min_lead_minutes < 15:
        print("--min-lead-minutes cannot be below the mandatory 15-minute gate", file=sys.stderr)
        return 2

    if args.send and args.preview_unpublished_summary:
        print("--preview-unpublished-summary cannot be combined with --send", file=sys.stderr)
        return 2
    if args.send and args.include_non_official:
        print("--include-non-official is preview-only and cannot be combined with --send", file=sys.stderr)
        return 2

    transport: Optional[TelegramBotTransport] = None
    audit_store: Optional[AuditStore] = None
    if args.send:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            print("--send requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID", file=sys.stderr)
            return 2
        transport = TelegramBotTransport(token, chat_id, timeout=args.timeout)
        try:
            audit_store = AuditStore(Path(args.audit_db))
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"Could not open Telegram audit database: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    elif args.command == "summary" and not args.preview_unpublished_summary:
        audit_path = Path(args.audit_db)
        if not audit_path.exists():
            print(
                "No sent daily-selection manifest exists; publish daily first or use "
                "--preview-unpublished-summary for an explicitly non-publication preview.",
                file=sys.stderr,
            )
            return 2
        try:
            audit_store = AuditStore(audit_path)
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"Could not open Telegram audit database: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    recorded_stakes: Optional[List[Decimal]] = None
    try:
        predictions = load_predictions(
            lock_csv,
            business_date=day,
            results_csv=results_csv if args.command == "summary" else None,
            official_only=not args.include_non_official,
        )
        if args.command == "daily":
            selection = select_publishable_predictions(
                predictions,
                as_of=datetime.now(timezone.utc),
                min_lead_minutes=args.min_lead_minutes,
            )
            predictions = list(selection.selected)
            excluded = (
                selection.duplicates_removed
                + selection.missing_start_utc
                + selection.too_close_or_started
                + selection.missing_prediction_odds
                + selection.unsupported_pick_market
            )
            if excluded:
                print(
                    "publication_gate "
                    f"selected={len(predictions)} duplicates={selection.duplicates_removed} "
                    f"missing_start_utc={selection.missing_start_utc} "
                    f"too_close_or_started={selection.too_close_or_started} "
                    f"missing_odds_at_prediction={selection.missing_prediction_odds}",
                    f"unsupported_pick_market={selection.unsupported_pick_market}",
                    file=sys.stderr,
                )
            messages = format_daily_messages(predictions, business_date=day, bankroll=bankroll, max_chars=args.max_chars)
        else:
            deduplicated, duplicate_count = deduplicate_predictions(predictions)
            predictions = list(deduplicated)
            if duplicate_count:
                print(f"summary_gate duplicates_removed={duplicate_count}", file=sys.stderr)
            if not args.preview_unpublished_summary:
                assert audit_store is not None
                manifest = audit_store.load_daily_selection(
                    business_date=day,
                    destination_hash=transport.destination if transport is not None else None,
                )
                if manifest is None:
                    raise InputDataError(
                        "No completed daily publication manifest for this date/channel; refusing an unrelated summary"
                    )
                predictions = list(filter_to_recorded_selection(predictions, manifest.items))
                if bankroll != manifest.bankroll:
                    print(
                        f"summary_bankroll using_recorded={manifest.bankroll:.2f} "
                        f"ignoring_requested={bankroll:.2f}",
                        file=sys.stderr,
                    )
                bankroll = manifest.bankroll
                recorded_stakes = [
                    manifest.items[prediction_fingerprint(item)].stake for item in predictions
                ]
            messages = format_summary_messages(
                predictions,
                business_date=day,
                bankroll=bankroll,
                recorded_stakes=recorded_stakes,
                max_chars=args.max_chars,
            )
    except (InputDataError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not args.send:
        _preview(messages)
        return 0

    assert transport is not None and audit_store is not None
    daily_reservation: Optional[DailySelectionReservation] = None
    if args.command == "daily":
        try:
            daily_reservation, reservation_status = audit_store.reserve_daily_selection(
                destination_hash=transport.destination,
                business_date=day,
                predictions=predictions,
                bankroll=bankroll,
                min_lead_minutes=args.min_lead_minutes,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"Could not reserve immutable daily selection: {exc}", file=sys.stderr)
            return 1
        if reservation_status == "already_completed":
            print(f"publication=daily date={day} duplicate_manifest=1 sent=0")
            return 0
        if reservation_status == "immutable_conflict":
            print(
                "A different daily selection was already reserved/published for this channel/date; "
                "refusing a second post.",
                file=sys.stderr,
            )
            return 2
        if daily_reservation is None:
            print("The same daily selection is currently being published by another process.", file=sys.stderr)
            return 1

    publisher = TelegramPublisher(transport, audit_store)
    try:
        report = publisher.publish(messages, publication_kind=args.command, business_date=day)
    except (OSError, sqlite3.Error, ValueError, AuditStateError) as exc:
        if daily_reservation is not None:
            try:
                audit_store.fail_daily_selection(
                    daily_reservation,
                    destination_hash=transport.destination,
                    business_date=day,
                    error=f"publisher exception: {type(exc).__name__}",
                )
            except (OSError, sqlite3.Error):
                pass
        print(f"Publication failed before a safe completion state: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if (
        args.command == "daily"
        and report.failed == 0
        and report.in_progress == 0
        and report.reconciliation_required == 0
        and report.sent + report.skipped_duplicates == len(messages)
    ):
        assert daily_reservation is not None
        try:
            audit_store.complete_daily_selection(
                daily_reservation,
                destination_hash=transport.destination,
                business_date=day,
                predictions=predictions,
                bankroll=bankroll,
                min_lead_minutes=args.min_lead_minutes,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"Daily post sent but its selection manifest could not be recorded: {exc}", file=sys.stderr)
            return 1
    elif args.command == "daily" and daily_reservation is not None and report.reconciliation_required == 0:
        try:
            audit_store.fail_daily_selection(
                daily_reservation,
                destination_hash=transport.destination,
                business_date=day,
                error="daily message batch did not complete",
            )
        except (OSError, sqlite3.Error):
            pass
    print(
        f"publication={args.command} date={day} sent={report.sent} "
        f"duplicates={report.skipped_duplicates} in_progress={report.in_progress} "
        f"reconciliation_required={report.reconciliation_required} failed={report.failed}"
    )
    for error in report.errors:
        print(error, file=sys.stderr)
    return 1 if report.failed or report.in_progress or report.reconciliation_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
