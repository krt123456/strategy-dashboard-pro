#!/usr/bin/env python3
"""Build parameter report for current EPL season from football-data.co.uk CSV."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Path to football-data CSV (E0_2526.csv)")
    ap.add_argument("--out", default="reports/current_season_parameters.csv")
    ap.add_argument("--md", default="reports/current_season_parameters.md")
    args = ap.parse_args()

    path = Path(args.csv)
    if not path.exists():
        print(f"missing: {path}", file=sys.stderr)
        return 1

    df = pd.read_csv(path, encoding="utf-8-sig")
    # Keep only rows with score
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"]).copy()

    teams = sorted(set(df["HomeTeam"]) | set(df["AwayTeam"]))

    def team_stats(team: str) -> dict:
        home = df[df["HomeTeam"] == team]
        away = df[df["AwayTeam"] == team]
        played = len(home) + len(away)
        gf = home["FTHG"].sum() + away["FTAG"].sum()
        ga = home["FTAG"].sum() + away["FTHG"].sum()

        stats = {
            "Team": team,
            "P": played,
            "GF": gf,
            "GA": ga,
            "GD": gf - ga,
        }

        # Optional stats if columns exist
        for label, hcol, acol in [
            ("Shots", "HS", "AS"),
            ("ShotsOnTarget", "HST", "AST"),
            ("Corners", "HC", "AC"),
            ("Fouls", "HF", "AF"),
            ("Yellow", "HY", "AY"),
            ("Red", "HR", "AR"),
        ]:
            if hcol in df.columns and acol in df.columns:
                val_for = home[hcol].sum() + away[acol].sum()
                val_against = home[acol].sum() + away[hcol].sum()
                stats[f"{label}_For"] = val_for
                stats[f"{label}_Against"] = val_against

        # Per-match averages
        if played > 0:
            stats["GF_per_match"] = gf / played
            stats["GA_per_match"] = ga / played
            for label in ["Shots", "ShotsOnTarget", "Corners", "Fouls", "Yellow", "Red"]:
                f = f"{label}_For"
                a = f"{label}_Against"
                if f in stats and a in stats:
                    stats[f"{label}_For_per_match"] = stats[f] / played
                    stats[f"{label}_Against_per_match"] = stats[a] / played

        return stats

    rows = [team_stats(t) for t in teams]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["P", "GD", "GF"], ascending=False).to_csv(out_path, index=False)

    # Markdown summary
    md_path = Path(args.md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    report = []
    report.append("# معلمات الموسم الحالي (EPL) من football-data.co.uk")
    report.append(f"- المصدر: {path.name}")
    report.append(f"- عدد المباريات: {len(df)}")
    report.append("")
    report.append("## أفضل 10 فرق هجوميًا (GF لكل مباراة)")
    df_rows = pd.DataFrame(rows)
    top_gf = df_rows.sort_values("GF_per_match", ascending=False).head(10)
    for _, r in top_gf.iterrows():
        report.append(f"- {r['Team']}: {r['GF_per_match']:.2f}")

    report.append("")
    report.append("## أفضل 10 دفاعيًا (GA لكل مباراة الأقل)")
    top_ga = df_rows.sort_values("GA_per_match", ascending=True).head(10)
    for _, r in top_ga.iterrows():
        report.append(f"- {r['Team']}: {r['GA_per_match']:.2f}")

    report.append("")
    report.append("## متوسطات تسديد/ركنيات (أعلى 10)")
    if "Shots_For_per_match" in df_rows.columns:
        top_shots = df_rows.sort_values("Shots_For_per_match", ascending=False).head(10)
        report.append("### تسديدات لكل مباراة")
        for _, r in top_shots.iterrows():
            report.append(f"- {r['Team']}: {r['Shots_For_per_match']:.2f}")

    if "Corners_For_per_match" in df_rows.columns:
        top_corners = df_rows.sort_values("Corners_For_per_match", ascending=False).head(10)
        report.append("")
        report.append("### ركنيات لكل مباراة")
        for _, r in top_corners.iterrows():
            report.append(f"- {r['Team']}: {r['Corners_For_per_match']:.2f}")

    md_path.write_text("\n".join(report), encoding="utf-8")

    print(f"saved: {out_path}")
    print(f"saved: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
