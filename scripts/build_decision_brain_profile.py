#!/usr/bin/env python3
"""Build a strict local decision-brain profile from existing reports only."""
from __future__ import annotations

import argparse
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_DIR / "reports"


def _read_csv(name: str) -> pd.DataFrame:
    path = REPORTS_DIR / name
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def _validation_datasets() -> list[dict[str, Any]]:
    league = _read_csv("primary_strategy_europe_per_league_summary.csv")
    league_acc: dict[str, float] = {}
    if not league.empty and {"Code", "PrimaryAcc"}.issubset(league.columns):
        league_acc = dict(zip(league["Code"].astype(str), pd.to_numeric(league["PrimaryAcc"], errors="coerce")))

    datasets: list[dict[str, Any]] = []
    for name in [
        "strategy_98pct_balanced_picks.csv",
        "primary_strategy_strict_picks.csv",
        "base96_current_season_CHN_excluded_picks.csv",
    ]:
        df = _read_csv(name)
        if df.empty:
            continue
        df = df.copy()
        if "Div" in df.columns and "Code" not in df.columns:
            df["Code"] = df["Div"]
        if "Actual" not in df.columns and "FTR" in df.columns:
            df["Actual"] = df["FTR"]
        if "CorrectFlag" in df.columns:
            correct = df["CorrectFlag"].astype(bool).to_numpy()
        elif {"Pred", "Actual"}.issubset(df.columns):
            correct = (df["Pred"].astype(str) == df["Actual"].astype(str)).to_numpy()
        elif "Correct" in df.columns:
            correct = pd.to_numeric(df["Correct"], errors="coerce").fillna(0).astype(bool).to_numpy()
        else:
            continue
        code = df.get("Code", pd.Series("", index=df.index)).astype(str)
        datasets.append(
            {
                "name": name,
                "conf": pd.to_numeric(df.get("Conf"), errors="coerce").fillna(-999).to_numpy(),
                "probd": pd.to_numeric(df.get("ProbD"), errors="coerce").fillna(999).to_numpy(),
                "margin": pd.to_numeric(df.get("ProbMargin"), errors="coerce").fillna(-999).to_numpy(),
                "gad": pd.to_numeric(df.get("GADiff"), errors="coerce").to_numpy(),
                "gdd": pd.to_numeric(df.get("GDDiff"), errors="coerce").to_numpy(),
                "league_acc": code.map(league_acc).fillna(-1).to_numpy(),
                "correct": correct,
            }
        )
    return datasets


def _candidate_stats(datasets: list[dict[str, Any]], candidate: dict[str, Any]) -> tuple[int, int, float, list[dict[str, Any]]]:
    total = 0
    wrong_total = 0
    min_accuracy = 1.0
    rows: list[dict[str, Any]] = []
    for data in datasets:
        gad = np.nan_to_num(data["gad"], nan=999.0)
        mask = (
            (data["conf"] >= candidate["min_conf"])
            & (data["probd"] <= candidate["max_draw_prob"])
            & (data["margin"] >= candidate["min_prob_margin"])
            & (data["league_acc"] >= candidate["min_league_acc"])
            & ~np.isnan(data["gdd"])
            & ~np.isnan(data["gad"])
        )
        if candidate["max_gadiff"] < 999:
            mask &= gad <= candidate["max_gadiff"]
        n = int(mask.sum())
        wrong = int((~data["correct"][mask]).sum()) if n else 0
        accuracy = float(data["correct"][mask].mean()) if n else 1.0
        total += n
        wrong_total += wrong
        min_accuracy = min(min_accuracy, accuracy)
        rows.append({"dataset": data["name"], "accepted": n, "wrong": wrong, "accuracy": accuracy})
    return total, wrong_total, min_accuracy, rows


def _fit_football_profiles() -> dict[str, dict[str, Any]]:
    datasets = _validation_datasets()
    if not datasets:
        base_profile = {
            "min_conf": 0.70,
            "max_draw_prob": 0.20,
            "min_prob_margin": 0.35,
            "max_gadiff": 1.0,
            "min_league_acc": 0.96,
            "accept_score": 60,
            "min_long_acc": 0.0,
            "min_long_samples": 999999,
        }
        return {"ultra_safe": base_profile, "balanced_precision": dict(base_profile)}

    candidates: list[dict[str, Any]] = []
    for min_conf, max_draw, min_margin, max_gadiff, min_league_acc in itertools.product(
        [0.56, 0.58, 0.60, 0.62, 0.65, 0.68, 0.70, 0.72, 0.75, 0.78],
        [0.18, 0.20, 0.22, 0.24, 0.26, 0.28],
        [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55],
        [0.0, 0.5, 1.0, 1.5, 2.0, 999.0],
        [0.875, 0.90, 0.92, 0.94, 0.96, 1.0],
    ):
        candidate = {
            "min_conf": min_conf,
            "max_draw_prob": max_draw,
            "min_prob_margin": min_margin,
            "max_gadiff": max_gadiff,
            "min_league_acc": min_league_acc,
            "accept_score": 60,
            "min_long_acc": 0.0,
            "min_long_samples": 999999,
        }
        total, wrong, min_accuracy, rows = _candidate_stats(datasets, candidate)
        if total < 30:
            continue
        candidate.update(
            {
                "observed_total": total,
                "observed_wrong": wrong,
                "observed_min_accuracy": min_accuracy,
                "observed_rows": rows,
                "fit_source": "multi_local_validation",
            }
        )
        candidates.append(candidate)

    zero_wrong = [c for c in candidates if c["observed_wrong"] == 0]
    if zero_wrong:
        ultra_safe = sorted(zero_wrong, key=lambda c: c["observed_total"], reverse=True)[0]
    else:
        ultra_safe = sorted(candidates, key=lambda c: (c["observed_min_accuracy"], c["observed_total"]), reverse=True)[0]

    balanced_pool: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["observed_total"] < 60:
            continue
        valid = True
        for row in candidate["observed_rows"]:
            if row["accepted"] >= 20 and row["accuracy"] < 0.985:
                valid = False
            if row["accepted"] > 0 and row["accuracy"] < 0.965:
                valid = False
            if row["wrong"] > 2:
                valid = False
        if valid:
            balanced_pool.append(candidate)
    balanced_precision = sorted(
        balanced_pool or candidates,
        key=lambda c: (c["observed_total"], -c["observed_wrong"], c["observed_min_accuracy"]),
        reverse=True,
    )[0]

    return {"ultra_safe": ultra_safe, "balanced_precision": balanced_precision}


def _parse_long_horizon_table(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line.startswith("| ") or line.startswith("| ---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 7 or cells[0] == "Code":
            continue
        code, league, matches, primary, correct, wrong, acc = cells[:7]
        try:
            out[code] = {
                "league": league,
                "long_matches": int(float(matches)),
                "long_picks": int(float(primary)),
                "long_correct": int(float(correct)),
                "long_wrong": int(float(wrong)),
                "long_acc": float(acc.rstrip("%")) / 100.0,
            }
        except Exception:
            continue
    return out


def _fit_football_thresholds() -> dict[str, Any]:
    df = _read_csv("base96_current_season_CHN_excluded_picks.csv")
    source_name = "base96_current_season_CHN_excluded_picks.csv"
    if df.empty:
        df = _read_csv("strategy_98pct_balanced_picks.csv")
        source_name = "strategy_98pct_balanced_picks.csv"
    if df.empty:
        return {
            "min_conf": 0.68,
            "max_draw_prob": 0.22,
            "min_prob_margin": 0.25,
            "max_gadiff": 1.0,
            "min_league_acc": 0.96,
            "observed_precision": None,
            "observed_picks": 0,
            "fit_source": "none",
        }
    df = df.copy()
    for col in ["Conf", "ProbD", "ProbMargin", "GADiff"]:
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    if "CorrectFlag" in df.columns:
        df["CorrectBool"] = df["CorrectFlag"].astype(bool)
    else:
        df["CorrectBool"] = df["Pred"].astype(str) == df["Actual"].astype(str)

    league = _read_csv("primary_strategy_europe_per_league_summary.csv")
    league_acc: dict[str, float] = {}
    if not league.empty and {"Code", "PrimaryAcc"}.issubset(league.columns):
        league_acc = dict(zip(league["Code"].astype(str), pd.to_numeric(league["PrimaryAcc"], errors="coerce")))
    df["LeagueAcc"] = df["Code"].astype(str).map(league_acc)

    candidates = []
    for min_conf in [0.68, 0.70, 0.72, 0.75, 0.78, 0.80, 0.82]:
        for max_draw in [0.16, 0.18, 0.20, 0.22, 0.24]:
            for min_margin in [0.35, 0.40, 0.45, 0.50, 0.55]:
                for max_gadiff in [0.0, 0.5, 1.0]:
                    for min_league_acc in [0.90, 0.92, 0.94, 0.96, 1.0]:
                        mask = (
                            (df["Conf"] >= min_conf)
                            & (df["ProbD"] <= max_draw)
                            & (df["ProbMargin"] >= min_margin)
                            & (df["GDDiff"].notna())
                            & (df["GADiff"].fillna(999) <= max_gadiff)
                            & (df["LeagueAcc"].fillna(0.92) >= min_league_acc)
                        )
                        picks = int(mask.sum())
                        if picks < 20:
                            continue
                        precision = float(df.loc[mask, "CorrectBool"].mean())
                        wrong = int((~df.loc[mask, "CorrectBool"]).sum())
                        candidates.append(
                            {
                                "precision": precision,
                                "coverage": picks / len(df),
                                "picks": picks,
                                "wrong": wrong,
                                "min_conf": min_conf,
                                "max_draw_prob": max_draw,
                                "min_prob_margin": min_margin,
                                "max_gadiff": max_gadiff,
                                "min_league_acc": min_league_acc,
                            }
                        )
    if not candidates:
        return {
            "min_conf": 0.68,
            "max_draw_prob": 0.22,
            "min_prob_margin": 0.25,
            "max_gadiff": 1.0,
            "min_league_acc": 0.96,
            "observed_precision": None,
            "observed_picks": 0,
            "fit_source": source_name,
        }
    candidates.sort(key=lambda item: (item["precision"], item["picks"]), reverse=True)
    best = candidates[0]
    return {
        "min_conf": best["min_conf"],
        "max_draw_prob": best["max_draw_prob"],
        "min_prob_margin": best["min_prob_margin"],
        "max_gadiff": best["max_gadiff"],
        "min_league_acc": best["min_league_acc"],
        "observed_precision": best["precision"],
        "observed_coverage": best["coverage"],
        "observed_picks": best["picks"],
        "observed_wrong": best["wrong"],
        "fit_source": source_name,
    }


def _league_memory() -> dict[str, dict[str, Any]]:
    memory: dict[str, dict[str, Any]] = {}
    league = _read_csv("primary_strategy_europe_per_league_summary.csv")
    if not league.empty:
        for _, row in league.iterrows():
            code = str(row.get("Code", "")).strip()
            if not code:
                continue
            memory[code] = {
                "league": row.get("League"),
                "primary_picks": int(pd.to_numeric(row.get("PrimaryPicks"), errors="coerce") or 0),
                "primary_correct": int(pd.to_numeric(row.get("PrimaryCorrect"), errors="coerce") or 0),
                "primary_wrong": int(pd.to_numeric(row.get("PrimaryWrong"), errors="coerce") or 0),
                "primary_acc": float(pd.to_numeric(row.get("PrimaryAcc"), errors="coerce") or 0.0),
            }
    long_memory = _parse_long_horizon_table(REPORTS_DIR / "primary_strategy_strict_3y_backtest.md")
    for code, info in long_memory.items():
        memory.setdefault(code, {"league": info.get("league")}).update(info)
    return memory


def build_profile() -> dict[str, Any]:
    fitted_profiles = _fit_football_profiles()
    active_profile = "balanced_precision"
    fitted = dict(fitted_profiles.get(active_profile) or fitted_profiles.get("ultra_safe") or _fit_football_thresholds())
    league_memory = _league_memory()
    for profile_cfg in fitted_profiles.values():
        profile_cfg["league_memory"] = league_memory
    profile = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "strict_precision",
        "active_profile": active_profile,
        "source_reports": [
            "strategy_98pct_balanced_picks.csv",
            "primary_strategy_strict_picks.csv",
            "base96_current_season_CHN_excluded_picks.csv",
            "primary_strategy_europe_per_league_summary.csv",
            "primary_strategy_strict_3y_backtest.md",
        ],
        "football": {
            "min_conf": fitted["min_conf"],
            "max_draw_prob": fitted["max_draw_prob"],
            "min_prob_margin": fitted["min_prob_margin"],
            "max_gadiff": fitted["max_gadiff"],
            "min_league_acc": fitted.get("min_league_acc", 0.96),
            "min_league_samples": 8,
            "min_long_acc": fitted.get("min_long_acc", 0.0),
            "min_long_samples": fitted.get("min_long_samples", 999999),
            "accept_score": fitted.get("accept_score", 60),
            "elite_conf": 0.78,
            "elite_draw_prob": 0.16,
            "elite_margin": 0.60,
            "observed_precision": fitted.get("observed_precision"),
            "observed_coverage": fitted.get("observed_coverage"),
            "observed_picks": fitted.get("observed_picks"),
            "observed_wrong": fitted.get("observed_wrong"),
            "observed_total": fitted.get("observed_total"),
            "observed_min_accuracy": fitted.get("observed_min_accuracy"),
            "observed_rows": fitted.get("observed_rows"),
            "fit_source": fitted.get("fit_source"),
            "league_memory": league_memory,
        },
        "profiles": {"ultra_safe": {"football": fitted_profiles["ultra_safe"]}, "balanced_precision": {"football": fitted_profiles["balanced_precision"]}},
        "basketball": {"min_prob": 0.60, "min_margin": 0.08, "accept_score": 72},
        "tennis": {"min_prob": 0.60, "min_elo_edge": 120, "accept_score": 72},
        "hockey": {"min_prob": 0.60, "max_odds": 2.05, "accept_score": 72},
        "tabletennis": {"min_prob": 0.60, "min_margin": 0.06, "accept_score": 72},
    }
    return profile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/decision_brain_profile.json")
    args = ap.parse_args()
    profile = build_profile()
    out_path = (PROJECT_DIR / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)
    football = profile["football"]
    print(
        "football_profile",
        {
            "min_conf": football["min_conf"],
            "max_draw_prob": football["max_draw_prob"],
            "min_prob_margin": football["min_prob_margin"],
            "max_gadiff": football["max_gadiff"],
            "min_league_acc": football["min_league_acc"],
            "observed_precision": football.get("observed_precision"),
            "observed_total": football.get("observed_total"),
            "observed_min_accuracy": football.get("observed_min_accuracy"),
            "observed_picks": football.get("observed_picks"),
            "observed_wrong": football.get("observed_wrong"),
            "fit_source": football.get("fit_source"),
            "active_profile": profile.get("active_profile"),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
