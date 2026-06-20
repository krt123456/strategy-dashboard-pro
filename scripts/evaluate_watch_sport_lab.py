#!/usr/bin/env python3
"""Replay historical public-watch rows against the current lab calibration."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List

from sports_strategy_profiles import get_profile, normalize_sport_key
from watch_sport_lab import evaluate_public_watch_candidate

BASE_DIR = Path(__file__).resolve().parents[1]
MEMORY_CSV = BASE_DIR / "data" / "prediction_result_memory.csv"
REPORTS_DIR = BASE_DIR / "reports"


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _finished_public_watch_rows() -> List[Dict[str, Any]]:
    if not MEMORY_CSV.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with MEMORY_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("ProbabilitySource") or "") != "public_market_watch_strategy":
                continue
            if str(row.get("PickOutcome") or "") not in {"CORRECT", "WRONG"}:
                continue
            rows.append(row)
    return rows


def _replay(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        sport = normalize_sport_key(row.get("Sport"))
        profile = get_profile(sport) or {}
        stored_prob = _as_float(row.get("Prob"))
        if stored_prob is None:
            continue
        haircut = _as_float(profile.get("haircut")) or 0.0
        replay = evaluate_public_watch_candidate(
            sport=sport,
            league=row.get("League"),
            base_prob=min(0.999, stored_prob + haircut),
            odds=_as_float(row.get("PickOdds")),
            margin=None,
        )
        out.append(
            {
                "Sport": sport,
                "League": row.get("League") or "",
                "Pick": row.get("Pick") or "",
                "StoredProb": round(stored_prob, 6),
                "PickOdds": row.get("PickOdds") or "",
                "PickOutcome": row.get("PickOutcome") or "",
                "StrategyVariant": replay["strategy_variant"],
                "StrategyVariantLabel": replay["strategy_variant_label"],
                "LabTier": replay["lab_tier"],
                "IncludeAfterCalibration": "yes" if replay["include"] else "no",
                "CalibratedProb": replay["calibrated_prob"],
                "DynamicMinProb": replay["dynamic_min_prob"],
                "ReliabilityScore": replay["reliability_score"],
                "MemorySample": replay["memory_sample"],
                "MemoryAccuracy": replay["memory_accuracy"],
                "LabNotes": replay["notes"],
            }
        )
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Sport",
        "League",
        "Pick",
        "StoredProb",
        "PickOdds",
        "PickOutcome",
        "StrategyVariant",
        "StrategyVariantLabel",
        "LabTier",
        "IncludeAfterCalibration",
        "CalibratedProb",
        "DynamicMinProb",
        "ReliabilityScore",
        "MemorySample",
        "MemoryAccuracy",
        "LabNotes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _accuracy(rows: Iterable[Dict[str, Any]]) -> tuple[int, int, float]:
    rows = list(rows)
    total = len(rows)
    correct = sum(1 for row in rows if str(row.get("PickOutcome") or "") == "CORRECT")
    acc = round((correct / total) * 100.0, 2) if total else 0.0
    return total, correct, acc


def _write_md(rows: List[Dict[str, Any]], path: Path) -> None:
    baseline_total, baseline_correct, baseline_acc = _accuracy(rows)
    selected = [row for row in rows if row.get("IncludeAfterCalibration") == "yes"]
    selected_total, selected_correct, selected_acc = _accuracy(selected)
    by_variant: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_variant.setdefault(str(row.get("StrategyVariantLabel") or "UNKNOWN"), []).append(row)
    by_sport: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_sport.setdefault(str(row.get("Sport") or "unknown"), []).append(row)
    excluded_counts = Counter(str(row.get("StrategyVariantLabel") or "UNKNOWN") for row in rows if row.get("IncludeAfterCalibration") != "yes")

    lines = [
        "# Watch sport lab validation",
        f"- Run date: {date.today().isoformat()}",
        f"- Historical public-watch rows replayed: {baseline_total}",
        f"- Baseline accuracy: {baseline_correct}/{baseline_total} = {baseline_acc:.2f}%",
        f"- After calibration kept: {selected_total}",
        f"- Accuracy after calibration: {selected_correct}/{selected_total} = {selected_acc:.2f}%",
        "- Interpretation: this validates the lab split only on already-finished public-watch history. It is not a guarantee for future rows.",
        "",
        "## By sport",
    ]
    for sport, sport_rows in sorted(by_sport.items()):
        total, correct, acc = _accuracy(sport_rows)
        kept_total, kept_correct, kept_acc = _accuracy([row for row in sport_rows if row.get("IncludeAfterCalibration") == "yes"])
        lines.append(f"- {sport}: baseline {correct}/{total} = {acc:.2f}% | kept {kept_correct}/{kept_total} = {kept_acc:.2f}%")
    lines.extend(
        [
            "",
            "## By variant",
        ]
    )
    for variant, variant_rows in sorted(by_variant.items()):
        total, correct, acc = _accuracy(variant_rows)
        kept_total, kept_correct, kept_acc = _accuracy([row for row in variant_rows if row.get("IncludeAfterCalibration") == "yes"])
        lines.append(f"- {variant}: baseline {correct}/{total} = {acc:.2f}% | kept {kept_correct}/{kept_total} = {kept_acc:.2f}%")
    lines.extend(
        [
            "",
            "## Main exclusions",
            *([f"- {variant}: {count}" for variant, count in excluded_counts.most_common()] or ["- none: 0"]),
            "",
            "## Kept rows",
            "| Sport | League | Pick | Odds | Variant | Tier | Outcome | Reliability |",
            "| --- | --- | --- | ---: | --- | --- | --- | ---: |",
        ]
    )
    for row in selected[:40]:
        lines.append(
            f"| {row.get('Sport')} | {row.get('League')} | {row.get('Pick')} | {row.get('PickOdds')} | "
            f"{row.get('StrategyVariantLabel')} | {row.get('LabTier')} | {row.get('PickOutcome')} | {row.get('ReliabilityScore')} |"
        )
    if not selected:
        lines.append("| - | - | - | - | - | - | - | - |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay public-watch history against the watch-sport lab calibration.")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = _replay(_finished_public_watch_rows())
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / "watch_sport_lab_validation.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / "watch_sport_lab_validation.md"
    _write_csv(rows, out_csv)
    _write_md(rows, out_md)
    selected = sum(1 for row in rows if row.get("IncludeAfterCalibration") == "yes")
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"replayed_rows={len(rows)} selected_rows={selected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
