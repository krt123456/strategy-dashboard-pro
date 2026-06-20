#!/usr/bin/env python3
"""Run selective strategy (no draws + confidence threshold) across European leagues.

Data sources:
- Main leagues: https://www.football-data.co.uk/mmz4281/{season}/{code}.csv
- Extra leagues: https://www.football-data.co.uk/new/{code}.csv (all seasons in one file)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise

try:
    import requests  # type: ignore
except Exception:
    print("Missing dependency: requests. Install with: pip install requests", file=sys.stderr)
    raise


def season_code_to_str(season_code: str) -> str:
    # e.g., 2526 -> 2025/2026
    if len(season_code) != 4 or not season_code.isdigit():
        return season_code
    start = int("20" + season_code[:2])
    end = int("20" + season_code[2:])
    return f"{start}/{end}"


def fetch_csv(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return True
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FootballData/1.0)"}
    resp = requests.get(url, timeout=30, headers=headers)
    if resp.status_code != 200:
        return False
    dest.write_bytes(resp.content)
    return True


def pick_odds_cols(df: pd.DataFrame) -> Tuple[str, str, str] | None:
    # Candidate odds columns (home/draw/away)
    candidates = [
        ("AvgH", "AvgD", "AvgA"),
        ("B365H", "B365D", "B365A"),
        ("AvgCH", "AvgCD", "AvgCA"),
        ("B365CH", "B365CD", "B365CA"),
        ("B365CH", "B365CD", "B36CA"),
        ("PSCH", "PSCD", "PSCA"),
        ("MaxCH", "MaxCD", "MaxCA"),
        ("MaxH", "MaxD", "MaxA"),
    ]
    for cols in candidates:
        if all(c in df.columns for c in cols):
            return cols  # type: ignore[return-value]
    return None


def compute_selective(df: pd.DataFrame, thresholds: List[float], target_acc: float) -> dict:
    odds_cols = pick_odds_cols(df)
    if not odds_cols:
        return {"error": "No odds columns"}

    df = df.dropna(subset=["FTHG", "FTAG"] + list(odds_cols)).copy()
    if df.empty:
        return {"error": "No finished matches with odds"}

    actual = df.apply(lambda r: "H" if r.FTHG > r.FTAG else ("A" if r.FTHG < r.FTAG else "D"), axis=1)

    p_h = 1.0 / df[odds_cols[0]]
    p_d = 1.0 / df[odds_cols[1]]
    p_a = 1.0 / df[odds_cols[2]]
    s = p_h + p_d + p_a
    p_h /= s
    p_d /= s
    p_a /= s

    probs = pd.DataFrame({"H": p_h, "D": p_d, "A": p_a})
    best = probs.idxmax(axis=1)
    conf = probs.max(axis=1)

    rows = []
    best90 = None
    best_by_coverage = None
    for t in thresholds:
        idx = (best != "D") & (conf >= t)
        if idx.sum() == 0:
            rows.append((t, 0.0, None))
            continue
        acc = (best[idx].values == actual[idx].values).mean()
        cov = idx.mean()
        rows.append((t, cov, acc))
        if best90 is None and acc >= target_acc:
            best90 = (t, cov, acc, int(idx.sum()))
        if acc >= target_acc:
            if best_by_coverage is None or cov > best_by_coverage[1] or (cov == best_by_coverage[1] and acc > best_by_coverage[2]):
                best_by_coverage = (t, cov, acc, int(idx.sum()))

    return {
        "rows": rows,
        "best90": best90,
        "best_by_coverage": best_by_coverage,
        "matches": int(len(df)),
        "odds_cols": odds_cols,
    }


def normalize_main(df: pd.DataFrame) -> pd.DataFrame:
    # Standard main format already uses FTHG/FTAG
    return df


def normalize_extra(df: pd.DataFrame, season_str: str) -> pd.DataFrame:
    # Extra leagues use all-seasons-in-one files
    if "Season" in df.columns:
        df = df[df["Season"].astype(str) == season_str].copy()
    # Rename score columns
    if "HG" in df.columns and "AG" in df.columns:
        df = df.rename(columns={"HG": "FTHG", "AG": "FTAG"})
    # Rename team columns to match
    if "Home" in df.columns:
        df = df.rename(columns={"Home": "HomeTeam", "Away": "AwayTeam"})
    return df


def parse_last_date(df: pd.DataFrame) -> str:
    if "Date" not in df.columns:
        return ""
    dates = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    if dates.isna().all():
        return ""
    return dates.max().date().isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2526")
    ap.add_argument("--out", default="reports/europe_summary.md")
    ap.add_argument("--thresholds", default="")
    ap.add_argument("--target-acc", type=float, default=0.90)
    args = ap.parse_args()

    season_code = str(args.season)
    season_str = season_code_to_str(season_code)
    if args.thresholds:
        thresholds = [float(x) for x in args.thresholds.split(",") if x]
    else:
        thresholds = [round(x, 2) for x in list(pd.Series([i / 100 for i in range(40, 91)]))]

    base_main = "https://www.football-data.co.uk/mmz4281"
    base_extra = "https://www.football-data.co.uk/new"

    # European leagues (main + extra) based on football-data lists
    main_leagues = [
        ("E0", "England Premier League"),
        ("E1", "England Championship"),
        ("E2", "England League One"),
        ("E3", "England League Two"),
        ("EC", "England Conference"),
        ("SC0", "Scotland Premier League"),
        ("SC1", "Scotland Division 1"),
        ("SC2", "Scotland Division 2"),
        ("SC3", "Scotland Division 3"),
        ("D1", "Germany Bundesliga 1"),
        ("D2", "Germany Bundesliga 2"),
        ("I1", "Italy Serie A"),
        ("I2", "Italy Serie B"),
        ("SP1", "Spain La Liga"),
        ("SP2", "Spain Segunda"),
        ("F1", "France Ligue 1"),
        ("F2", "France Ligue 2"),
        ("N1", "Netherlands Eredivisie"),
        ("B1", "Belgium Pro League"),
        ("P1", "Portugal Primeira"),
        ("T1", "Turkey Super Lig"),
        ("G1", "Greece Super League"),
    ]

    extra_leagues = [
        ("AUT", "Austria Bundesliga"),
        ("DNK", "Denmark Superliga"),
        ("FIN", "Finland Veikkausliiga"),
        ("IRL", "Ireland Premier Division"),
        ("NOR", "Norway Eliteserien"),
        ("POL", "Poland Ekstraklasa"),
        ("ROU", "Romania Liga 1"),
        ("RUS", "Russia Premier League"),
        ("SWE", "Sweden Allsvenskan"),
        ("SWZ", "Switzerland Super League"),
    ]

    raw_dir = Path("data/raw/football_data")
    report_dir = Path("reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []

    # Process main leagues
    for code, name in main_leagues:
        url = f"{base_main}/{season_code}/{code}.csv"
        dest = raw_dir / f"{code}_{season_code}.csv"
        ok = fetch_csv(url, dest)
        if not ok:
            summary_rows.append({"Code": code, "League": name, "Source": "main", "Status": "missing"})
            continue
        df = pd.read_csv(dest, encoding="utf-8-sig")
        df = normalize_main(df)
        last_date = parse_last_date(df)
        res = compute_selective(df, thresholds, args.target_acc)
        if "error" in res:
            summary_rows.append({"Code": code, "League": name, "Source": "main", "Status": res["error"]})
            continue

        # per-league report
        report_path = report_dir / f"selective_{code}.md"
        lines = []
        lines.append("# تقييم استراتيجية انتقائية (استبعاد التعادل + عتبة ثقة)")
        lines.append(f"- الدوري: {name} ({code})")
        lines.append(f"- الموسم: {season_code}")
        lines.append(f"- عدد المباريات المكتملة: {res['matches']}")
        if last_date:
            lines.append(f"- آخر تاريخ في البيانات: {last_date}")
        lines.append(f"- أعمدة الاحتمالات المستخدمة: {res['odds_cols']}")
        lines.append("")
        lines.append("| العتبة | التغطية | الدقة |")
        lines.append("|---:|---:|---:|")
        for t, cov, acc in res["rows"]:
            if acc is None:
                lines.append(f"| {t:.2f} | 0% | n/a |")
            else:
                lines.append(f"| {t:.2f} | {cov*100:.1f}% | {acc*100:.1f}% |")
        if res["best_by_coverage"]:
            t, cov, acc, n = res["best_by_coverage"]
            lines.append("")
            lines.append(f"أفضل عتبة تحقق ≥{args.target_acc*100:.0f}% (أعلى تغطية): **{t:.2f}** (تغطية {cov*100:.1f}%, مباريات {n}, دقة {acc*100:.1f}%).")
        report_path.write_text("\n".join(lines), encoding="utf-8")

        summary_rows.append(
            {
                "Code": code,
                "League": name,
                "Source": "main",
                "Status": "ok",
                "Matches": res["matches"],
                "LastDate": last_date,
                "BestThreshold": res["best_by_coverage"][0] if res["best_by_coverage"] else "",
                "BestCoverage": (res["best_by_coverage"][1] * 100) if res["best_by_coverage"] else "",
                "BestAccuracy": (res["best_by_coverage"][2] * 100) if res["best_by_coverage"] else "",
            }
        )

    # Process extra leagues
    for code, name in extra_leagues:
        url = f"{base_extra}/{code}.csv"
        dest = raw_dir / f"{code}_all.csv"
        ok = fetch_csv(url, dest)
        if not ok:
            summary_rows.append({"Code": code, "League": name, "Source": "extra", "Status": "missing"})
            continue
        df = pd.read_csv(dest, encoding="utf-8-sig")
        df = normalize_extra(df, season_str)
        last_date = parse_last_date(df)
        res = compute_selective(df, thresholds, args.target_acc)
        if "error" in res:
            summary_rows.append({"Code": code, "League": name, "Source": "extra", "Status": res["error"]})
            continue

        report_path = report_dir / f"selective_{code}.md"
        lines = []
        lines.append("# تقييم استراتيجية انتقائية (استبعاد التعادل + عتبة ثقة)")
        lines.append(f"- الدوري: {name} ({code})")
        lines.append(f"- الموسم: {season_str}")
        lines.append(f"- عدد المباريات المكتملة: {res['matches']}")
        if last_date:
            lines.append(f"- آخر تاريخ في البيانات: {last_date}")
        lines.append(f"- أعمدة الاحتمالات المستخدمة: {res['odds_cols']}")
        lines.append("")
        lines.append("| العتبة | التغطية | الدقة |")
        lines.append("|---:|---:|---:|")
        for t, cov, acc in res["rows"]:
            if acc is None:
                lines.append(f"| {t:.2f} | 0% | n/a |")
            else:
                lines.append(f"| {t:.2f} | {cov*100:.1f}% | {acc*100:.1f}% |")
        if res["best_by_coverage"]:
            t, cov, acc, n = res["best_by_coverage"]
            lines.append("")
            lines.append(f"أفضل عتبة تحقق ≥{args.target_acc*100:.0f}% (أعلى تغطية): **{t:.2f}** (تغطية {cov*100:.1f}%, مباريات {n}, دقة {acc*100:.1f}%).")
        report_path.write_text("\n".join(lines), encoding="utf-8")

        summary_rows.append(
            {
                "Code": code,
                "League": name,
                "Source": "extra",
                "Status": "ok",
                "Matches": res["matches"],
                "LastDate": last_date,
                "BestThreshold": res["best_by_coverage"][0] if res["best_by_coverage"] else "",
                "BestCoverage": (res["best_by_coverage"][1] * 100) if res["best_by_coverage"] else "",
                "BestAccuracy": (res["best_by_coverage"][2] * 100) if res["best_by_coverage"] else "",
            }
        )

    # Write summary
    summary_df = pd.DataFrame(summary_rows)
    summary_csv = report_dir / "europe_summary.csv"
    summary_df.to_csv(summary_csv, index=False)

    summary_md = Path(args.out)
    lines = []
    lines.append("# ملخص الاستراتيجية الانتقائية لجميع الدوريات الأوروبية")
    lines.append(f"- الموسم: {season_code} (للدوريات الرئيسية) / {season_str} (للدوريات الإضافية)")
    lines.append(f"- هدف الدقة: ≥{args.target_acc*100:.0f}% (اختيار أعلى تغطية يحقق الهدف)")
    lines.append("")
    lines.append("| الكود | الدوري | المصدر | الحالة | مباريات | آخر تاريخ | أفضل عتبة تحقق الهدف | تغطية | دقة |")
    lines.append("|---|---|---|---|---:|---|---:|---:|---:|")
    for _, r in summary_df.iterrows():
        lines.append(
            f"| {r.get('Code','')} | {r.get('League','')} | {r.get('Source','')} | {r.get('Status','')} | {r.get('Matches','')} | {r.get('LastDate','')} | {r.get('BestThreshold','')} | {r.get('BestCoverage','')} | {r.get('BestAccuracy','')} |"
        )

    summary_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"saved: {summary_csv}")
    print(f"saved: {summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
