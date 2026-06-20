#!/usr/bin/env python3
"""Compare before/after strategy outputs and write an audit report."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size <= 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _key(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(row.get("Date") or "").strip()[:10],
        str(row.get("Sport") or "").strip().lower(),
        str(row.get("Home") or "").strip().lower(),
        str(row.get("Away") or "").strip().lower(),
        str(row.get("Pick") or "").strip().lower(),
    )


def _map_rows(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str, str, str, str], Dict[str, Any]]:
    return {_key(row): row for row in rows}


def _match_key(row: Dict[str, Any], fields: Sequence[str]) -> Tuple[str, ...]:
    return tuple(str(row.get(field) or "").strip().lower() for field in fields)


def _counter(rows: Iterable[Dict[str, Any]], field: str) -> Counter[str]:
    out: Counter[str] = Counter()
    for row in rows:
        out[str(row.get(field) or "EMPTY")] += 1
    return out


def _decision_family(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "EMPTY"
    if raw.startswith("ACCEPT"):
        return "ACCEPT"
    if raw.startswith("WATCH"):
        return "WATCH"
    if raw == "REJECT":
        return "REJECT"
    return raw


def _other_summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, Counter[str]]:
    cached_rows = list(rows)
    return {
        "sport": _counter(cached_rows, "Sport"),
        "decision": _counter(cached_rows, "Decision"),
        "decision_family": Counter(_decision_family(row.get("Decision")) for row in cached_rows),
        "lab_tier": _counter(cached_rows, "LabTier"),
        "variant": _counter(cached_rows, "StrategyVariantLabel"),
    }


def _paired_rows(
    before_rows: List[Dict[str, Any]],
    after_rows: List[Dict[str, Any]],
    before_guard_rows: List[Dict[str, Any]],
    after_guard_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    before_map = _map_rows(before_rows)
    after_map = _map_rows(after_rows)
    before_guard = _map_rows(before_guard_rows)
    after_guard = _map_rows(after_guard_rows)
    keys = sorted(set(before_map) | set(after_map))
    out: List[Dict[str, Any]] = []
    for key in keys:
        before = before_map.get(key, {})
        after = after_map.get(key, {})
        before_g = before_guard.get(key, {})
        after_g = after_guard.get(key, {})
        if before and after:
            status = "KEPT"
        elif before:
            status = "REMOVED_AFTER_CHANGE"
        else:
            status = "ADDED_AFTER_CHANGE"
        out.append(
            {
                "ChangeStatus": status,
                "Date": before.get("Date") or after.get("Date") or "",
                "Sport": before.get("Sport") or after.get("Sport") or "",
                "League": before.get("League") or after.get("League") or "",
                "Home": before.get("Home") or after.get("Home") or "",
                "Away": before.get("Away") or after.get("Away") or "",
                "Pick": before.get("Pick") or after.get("Pick") or "",
                "BeforeDecision": before.get("Decision") or "",
                "AfterDecision": after.get("Decision") or "",
                "BeforeProb": before.get("Prob") or "",
                "AfterProb": after.get("Prob") or "",
                "BeforeOdds": before.get("PickOdds") or "",
                "AfterOdds": after.get("PickOdds") or "",
                "BeforeRankScore": before.get("RankScore") or "",
                "AfterRankScore": after.get("RankScore") or "",
                "BeforeLabTier": before.get("LabTier") or "",
                "AfterLabTier": after.get("LabTier") or "",
                "BeforeVariant": before.get("StrategyVariantLabel") or "",
                "AfterVariant": after.get("StrategyVariantLabel") or "",
                "BeforeGuardDecision": before_g.get("FinalDecision") or "",
                "AfterGuardDecision": after_g.get("FinalDecision") or "",
                "BeforeGuardScore": before_g.get("GuardScore") or "",
                "AfterGuardScore": after_g.get("GuardScore") or "",
            }
        )
    return out


def _build_input_diff_rows(
    before_rows: List[Dict[str, Any]],
    after_rows: List[Dict[str, Any]],
    *,
    key_fields: Sequence[str],
    compare_fields: Sequence[str],
    extra_id_fields: Sequence[str],
) -> List[Dict[str, Any]]:
    before_map = {_match_key(row, key_fields): row for row in before_rows}
    after_map = {_match_key(row, key_fields): row for row in after_rows}
    keys = sorted(set(before_map) | set(after_map))
    out: List[Dict[str, Any]] = []
    for key in keys:
        before = before_map.get(key, {})
        after = after_map.get(key, {})
        if before and after:
            status = (
                "KEPT_IDENTICAL"
                if all(str(before.get(field) or "") == str(after.get(field) or "") for field in compare_fields)
                else "KEPT_CHANGED"
            )
        elif before:
            status = "REMOVED"
        else:
            status = "ADDED"
        row: Dict[str, Any] = {
            "ChangeStatus": status,
            "Date": before.get("Date") or after.get("Date") or "",
            "Sport": before.get("Sport") or after.get("Sport") or "",
            "League": before.get("League") or after.get("League") or "",
            "Code": before.get("Code") or after.get("Code") or "",
            "Home": before.get("Home") or after.get("Home") or "",
            "Away": before.get("Away") or after.get("Away") or "",
        }
        for field in extra_id_fields:
            row[field] = before.get(field) or after.get(field) or ""
        for field in compare_fields:
            row[f"Before{field}"] = before.get(field) or ""
            row[f"After{field}"] = after.get(field) or ""
        out.append(row)
    return out


def _write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    fields = [
        "ChangeStatus",
        "Date",
        "Sport",
        "League",
        "Home",
        "Away",
        "Pick",
        "BeforeDecision",
        "AfterDecision",
        "BeforeProb",
        "AfterProb",
        "BeforeOdds",
        "AfterOdds",
        "BeforeRankScore",
        "AfterRankScore",
        "BeforeLabTier",
        "AfterLabTier",
        "BeforeVariant",
        "AfterVariant",
        "BeforeGuardDecision",
        "AfterGuardDecision",
        "BeforeGuardScore",
        "AfterGuardScore",
    ]
    _write_rows_csv(rows, fields, path)


def _write_rows_csv(rows: List[Dict[str, Any]], fields: Sequence[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _fmt_counter(counter: Counter[str]) -> List[str]:
    return [f"- {key}: {value}" for key, value in counter.most_common()] or ["- none: 0"]


def _strategy_identical(diff_rows: Iterable[Dict[str, Any]]) -> bool:
    statuses = {str(row.get("ChangeStatus") or "") for row in diff_rows}
    return not statuses or statuses <= {"KEPT_IDENTICAL"}


def _raw_file_identical(before_path: Path | None, after_path: Path | None) -> bool:
    if before_path is None or after_path is None:
        return False
    if not before_path.exists() or not after_path.exists():
        return False
    return before_path.read_bytes() == after_path.read_bytes()


def _transition_counter(
    rows: Iterable[Dict[str, Any]],
    *,
    before_field: str,
    after_field: str,
    status_field: str = "",
    allowed_statuses: Sequence[str] = (),
) -> Counter[str]:
    out: Counter[str] = Counter()
    allowed = set(allowed_statuses)
    for row in rows:
        if status_field and allowed and str(row.get(status_field) or "") not in allowed:
            continue
        before_value = str(row.get(before_field) or "EMPTY")
        after_value = str(row.get(after_field) or "EMPTY")
        out[f"{before_value} -> {after_value}"] += 1
    return out


def _counter_dict(counter: Counter[str]) -> Dict[str, int]:
    return {key: int(value) for key, value in counter.items()}


def _write_summary_json(data: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_input_diff_section(
    lines: List[str],
    *,
    title: str,
    diff_rows: List[Dict[str, Any]],
    selection_field: str,
    compare_fields: Sequence[str],
) -> None:
    status_counts = _counter(diff_rows, "ChangeStatus")
    changed = [row for row in diff_rows if row.get("ChangeStatus") == "KEPT_CHANGED"]
    added = [row for row in diff_rows if row.get("ChangeStatus") == "ADDED"]
    removed = [row for row in diff_rows if row.get("ChangeStatus") == "REMOVED"]
    lines.extend(
        [
            f"## {title} input diff",
            *_fmt_counter(status_counts),
            "",
            f"### {title} changed rows",
            f"| Match | Selection | {' | '.join(compare_fields)} |",
            f"| --- | --- | {' | '.join(['---'] * len(compare_fields))} |",
        ]
    )
    for row in changed[:20]:
        details = []
        for field in compare_fields:
            before_val = row.get(f"Before{field}") or "-"
            after_val = row.get(f"After{field}") or "-"
            if before_val == after_val:
                details.append(str(after_val))
            else:
                details.append(f"{before_val} -> {after_val}")
        selection_before = row.get(f"Before{selection_field}") or "-"
        selection_after = row.get(f"After{selection_field}") or "-"
        selection = selection_after if selection_before == selection_after else f"{selection_before} -> {selection_after}"
        lines.append(
            f"| {row.get('Home')} vs {row.get('Away')} | {selection} | {' | '.join(str(item) for item in details)} |"
        )
    if not changed:
        lines.append(f"| - | - | {' | '.join(['-'] * len(compare_fields))} |")

    lines.extend(
        [
            "",
            f"### {title} added rows",
            "| Match | Selection | League |",
            "| --- | --- | --- |",
        ]
    )
    for row in added[:20]:
        lines.append(
            f"| {row.get('Home')} vs {row.get('Away')} | {row.get(f'After{selection_field}') or '-'} | {row.get('League') or '-'} |"
        )
    if not added:
        lines.append("| - | - | - |")

    lines.extend(
        [
            "",
            f"### {title} removed rows",
            "| Match | Selection | League |",
            "| --- | --- | --- |",
        ]
    )
    for row in removed[:20]:
        lines.append(
            f"| {row.get('Home')} vs {row.get('Away')} | {row.get(f'Before{selection_field}') or '-'} | {row.get('League') or '-'} |"
        )
    if not removed:
        lines.append("| - | - | - |")
    lines.append("")


def _write_md(
    *,
    before_rows: List[Dict[str, Any]],
    after_rows: List[Dict[str, Any]],
    before_guard_rows: List[Dict[str, Any]],
    after_guard_rows: List[Dict[str, Any]],
    before_other_rows: List[Dict[str, Any]],
    after_other_rows: List[Dict[str, Any]],
    before_football_rows: List[Dict[str, Any]],
    after_football_rows: List[Dict[str, Any]],
    other_input_diff_rows: List[Dict[str, Any]],
    football_input_diff_rows: List[Dict[str, Any]],
    other_strategy_identical: bool,
    football_strategy_identical: bool,
    other_raw_identical: bool,
    football_raw_identical: bool,
    paired_rows: List[Dict[str, Any]],
    path: Path,
) -> None:
    before_sport = _counter(before_rows, "Sport")
    after_sport = _counter(after_rows, "Sport")
    before_decision = _counter(before_rows, "Decision")
    after_decision = _counter(after_rows, "Decision")
    before_guard = _counter(before_guard_rows, "FinalDecision")
    after_guard = _counter(after_guard_rows, "FinalDecision")
    change_counts = _counter(paired_rows, "ChangeStatus")

    removed = [row for row in paired_rows if row["ChangeStatus"] == "REMOVED_AFTER_CHANGE"]
    added = [row for row in paired_rows if row["ChangeStatus"] == "ADDED_AFTER_CHANGE"]
    kept = [row for row in paired_rows if row["ChangeStatus"] == "KEPT"]
    removed_by_sport = _counter(removed, "Sport")
    added_by_sport = _counter(added, "Sport")
    before_other_summary = _other_summary(before_other_rows) if before_other_rows else {}
    after_other_summary = _other_summary(after_other_rows) if after_other_rows else {}
    advisor_kept_transitions = _transition_counter(
        kept,
        before_field="BeforeDecision",
        after_field="AfterDecision",
    )
    guard_kept_transitions = _transition_counter(
        kept,
        before_field="BeforeGuardDecision",
        after_field="AfterGuardDecision",
    )
    other_input_transitions = _transition_counter(
        other_input_diff_rows,
        before_field="BeforeDecision",
        after_field="AfterDecision",
        status_field="ChangeStatus",
        allowed_statuses=("KEPT_IDENTICAL", "KEPT_CHANGED"),
    )
    football_input_transitions = _transition_counter(
        football_input_diff_rows,
        before_field="BeforePred",
        after_field="AfterPred",
        status_field="ChangeStatus",
        allowed_statuses=("KEPT_IDENTICAL", "KEPT_CHANGED"),
    )

    lines = [
        "# Before / after strategy comparison",
        f"- Before advisor rows: {len(before_rows)}",
        f"- After advisor rows: {len(after_rows)}",
        f"- Delta advisor rows: {len(after_rows) - len(before_rows):+d}",
        f"- Before guard rows: {len(before_guard_rows)}",
        f"- After guard rows: {len(after_guard_rows)}",
        f"- Before football-picks rows: {len(before_football_rows)}",
        f"- After football-picks rows: {len(after_football_rows)}",
        f"- Before other-sports rows: {len(before_other_rows)}",
        f"- After other-sports rows: {len(after_other_rows)}",
        f"- Football strategy-identical: {'yes' if football_strategy_identical else 'no'}",
        f"- Football raw-file-identical: {'yes' if football_raw_identical else 'no'}",
        f"- Other-sports strategy-identical: {'yes' if other_strategy_identical else 'no'}",
        f"- Other-sports raw-file-identical: {'yes' if other_raw_identical else 'no'}",
        "",
        "## Change counts",
        *_fmt_counter(change_counts),
        "",
        "## Sport distribution",
        "Before:",
        *_fmt_counter(before_sport),
        "After:",
        *_fmt_counter(after_sport),
        "",
        "## Advisor decision distribution",
        "Before:",
        *_fmt_counter(before_decision),
        "After:",
        *_fmt_counter(after_decision),
        "",
        "## Advisor kept-row transitions",
        *_fmt_counter(advisor_kept_transitions),
        "",
        "## Final guard distribution",
        "Before:",
        *_fmt_counter(before_guard),
        "After:",
        *_fmt_counter(after_guard),
        "",
        "## Final guard kept-row transitions",
        *_fmt_counter(guard_kept_transitions),
        "",
    ]
    if before_other_rows or after_other_rows:
        lines.extend(
            [
                "## Other sports decision families",
                "Before:",
                *_fmt_counter(before_other_summary.get("decision_family", Counter())),
                "After:",
                *_fmt_counter(after_other_summary.get("decision_family", Counter())),
                "",
                "## Other sports lab tiers",
                "Before:",
                *_fmt_counter(before_other_summary.get("lab_tier", Counter())),
                "After:",
                *_fmt_counter(after_other_summary.get("lab_tier", Counter())),
                "",
                "## Other sports top variants",
                "Before:",
                *_fmt_counter(before_other_summary.get("variant", Counter())),
                "After:",
                *_fmt_counter(after_other_summary.get("variant", Counter())),
                "",
                "## Other sports kept-row decision transitions",
                *_fmt_counter(other_input_transitions),
                "",
            ]
        )
        _append_input_diff_section(
            lines,
            title="Other sports",
            diff_rows=other_input_diff_rows,
            selection_field="Pick",
            compare_fields=("Decision", "LabTier", "StrategyVariantLabel", "Prob", "PickOdds"),
        )
    if before_football_rows or after_football_rows:
        _append_input_diff_section(
            lines,
            title="Football picks",
            diff_rows=football_input_diff_rows,
            selection_field="Pred",
            compare_fields=("Conf", "OddsH", "OddsD", "OddsA"),
        )
        lines.extend(
            [
                "## Football picks kept-row prediction transitions",
                *_fmt_counter(football_input_transitions),
                "",
            ]
        )
    lines.extend(
        [
            "## Removed by sport",
            *_fmt_counter(removed_by_sport),
            "",
            "## Added by sport",
            *_fmt_counter(added_by_sport),
            "",
            "## Key removed rows",
            "| Sport | Match | Pick | Before decision | Before guard |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in removed[:20]:
        lines.append(
            f"| {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('BeforeDecision')} | {row.get('BeforeGuardDecision')} |"
        )
    if not removed:
        lines.append("| - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Key kept rows",
            "| Sport | Match | Pick | Before decision | After decision | Before guard | After guard | After tier | After variant |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in kept[:20]:
        lines.append(
            f"| {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('BeforeDecision')} | {row.get('AfterDecision')} | {row.get('BeforeGuardDecision')} | "
            f"{row.get('AfterGuardDecision')} | {row.get('AfterLabTier')} | {row.get('AfterVariant')} |"
        )
    if not kept:
        lines.append("| - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Key added rows",
            "| Sport | Match | Pick | After decision | After guard | After tier | After variant |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in added[:20]:
        lines.append(
            f"| {row.get('Sport')} | {row.get('Home')} vs {row.get('Away')} | {row.get('Pick')} | "
            f"{row.get('AfterDecision')} | {row.get('AfterGuardDecision')} | {row.get('AfterLabTier')} | {row.get('AfterVariant')} |"
        )
    if not added:
        lines.append("| - | - | - | - | - | - | - |")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare before/after strategy outputs.")
    parser.add_argument("--before-advisor", required=True)
    parser.add_argument("--after-advisor", required=True)
    parser.add_argument("--before-guard", default="")
    parser.add_argument("--after-guard", default="")
    parser.add_argument("--before-football", default="")
    parser.add_argument("--after-football", default="")
    parser.add_argument("--before-other-sports", default="")
    parser.add_argument("--after-other-sports", default="")
    parser.add_argument("--out-csv", default="")
    parser.add_argument("--out-md", default="")
    parser.add_argument("--out-json", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    before_rows = _read_csv(Path(args.before_advisor))
    after_rows = _read_csv(Path(args.after_advisor))
    before_guard_rows = _read_csv(Path(args.before_guard)) if args.before_guard else []
    after_guard_rows = _read_csv(Path(args.after_guard)) if args.after_guard else []
    before_football_path = Path(args.before_football) if args.before_football else None
    after_football_path = Path(args.after_football) if args.after_football else None
    before_football_rows = _read_csv(before_football_path) if before_football_path else []
    after_football_rows = _read_csv(after_football_path) if after_football_path else []
    before_other_rows = _read_csv(Path(args.before_other_sports)) if args.before_other_sports else []
    after_other_rows = _read_csv(Path(args.after_other_sports)) if args.after_other_sports else []
    other_input_diff_rows = _build_input_diff_rows(
        before_other_rows,
        after_other_rows,
        key_fields=("Date", "Sport", "Home", "Away"),
        compare_fields=("Pick", "Side", "Decision", "LabTier", "StrategyVariantLabel", "Prob", "Margin", "PickOdds", "BrainScore"),
        extra_id_fields=(),
    )
    football_input_diff_rows = _build_input_diff_rows(
        before_football_rows,
        after_football_rows,
        key_fields=("Date", "League", "Code", "Home", "Away"),
        compare_fields=("Pred", "Conf", "OddsH", "OddsD", "OddsA"),
        extra_id_fields=(),
    )
    paired_rows = _paired_rows(before_rows, after_rows, before_guard_rows, after_guard_rows)
    out_csv = Path(args.out_csv) if args.out_csv else Path(args.after_advisor).with_name("before_after_strategy_comparison.csv")
    out_md = Path(args.out_md) if args.out_md else Path(args.after_advisor).with_name("before_after_strategy_comparison.md")
    out_json = Path(args.out_json) if args.out_json else out_csv.with_name(f"{out_csv.stem}.json")
    _write_csv(paired_rows, out_csv)
    if other_input_diff_rows:
        _write_rows_csv(
            other_input_diff_rows,
            [
                "ChangeStatus",
                "Date",
                "Sport",
                "League",
                "Code",
                "Home",
                "Away",
                "BeforePick",
                "AfterPick",
                "BeforeSide",
                "AfterSide",
                "BeforeDecision",
                "AfterDecision",
                "BeforeLabTier",
                "AfterLabTier",
                "BeforeStrategyVariantLabel",
                "AfterStrategyVariantLabel",
                "BeforeProb",
                "AfterProb",
                "BeforeMargin",
                "AfterMargin",
                "BeforePickOdds",
                "AfterPickOdds",
                "BeforeBrainScore",
                "AfterBrainScore",
            ],
            out_csv.with_name(f"{out_csv.stem}_other_sports_input_diff.csv"),
        )
    if football_input_diff_rows:
        _write_rows_csv(
            football_input_diff_rows,
            [
                "ChangeStatus",
                "Date",
                "Sport",
                "League",
                "Code",
                "Home",
                "Away",
                "BeforePred",
                "AfterPred",
                "BeforeConf",
                "AfterConf",
                "BeforeOddsH",
                "AfterOddsH",
                "BeforeOddsD",
                "AfterOddsD",
                "BeforeOddsA",
                "AfterOddsA",
            ],
            out_csv.with_name(f"{out_csv.stem}_football_input_diff.csv"),
        )
    change_counts = _counter(paired_rows, "ChangeStatus")
    advisor_kept_transitions = _transition_counter(
        [row for row in paired_rows if row.get("ChangeStatus") == "KEPT"],
        before_field="BeforeDecision",
        after_field="AfterDecision",
    )
    guard_kept_transitions = _transition_counter(
        [row for row in paired_rows if row.get("ChangeStatus") == "KEPT"],
        before_field="BeforeGuardDecision",
        after_field="AfterGuardDecision",
    )
    other_input_status_counts = _counter(other_input_diff_rows, "ChangeStatus")
    football_input_status_counts = _counter(football_input_diff_rows, "ChangeStatus")
    other_input_transitions = _transition_counter(
        other_input_diff_rows,
        before_field="BeforeDecision",
        after_field="AfterDecision",
        status_field="ChangeStatus",
        allowed_statuses=("KEPT_IDENTICAL", "KEPT_CHANGED"),
    )
    football_input_transitions = _transition_counter(
        football_input_diff_rows,
        before_field="BeforePred",
        after_field="AfterPred",
        status_field="ChangeStatus",
        allowed_statuses=("KEPT_IDENTICAL", "KEPT_CHANGED"),
    )
    other_strategy_identical = _strategy_identical(other_input_diff_rows)
    football_strategy_identical = _strategy_identical(football_input_diff_rows)
    other_raw_identical = _raw_file_identical(
        Path(args.before_other_sports) if args.before_other_sports else None,
        Path(args.after_other_sports) if args.after_other_sports else None,
    )
    football_raw_identical = _raw_file_identical(before_football_path, after_football_path)
    _write_md(
        before_rows=before_rows,
        after_rows=after_rows,
        before_guard_rows=before_guard_rows,
        after_guard_rows=after_guard_rows,
        before_football_rows=before_football_rows,
        after_football_rows=after_football_rows,
        before_other_rows=before_other_rows,
        after_other_rows=after_other_rows,
        other_input_diff_rows=other_input_diff_rows,
        football_input_diff_rows=football_input_diff_rows,
        other_strategy_identical=other_strategy_identical,
        football_strategy_identical=football_strategy_identical,
        other_raw_identical=other_raw_identical,
        football_raw_identical=football_raw_identical,
        paired_rows=paired_rows,
        path=out_md,
    )
    _write_summary_json(
        {
            "before_advisor_rows": len(before_rows),
            "after_advisor_rows": len(after_rows),
            "before_guard_rows": len(before_guard_rows),
            "after_guard_rows": len(after_guard_rows),
            "before_football_rows": len(before_football_rows),
            "after_football_rows": len(after_football_rows),
            "before_other_rows": len(before_other_rows),
            "after_other_rows": len(after_other_rows),
            "advisor_change_counts": _counter_dict(change_counts),
            "advisor_kept_transitions": _counter_dict(advisor_kept_transitions),
            "guard_kept_transitions": _counter_dict(guard_kept_transitions),
            "other_input_change_counts": _counter_dict(other_input_status_counts),
            "football_input_change_counts": _counter_dict(football_input_status_counts),
            "other_input_transitions": _counter_dict(other_input_transitions),
            "football_input_transitions": _counter_dict(football_input_transitions),
            "other_strategy_identical": other_strategy_identical,
            "football_strategy_identical": football_strategy_identical,
            "other_raw_file_identical": other_raw_identical,
            "football_raw_file_identical": football_raw_identical,
        },
        out_json,
    )
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_md}")
    print(f"Wrote {out_json}")
    print(f"before_rows={len(before_rows)} after_rows={len(after_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
