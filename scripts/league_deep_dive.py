#!/usr/bin/env python3
"""Deep dive analysis for one league across recent seasons.

Produces a team-profile report and league patterns using rolling, pre-match features.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Dict, Any, Tuple

import pandas as pd

from run_all_european_enhanced import (
    build_match_features,
    league_context,
    normalize_main,
    pick_odds_cols,
    parse_last_date,
)


def parse_season_code(path: Path) -> str | None:
    stem = path.stem
    parts = stem.split("_")
    if len(parts) != 2:
        return None
    return parts[1]


def load_league(code: str, raw_dir: Path, seasons: List[str]) -> pd.DataFrame:
    frames = []
    for season in seasons:
        path = raw_dir / f"{code}_{season}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, encoding="utf-8-sig")
        df = normalize_main(df)
        df["Season"] = season
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out


def team_long_frame(df: pd.DataFrame) -> pd.DataFrame:
    # Build a per-team, per-match long table with home/away splits.
    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("FTHG")) or pd.isna(r.get("FTAG")):
            continue
        home = r.get("HomeTeam")
        away = r.get("AwayTeam")
        if not home or not away:
            continue

        # common fields
        date = r.get("Date")
        season = r.get("Season")

        def pack(team: str, opp: str, is_home: bool) -> Dict[str, Any]:
            if is_home:
                gf = r.get("FTHG")
                ga = r.get("FTAG")
                ht_gf = r.get("HTHG")
                ht_ga = r.get("HTAG")
                sh = r.get("HS")
                sa = r.get("AS")
                sot = r.get("HST")
                sot_a = r.get("AST")
                corners = r.get("HC")
                corners_a = r.get("AC")
                fouls = r.get("HF")
                fouls_a = r.get("AF")
                yell = r.get("HY")
                red = r.get("HR")
                yell_a = r.get("AY")
                red_a = r.get("AR")
            else:
                gf = r.get("FTAG")
                ga = r.get("FTHG")
                ht_gf = r.get("HTAG")
                ht_ga = r.get("HTHG")
                sh = r.get("AS")
                sa = r.get("HS")
                sot = r.get("AST")
                sot_a = r.get("HST")
                corners = r.get("AC")
                corners_a = r.get("HC")
                fouls = r.get("AF")
                fouls_a = r.get("HF")
                yell = r.get("AY")
                red = r.get("AR")
                yell_a = r.get("HY")
                red_a = r.get("HR")

            pts = 3 if gf > ga else (1 if gf == ga else 0)
            ht_state = "L" if ht_gf < ht_ga else ("W" if ht_gf > ht_ga else "D")

            return {
                "Date": date,
                "Season": season,
                "Team": team,
                "Opp": opp,
                "IsHome": is_home,
                "GF": gf,
                "GA": ga,
                "GD": gf - ga,
                "Points": pts,
                "HT_GF": ht_gf,
                "HT_GA": ht_ga,
                "HT_State": ht_state,
                "SH": sh,
                "SA": sa,
                "SOT": sot,
                "SOT_A": sot_a,
                "Corners": corners,
                "Corners_A": corners_a,
                "Fouls": fouls,
                "Fouls_A": fouls_a,
                "Yell": yell,
                "Red": red,
                "Yell_A": yell_a,
                "Red_A": red_a,
            }

        rows.append(pack(home, away, True))
        rows.append(pack(away, home, False))

    return pd.DataFrame(rows)


def compute_team_profiles(df_long: pd.DataFrame) -> pd.DataFrame:
    # Aggregate per team
    g = df_long.groupby("Team", dropna=True)

    def ratio(num, den):
        return num / den if den and den != 0 else None

    records = []
    for team, t in g:
        matches = len(t)
        if matches == 0:
            continue
        home = t[t["IsHome"]]
        away = t[~t["IsHome"]]

        gf = t["GF"].sum()
        ga = t["GA"].sum()
        sh = t["SH"].sum(min_count=1)
        sa = t["SA"].sum(min_count=1)
        sot = t["SOT"].sum(min_count=1)
        sota = t["SOT_A"].sum(min_count=1)
        cor = t["Corners"].sum(min_count=1)
        cora = t["Corners_A"].sum(min_count=1)
        fouls = t["Fouls"].sum(min_count=1)
        yell = t["Yell"].sum(min_count=1)
        red = t["Red"].sum(min_count=1)

        ppm = t["Points"].mean()
        ppm_home = home["Points"].mean() if len(home) else None
        ppm_away = away["Points"].mean() if len(away) else None

        # HT resilience
        ht_l = t[t["HT_State"] == "L"]
        ht_w = t[t["HT_State"] == "W"]
        ht_d = t[t["HT_State"] == "D"]
        comeback_rate = (ht_l["Points"] > 0).mean() if len(ht_l) else None
        comeback_ppm = ht_l["Points"].mean() if len(ht_l) else None
        hold_rate = (ht_w["Points"] == 3).mean() if len(ht_w) else None
        ht_lead_rate = len(ht_w) / matches if matches else None

        # second-half strength
        sh_gf = (t["GF"] - t["HT_GF"]).sum()
        sh_ga = (t["GA"] - t["HT_GA"]).sum()
        sh_gd_per = (sh_gf - sh_ga) / matches if matches else None

        records.append(
            {
                "Team": team,
                "Matches": matches,
                "PPM": ppm,
                "PPM_H": ppm_home,
                "PPM_A": ppm_away,
                "GF_M": gf / matches,
                "GA_M": ga / matches,
                "GD_M": (gf - ga) / matches,
                "Shots_M": sh / matches if sh is not None else None,
                "ShotsA_M": sa / matches if sa is not None else None,
                "SOT_M": sot / matches if sot is not None else None,
                "SOTA_M": sota / matches if sota is not None else None,
                "ShotShare": ratio(sh, (sh + sa) if sh is not None and sa is not None else None),
                "SOTShare": ratio(sot, (sot + sota) if sot is not None and sota is not None else None),
                "ShotAcc": ratio(sot, sh),
                "ShotConv": ratio(gf, sh),
                "Corners_M": cor / matches if cor is not None else None,
                "CornersA_M": cora / matches if cora is not None else None,
                "CornerShare": ratio(cor, (cor + cora) if cor is not None and cora is not None else None),
                "Fouls_M": fouls / matches if fouls is not None else None,
                "Cards_M": (yell + red) / matches if yell is not None and red is not None else None,
                "HT_LeadRate": ht_lead_rate,
                "ComebackRate": comeback_rate,
                "ComebackPPM": comeback_ppm,
                "HoldRate": hold_rate,
                "SH_GD_M": sh_gd_per,
            }
        )
    out = pd.DataFrame(records)
    return out


def find_patterns(feat: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # Using rolling, pre-match features: evaluate lift in win rate.
    feats = [
        ("FormPtsDiff", [0.3, 0.5, 0.7]),
        ("GDDiff", [0.3, 0.5, 0.7]),
        ("SOTDiff", [0.5, 1.0, 1.5]),
        ("ShotsDiff", [1.0, 2.0]),
        ("ShotShareDiff", [0.03, 0.05]),
        ("ShotAccDiff", [0.03]),
        ("SHGDDiff", [0.1, 0.2]),
        ("HomeAwayPtsDiff", [0.2, 0.3, 0.5]),
        ("RestDiff", [1, 2]),
    ]
    records = []
    for name, thrs in feats:
        if name not in feat.columns:
            continue
        for thr in thrs:
            # home signal
            idx_h = feat[name].notna() & (feat[name] >= thr)
            if idx_h.sum() > 0:
                acc = (feat.loc[idx_h, "Actual"] == "H").mean()
                cov = idx_h.mean()
                records.append({"Side": "H", "Feature": name, "Thr": thr, "Coverage": cov, "WinRate": acc})
            # away signal
            idx_a = feat[name].notna() & (feat[name] <= -thr)
            if idx_a.sum() > 0:
                acc = (feat.loc[idx_a, "Actual"] == "A").mean()
                cov = idx_a.mean()
                records.append({"Side": "A", "Feature": name, "Thr": thr, "Coverage": cov, "WinRate": acc})

    patt = pd.DataFrame(records)
    # filter by minimum coverage and sort by win rate
    patt = patt[patt["Coverage"] >= 0.05].sort_values(["WinRate", "Coverage"], ascending=False)
    top_home = patt[patt["Side"] == "H"].head(6)
    top_away = patt[patt["Side"] == "A"].head(6)

    # draw balance conditions
    draw_records = []
    if "AbsFormPtsDiff" in feat.columns and "AbsGDDiff" in feat.columns:
        for pthr in [0.3, 0.5]:
            for gthr in [0.3, 0.5]:
                idx = (feat["AbsFormPtsDiff"] <= pthr) & (feat["AbsGDDiff"] <= gthr)
                if idx.sum() > 0:
                    acc = (feat.loc[idx, "Actual"] == "D").mean()
                    cov = idx.mean()
                    draw_records.append({"Feature": "Form+GD", "Thr": f"{pthr},{gthr}", "Coverage": cov, "DrawRate": acc})
    draw_df = pd.DataFrame(draw_records)
    if not draw_df.empty:
        draw_df = draw_df[draw_df["Coverage"] >= 0.05].sort_values(["DrawRate", "Coverage"], ascending=False).head(6)

    return top_home, top_away, draw_df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-code", default="E0")
    ap.add_argument("--last-n", type=int, default=5)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--out", default="")
    ap.add_argument("--external", default="data/processed/external_features.csv")
    args = ap.parse_args()

    code = args.league_code
    raw_dir = Path("data/raw/football_data")
    files = sorted(raw_dir.glob(f"{code}_*.csv"))
    seasons = [parse_season_code(p) for p in files]
    seasons = [s for s in seasons if s and s.isdigit()]
    seasons = sorted(set(seasons))
    if not seasons:
        print(f"No seasons found for {code}")
        return 1
    use_seasons = seasons[-args.last_n :]

    df = load_league(code, raw_dir, use_seasons)
    if df.empty:
        print(f"No data for {code} seasons: {use_seasons}")
        return 1

    odds_cols = pick_odds_cols(df)
    if not odds_cols:
        print("Missing odds columns in data.")
        return 1

    ctx = league_context(df)
    last_date = parse_last_date(df)

    ext_df = None
    ext_path = Path(args.external)
    if ext_path.exists():
        try:
            ext_df = pd.read_csv(ext_path, encoding="utf-8-sig")
        except Exception:
            ext_df = None

    feat = build_match_features(df, odds_cols, args.window, team_geo=None, external_features=ext_df)
    df_long = team_long_frame(df)
    profiles = compute_team_profiles(df_long)

    top_home, top_away, draw_df = find_patterns(feat)

    # Rank teams on key traits
    def topn(col: str, n: int = 8, asc: bool = False):
        if col not in profiles.columns:
            return pd.DataFrame()
        return profiles.sort_values(col, ascending=asc).head(n)[["Team", col, "Matches"]]

    out_path = Path(args.out) if args.out else Path(f"reports/league_deep_dive_{code}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("# League deep dive")
    lines.append(f"- Code: {code}")
    lines.append(f"- Seasons: {', '.join(use_seasons)}")
    if last_date:
        lines.append(f"- Last data date: {last_date}")
    lines.append("")
    lines.append("## League characteristics")
    if ctx:
        lines.append(f"- Home win rate: {ctx.get('home_win_rate', 0)*100:.1f}%")
        lines.append(f"- Draw rate: {ctx.get('draw_rate', 0)*100:.1f}%")
        lines.append(f"- Away win rate: {ctx.get('away_win_rate', 0)*100:.1f}%")
        lines.append(f"- Avg goals/match: {ctx.get('avg_goals', 0):.2f}")
        if "avg_sot" in ctx:
            lines.append(f"- Avg shots on target/match: {ctx.get('avg_sot', 0):.2f}")
    lines.append("")
    lines.append("## Pre-match signal patterns")
    if not top_home.empty:
        lines.append("### Strong home-win signals (coverage >= 5%)")
        for _, r in top_home.iterrows():
            lines.append(f"- {r['Feature']} >= {r['Thr']}: win {r['WinRate']*100:.1f}% | coverage {r['Coverage']*100:.1f}%")
    if not top_away.empty:
        lines.append("### Strong away-win signals (coverage >= 5%)")
        for _, r in top_away.iterrows():
            lines.append(f"- {r['Feature']} <= -{r['Thr']}: win {r['WinRate']*100:.1f}% | coverage {r['Coverage']*100:.1f}%")
    if isinstance(draw_df, pd.DataFrame) and not draw_df.empty:
        lines.append("### Draw-leaning conditions (coverage >= 5%)")
        for _, r in draw_df.iterrows():
            lines.append(f"- Balance (Form+GD) {r['Thr']}: draw {r['DrawRate']*100:.1f}% | coverage {r['Coverage']*100:.1f}%")

    lines.append("")
    lines.append("## Team profile highlights")
    lines.append("### Top attack (goals per match)")
    for _, r in topn("GF_M").iterrows():
        lines.append(f"- {r['Team']}: {r['GF_M']:.2f} (matches {int(r['Matches'])})")
    lines.append("### Strongest defense (fewest goals conceded)")
    for _, r in topn("GA_M", asc=True).iterrows():
        lines.append(f"- {r['Team']}: {r['GA_M']:.2f} (matches {int(r['Matches'])})")
    lines.append("### High pressure teams (ShotShare)")
    for _, r in topn("ShotShare").iterrows():
        lines.append(f"- {r['Team']}: {r['ShotShare']:.2f} (matches {int(r['Matches'])})")
    lines.append("### Best shot accuracy (ShotAcc)")
    for _, r in topn("ShotAcc").iterrows():
        lines.append(f"- {r['Team']}: {r['ShotAcc']:.2f} (matches {int(r['Matches'])})")
    lines.append("### Most clinical finishing (ShotConv)")
    for _, r in topn("ShotConv").iterrows():
        lines.append(f"- {r['Team']}: {r['ShotConv']:.2f} (matches {int(r['Matches'])})")
    lines.append("### Discipline (fewest cards per match)")
    for _, r in topn("Cards_M", asc=True).iterrows():
        lines.append(f"- {r['Team']}: {r['Cards_M']:.2f} (matches {int(r['Matches'])})")
    lines.append("### Resilient teams (ComebackRate when trailing at HT)")
    for _, r in topn("ComebackRate").iterrows():
        lines.append(f"- {r['Team']}: {r['ComebackRate']*100:.1f}% (matches {int(r['Matches'])})")
    lines.append("### Strong second-half teams (SH_GD_M)")
    for _, r in topn("SH_GD_M").iterrows():
        lines.append(f"- {r['Team']}: {r['SH_GD_M']:.2f} (matches {int(r['Matches'])})")
    lines.append("### Home advantage edge (PPM home - away)")
    if "PPM_H" in profiles.columns and "PPM_A" in profiles.columns:
        profiles["HomeEdge"] = profiles["PPM_H"] - profiles["PPM_A"]
        for _, r in profiles.sort_values("HomeEdge", ascending=False).head(8).iterrows():
            lines.append(f"- {r['Team']}: +{r['HomeEdge']:.2f} (matches {int(r['Matches'])})")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
