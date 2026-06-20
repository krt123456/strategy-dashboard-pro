#!/usr/bin/env python3
"""Build a daily post-match learning report from prediction outcomes."""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

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
    if not path.exists():
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


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _sort_key(row: Dict[str, Any]) -> tuple[int, str, str]:
    rank = _as_int(row.get("Rank"))
    return (rank if rank else 999999, str(row.get("Sport") or ""), str(row.get("Home") or ""))


def _fmt_float(value: Any, digits: int = 2) -> str:
    num = _as_float(value)
    if num is None:
        return ""
    if abs(num - int(num)) < 0.000001:
        return str(int(num))
    return f"{num:.{digits}f}"


def _score(row: Dict[str, Any]) -> str:
    home = _fmt_float(row.get("HomeScore"), 1)
    away = _fmt_float(row.get("AwayScore"), 1)
    return f"{home}-{away}" if home and away else ""


def _gate_family(row: Dict[str, Any]) -> str:
    gate = str(row.get("StrategyGate") or "").strip()
    if not gate or gate.lower() == "nan":
        return "CORE_OR_UNSPECIFIED"
    return gate.split(":", 1)[0].strip() or "CORE_OR_UNSPECIFIED"


def _is_finished(row: Dict[str, Any]) -> bool:
    return str(row.get("PickOutcome") or "").upper() in {"CORRECT", "WRONG"}


def _accuracy(correct: int, wrong: int) -> float | None:
    total = correct + wrong
    if total <= 0:
        return None
    return correct / total


def _sport_summaries(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"correct": 0, "wrong": 0, "pending": 0, "official": 0})
    for row in rows:
        sport = str(row.get("Sport") or "unknown")
        outcome = str(row.get("PickOutcome") or "").upper()
        if outcome == "CORRECT":
            stats[sport]["correct"] += 1
        elif outcome == "WRONG":
            stats[sport]["wrong"] += 1
        else:
            stats[sport]["pending"] += 1
        if str(row.get("OfficialEntry") or "").lower() == "yes":
            stats[sport]["official"] += 1

    out: List[Dict[str, Any]] = []
    for sport, item in stats.items():
        correct = item["correct"]
        wrong = item["wrong"]
        finished = correct + wrong
        acc = _accuracy(correct, wrong)
        if finished < 3:
            gate = "SAMPLE_MORE"
        elif acc is not None and acc >= 0.70:
            gate = "KEEP_STRONG_RAW_SIGNAL"
        elif acc is not None and acc >= 0.58:
            gate = "KEEP_WITH_CAUTION"
        else:
            gate = "TIGHTEN_OR_LAB_ONLY"
        out.append(
            {
                "Sport": sport,
                "Finished": finished,
                "Correct": correct,
                "Wrong": wrong,
                "Pending": item["pending"],
                "Accuracy": "" if acc is None else round(acc, 4),
                "OfficialEntries": item["official"],
                "RecommendedGate": gate,
            }
        )
    out.sort(key=lambda r: (-int(r["Finished"]), str(r["Sport"])))
    return out


def _learning_note(row: Dict[str, Any]) -> tuple[str, str, str]:
    sport = str(row.get("Sport") or "").lower()
    league = str(row.get("League") or "").lower()
    outcome = str(row.get("PickOutcome") or "").upper()
    gate = _gate_family(row)
    prob = _as_float(row.get("Prob"))
    odds = _as_float(row.get("PickOdds"))
    ev = _as_float(row.get("EVPercent"))
    official = str(row.get("OfficialEntry") or "").lower() == "yes"

    if outcome == "CORRECT":
        notes = []
        if not official:
            notes.append("raw signal only; no official stake was opened")
        if sport == "basketball" and prob is not None and prob >= 0.80:
            notes.append("high-probability basketball favorite confirmed")
        if sport == "baseball":
            notes.append("baseball hit, but the sport segment is not promotion-safe without pitcher/lineup/weather proof")
        if gate.startswith("WATCH_ONLY"):
            notes.append("watch-only sport landed but still needs sample/model proof")
        if not notes:
            notes.append("pick direction matched final score")
        action = "Keep as learning signal; do not promote to entry unless value and source gates pass."
        quality = "KEEP_SIGNAL"
        return quality, "; ".join(notes), action

    if outcome == "WRONG":
        notes = []
        if gate.startswith("WATCH_ONLY"):
            notes.append("strategy-lab gate prevented official entry")
        if sport == "tennis":
            notes.append("tennis volatility requires player/surface/withdrawal context")
            if "doubles" in league:
                notes.append("tennis doubles pair-level variance is now isolated from singles memory")
            if "qualification" in league or " qual" in league:
                notes.append("qualification draw form noise")
        if sport == "baseball":
            notes.append("baseball public-market signal failed without pitcher/lineup/weather gate")
            if "ncaa" in league or "college" in league:
                notes.append("college baseball midweek/rotation volatility")
            if prob is not None and prob < 0.70:
                notes.append("probability below repaired baseball floor 70%")
            if odds is not None and odds >= 1.55:
                notes.append("volatile baseball odds band")
        if prob is not None and prob < 0.70:
            notes.append("low probability bucket below 70%")
        if odds is not None and 1.30 <= odds <= 1.65:
            notes.append("mid-odds favorite/upset risk")
        if ev is not None and ev < 0:
            notes.append("market price was below model entry target")
        if not notes:
            notes.append("model direction failed final score")
        if sport == "baseball":
            action = "Demote baseball to stricter lab-only; require confirmed pitchers, lineups, bullpen/rest, weather/park, MLB/NCAA split backtest, and 70%+ evidence before review."
        elif sport == "tennis" and "doubles" in league:
            action = "Keep tennis doubles deep-lab; separate it from singles memory and require 8+ finished pair-level rows with at least 65% accuracy before review."
        elif gate.startswith("WATCH_ONLY") or sport == "tennis":
            action = "Keep lab-only; require player model, surface fit, news/injury check, and larger sample before review."
        elif sport == "basketball":
            action = "Tighten basketball edge for small-league/away-risk rows and require stronger price value before review."
        else:
            action = "Increase source/context requirements before this segment can affect review ranking."
        return "TIGHTEN_SEGMENT", "; ".join(notes), action

    status = str(row.get("ResultStatus") or "")
    if status == "STRATEGY_LAB_RESULT_DEFERRED":
        return "DEFERRED_LAB_RESULT", "watch-only sport result polling intentionally deferred", "Collect dataset/backtest before promotion."
    if status in {"NO_EVENT_ID", "RESULT_SOURCE_REQUIRED"}:
        return "SOURCE_REQUIRED", "result source or event id missing", "Add event mapping or independent result source."
    return "PENDING_RESULT", status or "result not finished yet", "Recheck after finish."


def _strategy_adjustments(summaries: Iterable[Dict[str, Any]], rows: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    rows = list(rows)
    for row in summaries:
        sport = str(row.get("Sport") or "unknown").lower()
        finished = _as_int(row.get("Finished"))
        correct = _as_int(row.get("Correct"))
        wrong = _as_int(row.get("Wrong"))
        acc = _as_float(row.get("Accuracy"))
        acc_label = "n/a" if acc is None else f"{acc:.2%}"
        if sport == "baseball":
            out.append(
                {
                    "Sport": row.get("Sport") or "baseball",
                    "Adjustment": "DEMOTE_AND_TIGHTEN",
                    "Reason": f"{correct}/{finished} correct ({acc_label}); wrong rows clustered in pitcher/lineup-missing public-market picks.",
                    "Patch": "min_prob 70%, min_margin 16%, max_odds 1.70, haircut 7.5%, require pitcher/lineup/weather and MLB/NCAA split backtest.",
                }
            )
        elif sport == "tennis":
            tennis_rows = [
                item for item in rows
                if str(item.get("Sport") or "").lower() == "tennis"
                and str(item.get("PickOutcome") or "").upper() in {"CORRECT", "WRONG"}
            ]
            singles_rows = [item for item in tennis_rows if "doubles" not in str(item.get("League") or "").lower()]
            doubles_rows = [item for item in tennis_rows if "doubles" in str(item.get("League") or "").lower()]

            singles_correct = sum(1 for item in singles_rows if str(item.get("PickOutcome") or "").upper() == "CORRECT")
            doubles_correct = sum(1 for item in doubles_rows if str(item.get("PickOutcome") or "").upper() == "CORRECT")
            singles_finished = len(singles_rows)
            doubles_finished = len(doubles_rows)
            singles_acc = _accuracy(singles_correct, singles_finished - singles_correct)
            doubles_acc = _accuracy(doubles_correct, doubles_finished - doubles_correct)

            if singles_finished:
                singles_acc_label = "n/a" if singles_acc is None else f"{singles_acc:.2%}"
                out.append(
                    {
                        "Sport": "Tennis Singles",
                        "Adjustment": "KEEP_WATCH_WITH_CONTEXT" if singles_acc is not None and singles_acc >= 0.68 else "TIGHTEN_WITH_CONTEXT",
                        "Reason": f"{singles_correct}/{singles_finished} correct ({singles_acc_label}); singles watch signal is still usable with player/surface/news filters.",
                        "Patch": "keep singles watch-only; require player model, surface fit, withdrawal/news gate, and exact round before review.",
                    }
                )
            if doubles_finished:
                doubles_acc_label = "n/a" if doubles_acc is None else f"{doubles_acc:.2%}"
                out.append(
                    {
                        "Sport": "Tennis Doubles",
                        "Adjustment": "DEMOTE_TO_DEEP_LAB" if doubles_acc is not None and doubles_acc < 0.65 else "KEEP_WITH_CAUTION",
                        "Reason": f"{doubles_correct}/{doubles_finished} correct ({doubles_acc_label}); pair-level variance is weaker than singles and should not inherit tennis-wide memory.",
                        "Patch": "split doubles from singles memory, keep doubles deep-lab until 8+ finished rows reach at least 65% accuracy, and add pair chemistry/serve-return context.",
                    }
                )
        elif finished >= 3 and acc is not None and acc >= 0.70:
            out.append(
                {
                    "Sport": row.get("Sport") or "unknown",
                    "Adjustment": "KEEP_WATCH_WITH_CONTEXT",
                    "Reason": f"{correct}/{finished} correct ({acc_label}) but still raw watch-only, not official-entry evidence.",
                    "Patch": "keep the segment gated; require its missing sport-specific context before promotion.",
                }
            )
        elif finished < 3:
            out.append(
                {
                    "Sport": row.get("Sport") or "unknown",
                    "Adjustment": "SAMPLE_MORE",
                    "Reason": f"only {finished} finished rows; sample is too small for promotion or demotion.",
                    "Patch": "collect finished rows and keep final guard in control.",
                }
            )
        elif acc is not None and acc < 0.58:
            out.append(
                {
                    "Sport": row.get("Sport") or "unknown",
                    "Adjustment": "TIGHTEN_OR_LAB_ONLY",
                    "Reason": f"{correct}/{finished} correct ({acc_label}) below the repaired memory watch floor.",
                    "Patch": "raise thresholds and require missing sport-specific context before review.",
                }
            )
    return out


def _annotated_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in sorted(rows, key=_sort_key):
        quality, note, action = _learning_note(row)
        out.append(
            {
                "Rank": row.get("Rank") or "",
                "Sport": row.get("Sport") or "",
                "Date": row.get("Date") or "",
                "League": row.get("League") or "",
                "Home": row.get("Home") or "",
                "Away": row.get("Away") or "",
                "Pick": row.get("Pick") or "",
                "Score": _score(row),
                "PickOutcome": row.get("PickOutcome") or "",
                "OfficialEntry": row.get("OfficialEntry") or "",
                "Prob": row.get("Prob") or "",
                "PickOdds": row.get("PickOdds") or "",
                "EVPercent": row.get("EVPercent") or "",
                "Decision": row.get("Decision") or "",
                "ValueVerdict": row.get("ValueVerdict") or "",
                "StrategyGateFamily": _gate_family(row),
                "ResultStatus": row.get("ResultStatus") or "",
                "ResultSource": row.get("ResultSource") or "",
                "LearningQuality": quality,
                "LearningNote": note,
                "RecommendedAction": action,
            }
        )
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "Rank",
        "Sport",
        "Date",
        "League",
        "Home",
        "Away",
        "Pick",
        "Score",
        "PickOutcome",
        "OfficialEntry",
        "Prob",
        "PickOdds",
        "EVPercent",
        "Decision",
        "ValueVerdict",
        "StrategyGateFamily",
        "ResultStatus",
        "ResultSource",
        "LearningQuality",
        "LearningNote",
        "RecommendedAction",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = ["Sport", "Finished", "Correct", "Wrong", "Pending", "Accuracy", "OfficialEntries", "RecommendedGate"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _md_table(rows: List[Dict[str, Any]], outcome: str, limit: int = 20) -> List[str]:
    selected = [r for r in rows if str(r.get("PickOutcome") or "").upper() == outcome][:limit]
    lines = [
        "| # | Sport | Match | Pick | Score | Note | Action |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for row in selected:
        lines.append(
            f"| {row.get('Rank')} | {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | "
            f"{row.get('Pick')} | {row.get('Score')} | {row.get('LearningNote')} | {row.get('RecommendedAction')} |"
        )
    if not selected:
        lines.append("| - | - | - | - | - | - | - |")
    return lines


def _write_md(rows: List[Dict[str, Any]], summaries: List[Dict[str, Any]], target: date, source: Path, path: Path) -> None:
    counts = Counter(str(r.get("PickOutcome") or "EMPTY").upper() for r in rows)
    finished = counts.get("CORRECT", 0) + counts.get("WRONG", 0)
    acc = _accuracy(counts.get("CORRECT", 0), counts.get("WRONG", 0))
    source_label = str(source)
    try:
        source_label = str(source.resolve().relative_to(BASE_DIR))
    except Exception:
        pass

    lines = [
        "# Post-match learning report",
        f"- Date: {target.isoformat()}",
        f"- Source: `{source_label}`",
        f"- Finished raw picks: {finished}",
        f"- Correct: {counts.get('CORRECT', 0)}",
        f"- Wrong: {counts.get('WRONG', 0)}",
        f"- Pending/deferred: {counts.get('PENDING', 0)}",
        f"- Raw accuracy: {'n/a' if acc is None else f'{acc:.2%}'}",
        "- Official entries remain separate from raw learning rows.",
        "",
        "## Sport Summary",
        "| Sport | Finished | Correct | Wrong | Pending | Accuracy | Recommended gate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summaries:
        accuracy = row.get("Accuracy")
        acc_label = "n/a" if accuracy in (None, "") else f"{float(accuracy):.2%}"
        lines.append(
            f"| {row.get('Sport')} | {row.get('Finished')} | {row.get('Correct')} | {row.get('Wrong')} | "
            f"{row.get('Pending')} | {acc_label} | {row.get('RecommendedGate')} |"
        )
    if not summaries:
        lines.append("| - | - | - | - | - | - | - |")

    adjustments = _strategy_adjustments(summaries, rows)
    lines.extend(
        [
            "",
            "## Strategy Adjustments",
            "| Sport | Adjustment | Reason | Patch applied/required |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in adjustments:
        lines.append(f"| {item['Sport']} | {item['Adjustment']} | {item['Reason']} | {item['Patch']} |")
    if not adjustments:
        lines.append("| - | - | - | - |")

    lines.extend(["", "## Correct Picks", *_md_table(rows, "CORRECT"), "", "## Wrong Picks", *_md_table(rows, "WRONG")])
    lines.extend(
        [
            "",
            "## Operating Rule",
            "- A correct raw pick is not proof that it should have been entered; price value and source gates still decide.",
            "- A wrong watch-only pick validates the lab gate: keep it out of final review until the segment has enough evidence.",
            "- Promote sports by finished sample quality, not by one isolated win.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build post-match learning report.")
    parser.add_argument("--date", default="today")
    parser.add_argument("--results-csv", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--summary-csv", default="")
    parser.add_argument("--out-md", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _target_date(args.date)
    source = Path(args.results_csv) if args.results_csv else REPORTS_DIR / f"prediction_results_{target.isoformat()}.csv"
    rows = _read_csv(source)
    if not rows:
        print(f"No prediction result rows found: {source}")
        return 1

    annotated = _annotated_rows(rows)
    summaries = _sport_summaries(rows)
    out_csv = Path(args.out_csv) if args.out_csv else REPORTS_DIR / f"postmatch_learning_{target.isoformat()}.csv"
    summary_csv = Path(args.summary_csv) if args.summary_csv else REPORTS_DIR / f"postmatch_learning_summary_{target.isoformat()}.csv"
    out_md = Path(args.out_md) if args.out_md else REPORTS_DIR / f"postmatch_learning_{target.isoformat()}.md"
    _write_csv(annotated, out_csv)
    _write_summary_csv(summaries, summary_csv)
    _write_md(annotated, summaries, target, source, out_md)

    counts = Counter(str(r.get("PickOutcome") or "EMPTY").upper() for r in annotated)
    print(f"Wrote {out_csv}")
    print(f"Wrote {summary_csv}")
    print(f"Wrote {out_md}")
    print(f"correct={counts.get('CORRECT', 0)} wrong={counts.get('WRONG', 0)} pending={counts.get('PENDING', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
