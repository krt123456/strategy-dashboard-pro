#!/usr/bin/env python3
"""Train Poisson + Dixon-Coles model on prepared EPL matches."""
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

from model_dc import build_team_index, log_likelihood, _expand_params, DCParams


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--xi", type=float, default=0.003)
    ap.add_argument("--maxiter", type=int, default=200)
    ap.add_argument("--out")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    processed = Path(cfg["paths"]["processed"]) / "epl_matches.csv"
    if not processed.exists():
        print("Run prepare_matches.py first.", file=sys.stderr)
        return 1

    df = pd.read_csv(processed, parse_dates=["Date"])
    if args.start:
        df = df[df["Date"] >= args.start]
    if args.end:
        df = df[df["Date"] <= args.end]
    df = df.sort_values("Date").reset_index(drop=True)

    teams = list(set(df["HomeTeam"]) | set(df["AwayTeam"]))
    team_index = build_team_index(teams)

    n_teams = len(team_index)
    n = n_teams - 1
    init = np.zeros(2 * n + 2)

    def objective(p):
        return -log_likelihood(p, df, team_index, args.xi)

    res = minimize(objective, init, method="L-BFGS-B", options={"maxiter": args.maxiter})
    if not res.success:
        print(f"Optimization failed: {res.message}", file=sys.stderr)

    params_vec = res.x
    attack = params_vec[:n]
    defense = params_vec[n : 2 * n]
    home_adv = params_vec[2 * n]
    rho_raw = params_vec[2 * n + 1]
    params = DCParams(attack=attack, defense=defense, home_adv=home_adv, rho_raw=rho_raw)
    attack_full, defense_full, home_adv, rho = _expand_params(params, n_teams)

    out_dir = Path(cfg["paths"]["models"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else out_dir / "poisson_dc_latest.npz"

    np.savez(
        out_path,
        teams=np.array(sorted(team_index, key=team_index.get)),
        attack=attack_full,
        defense=defense_full,
        home_adv=home_adv,
        rho=rho,
        xi=args.xi,
        start=args.start or "",
        end=args.end or "",
    )
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
