#!/usr/bin/env python3
"""Validate Decision Brain gates against local historical artifacts."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_DIR / "reports"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.decision_brain import annotate_decision_brain, load_decision_profile, safe_decisions_only  # noqa: E402


def _read_csv(name: str) -> pd.DataFrame:
    path = REPORTS_DIR / name
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def _normalize_football(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    rename = {}
    if "HomeTeam" in out.columns and "Home" not in out.columns:
        rename["HomeTeam"] = "Home"
    if "AwayTeam" in out.columns and "Away" not in out.columns:
        rename["AwayTeam"] = "Away"
    if "Div" in out.columns and "Code" not in out.columns:
        rename["Div"] = "Code"
    out = out.rename(columns=rename)
    if "FavOdds" not in out.columns:
        for col in ["AvgH", "AvgD", "AvgA"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        if {"Pred", "AvgH", "AvgD", "AvgA"}.issubset(out.columns):
            out["FavOdds"] = out.apply(
                lambda row: row["AvgH"]
                if row.get("Pred") == "H"
                else (row["AvgA"] if row.get("Pred") == "A" else row["AvgD"]),
                axis=1,
            )
    if "Result" not in out.columns and {"Pred", "Actual"}.issubset(out.columns):
        out["Result"] = out.apply(
            lambda row: "correct" if str(row.get("Pred")) == str(row.get("Actual")) else "wrong",
            axis=1,
        )
    return out


def _evaluate(name: str, df: pd.DataFrame) -> dict[str, object]:
    df = _normalize_football(df)
    if df.empty:
        return {"dataset": name, "candidates": 0, "accepted": 0, "blocked": 0, "accuracy": None}
    annotated = annotate_decision_brain(df, "football", PROJECT_DIR)
    safe = safe_decisions_only(annotated)
    finished = safe[safe["Result"].isin(["correct", "wrong"])] if "Result" in safe.columns else pd.DataFrame()
    correct = int((finished["Result"] == "correct").sum()) if not finished.empty else 0
    wrong = int((finished["Result"] == "wrong").sum()) if not finished.empty else 0
    accuracy = correct / (correct + wrong) if (correct + wrong) else None
    blocked = annotated[annotated.get("BrainVerdict") != "ACCEPT"] if "BrainVerdict" in annotated.columns else pd.DataFrame()
    top_blocks = []
    if not blocked.empty and "BrainReasons" in blocked.columns:
        top_blocks = blocked["BrainReasons"].astype(str).str.split(";").str[0].value_counts().head(8).to_dict()
    return {
        "dataset": name,
        "candidates": int(len(annotated)),
        "accepted": int(len(safe)),
        "blocked": int(len(annotated) - len(safe)),
        "correct": correct,
        "wrong": wrong,
        "accuracy": accuracy,
        "top_blocks": top_blocks,
    }


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def build_report() -> str:
    profile = load_decision_profile(PROJECT_DIR)
    active_profile = os.environ.get("STRATEGY_DASHBOARD_BRAIN_PROFILE") or str(profile.get("active_profile") or "default")
    datasets = [
        ("strategy_98pct_balanced_picks", _read_csv("strategy_98pct_balanced_picks.csv")),
        ("primary_strategy_strict_picks", _read_csv("primary_strategy_strict_picks.csv")),
        ("base96_current_season_CHN_excluded_picks", _read_csv("base96_current_season_CHN_excluded_picks.csv")),
    ]
    results = [_evaluate(name, df) for name, df in datasets]
    lines = [
        "# Decision Brain Validation",
        f"- Date: {date.today().isoformat()}",
        "- Source: local historical reports only",
        "- Network: not used",
        f"- Active profile: {active_profile}",
        "",
        "| Dataset | Candidates | Accepted | Blocked | Correct | Wrong | Accuracy |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in results:
        lines.append(
            "| {dataset} | {candidates} | {accepted} | {blocked} | {correct} | {wrong} | {accuracy} |".format(
                dataset=row["dataset"],
                candidates=row["candidates"],
                accepted=row["accepted"],
                blocked=row["blocked"],
                correct=row.get("correct", 0),
                wrong=row.get("wrong", 0),
                accuracy=_pct(row.get("accuracy")),  # type: ignore[arg-type]
            )
        )
    lines.extend(["", "## Main Block Reasons", ""])
    for row in results:
        lines.append(f"### {row['dataset']}")
        top = row.get("top_blocks") or {}
        if not top:
            lines.append("- No blocked rows or no reasons available.")
            continue
        for reason, count in top.items():
            lines.append(f"- {reason}: {count}")
        lines.append("")
    lines.extend(
        [
            "## Interpretation",
            "",
            "- `Accepted` rows are the only rows allowed to appear as decision-grade picks.",
            "- `Blocked` rows remain useful for analysis, but they are not treated as decisions.",
            "- If accuracy improves only by destroying coverage, tune profile thresholds deliberately and rerun this validation.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=f"reports/decision_brain_validation_{date.today().isoformat()}.md")
    args = ap.parse_args()
    out_path = (PROJECT_DIR / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(), encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
