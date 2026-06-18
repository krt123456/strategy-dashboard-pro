"""Probability calibrator — fixes the model's conservative bias.

The backtest showed:
- Basketball actual accuracy: 88.5% but model says ~70-74%
- Tennis actual accuracy: 67.9% but model says ~65-68%
- The model UNDERESTIMATES probability for strong sports

This module calibrates raw model probabilities using historical accuracy
per sport, so that prob=0.80 actually means 80% of those predictions win.

Approach: isotonic-style bucket calibration.
1. Group historical predictions by sport + probability bucket
2. Measure actual win rate per bucket
3. Build a calibration map: raw_prob -> calibrated_prob
4. Apply to new predictions
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _parse_outcome(row: dict) -> Optional[bool]:
    outcome = (row.get("PickOutcome", "") or "").lower()
    status = (row.get("ResultStatus", "") or "").lower()
    if outcome in ("correct", "win", "won", "right", "yes", "1") or "correct" in status:
        return True
    if outcome in ("wrong", "loss", "lost", "no", "0") or "wrong" in status:
        return False
    return None


def build_calibration_map(memory_path: Optional[Path] = None) -> dict:
    if memory_path is None:
        memory_path = PROJECT_DIR / "data" / "prediction_result_memory.csv"
    if not memory_path.exists():
        return {}

    buckets = defaultdict(lambda: {"wins": 0, "total": 0, "sum_prob": 0.0})
    bucket_size = 0.05

    with open(memory_path) as f:
        for row in csv.DictReader(f):
            won = _parse_outcome(row)
            if won is None:
                continue
            sport = row.get("Sport", "unknown")
            try:
                prob = float(row.get("Prob", 0) or 0)
            except (ValueError, TypeError):
                continue
            if prob <= 0 or prob >= 1:
                continue
            bucket = round(prob / bucket_size) * bucket_size
            key = f"{sport}|{bucket:.2f}"
            buckets[key]["wins"] += 1 if won else 0
            buckets[key]["total"] += 1
            buckets[key]["sum_prob"] += prob

    calibration = {}
    for key, data in buckets.items():
        sport, bucket_str = key.split("|")
        bucket = float(bucket_str)
        actual_rate = data["wins"] / data["total"] if data["total"] > 0 else 0
        avg_model_prob = data["sum_prob"] / data["total"] if data["total"] > 0 else 0
        calibration[key] = {
            "sport": sport,
            "bucket": bucket,
            "sample": data["total"],
            "actual_win_rate": round(actual_rate, 4),
            "avg_model_prob": round(avg_model_prob, 4),
            "adjustment": round(actual_rate - avg_model_prob, 4),
            "calibrated_prob": round(actual_rate, 4),
        }
    return calibration


def calibrate_prob(
    raw_prob: float,
    sport: str,
    calibration: dict,
    min_sample: int = 3,
) -> float:
    bucket = round(raw_prob / 0.05) * 0.05
    key = f"{sport}|{bucket:.2f}"
    entry = calibration.get(key)
    if entry and entry["sample"] >= min_sample:
        return entry["calibrated_prob"]
    sport_global = _sport_global_calibration(sport, calibration, min_sample)
    if sport_global is not None:
        ratio = sport_global["actual_rate"] / sport_global["avg_model_prob"]
        if 0.8 < ratio < 1.5:
            return min(raw_prob * ratio, 0.98)
    return raw_prob


def _sport_global_calibration(sport: str, calibration: dict, min_sample: int):
    total_wins = 0
    total_n = 0
    total_prob = 0.0
    for key, entry in calibration.items():
        if entry["sport"] == sport:
            total_wins += entry["actual_win_rate"] * entry["sample"]
            total_n += entry["sample"]
            total_prob += entry["avg_model_prob"] * entry["sample"]
    if total_n < min_sample:
        return None
    return {
        "actual_rate": total_wins / total_n,
        "avg_model_prob": total_prob / total_n,
        "sample": total_n,
    }


def calibration_report(calibration: dict) -> str:
    lines = ["# Probability Calibration Report", ""]
    by_sport = defaultdict(list)
    for key, entry in calibration.items():
        by_sport[entry["sport"]].append(entry)

    for sport in sorted(by_sport, key=lambda x: sum(e["sample"] for e in by_sport[x]), reverse=True):
        entries = sorted(by_sport[sport], key=lambda e: e["bucket"])
        total = sum(e["sample"] for e in entries)
        if total < 3:
            continue
        lines.append(f"## {sport} ({total} samples)")
        lines.append(f"| Prob bucket | Samples | Model avg | Actual win% | Adjustment |")
        lines.append(f"|-------------|---------|-----------|-------------|------------|")
        for e in entries:
            adj = "+" if e["adjustment"] >= 0 else ""
            lines.append(
                f"| {e['bucket']:.2f} | {e['sample']} | {e['avg_model_prob']:.1%} | "
                f"{e['actual_win_rate']:.1%} | {adj}{e['adjustment']:+.1%} |"
            )
        sg = _sport_global_calibration(sport, calibration, 1)
        if sg:
            ratio = sg["actual_rate"] / sg["avg_model_prob"] if sg["avg_model_prob"] > 0 else 1.0
            lines.append(f"Global: model={sg['avg_model_prob']:.1%} → actual={sg['actual_rate']:.1%} (ratio={ratio:.3f})\n")
    return "\n".join(lines)


if __name__ == "__main__":
    cal = build_calibration_map()
    print(calibration_report(cal))
    out = PROJECT_DIR / "data" / "probability_calibration.json"
    with open(out, "w") as f:
        json.dump(cal, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {out}")
