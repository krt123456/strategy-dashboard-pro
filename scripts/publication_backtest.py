#!/usr/bin/env python3
"""Reproducible audit for the strategy used by the publication channel.

The historical VPS timestamps in ``predictions.created_at`` are naive local
timestamps from Europe/Berlin.  Match start times are UTC.  Comparing those
strings directly creates a two-hour summer-time error, so this script always
uses :class:`zoneinfo.ZoneInfo` before applying the publication lead-time gate.

The report intentionally separates two decisions:

* ``nova_fade_favorite__xbet_linefeed`` remains the legacy active strategy.
* Selecting at most five lowest-odds candidates per day is an operational
  forward-validation policy.  Its retrospective result does not promote it to
  a new strategy.

No database writes are made.  Prefer a local SQLite snapshot: a long read-only
query can still delay a production writer when rollback-journal mode is used.
Unit P&L is recomputed from
``odds_at_prediction``: win = odds - 1, loss = -1.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_DIR / "data" / "betting_journal.db"
DEFAULT_JSON = PROJECT_DIR / "reports" / "publication_backtest.json"
DEFAULT_MD = PROJECT_DIR / "reports" / "publication_backtest.md"

LEGACY_STRATEGY = "nova_fade_favorite__xbet_linefeed"
LEGACY_CREATED_TIMEZONE = "Europe/Berlin"
DEFAULT_LEAD_MINUTES = 15.0
DEFAULT_MAX_PICKS = 5
DEFAULT_BOOTSTRAP_SAMPLES = 5000
DEFAULT_BOOTSTRAP_SEED = 20260820
DEFAULT_PAPER_BANKROLL = 100.0


@dataclass(frozen=True)
class PredictionRow:
    prediction_id: int
    match_date: str
    sport: str
    league: str
    home: str
    away: str
    pick: str
    prediction_source: str
    created_at: str
    start_utc: str
    odds: float | None
    won: int | None
    result_source: str | None
    lead_minutes: float | None

    @property
    def unit_return(self) -> float | None:
        if self.won not in (0, 1) or self.odds is None or self.odds <= 1.0:
            return None
        return self.odds - 1.0 if self.won == 1 else -1.0


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def _pct(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else 100.0 * numerator / denominator


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _lead_minutes(created_at: str, start_utc: str, created_zone: ZoneInfo) -> float | None:
    created = _parse_datetime(created_at)
    start = _parse_datetime(start_utc)
    if created is None or start is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=created_zone)
    else:
        created = created.astimezone(created_zone)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)
    return (start - created.astimezone(timezone.utc)).total_seconds() / 60.0


def _utc_sort_datetime(value: str) -> datetime:
    parsed = _parse_datetime(value)
    if parsed is None:
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _validate_schema(conn: sqlite3.Connection) -> None:
    required = {
        "predictions": {
            "id",
            "created_at",
            "match_date",
            "sport",
            "league",
            "home",
            "away",
            "pick",
            "source",
            "odds_at_prediction",
            "strategy",
            "start_utc",
        },
        "results": {"id", "prediction_id", "pick_won", "result_source", "checked_at"},
    }
    for table, expected in required.items():
        actual = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = expected - actual
        if missing:
            raise RuntimeError(f"{table} is missing required columns: {sorted(missing)}")


def _snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    pred = conn.execute(
        """
        SELECT COUNT(*) AS n, MIN(match_date) AS min_date, MAX(match_date) AS max_date,
               MIN(created_at) AS min_created, MAX(created_at) AS max_created,
               COUNT(DISTINCT strategy) AS used_strategies,
               SUM(real_odds IS NULL) AS null_real_odds,
               SUM(odds_at_prediction IS NULL OR odds_at_prediction <= 1) AS invalid_prediction_odds
        FROM predictions
        """
    ).fetchone()
    res = conn.execute(
        """
        SELECT COUNT(*) AS rows_n, COUNT(DISTINCT prediction_id) AS settled_predictions,
               MIN(checked_at) AS min_checked, MAX(checked_at) AS max_checked
        FROM results
        """
    ).fetchone()
    registry_rows = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    unregistered = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT p.strategy
            FROM predictions p
            LEFT JOIN strategies s ON s.name = p.strategy
            WHERE p.strategy IS NOT NULL AND s.name IS NULL
        )
        """
    ).fetchone()[0]
    duplicate_result_rows = int(res["rows_n"] or 0) - int(res["settled_predictions"] or 0)
    return {
        "predictions": int(pred["n"] or 0),
        "result_rows": int(res["rows_n"] or 0),
        "settled_predictions": int(res["settled_predictions"] or 0),
        "duplicate_result_rows": duplicate_result_rows,
        "match_date_min": pred["min_date"],
        "match_date_max": pred["max_date"],
        "created_at_min": pred["min_created"],
        "created_at_max": pred["max_created"],
        "checked_at_min": res["min_checked"],
        "checked_at_max": res["max_checked"],
        "used_strategy_names": int(pred["used_strategies"] or 0),
        "registered_strategy_rows": int(registry_rows or 0),
        "unregistered_strategy_names": int(unregistered or 0),
        "null_real_odds": int(pred["null_real_odds"] or 0),
        "invalid_odds_at_prediction": int(pred["invalid_prediction_odds"] or 0),
    }


def _settlement_by_date(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH settled AS (
            SELECT prediction_id FROM results GROUP BY prediction_id
        )
        SELECT p.match_date, COUNT(*) AS predictions,
               SUM(CASE WHEN s.prediction_id IS NOT NULL THEN 1 ELSE 0 END) AS settled
        FROM predictions p
        LEFT JOIN settled s ON s.prediction_id = p.id
        GROUP BY p.match_date
        ORDER BY p.match_date
        """
    ).fetchall()
    return [
        {
            "match_date": str(row["match_date"]),
            "predictions": int(row["predictions"] or 0),
            "settled": int(row["settled"] or 0),
            "pending": int(row["predictions"] or 0) - int(row["settled"] or 0),
        }
        for row in rows
    ]


def _auto_complete_cutoff(
    settlement_rows: Sequence[dict[str, Any]], override: str | None = None
) -> tuple[str, dict[str, Any]]:
    if not settlement_rows:
        raise RuntimeError("No prediction dates are available")
    if override:
        datetime.fromisoformat(override)
        return override, {
            "mode": "manual_override",
            "latest_match_date": settlement_rows[-1]["match_date"],
            "earliest_incomplete_date": next(
                (r["match_date"] for r in settlement_rows if r["pending"] > 0), None
            ),
        }

    first_incomplete = next((r for r in settlement_rows if r["pending"] > 0), None)
    if first_incomplete is None:
        cutoff = settlement_rows[-1]["match_date"]
    else:
        complete_prefix = [
            r["match_date"]
            for r in settlement_rows
            if r["match_date"] < first_incomplete["match_date"] and r["pending"] == 0
        ]
        if not complete_prefix:
            raise RuntimeError(
                "The first prediction date is incomplete; no conservative automatic cutoff exists"
            )
        cutoff = max(complete_prefix)
    incomplete = [r for r in settlement_rows if r["pending"] > 0]
    return cutoff, {
        "mode": "last_contiguous_complete_day",
        "latest_match_date": settlement_rows[-1]["match_date"],
        "earliest_incomplete_date": first_incomplete["match_date"] if first_incomplete else None,
        "incomplete_dates": incomplete,
    }


def _load_first_predictions(
    conn: sqlite3.Connection, strategy: str, created_zone: ZoneInfo
) -> tuple[list[PredictionRow], int]:
    raw_count = int(
        conn.execute("SELECT COUNT(*) FROM predictions WHERE strategy = ?", (strategy,)).fetchone()[0]
    )
    rows = conn.execute(
        """
        WITH ranked_predictions AS (
            SELECT p.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY p.strategy, p.match_date, p.sport, p.home, p.away, p.pick
                       ORDER BY p.created_at, p.id
                   ) AS prediction_rank
            FROM predictions p
            WHERE p.strategy = ?
        ), ranked_results AS (
            SELECT r.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.prediction_id ORDER BY r.checked_at, r.id
                   ) AS result_rank
            FROM results r
        )
        SELECT p.id, p.match_date, p.sport, COALESCE(p.league, '') AS league,
               p.home, p.away, p.pick, p.source, p.created_at,
               COALESCE(p.start_utc, '') AS start_utc, p.odds_at_prediction,
               r.pick_won, r.result_source
        FROM ranked_predictions p
        LEFT JOIN ranked_results r
          ON r.prediction_id = p.id AND r.result_rank = 1
        WHERE p.prediction_rank = 1
        ORDER BY p.match_date, p.created_at, p.id
        """,
        (strategy,),
    ).fetchall()

    out: list[PredictionRow] = []
    for row in rows:
        odds: float | None
        try:
            odds = float(row["odds_at_prediction"])
        except (TypeError, ValueError):
            odds = None
        won = int(row["pick_won"]) if row["pick_won"] in (0, 1) else None
        created_at = str(row["created_at"] or "")
        start_utc = str(row["start_utc"] or "")
        out.append(
            PredictionRow(
                prediction_id=int(row["id"]),
                match_date=str(row["match_date"]),
                sport=str(row["sport"] or ""),
                league=str(row["league"] or ""),
                home=str(row["home"] or ""),
                away=str(row["away"] or ""),
                pick=str(row["pick"] or ""),
                prediction_source=str(row["source"] or ""),
                created_at=created_at,
                start_utc=start_utc,
                odds=odds,
                won=won,
                result_source=str(row["result_source"]) if row["result_source"] else None,
                lead_minutes=_lead_minutes(created_at, start_utc, created_zone),
            )
        )
    return out, raw_count


def _timing_summary(rows: Sequence[PredictionRow], lead_minutes: float) -> dict[str, Any]:
    missing_or_invalid_start = sum(row.lead_minutes is None for row in rows)
    post_start = sum(row.lead_minutes is not None and row.lead_minutes < 0 for row in rows)
    short_lead = sum(
        row.lead_minutes is not None and 0 <= row.lead_minutes < lead_minutes for row in rows
    )
    valid_prelead = sum(
        row.lead_minutes is not None
        and row.lead_minutes >= lead_minutes
        and row.odds is not None
        and row.odds > 1.0
        for row in rows
    )
    invalid_odds = sum(row.odds is None or row.odds <= 1.0 for row in rows)
    return {
        "first_prediction_rows": len(rows),
        "missing_or_invalid_start": missing_or_invalid_start,
        "post_start": post_start,
        "post_start_pct": _round(_pct(post_start, len(rows))),
        "lead_between_0_and_threshold": short_lead,
        "lead_threshold_minutes": lead_minutes,
        "eligible_prelead_rows": valid_prelead,
        "invalid_odds": invalid_odds,
    }


def _eligible(rows: Iterable[PredictionRow], lead_minutes: float) -> list[PredictionRow]:
    return [
        row
        for row in rows
        if row.lead_minutes is not None
        and row.lead_minutes >= lead_minutes
        and row.odds is not None
        and row.odds > 1.0
    ]


def _metrics(rows: Sequence[PredictionRow]) -> dict[str, Any]:
    settled = [row for row in rows if row.unit_return is not None]
    returns = [float(row.unit_return) for row in settled if row.unit_return is not None]
    odds = [float(row.odds) for row in settled if row.odds is not None]
    wins = sum(row.won == 1 for row in settled)
    pnl = sum(returns)
    dates = sorted({row.match_date for row in settled})
    return {
        "bets": len(settled),
        "wins": wins,
        "losses": len(settled) - wins,
        "win_rate_pct": _round(_pct(wins, len(settled))),
        "pnl_units": _round(pnl),
        "roi_pct": _round(_pct(pnl, len(settled))),
        "average_odds": _round(sum(odds) / len(odds)) if odds else None,
        "median_odds": _round(median(odds)) if odds else None,
        "min_odds": _round(min(odds)) if odds else None,
        "max_odds": _round(max(odds)) if odds else None,
        "days": len(dates),
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
    }


def _weekly(rows: Sequence[PredictionRow]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int], list[PredictionRow]] = defaultdict(list)
    for row in rows:
        if row.unit_return is None:
            continue
        iso = datetime.fromisoformat(row.match_date).isocalendar()
        groups[(iso.year, iso.week)].append(row)
    out: list[dict[str, Any]] = []
    for (year, week), group in sorted(groups.items()):
        entry = _metrics(group)
        entry["week"] = f"{year}-W{week:02d}"
        out.append(entry)
    return out


def _percentile(sorted_values: Sequence[float], q: float) -> float | None:
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    fraction = position - low
    return sorted_values[low] * (1.0 - fraction) + sorted_values[high] * fraction


def _day_cluster_bootstrap(
    rows: Sequence[PredictionRow], samples: int, seed: int
) -> dict[str, Any]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.unit_return is not None:
            by_day[row.match_date].append(float(row.unit_return))
    clusters = list(by_day.values())
    if not clusters or samples <= 0:
        return {
            "method": "resample_days_with_replacement_preserving_all_bets_within_each_day",
            "samples": samples,
            "seed": seed,
            "ci_95_low_pct": None,
            "ci_95_high_pct": None,
        }
    rng = random.Random(seed)
    bootstrapped: list[float] = []
    for _ in range(samples):
        selected = [clusters[rng.randrange(len(clusters))] for _ in range(len(clusters))]
        count = sum(len(cluster) for cluster in selected)
        bootstrapped.append(100.0 * sum(sum(cluster) for cluster in selected) / count)
    bootstrapped.sort()
    return {
        "method": "resample_days_with_replacement_preserving_all_bets_within_each_day",
        "samples": samples,
        "seed": seed,
        "ci_95_low_pct": _round(_percentile(bootstrapped, 0.025)),
        "ci_95_high_pct": _round(_percentile(bootstrapped, 0.975)),
    }


def _max_lowest_odds_per_day(
    rows: Sequence[PredictionRow], max_picks: int
) -> list[PredictionRow]:
    by_day: dict[str, list[PredictionRow]] = defaultdict(list)
    for row in rows:
        if row.unit_return is not None:
            by_day[row.match_date].append(row)
    selected: list[PredictionRow] = []
    for match_date in sorted(by_day):
        ranked = sorted(
            by_day[match_date],
            key=lambda row: (
                float(row.odds or math.inf),
                _utc_sort_datetime(row.start_utc),
                row.prediction_id,
            ),
        )
        selected.extend(ranked[:max_picks])
    return selected


def _paper_daily_summary(
    rows: Sequence[PredictionRow], bankroll: float
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_day: dict[str, list[PredictionRow]] = defaultdict(list)
    for row in rows:
        if row.unit_return is not None:
            by_day[row.match_date].append(row)
    daily: list[dict[str, Any]] = []
    for match_date in sorted(by_day):
        group = by_day[match_date]
        stake_each = bankroll / len(group)
        profit = stake_each * sum(float(row.unit_return) for row in group if row.unit_return is not None)
        wins = sum(row.won == 1 for row in group)
        daily.append(
            {
                "match_date": match_date,
                "bets": len(group),
                "wins": wins,
                "losses": len(group) - wins,
                "stake_each": _round(stake_each),
                "profit": _round(profit),
                "ending_value": _round(bankroll + profit),
            }
        )
    profits = [float(row["profit"]) for row in daily]
    total_profit = sum(profits)
    summary = {
        "interpretation": "The same paper amount is reset and fully divided each active day; this is not compounding.",
        "paper_amount_per_day": _round(bankroll),
        "days": len(daily),
        "total_turnover": _round(bankroll * len(daily)),
        "total_profit": _round(total_profit),
        "roi_on_turnover_pct": _round(_pct(total_profit, bankroll * len(daily))),
        "winning_days": sum(value > 0 for value in profits),
        "losing_days": sum(value < 0 for value in profits),
        "flat_days": sum(value == 0 for value in profits),
        "worst_day_profit": _round(min(profits)) if profits else None,
        "best_day_profit": _round(max(profits)) if profits else None,
    }
    return summary, daily


def _resolution_summary(rows: Sequence[PredictionRow]) -> dict[str, Any]:
    settled = [row for row in rows if row.unit_return is not None]
    pending = [row for row in rows if row.unit_return is None]
    observed_pnl = sum(float(row.unit_return) for row in settled if row.unit_return is not None)
    upper_pending = sum(float(row.odds or 1.0) - 1.0 for row in pending)
    by_source: dict[str, list[PredictionRow]] = defaultdict(list)
    for row in rows:
        by_source[row.result_source or "PENDING"].append(row)
    sources: list[dict[str, Any]] = []
    for source, group in sorted(by_source.items(), key=lambda item: (-len(item[1]), item[0])):
        source_settled = [row for row in group if row.unit_return is not None]
        source_pnl = sum(
            float(row.unit_return) for row in source_settled if row.unit_return is not None
        )
        source_wins = sum(row.won == 1 for row in source_settled)
        sources.append(
            {
                "result_source": source,
                "rows": len(group),
                "share_pct": _round(_pct(len(group), len(rows))),
                "settled": len(source_settled),
                "wins": source_wins,
                "win_rate_pct": _round(_pct(source_wins, len(source_settled))),
                "roi_pct": _round(_pct(source_pnl, len(source_settled))),
            }
        )
    return {
        "eligible": len(rows),
        "settled": len(settled),
        "pending": len(pending),
        "resolved_pct": _round(_pct(len(settled), len(rows))),
        "observed_roi_pct": _round(_pct(observed_pnl, len(settled))),
        "all_pending_lose_roi_pct": _round(_pct(observed_pnl - len(pending), len(rows))),
        "all_pending_win_roi_pct": _round(_pct(observed_pnl + upper_pending, len(rows))),
        "by_result_source": sources,
    }


def build_report(
    db_path: Path,
    *,
    strategy: str = LEGACY_STRATEGY,
    created_timezone: str = LEGACY_CREATED_TIMEZONE,
    lead_minutes: float = DEFAULT_LEAD_MINUTES,
    max_picks: int = DEFAULT_MAX_PICKS,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    paper_bankroll: float = DEFAULT_PAPER_BANKROLL,
    cutoff_override: str | None = None,
) -> dict[str, Any]:
    try:
        created_zone = ZoneInfo(created_timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"Timezone data for {created_timezone!r} is unavailable. Install the Python "
            "'tzdata' package on platforms without an IANA timezone database."
        ) from exc
    if lead_minutes < 0:
        raise ValueError("lead_minutes must be non-negative")
    if max_picks <= 0:
        raise ValueError("max_picks must be positive")
    if paper_bankroll <= 0:
        raise ValueError("paper_bankroll must be positive")

    # Close the SQLite handle before bootstrap/report rendering.  A read-only
    # connection can still delay a writer when a rollback journal is in use.
    conn = _connect_read_only(db_path)
    try:
        _validate_schema(conn)
        snapshot = _snapshot(conn)
        settlement_rows = _settlement_by_date(conn)
        cutoff, cutoff_info = _auto_complete_cutoff(settlement_rows, cutoff_override)
        first_rows, raw_count = _load_first_predictions(conn, strategy, created_zone)
    finally:
        conn.close()

    eligible_all = _eligible(first_rows, lead_minutes)
    cutoff_rows = [
        row for row in eligible_all if row.match_date <= cutoff and row.unit_return is not None
    ]
    policy_rows = _max_lowest_odds_per_day(cutoff_rows, max_picks)
    base_paper_summary, base_daily = _paper_daily_summary(cutoff_rows, paper_bankroll)
    policy_paper_summary, policy_daily = _paper_daily_summary(policy_rows, paper_bankroll)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": str(db_path.resolve()),
        "methodology": {
            "database_mode": "read_only",
            "execution_safety": "run against a local SQLite snapshot, not the live writer database",
            "dedup_key": ["strategy", "match_date", "sport", "home", "away", "pick"],
            "dedup_choice": "earliest created_at, then lowest prediction id",
            "historical_created_at_timezone": created_timezone,
            "start_utc_timezone": "UTC",
            "minimum_lead_minutes": lead_minutes,
            "unit_pnl": "win = odds_at_prediction - 1; loss = -1",
            "cutoff": cutoff,
            "cutoff_policy": cutoff_info,
            "bootstrap": "deterministic day-cluster resampling",
        },
        "snapshot": snapshot,
        "decision": {
            "strategy": strategy,
            "strategy_status": "legacy_active",
            "strategy_promoted": False,
            "operational_policy": "max_lowest_odds_per_day",
            "operational_policy_status": "forward_validation",
            "operational_policy_promoted": False,
            "max_picks_per_day": max_picks,
            "ranking": ["odds_at_prediction ASC", "start_utc ASC", "prediction_id ASC"],
            "publication_guard": {
                "strategy_must_equal": strategy,
                "require_first_prediction": True,
                "require_valid_start_utc": True,
                "require_minimum_lead_minutes": lead_minutes,
                "require_odds_at_prediction_gt": 1.0,
                "require_strategy_provenance_suffix": "__xbet_linefeed",
            },
            "caution": (
                "The max-picks rule was evaluated retrospectively. Keep it as an operational "
                "paper/forward-validation policy until a predeclared future sample is complete."
            ),
        },
        "data_quality": {
            "raw_strategy_rows": raw_count,
            "first_prediction_rows": len(first_rows),
            "deduplicated_rows_removed": raw_count - len(first_rows),
            "timing": _timing_summary(first_rows, lead_minutes),
            "resolution_all_eligible_dates": _resolution_summary(eligible_all),
        },
        "legacy_strategy_backtest": {
            "complete_cutoff_metrics": _metrics(cutoff_rows),
            "weekly": _weekly(cutoff_rows),
            "day_cluster_bootstrap": _day_cluster_bootstrap(
                cutoff_rows, bootstrap_samples, bootstrap_seed
            ),
            "paper_daily_100": base_paper_summary,
            "paper_daily_rows": base_daily,
        },
        "operational_policy_forward_validation": {
            "policy": "select at most N lowest decimal odds per match_date",
            "max_picks_per_day": max_picks,
            "promotion_status": "not_promoted_retrospective_only",
            "complete_cutoff_metrics": _metrics(policy_rows),
            "weekly": _weekly(policy_rows),
            "day_cluster_bootstrap": _day_cluster_bootstrap(
                policy_rows, bootstrap_samples, bootstrap_seed
            ),
            "paper_daily_100": policy_paper_summary,
            "paper_daily_rows": policy_daily,
        },
    }


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def render_markdown(report: dict[str, Any]) -> str:
    decision = report["decision"]
    method = report["methodology"]
    snapshot = report["snapshot"]
    quality = report["data_quality"]
    legacy = report["legacy_strategy_backtest"]
    policy = report["operational_policy_forward_validation"]
    base = legacy["complete_cutoff_metrics"]
    op = policy["complete_cutoff_metrics"]
    base_ci = legacy["day_cluster_bootstrap"]
    op_ci = policy["day_cluster_bootstrap"]
    resolution = quality["resolution_all_eligible_dates"]

    lines = [
        "# Publication strategy audit",
        "",
        f"Generated: `{report['generated_at_utc']}`",
        "",
        "## Decision",
        "",
        f"- Legacy strategy: `{decision['strategy']}` — **ACTIVE (legacy)**.",
        "- New strategy promotion: **NO**.",
        f"- Operational policy: select at most **{decision['max_picks_per_day']}** lowest-odds "
        "eligible picks per day — **FORWARD VALIDATION ONLY**.",
        f"- Guard: first prediction only; valid raw odds; at least "
        f"{method['minimum_lead_minutes']:.0f} minutes before start after converting "
        f"`created_at` from `{method['historical_created_at_timezone']}` to UTC.",
        "",
        "## Data cutoff and integrity",
        "",
        f"- Conservative cutoff: **{method['cutoff']}** "
        f"(`{method['cutoff_policy']['mode']}`).",
        f"- Database range: {snapshot['match_date_min']} to {snapshot['match_date_max']}; "
        f"{snapshot['predictions']:,} predictions and {snapshot['settled_predictions']:,} "
        "settled prediction ids.",
        f"- Strategy rows: {quality['raw_strategy_rows']:,}; first unique rows: "
        f"{quality['first_prediction_rows']:,}; removed duplicates: "
        f"{quality['deduplicated_rows_removed']:,}.",
        f"- Corrected post-start rows for this strategy: "
        f"{quality['timing']['post_start']:,} "
        f"({_fmt(quality['timing']['post_start_pct'], '%')}).",
        f"- All eligible dates resolution: {resolution['settled']:,}/{resolution['eligible']:,} "
        f"({_fmt(resolution['resolved_pct'], '%')}); pending={resolution['pending']:,}.",
        "",
        "## Complete-cutoff results",
        "",
        "| Set | Bets | W-L | Win rate | Avg odds | Unit P&L | ROI | Day-cluster 95% CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| Legacy all eligible | {base['bets']:,} | {base['wins']:,}-{base['losses']:,} | "
        f"{_fmt(base['win_rate_pct'], '%')} | {_fmt(base['average_odds'])} | "
        f"{_fmt(base['pnl_units'])} | {_fmt(base['roi_pct'], '%')} | "
        f"{_fmt(base_ci['ci_95_low_pct'], '%')} to {_fmt(base_ci['ci_95_high_pct'], '%')} |",
        f"| Max-{policy['max_picks_per_day']} lowest odds (retrospective) | {op['bets']:,} | "
        f"{op['wins']:,}-{op['losses']:,} | {_fmt(op['win_rate_pct'], '%')} | "
        f"{_fmt(op['average_odds'])} | {_fmt(op['pnl_units'])} | {_fmt(op['roi_pct'], '%')} | "
        f"{_fmt(op_ci['ci_95_low_pct'], '%')} to {_fmt(op_ci['ci_95_high_pct'], '%')} |",
        "",
        "The max-picks row is descriptive, not a promoted strategy. It must be judged on a "
        "predeclared future sample.",
        "",
        "## Paper $100-per-day convention",
        "",
        "The same notional $100 is reset each active day and divided equally across that "
        "day's published picks. It is not a claim about a continuously compounded bankroll.",
        "",
        "| Set | Days | Turnover | Net | ROI on turnover | Winning days | Losing days | Worst | Best |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, section in (
        ("Legacy all eligible", legacy["paper_daily_100"]),
        (f"Max-{policy['max_picks_per_day']} policy", policy["paper_daily_100"]),
    ):
        lines.append(
            f"| {label} | {section['days']} | ${_fmt(section['total_turnover'])} | "
            f"${_fmt(section['total_profit'])} | {_fmt(section['roi_on_turnover_pct'], '%')} | "
            f"{section['winning_days']} | {section['losing_days']} | "
            f"${_fmt(section['worst_day_profit'])} | ${_fmt(section['best_day_profit'])} |"
        )

    lines.extend(
        [
            "",
            "## Weekly stability: legacy strategy",
            "",
            "| Week | Dates | Bets | W-L | Unit P&L | ROI |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for week in legacy["weekly"]:
        lines.append(
            f"| {week['week']} | {week['date_min']}–{week['date_max']} | {week['bets']} | "
            f"{week['wins']}-{week['losses']} | {_fmt(week['pnl_units'])} | "
            f"{_fmt(week['roi_pct'], '%')} |"
        )

    lines.extend(
        [
            "",
            "## Result-source and pending-outcome sensitivity",
            "",
            f"- Observed ROI across settled eligible rows: {_fmt(resolution['observed_roi_pct'], '%')}.",
            f"- If every pending row lost: {_fmt(resolution['all_pending_lose_roi_pct'], '%')}.",
            f"- If every pending row won at captured odds: {_fmt(resolution['all_pending_win_roi_pct'], '%')}.",
            "",
            "| Result source | Rows | Share | Settled | Win rate | ROI |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for source in resolution["by_result_source"]:
        lines.append(
            f"| {source['result_source']} | {source['rows']:,} | "
            f"{_fmt(source['share_pct'], '%')} | {source['settled']:,} | "
            f"{_fmt(source['win_rate_pct'], '%')} | {_fmt(source['roi_pct'], '%')} |"
        )
    lines.extend(
        [
            "",
            "## Calculation rule for a daily result post",
            "",
            "For `n` published picks, each notional stake is `$100 / n`. A win at decimal "
            "odds `o` returns profit `stake × (o - 1)`; a loss returns `-stake`. Net daily "
            "profit is the sum, and the displayed ending value is `$100 + net profit`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="Local SQLite snapshot (preferred); opened with mode=ro and PRAGMA query_only",
    )
    parser.add_argument("--strategy", default=LEGACY_STRATEGY)
    parser.add_argument("--created-timezone", default=LEGACY_CREATED_TIMEZONE)
    parser.add_argument("--lead-minutes", type=float, default=DEFAULT_LEAD_MINUTES)
    parser.add_argument("--max-picks", type=int, default=DEFAULT_MAX_PICKS)
    parser.add_argument("--bootstrap-samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument("--paper-bankroll", type=float, default=DEFAULT_PAPER_BANKROLL)
    parser.add_argument("--cutoff", default=None, help="YYYY-MM-DD override; default is automatic")
    parser.add_argument("--json-out", default=str(DEFAULT_JSON))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()

    report = build_report(
        Path(args.db),
        strategy=args.strategy,
        created_timezone=args.created_timezone,
        lead_minutes=args.lead_minutes,
        max_picks=args.max_picks,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
        paper_bankroll=args.paper_bankroll,
        cutoff_override=args.cutoff,
    )
    json_path = Path(args.json_out)
    md_path = Path(args.md_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    print(
        f"Decision: {report['decision']['strategy_status']} / "
        f"operational policy={report['decision']['operational_policy_status']}"
    )
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
