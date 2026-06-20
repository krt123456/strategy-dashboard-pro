#!/usr/bin/env python3
"""Evaluate club leagues outside Europe using football-data.co.uk (new/* CSVs)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Any, List

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise

try:
    import yaml  # type: ignore
except Exception:
    print("Missing dependency: PyYAML. Install with: pip install pyyaml", file=sys.stderr)
    raise

from run_all_european_enhanced import (
    fetch_csv,
    parse_last_date,
    pick_odds_cols,
    build_match_features,
    evaluate_strategies,
    normalize_team_name,
)


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_new(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "HG" in df.columns and "AG" in df.columns:
        df = df.rename(columns={"HG": "FTHG", "AG": "FTAG"})
    if "Home" in df.columns and "Away" in df.columns:
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam"})
    return df


def load_team_geo(path: Path) -> Dict[str, Dict[str, float]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    out: Dict[str, Dict[str, float]] = {}
    for _, row in df.iterrows():
        name = str(row.get("TeamName", "")).strip()
        if not name:
            continue
        key = normalize_team_name(name)
        lat = pd.to_numeric(row.get("Latitude"), errors="coerce")
        lon = pd.to_numeric(row.get("Longitude"), errors="coerce")
        cap = pd.to_numeric(row.get("Capacity"), errors="coerce")
        payload: Dict[str, float] = {}
        if pd.notna(lat) and pd.notna(lon):
            payload["lat"] = float(lat)
            payload["lon"] = float(lon)
        if pd.notna(cap):
            payload["capacity"] = float(cap)
        if payload:
            out[key] = payload
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lists", default="data/league_search_lists.yaml")
    ap.add_argument("--out", default="reports/global_fd_summary_enhanced.csv")
    ap.add_argument("--team-stadiums", default="data/processed/team_stadiums.csv")
    ap.add_argument("--continents", default="asia,north_america,south_america,oceania,intercontinental")
    ap.add_argument("--target-acc", type=float, default=0.90)
    ap.add_argument("--min-coverage", type=float, default=0.06)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--thresholds", default="")
    ap.add_argument("--thresholds-ext", default="")
    args = ap.parse_args()

    cfg = load_yaml(Path(args.lists))
    entries = cfg.get("lists", {}).get("football_data_new", [])
    continents = {c.strip() for c in args.continents.split(",") if c.strip()}

    if continents:
        entries = [e for e in entries if str(e.get("continent", "")) in continents]

    if not entries:
        print("No football-data new entries found for the requested continents.", file=sys.stderr)
        return 1

    team_geo = load_team_geo(Path(args.team_stadiums))

    if args.thresholds:
        thresholds = [float(x) for x in args.thresholds.split(",") if x]
    else:
        thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    if args.thresholds_ext:
        thresholds_ext = [float(x) for x in args.thresholds_ext.split(",") if x]
    else:
        thresholds_ext = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]

    raw_dir = Path("data/raw/football_data")
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: List[Dict[str, Any]] = []

    for entry in entries:
        code = str(entry.get("code", "")).strip()
        name = str(entry.get("name", "")).strip()
        url = str(entry.get("url", "")).strip()
        if not code or not url:
            continue

        dest = raw_dir / f"{code}_all.csv"
        ok = fetch_csv(url, dest)
        if not ok:
            summary_rows.append({"Code": code, "League": name, "Status": "missing"})
            continue

        df = pd.read_csv(dest, encoding="utf-8-sig")
        df = normalize_new(df)
        last_date = parse_last_date(df)

        odds_cols = pick_odds_cols(df)
        if not odds_cols:
            summary_rows.append({"Code": code, "League": name, "Status": "No odds columns"})
            continue

        for col in odds_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        feat = build_match_features(df, odds_cols, args.window, team_geo, external_features=None)
        if feat.empty:
            summary_rows.append({"Code": code, "League": name, "Status": "No finished matches"})
            continue

        res = evaluate_strategies(feat, thresholds, args.target_acc, extended=False, allow_draws=False)
        best = res.get("best")
        extended_used = False
        if (best is None) or (best["coverage"] < args.min_coverage):
            res_ext = evaluate_strategies(feat, thresholds_ext, args.target_acc, extended=True, allow_draws=True)
            best_ext = res_ext.get("best")
            if best_ext and (
                best is None
                or best_ext["coverage"] > best["coverage"]
                or (best_ext["coverage"] == best["coverage"] and best_ext["accuracy"] > best["accuracy"])
            ):
                best = best_ext
                extended_used = True

        if not best:
            summary_rows.append({"Code": code, "League": name, "Status": "No strategy >= target"})
            continue

        report_path = report_dir / f"selective_{code}_enhanced.md"
        lines = []
        lines.append("# تقييم استراتيجية انتقائية محسّنة")
        lines.append(f"- الدوري: {name} ({code})")
        lines.append("- المصدر: football-data.co.uk")
        lines.append(f"- عدد المباريات المكتملة: {len(feat)}")
        if last_date:
            lines.append(f"- آخر تاريخ في البيانات: {last_date}")
        lines.append(f"- أعمدة الاحتمالات المستخدمة: {odds_cols}")
        lines.append("")
        lines.append(f"## الاستراتيجية المختارة (أعلى تغطية مع دقة ≥{args.target_acc*100:.0f}%)")
        lines.append(f"- النوع: {best['strategy']}")
        if extended_used:
            lines.append("- ملاحظة: تم استخدام بحث موسّع لأن التغطية كانت منخفضة")
        lines.append(f"- المعلمات: {best['params']}")
        lines.append(f"- التغطية: {best['coverage']*100:.1f}%")
        lines.append(f"- الدقة: {best['accuracy']*100:.1f}%")
        lines.append(f"- عدد المباريات: {best['n']}")
        report_path.write_text("\n".join(lines), encoding="utf-8")

        summary_rows.append(
            {
                "Code": code,
                "League": name,
                "Status": "ok",
                "Matches": len(feat),
                "LastDate": last_date,
                "Strategy": best["strategy"],
                "Params": best["params"],
                "Coverage": best["coverage"] * 100,
                "Accuracy": best["accuracy"] * 100,
                "Extended": extended_used,
            }
        )

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(args.out, index=False)

    over90 = summary_df[(summary_df["Status"] == "ok") & (summary_df["Accuracy"] >= 90.0)].copy()
    over90.sort_values(["Accuracy", "Coverage"], ascending=False, inplace=True)
    over90_csv = report_dir / "global_fd_supported_over90.csv"
    over90.to_csv(over90_csv, index=False)

    over90_md = report_dir / "global_fd_supported_over90.md"
    lines = ["# Football-data global competitions >= 90% accuracy"]
    if over90.empty:
        lines.append("- None")
    else:
        for _, row in over90.iterrows():
            lines.append(
                f"- [{row['Code']}] {row['League']} — Acc {row['Accuracy']:.2f}% | Cov {row['Coverage']:.2f}%"
            )
    over90_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"saved: {args.out}")
    print(f"saved: {over90_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
