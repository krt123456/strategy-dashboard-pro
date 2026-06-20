#!/usr/bin/env python3
"""Analyze stadium capacity + travel distance impact on match outcomes.

Uses only standard library to avoid extra dependencies.
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def normalize_team_name(name: str) -> str:
    import re

    s = str(name).lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(fc|cf|afc|sc|ac|ss|as|the)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_float(val: Optional[str]) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    s = s.replace(",", "").replace("_", "")
    try:
        return float(s)
    except Exception:
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    if hi == lo:
        return vals[lo]
    frac = pos - lo
    return vals[lo] + (vals[hi] - vals[lo]) * frac


def load_team_geo(path: Path) -> Dict[str, Dict[str, float]]:
    geo: Dict[str, Dict[str, float]] = {}
    if not path.exists():
        return geo
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("TeamName") or "").strip()
            if not name:
                continue
            key = normalize_team_name(name)
            lat = parse_float(row.get("Latitude"))
            lon = parse_float(row.get("Longitude"))
            cap = parse_float(row.get("Capacity"))
            if lat is None or lon is None:
                if cap is None:
                    continue
                existing = geo.get(key)
                if existing is None:
                    geo[key] = {"capacity": cap}
                else:
                    existing_cap = existing.get("capacity")
                    if existing_cap is None or cap > existing_cap:
                        existing["capacity"] = cap
                continue
            if key not in geo:
                geo[key] = {"lat": lat, "lon": lon, "capacity": cap} if cap is not None else {"lat": lat, "lon": lon}
                continue
            # prefer rows with capacity and keep the larger capacity if both exist
            if cap is not None:
                existing = geo[key]
                existing_cap = existing.get("capacity")
                if existing_cap is None or cap > existing_cap:
                    geo[key] = {"lat": lat, "lon": lon, "capacity": cap}
    return geo


def load_league_files(raw_dir: Path, code: str) -> List[Path]:
    all_path = raw_dir / f"{code}_all.csv"
    if all_path.exists():
        return [all_path]
    return sorted(raw_dir.glob(f"{code}_*.csv"))


def iter_matches(path: Path) -> Iterable[Tuple[str, str, float, float]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            home = (row.get("HomeTeam") or row.get("Home") or "").strip()
            away = (row.get("AwayTeam") or row.get("Away") or "").strip()
            if not home or not away:
                continue
            hg = parse_float(row.get("FTHG") or row.get("HG"))
            ag = parse_float(row.get("FTAG") or row.get("AG"))
            if hg is None or ag is None:
                continue
            yield home, away, hg, ag


def result_label(hg: float, ag: float) -> str:
    if hg > ag:
        return "H"
    if ag > hg:
        return "A"
    return "D"


def fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "NA"
    return f"{val * 100:.1f}%"


def fmt_num(val: Optional[float]) -> str:
    if val is None:
        return "NA"
    return f"{val:.1f}"


def rate(labels: List[str], target: str) -> Optional[float]:
    if not labels:
        return None
    return labels.count(target) / len(labels)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw/football_data")
    ap.add_argument("--team-stadiums", default="data/processed/team_stadiums.csv")
    ap.add_argument("--out", default="reports/stadium_effect_report.md")
    args = ap.parse_args()

    raw_dir = Path(args.raw)
    team_geo = load_team_geo(Path(args.team_stadiums))

    leagues = [
        ("E0", "England Premier League"),
        ("E1", "England Championship"),
        ("E2", "England League One"),
        ("E3", "England League Two"),
        ("EC", "England Conference"),
        ("SC0", "Scotland Premiership"),
        ("SC1", "Scotland Championship"),
        ("SC2", "Scotland League One"),
        ("SC3", "Scotland League Two"),
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

    per_league = []
    all_matches = []

    for code, name in leagues:
        paths = load_league_files(raw_dir, code)
        if not paths:
            continue
        matches: List[Tuple[Optional[float], Optional[float], str]] = []
        for path in paths:
            for home, away, hg, ag in iter_matches(path):
                h_key = normalize_team_name(home)
                a_key = normalize_team_name(away)
                h_geo = team_geo.get(h_key)
                a_geo = team_geo.get(a_key)
                cap = h_geo.get("capacity") if h_geo else None
                travel = None
                if h_geo and a_geo:
                    if all(k in h_geo for k in ["lat", "lon"]) and all(k in a_geo for k in ["lat", "lon"]):
                        travel = haversine_km(a_geo["lat"], a_geo["lon"], h_geo["lat"], h_geo["lon"])
                res = result_label(hg, ag)
                matches.append((cap, travel, res))
                all_matches.append((cap, travel, res, name))

        if not matches:
            continue

        caps = [c for c, _, _ in matches if c is not None]
        travels = [t for _, t, _ in matches if t is not None]
        cap_q1 = quantile(caps, 0.25)
        cap_q3 = quantile(caps, 0.75)
        trav_q1 = quantile(travels, 0.25)
        trav_q3 = quantile(travels, 0.75)

        cap_low = [r for c, _, r in matches if cap_q1 is not None and c is not None and c <= cap_q1]
        cap_high = [r for c, _, r in matches if cap_q3 is not None and c is not None and c >= cap_q3]
        trav_low = [r for _, t, r in matches if trav_q1 is not None and t is not None and t <= trav_q1]
        trav_high = [r for _, t, r in matches if trav_q3 is not None and t is not None and t >= trav_q3]

        base_hw = rate([r for _, _, r in matches], "H")
        cap_low_hw = rate(cap_low, "H")
        cap_high_hw = rate(cap_high, "H")
        trav_low_hw = rate(trav_low, "H")
        trav_high_hw = rate(trav_high, "H")

        per_league.append(
            {
                "league": name,
                "n_total": len(matches),
                "cap_cov": len(caps) / len(matches) if matches else 0.0,
                "cap_q1": cap_q1,
                "cap_q3": cap_q3,
                "cap_low_hw": cap_low_hw,
                "cap_high_hw": cap_high_hw,
                "cap_delta": (cap_high_hw - cap_low_hw) if cap_low_hw is not None and cap_high_hw is not None else None,
                "trav_cov": len(travels) / len(matches) if matches else 0.0,
                "trav_q1": trav_q1,
                "trav_q3": trav_q3,
                "trav_low_hw": trav_low_hw,
                "trav_high_hw": trav_high_hw,
                "trav_delta": (trav_high_hw - trav_low_hw) if trav_low_hw is not None and trav_high_hw is not None else None,
                "base_hw": base_hw,
            }
        )

    overall_caps = [c for c, _, _, _ in all_matches if c is not None]
    overall_travel = [t for _, t, _, _ in all_matches if t is not None]
    overall_q1 = quantile(overall_caps, 0.25)
    overall_q3 = quantile(overall_caps, 0.75)
    overall_t1 = quantile(overall_travel, 0.25)
    overall_t3 = quantile(overall_travel, 0.75)

    overall_cap_low = [r for c, _, r, _ in all_matches if overall_q1 is not None and c is not None and c <= overall_q1]
    overall_cap_high = [r for c, _, r, _ in all_matches if overall_q3 is not None and c is not None and c >= overall_q3]
    overall_trav_low = [r for _, t, r, _ in all_matches if overall_t1 is not None and t is not None and t <= overall_t1]
    overall_trav_high = [r for _, t, r, _ in all_matches if overall_t3 is not None and t is not None and t >= overall_t3]

    overall_base_hw = rate([r for _, _, r, _ in all_matches], "H")
    overall_cap_low_hw = rate(overall_cap_low, "H")
    overall_cap_high_hw = rate(overall_cap_high, "H")
    overall_trav_low_hw = rate(overall_trav_low, "H")
    overall_trav_high_hw = rate(overall_trav_high, "H")

    out_lines = []
    out_lines.append("# Stadium Effect Report")
    out_lines.append("")
    out_lines.append("Data coverage:")
    out_lines.append(f"- Leagues analyzed: {len(per_league)}")
    out_lines.append(f"- Matches with results: {len(all_matches)}")
    out_lines.append(f"- Matches with capacity: {len(overall_caps)} ({(len(overall_caps) / len(all_matches) * 100) if all_matches else 0:.1f}%)")
    out_lines.append(f"- Matches with travel km: {len(overall_travel)} ({(len(overall_travel) / len(all_matches) * 100) if all_matches else 0:.1f}%)")
    out_lines.append("")
    out_lines.append("Overall home-win effect:")
    out_lines.append(f"- Base home win rate: {fmt_pct(overall_base_hw)}")
    out_lines.append(
        f"- Capacity Q1/Q3: {fmt_num(overall_q1)} / {fmt_num(overall_q3)} | "
        f"HW low={fmt_pct(overall_cap_low_hw)} vs high={fmt_pct(overall_cap_high_hw)}"
    )
    out_lines.append(
        f"- Travel Q1/Q3 (km): {fmt_num(overall_t1)} / {fmt_num(overall_t3)} | "
        f"HW short={fmt_pct(overall_trav_low_hw)} vs long={fmt_pct(overall_trav_high_hw)}"
    )
    out_lines.append("")
    out_lines.append("Per-league summary (home-win rate in low/high bins):")
    out_lines.append("")
    out_lines.append(
        "| League | Matches | CapCov | CapQ1 | CapQ3 | HW low | HW high | Delta | TravelCov | TravQ1 | TravQ3 | HW short | HW long | Delta |"
    )
    out_lines.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    for row in per_league:
        out_lines.append(
            "| {league} | {n_total} | {cap_cov} | {cap_q1} | {cap_q3} | {cap_low} | {cap_high} | {cap_delta} | "
            "{trav_cov} | {trav_q1} | {trav_q3} | {trav_low} | {trav_high} | {trav_delta} |".format(
                league=row["league"],
                n_total=row["n_total"],
                cap_cov=fmt_pct(row["cap_cov"]),
                cap_q1=fmt_num(row["cap_q1"]),
                cap_q3=fmt_num(row["cap_q3"]),
                cap_low=fmt_pct(row["cap_low_hw"]),
                cap_high=fmt_pct(row["cap_high_hw"]),
                cap_delta=fmt_pct(row["cap_delta"]) if row["cap_delta"] is not None else "NA",
                trav_cov=fmt_pct(row["trav_cov"]),
                trav_q1=fmt_num(row["trav_q1"]),
                trav_q3=fmt_num(row["trav_q3"]),
                trav_low=fmt_pct(row["trav_low_hw"]),
                trav_high=fmt_pct(row["trav_high_hw"]),
                trav_delta=fmt_pct(row["trav_delta"]) if row["trav_delta"] is not None else "NA",
            )
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
