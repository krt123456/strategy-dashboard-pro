#!/usr/bin/env python3
"""Summarize prediction-result memory into decision-quality diagnostics."""
from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_MEMORY = BASE_DIR / "data" / "prediction_result_memory.csv"


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


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _bucket_probability(value: Any) -> str:
    prob = _as_float(value)
    if prob is None:
        return "prob_unknown"
    if prob < 0.60:
        return "prob_<60"
    if prob < 0.70:
        return "prob_60_70"
    if prob < 0.80:
        return "prob_70_80"
    if prob < 0.90:
        return "prob_80_90"
    return "prob_90_plus"


def _bucket_odds(value: Any) -> str:
    odds = _as_float(value)
    if odds is None:
        return "odds_unknown"
    if odds <= 1.10:
        return "odds_<=1.10"
    if odds <= 1.30:
        return "odds_1.11_1.30"
    if odds <= 1.60:
        return "odds_1.31_1.60"
    if odds <= 2.00:
        return "odds_1.61_2.00"
    return "odds_>2.00"


def _family(value: Any, default: str = "unknown") -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "nan":
        return default
    return raw.split(":", 1)[0].strip() or default


def _source_family(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "nan":
        return "source_unknown"
    if raw.startswith("data/raw/"):
        parts = raw.split("/")
        return "/".join(parts[:3]) if len(parts) >= 3 else raw
    return raw.split("_sport_", 1)[0]


def _finished_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if str(r.get("PickOutcome") or "").upper() in {"CORRECT", "WRONG"}]


def _segments(row: Dict[str, Any]) -> List[Tuple[str, str]]:
    return [
        ("all", "all"),
        ("sport", str(row.get("Sport") or "unknown")),
        ("official", str(row.get("OfficialEntry") or "unknown")),
        ("value_verdict", str(row.get("ValueVerdict") or "unknown")),
        ("decision", str(row.get("Decision") or "unknown")),
        ("prob_bucket", _bucket_probability(row.get("Prob"))),
        ("odds_bucket", _bucket_odds(row.get("PickOdds"))),
        ("freshness", str(row.get("OneXBetOddsFreshness") or "unknown")),
        ("strategy_gate", _family(row.get("StrategyGate"), "strategy_gate_unknown")),
        ("entry_readiness", str(row.get("EntryReadiness") or "unknown")),
        ("action_verdict", str(row.get("ActionVerdict") or "unknown")),
        ("probability_source", str(row.get("ProbabilitySource") or "unknown")),
        ("odds_flag", str(row.get("OddsFlag") or "unknown")),
        ("one_xbet_status", str(row.get("OneXBetStatus") or "unknown")),
        ("event_timing", str(row.get("EventTimingStatus") or "unknown")),
        ("odds_source", str(row.get("OddsSourceUsed") or "unknown")),
        ("source_family", _source_family(row.get("Source"))),
    ]


def _summaries(rows: List[Dict[str, Any]], min_samples: int) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0, "wrong": 0, "official": 0})
    for row in rows:
        outcome = str(row.get("PickOutcome") or "").upper()
        for seg_type, seg_value in _segments(row):
            key = (seg_type, seg_value)
            buckets[key]["total"] += 1
            buckets[key]["correct"] += 1 if outcome == "CORRECT" else 0
            buckets[key]["wrong"] += 1 if outcome == "WRONG" else 0
            buckets[key]["official"] += 1 if str(row.get("OfficialEntry") or "").lower() == "yes" else 0

    out: List[Dict[str, Any]] = []
    for (seg_type, seg_value), stats in buckets.items():
        total = stats["total"]
        correct = stats["correct"]
        wrong = stats["wrong"]
        acc = correct / total if total else 0.0
        if total < min_samples:
            signal = "INSUFFICIENT_SAMPLE"
        elif acc >= 0.70:
            signal = "STRONG_SEGMENT"
        elif acc >= 0.58:
            signal = "WATCH_SEGMENT"
        else:
            signal = "WEAK_SEGMENT_REVIEW"
        out.append(
            {
                "SegmentType": seg_type,
                "Segment": seg_value,
                "Total": total,
                "Correct": correct,
                "Wrong": wrong,
                "Accuracy": round(acc, 4),
                "OfficialEntries": stats["official"],
                "Signal": signal,
            }
        )
    out.sort(key=lambda r: (r["Signal"] == "INSUFFICIENT_SAMPLE", -int(r["Total"]), str(r["SegmentType"]), str(r["Segment"])))
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = ["SegmentType", "Segment", "Total", "Correct", "Wrong", "Accuracy", "OfficialEntries", "Signal"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_md(rows: List[Dict[str, Any]], finished_count: int, source: Path, path: Path, min_samples: int) -> None:
    try:
        source_label = str(source.resolve().relative_to(BASE_DIR))
    except Exception:
        source_label = str(source)
    lines = [
        "# Prediction memory analysis",
        f"- Date: {date.today().isoformat()}",
        f"- Memory source: `{source_label}`",
        f"- Finished rows analyzed: {finished_count}",
        f"- Minimum sample for a segment signal: {min_samples}",
        "",
    ]
    if not rows:
        lines.extend(
            [
                "## Status",
                "No finished prediction rows are available yet.",
                "",
                "The analyzer will become useful after `check_prediction_results.py` records finished matches in `data/prediction_result_memory.csv`.",
            ]
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    weak = [r for r in rows if r["Signal"] == "WEAK_SEGMENT_REVIEW"]
    strong = [r for r in rows if r["Signal"] == "STRONG_SEGMENT"]
    lines.extend(
        [
            "## Strong segments",
            "| Segment type | Segment | Total | Correct | Wrong | Accuracy |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in strong[:20]:
        lines.append(
            f"| {row['SegmentType']} | {row['Segment']} | {row['Total']} | {row['Correct']} | {row['Wrong']} | {float(row['Accuracy']):.2%} |"
        )
    if not strong:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Weak segments to review",
            "| Segment type | Segment | Total | Correct | Wrong | Accuracy |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in weak[:20]:
        lines.append(
            f"| {row['SegmentType']} | {row['Segment']} | {row['Total']} | {row['Correct']} | {row['Wrong']} | {float(row['Accuracy']):.2%} |"
        )
    if not weak:
        lines.append("| - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## All segments",
            "| Segment type | Segment | Total | Correct | Wrong | Accuracy | Signal |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in rows[:80]:
        lines.append(
            f"| {row['SegmentType']} | {row['Segment']} | {row['Total']} | {row['Correct']} | {row['Wrong']} | {float(row['Accuracy']):.2%} | {row['Signal']} |"
        )

    lines.extend(
        [
            "",
            "## Use",
            "- Do not auto-block a segment before it reaches the minimum sample size.",
            "- Strong segments now require at least 70% realized accuracy; below 58% becomes a hard review weakness.",
            "- Weak segments should trigger stricter manual-source review, tighter entry thresholds, or lab-only demotion.",
            "- Official-entry performance is separated from raw watchlist performance.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze prediction-result memory.")
    parser.add_argument("--memory-csv", default=str(DEFAULT_MEMORY))
    parser.add_argument("--out-csv", default=str(REPORTS_DIR / "prediction_memory_analysis.csv"))
    parser.add_argument("--out-md", default=str(REPORTS_DIR / "prediction_memory_analysis.md"))
    parser.add_argument("--min-samples", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    memory = Path(args.memory_csv)
    finished = _finished_rows(_read_csv(memory))
    summaries = _summaries(finished, args.min_samples)
    _write_csv(summaries, Path(args.out_csv))
    _write_md(summaries, len(finished), memory, Path(args.out_md), args.min_samples)
    print(f"Wrote {args.out_csv}")
    print(f"Wrote {args.out_md}")
    print(f"finished_rows={len(finished)} segments={len(summaries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
