#!/usr/bin/env python3
"""Explain wrong raw forecasts and turn them into strategy repair actions."""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

from watch_sport_lab import canonical_watch_variant, watch_memory_by_variant

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"


def _target_date(value: str) -> date:
    raw = (value or "today").strip().lower()
    today = date.today()
    if raw in {"today", "اليوم"}:
        return today
    if raw in {"tomorrow", "غدا", "غداً"}:
        return today + timedelta(days=1)
    return date.fromisoformat(raw)


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except Exception:
        return None


def _fmt_float(value: Any, digits: int = 1) -> str:
    num = _as_float(value)
    if num is None:
        return ""
    if abs(num - int(num)) < 0.000001:
        return str(int(num))
    return f"{num:.{digits}f}"


def _score(row: Dict[str, Any]) -> str:
    home = _fmt_float(row.get("HomeScore"))
    away = _fmt_float(row.get("AwayScore"))
    return f"{home}-{away}" if home and away else ""


def _memory_by_sport() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in _read_csv(REPORTS_DIR / "prediction_memory_analysis.csv"):
        if str(row.get("SegmentType") or "") == "sport":
            out[str(row.get("Segment") or "").strip().lower()] = row
    return out


def _add(tags: List[str], tag: str) -> None:
    if tag and tag not in tags:
        tags.append(tag)


def _segment_memory_signal(sample: int, accuracy: float | None) -> str:
    if sample <= 0 or accuracy is None:
        return "NO_MEMORY_SIGNAL"
    if sample < 3:
        return "EXPLORATORY_SAMPLE"
    if accuracy < 0.58:
        return "WEAK_SEGMENT_REVIEW"
    if accuracy < 0.70:
        return "CAUTION_SEGMENT"
    return "KEEP_SEGMENT"


def _root_causes(row: Dict[str, Any], memory_signal: str, variant: str) -> List[str]:
    sport = str(row.get("Sport") or "").strip().lower()
    league = str(row.get("League") or "").strip().lower()
    gate = str(row.get("StrategyGate") or "").upper()
    prob = _as_float(row.get("Prob"))
    odds = _as_float(row.get("PickOdds"))
    ev_pct = _as_float(row.get("EVPercent"))
    tags: List[str] = []

    if gate.startswith("WATCH_ONLY"):
        _add(tags, "watch_only_gate_saved_entry")
    if memory_signal == "WEAK_SEGMENT_REVIEW":
        _add(tags, "weak_memory_segment")
    if prob is not None and prob < 0.70:
        _add(tags, "probability_below_70")
    if ev_pct is not None and ev_pct < 0:
        _add(tags, "negative_ev_price")
    if odds is not None and 1.30 <= odds <= 1.70:
        _add(tags, "favorite_upset_odds_band")

    if sport == "baseball":
        _add(tags, "baseball_missing_pitcher_lineup_weather_model")
        if prob is not None and prob < 0.70:
            _add(tags, "baseball_below_repaired_probability_floor")
        if odds is not None and odds >= 1.55:
            _add(tags, "baseball_high_variance_odds_band")
        if "ncaa" in league or "college" in league:
            _add(tags, "college_baseball_midweek_rotation_risk")
    elif sport == "tennis":
        _add(tags, "tennis_missing_player_surface_withdrawal_model")
        if variant == "tennis_doubles":
            _add(tags, "tennis_doubles_pair_chemistry_variance")
            _add(tags, "tennis_doubles_needs_pair_level_memory")
        elif variant == "tennis_qualification":
            _add(tags, "tennis_qualification_form_noise")
    elif sport == "basketball":
        _add(tags, "basketball_needs_roster_rest_league_filter")

    if not tags:
        _add(tags, "model_direction_failed_without_specific_root_cause")
    return tags


def _strategy_patch(row: Dict[str, Any], tags: Iterable[str], variant: str) -> str:
    sport = str(row.get("Sport") or "").strip().lower()
    tag_set = set(tags)
    if sport == "baseball":
        return (
            "Keep baseball lab-only; require confirmed pitchers, lineups, bullpen/rest, weather/park, "
            "MLB/NCAA split backtest, min_prob 70%, min_margin 16%, max_odds 1.70, and 7.5% haircut."
        )
    if sport == "tennis":
        if variant == "tennis_doubles":
            return (
                "Keep tennis doubles deep-lab; separate it from singles memory, require pair-level memory >= 65% over "
                "8+ finished rows, and add pair chemistry/serve-return context before review."
            )
        if variant == "tennis_qualification":
            return "Keep tennis qualification deep-lab until qualification-only memory recovers and player-form context is stronger."
        return "Keep tennis watch-only; add player/surface/withdrawal form gate and reject sub-70% raw favorites until larger memory confirms them."
    if "negative_ev_price" in tag_set:
        return "Keep price gate strict; do not promote rows where live price is below the model target."
    return "Increase context and sample requirements before this segment can influence review ranking."


def _build(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    memory = _memory_by_sport()
    variant_memory = watch_memory_by_variant()
    out: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("PickOutcome") or "").upper() != "WRONG":
            continue
        sport = str(row.get("Sport") or "").strip().lower()
        variant = canonical_watch_variant(sport, row.get("League"))
        memory_row = memory.get(sport, {})
        segment_memory = variant_memory.get((sport, variant), {})
        segment_sample = int(float(segment_memory.get("sample", 0.0) or 0.0))
        segment_accuracy = float(segment_memory.get("accuracy", 0.0) or 0.0) if segment_sample else None
        memory_signal = _segment_memory_signal(segment_sample, segment_accuracy) if segment_sample else str(memory_row.get("Signal") or "NO_MEMORY_SIGNAL")
        tags = _root_causes(row, memory_signal, variant)
        out.append(
            {
                "Rank": row.get("Rank") or "",
                "Sport": row.get("Sport") or "",
                "StrategyVariant": variant,
                "League": row.get("League") or "",
                "Home": row.get("Home") or "",
                "Away": row.get("Away") or "",
                "Pick": row.get("Pick") or "",
                "Score": _score(row),
                "Prob": row.get("Prob") or "",
                "PickOdds": row.get("PickOdds") or "",
                "EVPercent": row.get("EVPercent") or "",
                "Decision": row.get("Decision") or "",
                "ValueVerdict": row.get("ValueVerdict") or "",
                "SegmentMemorySignal": memory_signal,
                "SegmentMemorySample": segment_sample or "",
                "SegmentMemoryAccuracy": "" if segment_accuracy is None else round(segment_accuracy, 6),
                "SportMemorySignal": memory_row.get("Signal") or "",
                "SportMemoryAccuracy": memory_row.get("Accuracy") or "",
                "RootCauseTags": ";".join(tags),
                "StrategyPatch": _strategy_patch(row, tags, variant),
                "ResultSource": row.get("ResultSource") or "",
                "ResultNote": row.get("ResultNote") or "",
            }
        )
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Sport",
        "StrategyVariant",
        "League",
        "Home",
        "Away",
        "Pick",
        "Score",
        "Prob",
        "PickOdds",
        "EVPercent",
        "Decision",
        "ValueVerdict",
        "SegmentMemorySignal",
        "SegmentMemorySample",
        "SegmentMemoryAccuracy",
        "SportMemorySignal",
        "SportMemoryAccuracy",
        "RootCauseTags",
        "StrategyPatch",
        "ResultSource",
        "ResultNote",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], target: date, source: Path, path: Path) -> None:
    tag_counts: Counter[str] = Counter()
    sport_counts: Counter[str] = Counter()
    for row in rows:
        sport_counts[str(row.get("Sport") or "unknown")] += 1
        for tag in str(row.get("RootCauseTags") or "").split(";"):
            if tag:
                tag_counts[tag] += 1

    try:
        source_label = str(source.resolve().relative_to(BASE_DIR))
    except Exception:
        source_label = str(source)
    lines = [
        "# Strategy error review",
        f"- Date: {target.isoformat()}",
        f"- Source: `{source_label}`",
        f"- Wrong finished raw picks reviewed: {len(rows)}",
        "- Purpose: convert failed raw forecasts into stricter strategy rules. This is learning only, not an entry list.",
        "",
        "## Error Concentration",
        *([f"- {sport}: {count}" for sport, count in sport_counts.most_common()] or ["- none: 0"]),
        "",
        "## Root Cause Tags",
        *([f"- {tag}: {count}" for tag, count in tag_counts.most_common()] or ["- none: 0"]),
        "",
        "## Wrong Rows",
        "| # | Sport | Match | Pick | Score | Memory | Root causes | Strategy patch |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {row.get('Score')} | {row.get('StrategyVariant')} {row.get('SegmentMemorySignal')} "
            f"{row.get('SegmentMemoryAccuracy')}/{row.get('SegmentMemorySample')} | "
            f"{row.get('RootCauseTags')} | {row.get('StrategyPatch')} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Repair Rule",
            "- Do not promote a sport because it had isolated correct raw picks.",
            "- A weak memory segment now has to tighten thresholds or stay lab-only.",
            "- Tennis doubles is tracked separately from singles so pair-level variance cannot hide under a healthy tennis average.",
            "- Baseball requires pitcher/lineup/weather and MLB/NCAA split validation before it can affect final review.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build strategy error review from wrong prediction results.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--results-csv", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    source = Path(args.results_csv) if args.results_csv else REPORTS_DIR / f"prediction_results_{target.isoformat()}.csv"
    rows = _read_csv(source)
    reviewed = _build(rows)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"strategy_error_review_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"strategy_error_review_{target.isoformat()}.md"
    _write_csv(reviewed, out_csv)
    _write_md(reviewed, target, source, out_md)
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"wrong_rows_reviewed={len(reviewed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
