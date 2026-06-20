#!/usr/bin/env python3
"""Elite table tennis strategy with per-league adaptive thresholds.

Builds ELO/form/rest features once, then searches per-league thresholds
that maximize accuracy while keeping coverage. Outputs picks + summaries
and a reusable league-rules file.
"""
from __future__ import annotations

import argparse
import itertools
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any

import pandas as pd
import unicodedata
import yaml
import numpy as np


HOME_ADV_ELO = 0.0
BASE_ELO = 1500.0
ELO_K = 22.0
FORM_WINDOW = 6


@dataclass
class TeamForm:
    points: float
    gd: float
    gf: float
    ga: float
    games: int


def _implied_probs(odd_h: float | None, odd_a: float | None) -> Tuple[float | None, float | None]:
    if not odd_h or not odd_a:
        return None, None
    ph = 1.0 / odd_h if odd_h > 0 else None
    pa = 1.0 / odd_a if odd_a > 0 else None
    if ph is None or pa is None:
        return None, None
    total = ph + pa
    if total <= 0:
        return None, None
    return ph / total, pa / total


def _elo_prob(elo_diff: float) -> float:
    return 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))


def _update_elo(elo_home: float, elo_away: float, home_goals: int, away_goals: int) -> Tuple[float, float]:
    diff = elo_home - elo_away
    expected_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    score_home = 1.0 if home_goals > away_goals else 0.0
    mov = abs(home_goals - away_goals)
    mov_mult = (2.2 / (abs(diff) * 0.001 + 2.2)) * (1.0 + mov / 6.0)
    k = ELO_K * mov_mult
    new_home = elo_home + k * (score_home - expected_home)
    new_away = elo_away - k * (score_home - expected_home)
    return new_home, new_away


def _form_stats(history: List[Tuple[int, int, int]], window: int = FORM_WINDOW) -> TeamForm:
    recent = history[-window:]
    if not recent:
        return TeamForm(0.0, 0.0, 0.0, 0.0, 0)
    pts = 0.0
    gd = 0.0
    gf = 0.0
    ga = 0.0
    for gf_i, ga_i, outcome in recent:
        gf += gf_i
        ga += ga_i
        gd += gf_i - ga_i
        pts += outcome
    return TeamForm(pts, gd, gf, ga, len(recent))


def _load_matches(data_dir: Path) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []
    for path in sorted(data_dir.glob("*.csv")):
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    df = pd.concat(rows, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam"])
    df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
    df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")
    if "AvgH" in df.columns:
        df["AvgH"] = pd.to_numeric(df["AvgH"], errors="coerce")
    else:
        df["AvgH"] = None
    if "AvgA" in df.columns:
        df["AvgA"] = pd.to_numeric(df["AvgA"], errors="coerce")
    else:
        df["AvgA"] = None
    if "Season" not in df.columns:
        df["Season"] = df["Date"].dt.year.astype(str)
    if "League" not in df.columns:
        df["League"] = "Unknown"
    return df.sort_values("Date").reset_index(drop=True)


def _actual_result(home_goals: float | None, away_goals: float | None) -> str | None:
    if home_goals is None or away_goals is None:
        return None
    return "H" if home_goals > away_goals else "A"


def _norm_league(name: str) -> str:
    name = name.strip()
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower()
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_feature_frame(
    df: pd.DataFrame,
    *,
    weight_market: float = 0.0,
    player_acc_min: float | None = 0.92,
    player_min_picks: int = 30,
    player_strict_prob_boost: float = 0.05,
    player_strict_margin_boost: float = 0.03,
) -> pd.DataFrame:
    if df.empty:
        return df

    elo: Dict[str, float] = {}
    last_date: Dict[str, date] = {}
    form_hist: Dict[str, List[Tuple[int, int, int]]] = {}
    season_games: Dict[Tuple[str, str], int] = {}
    player_stats: Dict[str, Dict[str, float]] = {}

    rows = []
    for _, row in df.iterrows():
        match_date = row["Date"].date()
        home = str(row["HomeTeam"]).strip()
        away = str(row["AwayTeam"]).strip()
        league = str(row.get("League", ""))
        season = str(row.get("Season", ""))

        elo_home = elo.get(home, BASE_ELO)
        elo_away = elo.get(away, BASE_ELO)

        rest_home = (match_date - last_date[home]).days if home in last_date else None
        rest_away = (match_date - last_date[away]).days if away in last_date else None
        rest_diff = rest_home - rest_away if rest_home is not None and rest_away is not None else None

        form_home = _form_stats(form_hist.get(home, []))
        form_away = _form_stats(form_hist.get(away, []))
        form_pts_diff = (form_home.points / form_home.games - form_away.points / form_away.games) if form_home.games and form_away.games else None
        gd_diff = (form_home.gd / form_home.games - form_away.gd / form_away.games) if form_home.games and form_away.games else None

        elo_diff = (elo_home + HOME_ADV_ELO) - elo_away
        elo_home_prob = _elo_prob(elo_diff)

        m_ph, m_pa = _implied_probs(row.get("AvgH"), row.get("AvgA"))
        if m_ph is None:
            prob_home = elo_home_prob
            market_probs = (None, None)
        else:
            market_probs = (m_ph, m_pa)
            prob_home = weight_market * m_ph + (1.0 - weight_market) * elo_home_prob

        prob_away = 1.0 - prob_home
        if prob_home >= prob_away:
            pred = "H"
            prob = prob_home
            implied = market_probs[0]
        else:
            pred = "A"
            prob = prob_away
            implied = market_probs[1]

        prob_margin = abs(prob_home - prob_away)
        edge = None
        if implied is not None:
            edge = prob - implied

        games_home = season_games.get((season, home), 0)
        games_away = season_games.get((season, away), 0)
        min_games = min(games_home, games_away)

        # rolling player strictness
        player_strict = False
        if player_acc_min is not None:
            for name in (home, away):
                stats = player_stats.get(name)
                if not stats:
                    continue
                if stats["picks"] >= player_min_picks:
                    acc = stats["correct"] / max(1.0, stats["picks"])
                    if acc < player_acc_min:
                        player_strict = True
                        break

        actual = _actual_result(row.get("FTHG"), row.get("FTAG"))
        correct = None
        if actual is not None:
            correct = int(pred == actual)
            for name in (home, away):
                stats = player_stats.setdefault(name, {"picks": 0.0, "correct": 0.0})
                stats["picks"] += 1.0
                stats["correct"] += float(correct)

        rows.append(
            {
                "Date": row["Date"],
                "League": league,
                "HomeTeam": home,
                "AwayTeam": away,
                "Pred": pred,
                "Prob": prob,
                "ProbMargin": prob_margin,
                "Edge": edge,
                "RestDiff": rest_diff,
                "FormPtsDiff": form_pts_diff,
                "GDDiff": gd_diff,
                "MinGames": min_games,
                "PlayerStrict": int(player_strict),
                "Actual": actual,
                "Correct": correct,
            }
        )

        if actual is not None:
            new_home, new_away = _update_elo(elo_home, elo_away, int(row["FTHG"]), int(row["FTAG"]))
            elo[home] = new_home
            elo[away] = new_away
            last_date[home] = match_date
            last_date[away] = match_date
            season_games[(season, home)] = games_home + 1
            season_games[(season, away)] = games_away + 1

            outcome_home = 1 if actual == "H" else 0
            outcome_away = 1 if actual == "A" else 0
            form_hist.setdefault(home, []).append((int(row["FTHG"]), int(row["FTAG"]), outcome_home))
            form_hist.setdefault(away, []).append((int(row["FTAG"]), int(row["FTHG"]), outcome_away))

    return pd.DataFrame(rows)


def apply_thresholds(df: pd.DataFrame, params: Dict[str, float]) -> pd.DataFrame:
    if df.empty:
        return df

    eff_min_prob = params.get("min_prob", 0.7)
    eff_min_margin = params.get("min_prob_margin", 0.12)
    min_games = params.get("min_games", 4)
    max_rest = params.get("max_rest_disadv", 2)
    min_form = params.get("min_form_diff", 0.0)
    min_gd = params.get("min_gd_diff", 0.0)
    prob_boost = params.get("player_strict_prob_boost", 0.05)
    margin_boost = params.get("player_strict_margin_boost", 0.03)

    df = df.copy()
    strict_mask = df["PlayerStrict"] == 1
    df.loc[strict_mask, "EffMinProb"] = eff_min_prob + prob_boost
    df.loc[~strict_mask, "EffMinProb"] = eff_min_prob
    df.loc[strict_mask, "EffMinMargin"] = eff_min_margin + margin_boost
    df.loc[~strict_mask, "EffMinMargin"] = eff_min_margin

    min_games_ok = df["MinGames"] >= min_games

    prob_ok = df["Prob"] >= df["EffMinProb"]
    margin_ok = df["ProbMargin"] >= df["EffMinMargin"]

    rest_ok = pd.Series(True, index=df.index)
    has_rest = df["RestDiff"].notna()
    rest_ok.loc[has_rest & (df["Pred"] == "H")] = df.loc[has_rest & (df["Pred"] == "H"), "RestDiff"] >= -max_rest
    rest_ok.loc[has_rest & (df["Pred"] == "A")] = df.loc[has_rest & (df["Pred"] == "A"), "RestDiff"] <= max_rest

    form_ok = pd.Series(True, index=df.index)
    if min_form > 0:
        has_form = df["FormPtsDiff"].notna()
        form_ok.loc[has_form & (df["Pred"] == "H")] = df.loc[has_form & (df["Pred"] == "H"), "FormPtsDiff"] >= min_form
        form_ok.loc[has_form & (df["Pred"] == "A")] = df.loc[has_form & (df["Pred"] == "A"), "FormPtsDiff"] <= -min_form

    gd_ok = pd.Series(True, index=df.index)
    if min_gd > 0:
        has_gd = df["GDDiff"].notna()
        gd_ok.loc[has_gd & (df["Pred"] == "H")] = df.loc[has_gd & (df["Pred"] == "H"), "GDDiff"] >= min_gd
        gd_ok.loc[has_gd & (df["Pred"] == "A")] = df.loc[has_gd & (df["Pred"] == "A"), "GDDiff"] <= -min_gd

    qualifies = min_games_ok & prob_ok & margin_ok & rest_ok & form_ok & gd_ok
    df["Qualifies"] = qualifies.astype(int)
    df["Score"] = (df["Prob"] * 100.0).round(1)
    return df


def tune_league_thresholds(
    df: pd.DataFrame,
    *,
    target_acc: float = 0.95,
    min_picks: int = 40,
    grid_min_prob: List[float] | None = None,
    grid_margin: List[float] | None = None,
    grid_min_games: List[int] | None = None,
    grid_rest: List[int] | None = None,
    grid_form: List[float] | None = None,
    grid_gd: List[float] | None = None,
) -> Dict[str, Dict[str, float]]:
    if df.empty:
        return {}

    grid_min_prob = grid_min_prob or [0.72, 0.76, 0.80, 0.84, 0.86, 0.88, 0.90, 0.92]
    grid_margin = grid_margin or [0.08, 0.10, 0.12, 0.15, 0.18, 0.22]
    grid_min_games = grid_min_games or [3, 4, 5, 6]
    grid_rest = grid_rest or [1, 2, 3]
    grid_form = grid_form or [0.0, 0.1, 0.15, 0.2]
    grid_gd = grid_gd or [0.0, 0.2, 0.3, 0.4]

    combos = list(itertools.product(grid_min_prob, grid_margin, grid_min_games, grid_rest, grid_form, grid_gd))

    output: Dict[str, Dict[str, float]] = {}
    prob_boost = 0.05
    margin_boost = 0.03

    for league, g in df.groupby("League"):
        g = g[g["Correct"].notna()]
        if g.empty:
            continue

        prob = g["Prob"].to_numpy(float)
        margin = g["ProbMargin"].to_numpy(float)
        min_games_arr = g["MinGames"].to_numpy(int)
        rest = g["RestDiff"].to_numpy(float)
        form = g["FormPtsDiff"].to_numpy(float)
        gd = g["GDDiff"].to_numpy(float)
        pred_home = (g["Pred"].to_numpy(str) == "H")
        strict = g["PlayerStrict"].to_numpy(int)
        correct_arr = g["Correct"].to_numpy(float)

        best = None
        for min_prob, min_margin, min_games, max_rest, min_form, min_gd in combos:
            eff_min_prob = min_prob + strict * prob_boost
            eff_min_margin = min_margin + strict * margin_boost

            mask = min_games_arr >= min_games
            mask &= prob >= eff_min_prob
            mask &= margin >= eff_min_margin

            # rest filter
            if max_rest is not None:
                rest_mask = np.ones_like(mask, dtype=bool)
                has_rest = ~np.isnan(rest)
                if has_rest.any():
                    idx = has_rest & pred_home
                    rest_mask[idx] = rest[idx] >= -max_rest
                    idx = has_rest & ~pred_home
                    rest_mask[idx] = rest[idx] <= max_rest
                mask &= rest_mask

            # form filter
            if min_form > 0:
                form_mask = np.ones_like(mask, dtype=bool)
                has_form = ~np.isnan(form)
                if has_form.any():
                    idx = has_form & pred_home
                    form_mask[idx] = form[idx] >= min_form
                    idx = has_form & ~pred_home
                    form_mask[idx] = form[idx] <= -min_form
                mask &= form_mask

            # gd filter
            if min_gd > 0:
                gd_mask = np.ones_like(mask, dtype=bool)
                has_gd = ~np.isnan(gd)
                if has_gd.any():
                    idx = has_gd & pred_home
                    gd_mask[idx] = gd[idx] >= min_gd
                    idx = has_gd & ~pred_home
                    gd_mask[idx] = gd[idx] <= -min_gd
                mask &= gd_mask

            total = int(mask.sum())
            if total < min_picks:
                continue
            correct = float(np.nansum(correct_arr[mask]))
            acc = correct / total if total else 0.0
            if acc < target_acc:
                continue
            score = (total, acc)
            if best is None or score > (best["Picks"], best["Accuracy"]):
                best = {
                    "League": league,
                    "Picks": total,
                    "Accuracy": acc,
                    "min_prob": min_prob,
                    "min_prob_margin": min_margin,
                    "min_games": min_games,
                    "max_rest_disadv": max_rest,
                    "min_form_diff": min_form,
                    "min_gd_diff": min_gd,
                }

        if best:
            output[league] = {
                "min_prob": best["min_prob"],
                "min_prob_margin": best["min_prob_margin"],
                "min_games": best["min_games"],
                "max_rest_disadv": best["max_rest_disadv"],
                "min_form_diff": best["min_form_diff"],
                "min_gd_diff": best["min_gd_diff"],
            }

    return output


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    picks = df[df["Qualifies"] == 1].copy()
    if picks.empty:
        return pd.DataFrame()
    finished = picks[picks["Correct"].notna()]
    if finished.empty:
        return pd.DataFrame()
    summary = (
        finished.groupby("League")
        .agg(Picks=("Qualifies", "size"), Correct=("Correct", "sum"))
        .reset_index()
    )
    summary["Wrong"] = summary["Picks"] - summary["Correct"]
    summary["Accuracy"] = (summary["Correct"] / summary["Picks"]).round(4)
    return summary.sort_values("Accuracy", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/raw/tabletennis_scoretennis")
    ap.add_argument("--out-picks", default="reports/tabletennis_elite_picks.csv")
    ap.add_argument("--out-summary", default="reports/tabletennis_elite_summary.csv")
    ap.add_argument("--out-thresholds", default="reports/tabletennis_elite_league_rules.yaml")
    ap.add_argument("--target-acc", type=float, default=0.95)
    ap.add_argument("--min-picks", type=int, default=40)
    ap.add_argument("--holdout-days", type=int, default=30)
    ap.add_argument("--fallback-min-prob", type=float, default=None)
    ap.add_argument("--fallback-min-margin", type=float, default=None)
    ap.add_argument("--fallback-min-games", type=int, default=None)
    ap.add_argument("--fallback-max-rest", type=int, default=None)
    ap.add_argument("--fallback-min-form", type=float, default=None)
    ap.add_argument("--fallback-min-gd", type=float, default=None)
    ap.add_argument("--start-date", default=None)
    ap.add_argument("--end-date", default=None)
    args = ap.parse_args()

    df = _load_matches(Path(args.data_dir))
    if df.empty:
        print("No table tennis data found. Download results first.")
        return 1
    if args.start_date:
        df = df[df["Date"] >= pd.to_datetime(args.start_date)]
    if args.end_date:
        df = df[df["Date"] <= pd.to_datetime(args.end_date)]
    if df.empty:
        print("No data in selected date range.")
        return 1

    features = build_feature_frame(df)

    if args.holdout_days > 0:
        cutoff = features["Date"].max() - timedelta(days=args.holdout_days)
        train = features[features["Date"] <= cutoff]
        holdout = features[features["Date"] > cutoff]
    else:
        train = features
        holdout = pd.DataFrame()

    league_overrides = tune_league_thresholds(
        train,
        target_acc=args.target_acc,
        min_picks=args.min_picks,
    )

    # Apply tuned thresholds per league
    fallback_params = None
    if args.fallback_min_prob is not None:
        fallback_params = {
            "min_prob": args.fallback_min_prob,
            "min_prob_margin": args.fallback_min_margin or 0.1,
            "min_games": args.fallback_min_games or 3,
            "max_rest_disadv": args.fallback_max_rest or 2,
            "min_form_diff": args.fallback_min_form or 0.0,
            "min_gd_diff": args.fallback_min_gd or 0.0,
        }

    tuned_rows = []
    for league, g in features.groupby("League"):
        params = league_overrides.get(league)
        if not params:
            if fallback_params is None:
                continue
            params = fallback_params
        tuned_rows.append(apply_thresholds(g, params))
    if tuned_rows:
        tuned = pd.concat(tuned_rows, ignore_index=True)
    else:
        tuned = pd.DataFrame()

    out_picks = Path(args.out_picks)
    out_picks.parent.mkdir(parents=True, exist_ok=True)
    tuned.to_csv(out_picks, index=False)

    summary = summarize(tuned)
    if not summary.empty:
        out_summary = Path(args.out_summary)
        out_summary.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(out_summary, index=False)
        print(summary.head(20).to_string(index=False))
    else:
        print("No qualifying picks with tuned thresholds.")

    rules = {
        "drop_blacklisted": False,
        "blacklist": [],
        "overrides": {k: v for k, v in league_overrides.items()},
    }
    Path(args.out_thresholds).write_text(yaml.safe_dump(rules, sort_keys=False, allow_unicode=True), encoding="utf-8")

    if not holdout.empty and not tuned.empty:
        # Evaluate on holdout
        holdout_rows = []
        for league, g in holdout.groupby("League"):
            params = league_overrides.get(league)
            if not params:
                continue
            holdout_rows.append(apply_thresholds(g, params))
        if holdout_rows:
            hold = pd.concat(holdout_rows, ignore_index=True)
            hold_summary = summarize(hold)
            if not hold_summary.empty:
                out_hold = out_picks.with_name(out_picks.stem + "_holdout_summary.csv")
                hold_summary.to_csv(out_hold, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
