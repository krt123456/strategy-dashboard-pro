#!/usr/bin/env python3
"""Analyze current EPL season and evaluate model trained on prior seasons."""
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


def outcome(row) -> str:
    if row["FTHG"] > row["FTAG"]:
        return "H"
    if row["FTHG"] < row["FTAG"]:
        return "A"
    return "D"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--xi", type=float, default=0.003)
    ap.add_argument("--maxiter", type=int, default=150)
    ap.add_argument("--season", help="SeasonCode to evaluate, e.g., 2526. Default = latest.")
    ap.add_argument("--out", default="reports/current_season_report.md")
    ap.add_argument("--preds-out", default="reports/current_season_predictions.csv")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    processed = Path(cfg["paths"]["processed"]) / "epl_matches.csv"
    if not processed.exists():
        print("Run prepare_matches.py first.", file=sys.stderr)
        return 1

    df = pd.read_csv(processed, parse_dates=["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    seasons = sorted(df["SeasonCode"].dropna().unique())
    if not seasons:
        print("No seasons found.", file=sys.stderr)
        return 1

    season = str(args.season or seasons[-1])
    train = df[df["SeasonCode"].astype(str) < season].copy()
    test = df[df["SeasonCode"].astype(str) == season].copy()

    if train.empty or test.empty:
        print("Not enough data for the requested season split.", file=sys.stderr)
        return 1

    team_index, attack, defense, home_adv, rho = fit_model(train, args.xi, args.maxiter)

    # Evaluate on current season matches
    rows = []
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
        out = outcome(row)
        probs = {"H": p_home, "D": p_draw, "A": p_away}
        p = max(min(probs[out], 1 - 1e-12), 1e-12)
        log_loss += -np.log(p)
        brier += (p_home - (out == "H")) ** 2
        brier += (p_draw - (out == "D")) ** 2
        brier += (p_away - (out == "A")) ** 2
        n += 1

        rows.append(
            {
                "Date": row["Date"].date().isoformat(),
                "HomeTeam": home,
                "AwayTeam": away,
                "FTHG": int(row["FTHG"]),
                "FTAG": int(row["FTAG"]),
                "P_Home": p_home,
                "P_Draw": p_draw,
                "P_Away": p_away,
            }
        )

    # Descriptive stats for current season
    total_matches = len(test)
    home_wins = (test["FTHG"] > test["FTAG"]).mean() if total_matches else 0
    draws = (test["FTHG"] == test["FTAG"]).mean() if total_matches else 0
    away_wins = (test["FTHG"] < test["FTAG"]).mean() if total_matches else 0
    avg_goals = (test["FTHG"] + test["FTAG"]).mean() if total_matches else 0
    avg_home_goals = test["FTHG"].mean() if total_matches else 0
    avg_away_goals = test["FTAG"].mean() if total_matches else 0

    # Per-team table (basic)
    table = []
    teams = sorted(set(test["HomeTeam"]) | set(test["AwayTeam"]))
    for t in teams:
        home = test[test["HomeTeam"] == t]
        away = test[test["AwayTeam"] == t]
        played = len(home) + len(away)
        gf = home["FTHG"].sum() + away["FTAG"].sum()
        ga = home["FTAG"].sum() + away["FTHG"].sum()
        wins = (home["FTHG"] > home["FTAG"]).sum() + (away["FTAG"] > away["FTHG"]).sum()
        draws_team = (home["FTHG"] == home["FTAG"]).sum() + (away["FTAG"] == away["FTHG"]).sum()
        points = wins * 3 + draws_team
        table.append(
            {
                "Team": t,
                "P": played,
                "W": wins,
                "D": draws_team,
                "L": played - wins - draws_team,
                "GF": gf,
                "GA": ga,
                "GD": gf - ga,
                "Pts": points,
            }
        )

    table_df = pd.DataFrame(table).sort_values(["Pts", "GD", "GF"], ascending=False)

    # Write outputs
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = []
    report.append("# تقرير الموسم الحالي (EPL)")
    report.append(f"- الموسم: {season}")
    report.append(f"- عدد المباريات: {total_matches}")
    report.append(f"- متوسط الأهداف للمباراة: {avg_goals:.3f}")
    report.append(f"- متوسط أهداف صاحب الأرض: {avg_home_goals:.3f}")
    report.append(f"- متوسط أهداف الضيف: {avg_away_goals:.3f}")
    report.append(f"- نسبة فوز صاحب الأرض: {home_wins:.3%}")
    report.append(f"- نسبة التعادل: {draws:.3%}")
    report.append(f"- نسبة فوز الضيف: {away_wins:.3%}")
    report.append("")
    report.append("## أداء النموذج على الموسم الحالي")
    report.append(f"- LogLoss: {log_loss / n:.4f}" if n else "- LogLoss: n/a")
    report.append(f"- Brier: {brier / n:.4f}" if n else "- Brier: n/a")
    report.append("")
    report.append("## جدول مختصر (نقاط حتى تاريخ آخر مباراة في البيانات)")
    report.append("")
    report.append("| الفريق | لُعب | فاز | تعادل | خسر | له | عليه | فارق | نقاط |")
    report.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in table_df.iterrows():
        report.append(
            f"| {r['Team']} | {int(r['P'])} | {int(r['W'])} | {int(r['D'])} | {int(r['L'])} | {int(r['GF'])} | {int(r['GA'])} | {int(r['GD'])} | {int(r['Pts'])} |"
        )

    out_path.write_text("\n".join(report), encoding="utf-8")

    preds_out = Path(args.preds_out)
    preds_out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(preds_out, index=False)

    print(f"saved: {out_path}")
    print(f"saved: {preds_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
