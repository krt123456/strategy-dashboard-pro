#!/usr/bin/env python3
"""Walk-forward backtest using Poisson + Dixon-Coles model."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:
    print("Missing dependency: PyYAML. Install with: pip install pyyaml", file=sys.stderr)
    raise

try:
    import numpy as np  # type: ignore
except Exception:
    print("Missing dependency: numpy. Install with: pip install numpy", file=sys.stderr)
    raise

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise

try:
    from scipy.optimize import minimize  # type: ignore
except Exception:
    print("Missing dependency: scipy. Install with: pip install scipy", file=sys.stderr)
    raise

from model_dc import build_team_index, log_likelihood, _expand_params, DCParams, predict_probabilities


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fit_model(df: pd.DataFrame, xi: float, maxiter: int):
    teams = list(set(df["HomeTeam"]) | set(df["AwayTeam"]))
    team_index = build_team_index(teams)

    n_teams = len(team_index)
    n = n_teams - 1
    init = np.zeros(2 * n + 2)

    def objective(p):
        return -log_likelihood(p, df, team_index, xi)

    res = minimize(objective, init, method="L-BFGS-B", options={"maxiter": maxiter})
    if not res.success:
        print(f"Optimization failed: {res.message}", file=sys.stderr)

    params_vec = res.x
    attack = params_vec[:n]
    defense = params_vec[n : 2 * n]
    home_adv = params_vec[2 * n]
    rho_raw = params_vec[2 * n + 1]
    params = DCParams(attack=attack, defense=defense, home_adv=home_adv, rho_raw=rho_raw)
    attack_full, defense_full, home_adv, rho = _expand_params(params, n_teams)

    return team_index, attack_full, defense_full, home_adv, rho


def outcome_from_row(row) -> str:
    if row["FTHG"] > row["FTAG"]:
        return "H"
    if row["FTHG"] < row["FTAG"]:
        return "A"
    return "D"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--xi", type=float, default=0.003)
    ap.add_argument("--min-train-seasons", type=int, default=3)
    ap.add_argument("--maxiter", type=int, default=150)
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    processed = Path(cfg["paths"]["processed"]) / "epl_matches.csv"
    if not processed.exists():
        print("Run prepare_matches.py first.", file=sys.stderr)
        return 1

    df = pd.read_csv(processed, parse_dates=["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    seasons = sorted(df["SeasonCode"].dropna().unique())
    if len(seasons) <= args.min_train_seasons:
        print("Not enough seasons for walk-forward backtest.", file=sys.stderr)
        return 1

    rows = []
    for i in range(args.min_train_seasons, len(seasons)):
        train_seasons = seasons[:i]
        test_season = seasons[i]

        train = df[df["SeasonCode"].isin(train_seasons)].copy()
        test = df[df["SeasonCode"] == test_season].copy()

        team_index, attack, defense, home_adv, rho = fit_model(train, args.xi, args.maxiter)

        log_loss = 0.0
        brier = 0.0
        n = 0
        for _, row in test.iterrows():
            home = row["HomeTeam"]
            away = row["AwayTeam"]
            if home not in team_index or away not in team_index:
                continue
            p_home, p_draw, p_away = predict_probabilities(
                home, away, team_index, attack, defense, home_adv, rho
            )
            outcome = outcome_from_row(row)
            probs = {"H": p_home, "D": p_draw, "A": p_away}
            p = max(min(probs[outcome], 1 - 1e-12), 1e-12)
            log_loss += -np.log(p)

            brier += (p_home - (outcome == "H")) ** 2
            brier += (p_draw - (outcome == "D")) ** 2
            brier += (p_away - (outcome == "A")) ** 2
            n += 1

        if n == 0:
            continue
        rows.append(
            {
                "TestSeason": str(test_season),
                "TrainSeasons": ",".join(map(str, train_seasons)),
                "Matches": n,
                "LogLoss": log_loss / n,
                "Brier": brier / n,
            }
        )

    out_dir = Path(cfg["paths"]["reports"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "backtest.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
