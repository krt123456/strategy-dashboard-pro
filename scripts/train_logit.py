#!/usr/bin/env python3
"""Train a multinomial logistic regression model with rolling features + odds."""
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
    from sklearn.linear_model import LogisticRegression  # type: ignore
    from sklearn.preprocessing import StandardScaler  # type: ignore
    from sklearn.pipeline import Pipeline  # type: ignore
except Exception:
    print("Missing dependency: scikit-learn. Install with: pip install scikit-learn", file=sys.stderr)
    raise


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def implied_probs(h: float, d: float, a: float) -> tuple[float, float, float]:
    p_h = 1.0 / h
    p_d = 1.0 / d
    p_a = 1.0 / a
    s = p_h + p_d + p_a
    return p_h / s, p_d / s, p_a / s


def rolling_mean(vals: list[float]) -> float:
    return float(np.mean(vals)) if vals else np.nan


def build_features(df: pd.DataFrame, window: int) -> tuple[pd.DataFrame, pd.Series]:
    df = df.sort_values("Date").reset_index(drop=True)

    # stats history per team
    hist = {}

    rows = []
    labels = []

    for _, r in df.iterrows():
        home = r["HomeTeam"]
        away = r["AwayTeam"]

        # ensure history containers
        if home not in hist:
            hist[home] = []
        if away not in hist:
            hist[away] = []

        # require enough history
        if len(hist[home]) < window or len(hist[away]) < window:
            # update history after match, then skip
            hist[home].append(r)
            hist[away].append(r)
            continue

        def team_stats(team: str, is_home: bool):
            recent = hist[team][-window:]
            # from team's perspective
            gf = []
            ga = []
            shots = []
            sot = []
            corners = []
            yellow = []
            red = []
            for m in recent:
                if m["HomeTeam"] == team:
                    gf.append(m["FTHG"])
                    ga.append(m["FTAG"])
                    shots.append(m.get("HS", np.nan))
                    sot.append(m.get("HST", np.nan))
                    corners.append(m.get("HC", np.nan))
                    yellow.append(m.get("HY", np.nan))
                    red.append(m.get("HR", np.nan))
                else:
                    gf.append(m["FTAG"])
                    ga.append(m["FTHG"])
                    shots.append(m.get("AS", np.nan))
                    sot.append(m.get("AST", np.nan))
                    corners.append(m.get("AC", np.nan))
                    yellow.append(m.get("AY", np.nan))
                    red.append(m.get("AR", np.nan))

            return {
                "GF": rolling_mean(gf),
                "GA": rolling_mean(ga),
                "Shots": rolling_mean(shots),
                "SOT": rolling_mean(sot),
                "Corners": rolling_mean(corners),
                "Yellow": rolling_mean(yellow),
                "Red": rolling_mean(red),
            }

        hs = team_stats(home, True)
        as_ = team_stats(away, False)

        # odds (avg if present, fallback to B365)
        odds_cols = ["AvgH", "AvgD", "AvgA"]
        if all(c in df.columns for c in odds_cols) and not pd.isna(r[odds_cols]).any():
            h, d, a = r["AvgH"], r["AvgD"], r["AvgA"]
        else:
            h, d, a = r["B365H"], r["B365D"], r["B365A"]
        p_h, p_d, p_a = implied_probs(h, d, a)

        row = {
            "p_home": p_h,
            "p_draw": p_d,
            "p_away": p_a,
            "home_GF": hs["GF"],
            "home_GA": hs["GA"],
            "away_GF": as_["GF"],
            "away_GA": as_["GA"],
            "home_Shots": hs["Shots"],
            "away_Shots": as_["Shots"],
            "home_SOT": hs["SOT"],
            "away_SOT": as_["SOT"],
            "home_Corners": hs["Corners"],
            "away_Corners": as_["Corners"],
            "home_Yellow": hs["Yellow"],
            "away_Yellow": as_["Yellow"],
            "home_Red": hs["Red"],
            "away_Red": as_["Red"],
        }

        # diffs
        row.update(
            {
                "diff_GF": row["home_GF"] - row["away_GF"],
                "diff_GA": row["home_GA"] - row["away_GA"],
                "diff_Shots": row["home_Shots"] - row["away_Shots"],
                "diff_SOT": row["home_SOT"] - row["away_SOT"],
                "diff_Corners": row["home_Corners"] - row["away_Corners"],
            }
        )

        rows.append(row)

        if r["FTHG"] > r["FTAG"]:
            labels.append("H")
        elif r["FTHG"] < r["FTAG"]:
            labels.append("A")
        else:
            labels.append("D")

        # update history after match
        hist[home].append(r)
        hist[away].append(r)

    X = pd.DataFrame(rows)
    y = pd.Series(labels)
    return X, y


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--season", default="2526")
    ap.add_argument("--out", default="reports/logit_eval.md")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    processed = Path(cfg["paths"]["processed"]) / "epl_matches.csv"
    if not processed.exists():
        print("Run prepare_matches.py first.", file=sys.stderr)
        return 1

    df = pd.read_csv(processed, parse_dates=["Date"])
    df = df.dropna(subset=["FTHG", "FTAG", "HomeTeam", "AwayTeam"])

    # split by season code
    season = str(args.season)
    train_df = df[df["SeasonCode"].astype(str) < season].copy()
    test_df = df[df["SeasonCode"].astype(str) == season].copy()

    if train_df.empty or test_df.empty:
        print("Not enough data for train/test split.", file=sys.stderr)
        return 1

    X_train, y_train = build_features(train_df, args.window)
    X_test, y_test = build_features(test_df, args.window)

    # drop rows with NaN
    X_train = X_train.dropna()
    y_train = y_train.iloc[X_train.index]

    X_test = X_test.dropna()
    y_test = y_test.iloc[X_test.index]

    if len(X_test) == 0:
        print("No test rows after feature filtering.", file=sys.stderr)
        return 1

    clf = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(max_iter=500)),
        ]
    )
    clf.fit(X_train, y_train)

    preds = clf.predict(X_test)
    acc = (preds == y_test).mean() * 100

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(
            [
                "# تقييم نموذج Logistic Regression (EPL)",
                f"- الموسم المختبر: {season}",
                f"- حجم تدريب: {len(X_train)}",
                f"- حجم اختبار: {len(X_test)}",
                f"- الدقة (Top-1 Accuracy): {acc:.2f}%",
            ]
        ),
        encoding="utf-8",
    )

    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
