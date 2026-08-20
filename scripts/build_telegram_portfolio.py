#!/usr/bin/env python3
"""Build the immutable Telegram portfolio and its settlement snapshot.

This adapter is deliberately narrow.  It publishes only the legacy strategy
that passed the corrected publication backtest, and never mutates the betting
journal.  The daily lock is immutable unless an operator explicitly supplies
``--force``; the results file is an atomic, refreshable snapshot of that lock.

Examples::

    python scripts/build_telegram_portfolio.py daily --date 2026-08-20
    python scripts/build_telegram_portfolio.py results --date 2026-08-20

All naive historical ``created_at`` values are interpreted as Europe/Berlin,
which is how the legacy runner wrote them.  ``start_utc`` is always interpreted
as UTC when its legacy value lacks an offset.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB = BASE_DIR / "data" / "betting_journal.db"
DEFAULT_LINEFEED = BASE_DIR / "data" / "one_xbet_linefeed_snapshot.csv"
DEFAULT_LOCK_DIR = BASE_DIR / "reports" / "locked_forecasts"
DEFAULT_REPORTS_DIR = BASE_DIR / "reports"

LEGACY_STRATEGY = "nova_fade_favorite__xbet_linefeed"
# The strategy suffix is the provenance of the underlying fixture/price feed.
# Expert strategies historically stored their implementation label in the
# generic ``source`` column, so approved 1xBet rows have ``expert_vig`` there.
# Keep that schema detail private and publish the true feed provenance.
DB_STORED_SOURCE = "expert_vig"
XBET_SOURCE = "xbet_linefeed"
MIN_ODDS = Decimal("2.50")
MAX_ODDS = Decimal("5.50")
MIN_ALLOWED_LEAD_MINUTES = 15
DEFAULT_LEAD_MINUTES = 60
MAX_ALLOWED_PICKS = 5

FINAL_WIN_OUTCOMES = {"WIN", "WON", "CORRECT"}
FINAL_LOSS_OUTCOMES = {"LOSS", "LOST", "WRONG"}
FINAL_REFUND_OUTCOMES = {"PUSH", "VOID", "REFUND", "REFUNDED", "CANCELLED", "CANCELED"}


class PortfolioBuildError(RuntimeError):
    """Raised when an input cannot be used without weakening a safety gate."""


def _last_sunday(year: int, month: int, hour: int) -> datetime:
    """Return the last Sunday of *month* at *hour* (naive local time)."""

    if month == 12:
        next_month = datetime(year + 1, 1, 1, hour)
    else:
        next_month = datetime(year, month + 1, 1, hour)
    candidate = next_month - timedelta(days=1)
    return candidate - timedelta(days=(candidate.weekday() + 1) % 7)


class _EuropeBerlinFallback(tzinfo):
    """Small EU-DST fallback for Windows hosts without the ``tzdata`` wheel.

    Linux production hosts use :class:`zoneinfo.ZoneInfo`.  This fallback keeps
    local development and tests correct for the EU rule (last Sunday in March
    through last Sunday in October) instead of silently assuming a fixed offset.
    """

    _STD = timedelta(hours=1)
    _DST = timedelta(hours=1)

    def tzname(self, dt: Optional[datetime]) -> str:
        return "CEST" if self.dst(dt) else "CET"

    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        return self._STD + self.dst(dt)

    def dst(self, dt: Optional[datetime]) -> timedelta:
        if dt is None:
            return timedelta(0)
        naive = dt.replace(tzinfo=None)
        start = _last_sunday(naive.year, 3, 2)
        end = _last_sunday(naive.year, 10, 3)
        one_hour = timedelta(hours=1)
        if start + one_hour <= naive < end - one_hour:
            return self._DST
        if end - one_hour <= naive < end:
            return timedelta(0) if dt.fold else self._DST
        if start <= naive < start + one_hour:
            return self._DST if dt.fold else timedelta(0)
        return timedelta(0)

    def fromutc(self, dt: datetime) -> datetime:
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo is not self")
        year = dt.year
        start = _last_sunday(year, 3, 2).replace(tzinfo=self)
        end = _last_sunday(year, 10, 3).replace(tzinfo=self)
        std_time = dt + self._STD
        dst_time = std_time + self._DST
        one_hour = timedelta(hours=1)
        if end <= dst_time < end + one_hour:
            return std_time.replace(fold=1)
        if std_time < start or dst_time >= end:
            return std_time
        if start <= std_time < end - one_hour:
            return dst_time
        return std_time


try:
    BERLIN_TZ: tzinfo = ZoneInfo("Europe/Berlin")
except ZoneInfoNotFoundError:
    BERLIN_TZ = _EuropeBerlinFallback()


LOCK_FIELDS = [
    "ForecastDate",
    "LockedAt",
    "PredictionId",
    "Rank",
    "Sport",
    "League",
    "Home",
    "Away",
    "Pick",
    "Prob",
    "OddsAtPrediction",
    "OddsCapturedAt",
    "EventId",
    "StartUtc",
    "StartTimeLocal",
    "Source",
    "StrategyGate",
    "OfficialEntry",
    "FinalDecision",
    "ForecastPurpose",
]

RESULT_FIELDS = [
    "ForecastDate",
    "CheckedAt",
    "PredictionId",
    "Sport",
    "League",
    "Home",
    "Away",
    "Pick",
    "EventId",
    "StartUtc",
    "HomeScore",
    "AwayScore",
    "PickOutcome",
    "EntryOutcome",
    "Outcome",
    "ResultStatus",
    "ResultSource",
    "SettlementNote",
]


@dataclass(frozen=True)
class DailyBuildReport:
    output: Path
    selected: int
    eligible_before_limit: int
    duplicates_removed: int


@dataclass(frozen=True)
class ResultsBuildReport:
    output: Path
    total: int
    finished: int
    pending: int
    conflicts: int


@dataclass(frozen=True)
class _Candidate:
    prediction_id: int
    created_utc: Optional[datetime]
    start_utc: datetime
    sport: str
    league: str
    home: str
    away: str
    pick: str
    probability: Optional[Decimal]
    odds: Optional[Decimal]
    event_id: str


def _normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())


def _strict_date(value: str) -> date:
    raw = str(value or "").strip()
    if raw.casefold() in {"today", "اليوم"}:
        return datetime.now(BERLIN_TZ).date()
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise PortfolioBuildError("date must be today or a complete ISO date (YYYY-MM-DD)") from exc
    if raw != parsed.isoformat():
        raise PortfolioBuildError("date must be today or a complete ISO date (YYYY-MM-DD)")
    return parsed


def _parse_iso(value: Any, *, naive_tz: tzinfo) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if "T" not in raw and " " not in raw:
        # A date-only value is not a safe substitute for an event timestamp.
        return None
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        try:
            return parsed.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            return None

    # Fail closed for a nonexistent or ambiguous Europe/Berlin wall time.  The
    # two folds round-trip to one instant for ordinary local timestamps.
    candidates: List[datetime] = []
    for fold in (0, 1):
        local = parsed.replace(tzinfo=naive_tz, fold=fold)
        utc_value = local.astimezone(timezone.utc)
        round_trip = utc_value.astimezone(naive_tz).replace(tzinfo=None)
        if round_trip == parsed:
            candidates.append(utc_value)
    unique = {item.isoformat(): item for item in candidates}
    if len(unique) != 1:
        return None
    return next(iter(unique.values()))


def _parse_start_utc(value: Any) -> Optional[datetime]:
    return _parse_iso(value, naive_tz=timezone.utc)


def _parse_created_utc(value: Any) -> Optional[datetime]:
    return _parse_iso(value, naive_tz=BERLIN_TZ)


def _decimal(value: Any) -> Optional[Decimal]:
    raw = str(value if value is not None else "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _decimal_text(value: Optional[Decimal]) -> str:
    if value is None:
        return ""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        raise PortfolioBuildError(f"missing or empty CSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [str(item or "").strip() for item in (reader.fieldnames or [])]
        if not headers:
            raise PortfolioBuildError(f"CSV has no header: {path}")
        return headers, list(reader)


def _atomic_write_csv(
    path: Path,
    fields: Sequence[str],
    rows: Iterable[Mapping[str, Any]],
    *,
    replace: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8-sig",
        newline="",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise PortfolioBuildError(
                    f"daily lock already exists and is immutable: {path}; use --force only for an audited correction"
                ) from exc
            temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.exists() or not path.is_file():
        raise PortfolioBuildError(f"SQLite database not found: {path}")
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise PortfolioBuildError(f"cannot open SQLite database read-only: {path}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _require_columns(connection: sqlite3.Connection, table: str, required: Sequence[str]) -> set[str]:
    try:
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error as exc:
        raise PortfolioBuildError(f"cannot inspect SQLite table: {table}") from exc
    missing = sorted(set(required) - columns)
    if missing:
        raise PortfolioBuildError(f"SQLite table {table} is missing columns: {', '.join(missing)}")
    return columns


def _linefeed_index(path: Optional[Path]) -> Dict[Tuple[str, str, str, str], str]:
    """Index unique 1xBet event IDs by sport, teams, and UTC start."""

    if path is None or not path.exists() or path.stat().st_size == 0:
        return {}
    headers, rows = _read_csv(path)
    required = {"Sport", "Home", "Away", "StartUtc", "EventId"}
    if not required.issubset(headers):
        return {}
    gathered: Dict[Tuple[str, str, str, str], set[str]] = {}
    for row in rows:
        source = _normalise(row.get("Source"))
        if source and source not in {"1xbetpubliclinefeed", "xbetlinefeed"}:
            continue
        start = _parse_start_utc(row.get("StartUtc"))
        event_id = str(row.get("EventId") or "").strip()
        if start is None or not event_id:
            continue
        key = (
            _normalise(row.get("Sport")),
            _normalise(row.get("Home")),
            _normalise(row.get("Away")),
            _utc_text(start),
        )
        if all(key[:3]):
            gathered.setdefault(key, set()).add(event_id)
    return {key: next(iter(values)) for key, values in gathered.items() if len(values) == 1}


def _event_identity(candidate: _Candidate) -> Tuple[str, ...]:
    if candidate.event_id:
        match = (f"event:{_normalise(candidate.event_id)}",)
    else:
        match = (
            _normalise(candidate.home),
            _normalise(candidate.away),
            _utc_text(candidate.start_utc),
        )
    return (_normalise(candidate.sport), *match, _normalise(candidate.pick))


def _candidate_from_row(row: sqlite3.Row, event_ids: Mapping[Tuple[str, str, str, str], str]) -> Optional[_Candidate]:
    start = _parse_start_utc(row["start_utc"])
    created = _parse_created_utc(row["created_at"])
    odds = _decimal(row["odds_at_prediction"])
    probability = _decimal(row["model_prob"])
    home = str(row["home"] or "").strip()
    away = str(row["away"] or "").strip()
    pick = str(row["pick"] or "").strip()
    sport = str(row["sport"] or "").strip()
    if start is None or not all((home, away, pick, sport)):
        return None
    if probability is not None and not Decimal("0") <= probability <= Decimal("1"):
        probability = None
    event_key = (_normalise(sport), _normalise(home), _normalise(away), _utc_text(start))
    return _Candidate(
        prediction_id=int(row["id"]),
        created_utc=created,
        start_utc=start,
        sport=sport,
        league=str(row["league"] or "").strip(),
        home=home,
        away=away,
        pick=pick,
        probability=probability,
        odds=odds,
        event_id=str(event_ids.get(event_key) or ""),
    )


def select_daily_candidates(
    db_path: Path,
    target: date,
    *,
    linefeed_csv: Optional[Path] = None,
    min_lead_minutes: int = DEFAULT_LEAD_MINUTES,
    max_picks: int = MAX_ALLOWED_PICKS,
    now_utc: Optional[datetime] = None,
) -> Tuple[List[_Candidate], int, int]:
    """Return eligible first-seen legacy picks, sorted by lowest odds.

    Both the original prediction lead and the publication-time lead must meet
    ``min_lead_minutes``.  This prevents a once-valid prediction from being
    published after the event has started or become imminent.
    """

    if min_lead_minutes < MIN_ALLOWED_LEAD_MINUTES:
        raise PortfolioBuildError(f"min-lead-minutes cannot be below {MIN_ALLOWED_LEAD_MINUTES}")
    if not 1 <= max_picks <= MAX_ALLOWED_PICKS:
        raise PortfolioBuildError(f"max-picks must be between 1 and {MAX_ALLOWED_PICKS}")
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise PortfolioBuildError("now_utc must be timezone-aware")
    now = now.astimezone(timezone.utc)
    event_ids = _linefeed_index(linefeed_csv)

    connection = _readonly_connection(db_path)
    try:
        _require_columns(
            connection,
            "predictions",
            [
                "id", "created_at", "sport", "league", "home", "away", "pick",
                "source", "model_prob", "odds_at_prediction", "strategy", "start_utc",
            ],
        )
        rows = connection.execute(
            """
            SELECT id, created_at, sport, league, home, away, pick, model_prob,
                   odds_at_prediction, start_utc
              FROM predictions
             WHERE strategy = ? AND source = ?
             ORDER BY id ASC
            """,
            (LEGACY_STRATEGY, DB_STORED_SOURCE),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PortfolioBuildError("failed to read legacy predictions") from exc
    finally:
        connection.close()

    # Group before validating chronology/price.  Otherwise a malformed or
    # out-of-range first record could be silently replaced by a later record,
    # violating the first-prediction lock.
    parsed = [candidate for row in rows if (candidate := _candidate_from_row(row, event_ids)) is not None]
    parsed = [candidate for candidate in parsed if candidate.start_utc.astimezone(BERLIN_TZ).date() == target]
    grouped: Dict[Tuple[str, ...], List[_Candidate]] = {}
    for candidate in parsed:
        grouped.setdefault(_event_identity(candidate), []).append(candidate)
    duplicates_removed = sum(max(0, len(items) - 1) for items in grouped.values())

    first_by_identity: Dict[Tuple[str, ...], _Candidate] = {}
    for identity, items in grouped.items():
        if any(item.created_utc is None for item in items):
            continue
        first_by_identity[identity] = min(
            items,
            key=lambda item: (item.created_utc or datetime.max.replace(tzinfo=timezone.utc), item.prediction_id),
        )

    lead = timedelta(minutes=min_lead_minutes)
    eligible = [
        item
        for item in first_by_identity.values()
        if item.odds is not None
        and item.created_utc is not None
        and _normalise(item.pick) in {_normalise(item.home), _normalise(item.away)}
        and MIN_ODDS <= item.odds <= MAX_ODDS
        and item.start_utc - item.created_utc >= lead
        and item.start_utc - now >= lead
    ]
    eligible.sort(
        key=lambda item: (
            item.odds or Decimal("Infinity"),
            item.start_utc,
            _normalise(item.sport),
            _normalise(item.home),
            _normalise(item.away),
            item.prediction_id,
        )
    )
    eligible_before_limit = len(eligible)
    return eligible[:max_picks], eligible_before_limit, duplicates_removed


def _lock_rows(candidates: Sequence[_Candidate], target: date, locked_at: datetime) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for rank, candidate in enumerate(candidates, start=1):
        rows.append(
            {
                "ForecastDate": target.isoformat(),
                "LockedAt": _utc_text(locked_at),
                "PredictionId": str(candidate.prediction_id),
                "Rank": str(rank),
                "Sport": candidate.sport,
                "League": candidate.league,
                "Home": candidate.home,
                "Away": candidate.away,
                "Pick": candidate.pick,
                "Prob": _decimal_text(candidate.probability),
                "OddsAtPrediction": _decimal_text(candidate.odds),
                "OddsCapturedAt": _utc_text(candidate.created_utc),
                "EventId": candidate.event_id,
                "StartUtc": _utc_text(candidate.start_utc),
                "StartTimeLocal": candidate.start_utc.astimezone(BERLIN_TZ).isoformat(timespec="minutes"),
                "Source": XBET_SOURCE,
                "StrategyGate": LEGACY_STRATEGY,
                "OfficialEntry": "yes",
                "FinalDecision": "APPROVED_FOR_PUBLICATION",
                "ForecastPurpose": "HYPOTHETICAL_PAPER_SIMULATION",
            }
        )
    return rows


def build_daily_lock(
    db_path: Path,
    target: date,
    output: Path,
    *,
    linefeed_csv: Optional[Path] = None,
    min_lead_minutes: int = DEFAULT_LEAD_MINUTES,
    max_picks: int = MAX_ALLOWED_PICKS,
    force: bool = False,
    now_utc: Optional[datetime] = None,
) -> DailyBuildReport:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise PortfolioBuildError("now_utc must be timezone-aware")
    now = now.astimezone(timezone.utc)
    candidates, eligible_before_limit, duplicates_removed = select_daily_candidates(
        db_path,
        target,
        linefeed_csv=linefeed_csv,
        min_lead_minutes=min_lead_minutes,
        max_picks=max_picks,
        now_utc=now,
    )
    _atomic_write_csv(output, LOCK_FIELDS, _lock_rows(candidates, target, now), replace=force)
    return DailyBuildReport(output, len(candidates), eligible_before_limit, duplicates_removed)


def _lock_identity_matches(row: Mapping[str, str], prediction: sqlite3.Row) -> bool:
    if str(prediction["strategy"] or "") != LEGACY_STRATEGY or str(prediction["source"] or "") != DB_STORED_SOURCE:
        return False
    if any(
        _normalise(row.get(field)) != _normalise(prediction[column])
        for field, column in (("Sport", "sport"), ("Home", "home"), ("Away", "away"), ("Pick", "pick"))
    ):
        return False
    lock_start = _parse_start_utc(row.get("StartUtc"))
    db_start = _parse_start_utc(prediction["start_utc"])
    lock_odds = _decimal(row.get("OddsAtPrediction"))
    db_odds = _decimal(prediction["odds_at_prediction"])
    return bool(
        lock_start is not None
        and db_start is not None
        and lock_start == db_start
        and lock_odds is not None
        and db_odds is not None
        and lock_odds == db_odds
    )


def _integer_score(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        decimal_value = Decimal(str(value))
        if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
            return None
        number = int(decimal_value)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _classify_result(
    rows: Sequence[sqlite3.Row], *, home: str, away: str, pick: str
) -> Tuple[str, Optional[sqlite3.Row], str]:
    """Classify a journal result, rejecting incomplete or conflicting rows."""

    final: List[Tuple[Tuple[Any, ...], sqlite3.Row, str]] = []
    for row in rows:
        outcome = str(row["outcome"] or "").strip().upper()
        checked = str(row["checked_at"] or "").strip()
        result_source = str(row["result_source"] or "").strip()
        if _parse_created_utc(checked) is None or not result_source:
            continue
        if outcome in FINAL_REFUND_OUTCOMES:
            final.append((("REFUND",), row, "REFUND"))
            continue
        if outcome not in FINAL_WIN_OUTCOMES | FINAL_LOSS_OUTCOMES:
            continue
        home_score = _integer_score(row["home_score"])
        away_score = _integer_score(row["away_score"])
        if home_score is None or away_score is None or row["pick_won"] not in (0, 1):
            continue
        normalised_pick = _normalise(pick)
        if normalised_pick == _normalise(home):
            score_says_won = home_score > away_score
        elif normalised_pick == _normalise(away):
            score_says_won = away_score > home_score
        else:
            continue
        if bool(row["pick_won"]) != score_says_won:
            continue
        category = "WIN" if int(row["pick_won"]) == 1 else "LOSS"
        if (outcome in FINAL_WIN_OUTCOMES) != (category == "WIN"):
            continue
        final.append(((category, home_score, away_score), row, category))

    signatures = {item[0] for item in final}
    if len(signatures) > 1:
        return "PENDING", None, "conflicting final result rows"
    if not final:
        return "PENDING", None, "no complete final result"
    # Equivalent duplicate records are harmless; preserve the most recent one.
    chosen = max(final, key=lambda item: (str(item[1]["checked_at"] or ""), int(item[1]["result_id"])))
    return chosen[2], chosen[1], ""


def _pending_result_row(lock: Mapping[str, str], target: date, note: str) -> Dict[str, str]:
    return {
        "ForecastDate": target.isoformat(),
        "PredictionId": str(lock.get("PredictionId") or "").strip(),
        "Sport": str(lock.get("Sport") or "").strip(),
        "League": str(lock.get("League") or "").strip(),
        "Home": str(lock.get("Home") or "").strip(),
        "Away": str(lock.get("Away") or "").strip(),
        "Pick": str(lock.get("Pick") or "").strip(),
        "EventId": str(lock.get("EventId") or "").strip(),
        "StartUtc": str(lock.get("StartUtc") or "").strip(),
        "PickOutcome": "PENDING",
        "EntryOutcome": "PENDING",
        "Outcome": "PENDING",
        "ResultStatus": "PENDING",
        "SettlementNote": note,
    }


def build_results_csv(db_path: Path, target: date, lock_csv: Path, output: Path) -> ResultsBuildReport:
    headers, lock_rows = _read_csv(lock_csv)
    required = {
        "ForecastDate", "PredictionId", "Sport", "Home", "Away", "Pick",
        "OddsAtPrediction", "StartUtc", "OfficialEntry", "StrategyGate", "Source",
    }
    missing = sorted(required - set(headers))
    if missing:
        raise PortfolioBuildError(f"daily lock is missing columns: {', '.join(missing)}")
    for number, row in enumerate(lock_rows, start=2):
        if str(row.get("ForecastDate") or "").strip() != target.isoformat():
            raise PortfolioBuildError(f"daily lock row {number} belongs to another date")
        if str(row.get("OfficialEntry") or "").strip().casefold() not in {"yes", "true", "1"}:
            raise PortfolioBuildError(f"daily lock row {number} is not an official entry")
        if str(row.get("StrategyGate") or "").strip() != LEGACY_STRATEGY:
            raise PortfolioBuildError(f"daily lock row {number} uses an unapproved strategy")
        if str(row.get("Source") or "").strip() != XBET_SOURCE:
            raise PortfolioBuildError(f"daily lock row {number} uses a non-1xBet source")
        start = _parse_start_utc(row.get("StartUtc"))
        odds = _decimal(row.get("OddsAtPrediction"))
        if start is None or start.astimezone(BERLIN_TZ).date() != target:
            raise PortfolioBuildError(f"daily lock row {number} has an invalid target-date start")
        if odds is None or not MIN_ODDS <= odds <= MAX_ODDS:
            raise PortfolioBuildError(f"daily lock row {number} has odds outside the approved range")
    if len(lock_rows) > MAX_ALLOWED_PICKS:
        raise PortfolioBuildError(f"daily lock exceeds the {MAX_ALLOWED_PICKS}-pick publication cap")

    ids: List[int] = []
    for row in lock_rows:
        raw = str(row.get("PredictionId") or "").strip()
        if raw.isdigit() and int(raw) > 0:
            ids.append(int(raw))
    if len(ids) != len(lock_rows) or len(set(ids)) != len(ids):
        raise PortfolioBuildError("daily lock needs one unique positive PredictionId per row")

    predictions: Dict[int, sqlite3.Row] = {}
    result_rows: Dict[int, List[sqlite3.Row]] = {}
    connection = _readonly_connection(db_path)
    try:
        _require_columns(
            connection,
            "predictions",
            [
                "id", "sport", "league", "home", "away", "pick", "source",
                "strategy", "odds_at_prediction", "start_utc",
            ],
        )
        _require_columns(
            connection,
            "results",
            ["id", "prediction_id", "checked_at", "home_score", "away_score", "pick_won", "outcome", "result_source"],
        )
        if ids:
            placeholders = ",".join("?" for _ in ids)
            for row in connection.execute(
                f"""
                SELECT id, sport, league, home, away, pick, source, strategy,
                       odds_at_prediction, start_utc
                  FROM predictions WHERE id IN ({placeholders})
                """,
                ids,
            ):
                predictions[int(row["id"])] = row
            for row in connection.execute(
                f"""
                SELECT id AS result_id, prediction_id, checked_at, home_score,
                       away_score, pick_won, outcome, result_source
                  FROM results WHERE prediction_id IN ({placeholders})
                 ORDER BY id ASC
                """,
                ids,
            ):
                result_rows.setdefault(int(row["prediction_id"]), []).append(row)
    except sqlite3.Error as exc:
        raise PortfolioBuildError("failed to read settlement records") from exc
    finally:
        connection.close()

    output_rows: List[Dict[str, str]] = []
    finished = 0
    conflicts = 0
    for lock in lock_rows:
        pending = _pending_result_row(lock, target, "")
        raw_id = str(lock.get("PredictionId") or "").strip()
        if not raw_id.isdigit() or int(raw_id) <= 0:
            pending["SettlementNote"] = "missing immutable prediction id"
            output_rows.append(pending)
            continue
        prediction_id = int(raw_id)
        prediction = predictions.get(prediction_id)
        if prediction is None or not _lock_identity_matches(lock, prediction):
            pending["SettlementNote"] = "lock does not match its journal prediction"
            output_rows.append(pending)
            continue
        classification, result, note = _classify_result(
            result_rows.get(prediction_id, []),
            home=str(prediction["home"] or ""),
            away=str(prediction["away"] or ""),
            pick=str(prediction["pick"] or ""),
        )
        if classification == "PENDING" or result is None:
            pending["SettlementNote"] = note
            if note.startswith("conflicting"):
                conflicts += 1
            output_rows.append(pending)
            continue

        final_row = dict(pending)
        final_row.update(
            {
                "CheckedAt": str(result["checked_at"] or "").strip(),
                "HomeScore": "" if result["home_score"] is None else str(result["home_score"]),
                "AwayScore": "" if result["away_score"] is None else str(result["away_score"]),
                "PickOutcome": "CORRECT" if classification == "WIN" else "WRONG" if classification == "LOSS" else "VOID",
                "EntryOutcome": "CORRECT" if classification == "WIN" else "WRONG" if classification == "LOSS" else "VOID",
                "Outcome": "WON" if classification == "WIN" else "LOST" if classification == "LOSS" else "VOID",
                "ResultStatus": "FINISHED" if classification in {"WIN", "LOSS"} else "VOID",
                "ResultSource": str(result["result_source"] or "").strip(),
                "SettlementNote": "",
            }
        )
        output_rows.append(final_row)
        finished += 1

    _atomic_write_csv(output, RESULT_FIELDS, output_rows, replace=True)
    total = len(output_rows)
    return ResultsBuildReport(output, total, finished, total - finished, conflicts)


def _daily_default(target: date) -> Path:
    return DEFAULT_LOCK_DIR / f"forecast_lock_{target.isoformat()}.csv"


def _results_default(target: date) -> Path:
    return DEFAULT_REPORTS_DIR / f"prediction_results_{target.isoformat()}.csv"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a safe Telegram publication portfolio from the journal.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    daily = subparsers.add_parser("daily", help="create the immutable daily lock")
    daily.add_argument("--date", default="today")
    daily.add_argument("--db", type=Path, default=DEFAULT_DB)
    daily.add_argument("--linefeed", type=Path, default=DEFAULT_LINEFEED)
    daily.add_argument("--out", type=Path, default=None)
    daily.add_argument("--min-lead-minutes", type=int, default=DEFAULT_LEAD_MINUTES)
    daily.add_argument("--max-picks", type=int, default=MAX_ALLOWED_PICKS)
    daily.add_argument("--force", action="store_true", help="replace an existing lock (audited correction only)")

    results = subparsers.add_parser("results", help="refresh settlements for the immutable daily lock")
    results.add_argument("--date", default="today")
    results.add_argument("--db", type=Path, default=DEFAULT_DB)
    results.add_argument("--lock", type=Path, default=None)
    results.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        target = _strict_date(args.date)
        if args.mode == "daily":
            report = build_daily_lock(
                args.db,
                target,
                args.out or _daily_default(target),
                linefeed_csv=args.linefeed,
                min_lead_minutes=args.min_lead_minutes,
                max_picks=args.max_picks,
                force=args.force,
            )
            print(
                f"daily lock: selected={report.selected} eligible={report.eligible_before_limit} "
                f"duplicates_removed={report.duplicates_removed} output={report.output}"
            )
        else:
            report = build_results_csv(
                args.db,
                target,
                args.lock or _daily_default(target),
                args.out or _results_default(target),
            )
            print(
                f"results: total={report.total} finished={report.finished} pending={report.pending} "
                f"conflicts={report.conflicts} output={report.output}"
            )
    except PortfolioBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
