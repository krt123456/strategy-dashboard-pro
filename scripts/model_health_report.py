#!/usr/bin/env python3
"""Build a local model-health report from existing reports only.

This script intentionally does not fetch network data. It reads the CSV/MD
artifacts already present in reports/ and writes a compact health snapshot.
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_DIR / "reports"


def _pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _read_csv(name: str) -> pd.DataFrame:
    path = REPORTS_DIR / name
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def _md_metric(name: str, label: str) -> str | None:
    path = REPORTS_DIR / name
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(rf"^- {re.escape(label)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _table(rows: Iterable[Iterable[object]], headers: list[str]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join("" if x is None else str(x) for x in row) + " |")
    return lines


def build_report() -> str:
    lines: list[str] = [
        "# Model Health Report",
        f"- Date: {date.today().isoformat()}",
        "- Source: local reports/ artifacts only",
        "- Network: not used",
        "",
    ]

    lines.extend(["## Validation Snapshots", ""])
    snapshot_rows = []
    for file_name, label in [
        ("primary_strategy_strict_current_season_backtest.md", "current season strict"),
        ("primary_strategy_strict_1y_backtest.md", "1y strict"),
        ("primary_strategy_strict_3y_backtest.md", "3y strict"),
    ]:
        snapshot_rows.append(
            [
                label,
                _md_metric(file_name, "Range") or "missing",
                _md_metric(file_name, "Primary picks") or "missing",
                _md_metric(file_name, "Correct") or "missing",
                _md_metric(file_name, "Wrong") or "missing",
                _md_metric(file_name, "Accuracy") or "missing",
            ]
        )
    lines.extend(_table(snapshot_rows, ["Test", "Range", "Picks", "Correct", "Wrong", "Accuracy"]))
    lines.append("")

    base = _read_csv("base96_current_season_CHN_excluded_picks.csv")
    wrong = _read_csv("base96_wrong_current_season_CHN_excluded.csv")
    lines.extend(["## Base96 Current Season", ""])
    if base.empty:
        lines.append("- Missing base96_current_season_CHN_excluded_picks.csv")
    else:
        wrong_count = len(wrong) if not wrong.empty else None
        accuracy = None
        if wrong_count is not None and len(base):
            accuracy = 1.0 - (wrong_count / len(base))
        lines.extend(
            [
                f"- Picks: {len(base)}",
                f"- Known wrong rows: {wrong_count if wrong_count is not None else 'missing'}",
                f"- Approx accuracy from wrong file: {_pct(accuracy)}",
            ]
        )
        if "Actual" in base.columns:
            actual_counts = base["Actual"].value_counts(dropna=False).to_dict()
            lines.append(f"- Actual distribution: {actual_counts}")
        feature_rows = []
        for col in [
            "Conf",
            "ProbD",
            "ProbMargin",
            "GDDiff",
            "GADiff",
            "XGDiff",
            "InjuryDiff",
            "LineupDiff",
            "TravelKm",
            "RefDrawRate",
        ]:
            if col in base.columns:
                feature_rows.append([col, f"{base[col].isna().mean() * 100:.1f}%"])
        if feature_rows:
            lines.extend(["", "### Feature Missingness", ""])
            lines.extend(_table(feature_rows, ["Feature", "Missing"]))
    lines.append("")

    lines.extend(["## Error Pattern", ""])
    if wrong.empty:
        lines.append("- Missing base96_wrong_current_season_CHN_excluded.csv")
    else:
        lines.append(f"- Wrong rows: {len(wrong)}")
        if "Actual" in wrong.columns:
            actual_counts = wrong["Actual"].value_counts(dropna=False).to_dict()
            draw_count = int((wrong["Actual"] == "D").sum())
            draw_share = draw_count / len(wrong) if len(wrong) else None
            lines.append(f"- Wrong actual distribution: {actual_counts}")
            lines.append(f"- Draw share among wrong rows: {draw_count}/{len(wrong)} = {_pct(draw_share)}")
        if "Pred" in wrong.columns:
            lines.append(f"- Wrong prediction distribution: {wrong['Pred'].value_counts(dropna=False).to_dict()}")
        if "LeagueName" in wrong.columns:
            top = wrong["LeagueName"].value_counts().head(10)
            lines.extend(["", "### Top Wrong Leagues", ""])
            lines.extend(_table(top.reset_index().values.tolist(), ["League", "Wrong"]))
        sample_cols = [
            col
            for col in ["Date", "LeagueName", "HomeTeam", "AwayTeam", "Pred", "Actual", "Conf", "ProbD", "GDDiff"]
            if col in wrong.columns
        ]
        if sample_cols:
            sample = wrong[sample_cols].head(10).copy()
            lines.extend(["", "### Wrong Sample", ""])
            lines.extend(_table(sample.values.tolist(), sample_cols))
    lines.append("")

    platform = _read_csv("platform_full_test_summary_with_counts.csv")
    lines.extend(["## Coverage And Data Sources", ""])
    if platform.empty:
        lines.append("- Missing platform_full_test_summary_with_counts.csv")
    else:
        lines.append(f"- Competitions in audit: {len(platform)}")
        if "Status" in platform.columns:
            lines.append(f"- Status counts: {platform['Status'].value_counts(dropna=False).to_dict()}")
        if "Wrong" in platform.columns:
            cols = [c for c in ["Region", "Competition", "Code", "Status", "Picks", "Correct", "Wrong", "Accuracy", "Coverage"] if c in platform.columns]
            top_wrong = platform[platform["Wrong"].fillna(0) > 0].sort_values(["Wrong", "Picks"], ascending=False).head(12)
            if not top_wrong.empty:
                top_wrong = top_wrong[cols].copy()
                if "Accuracy" in top_wrong.columns:
                    top_wrong["Accuracy"] = top_wrong["Accuracy"].map(lambda v: "n/a" if pd.isna(v) else f"{float(v):.2f}%")
                lines.extend(["", "### Highest Wrong Counts", ""])
                lines.extend(_table(top_wrong.values.tolist(), cols))
    lines.append("")

    eu = _read_csv("primary_strategy_europe_per_league_summary.csv")
    lines.extend(["## League Reliability", ""])
    if eu.empty:
        lines.append("- Missing primary_strategy_europe_per_league_summary.csv")
    else:
        cols = [c for c in ["Code", "League", "PrimaryPicks", "PrimaryCorrect", "PrimaryWrong", "PrimaryAcc"] if c in eu.columns]
        if "PrimaryAcc" in eu.columns:
            weakest = eu.sort_values(["PrimaryAcc", "PrimaryPicks"], ascending=[True, False]).head(12)[cols].copy()
            weakest["PrimaryAcc"] = weakest["PrimaryAcc"].map(_pct)
            lines.extend(_table(weakest.values.tolist(), cols))
    lines.append("")

    lines.extend(
        [
            "## Recommended Next Actions",
            "",
            "1. Build and test a draw-risk gate before adding more hard filters.",
            "2. Add calibration metrics beside accuracy and coverage.",
            "3. Treat missing advanced features as explicit risk signals.",
            "4. Require 1y and 3y validation before accepting a new strategy.",
            "5. Prioritize leagues with repeated wrong rows and enough sample size.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=f"reports/model_health_{date.today().isoformat()}.md")
    args = ap.parse_args()
    out_path = (PROJECT_DIR / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(), encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
