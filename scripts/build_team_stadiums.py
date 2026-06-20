#!/usr/bin/env python3
"""Build team->stadium mapping with coordinates using openfootball clubs + stadiums dataset."""
from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import pandas as pd  # type: ignore
except Exception:
    print("Missing dependency: pandas. Install with: pip install pandas", file=sys.stderr)
    raise


def normalize_name(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(fc|cf|afc|sc|ac|ss|as|the)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_clubs(root_dir: Path) -> List[Dict[str, str]]:
    records = []
    # find all .txt files under extracted repo
    for path in root_dir.rglob("*.txt"):
        # skip readme or unrelated files
        if path.name.lower() in {"readme.txt", "readme.md", "notes.txt"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        country = ""
        current = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("="):
                country = line.lstrip("=").strip()
                continue
            if line.startswith("|"):
                # alias/variant for current club
                if current:
                    alias = line.lstrip("|").strip()
                    rec = current.copy()
                    rec["Name"] = alias
                    records.append(rec)
                continue

            # parse club line: "Club Name, City, @ Stadium" or variations
            # heuristic: split by "@" for stadium
            parts = [p.strip() for p in line.split("@")]
            left = parts[0]
            stadium = parts[1].strip() if len(parts) > 1 else ""

            # left side may include year and city: "Club, City" or "Club, Year, City"
            left_parts = [p.strip() for p in left.split(",")]
            name = left_parts[0]
            city = left_parts[-1] if len(left_parts) > 1 else ""

            current = {
                "Name": name,
                "City": city,
                "Stadium": stadium,
                "Country": country,
            }
            records.append(current)
    return records


def load_stadiums(path: Path) -> List[Dict[str, str]]:
    def parse_capacity(value) -> float | None:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        s = s.replace(",", "").replace("_", "")
        try:
            cap = float(s)
        except Exception:
            return None
        # Dataset stores many capacities in thousands (e.g., 99.354 -> 99,354).
        if cap < 1000:
            cap *= 1000
        return cap

    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for s in data:
        # Support both TitleCase and lowercase keys.
        name = s.get("name", s.get("Name", ""))
        town = s.get("town", s.get("Town", ""))
        country = s.get("country", s.get("Nation", ""))
        lat = s.get("latitude", s.get("Latitude"))
        lon = s.get("longitude", s.get("Longitude"))
        cap = parse_capacity(s.get("capacity", s.get("Capacity")))
        out.append(
            {
                "Stadium": name,
                "City": town,
                "Country": country,
                "Latitude": lat,
                "Longitude": lon,
                "Capacity": cap,
            }
        )
    return out


def best_stadium_match(stadium: str, city: str, stadiums: List[Dict[str, str]]) -> Dict[str, str] | None:
    if not stadium:
        if not city:
            return None
        city_norm = normalize_name(city)
        candidates = [s for s in stadiums if normalize_name(s["City"]) == city_norm]
        if candidates:
            # pick the largest capacity if available
            def cap_val(item):
                try:
                    return float(item.get("Capacity") or 0)
                except Exception:
                    return 0.0

            return sorted(candidates, key=cap_val, reverse=True)[0]
        return None
    st_norm = normalize_name(stadium)
    city_norm = normalize_name(city)

    # exact match on stadium + city
    for s in stadiums:
        if normalize_name(s["Stadium"]) == st_norm and (not city_norm or normalize_name(s["City"]) == city_norm):
            return s

    # fallback: stadium name only
    candidates = [s for s in stadiums if normalize_name(s["Stadium"]) == st_norm]
    if candidates:
        return candidates[0]

    # fuzzy match
    best = None
    best_score = 0.0
    for s in stadiums:
        score = SequenceMatcher(None, st_norm, normalize_name(s["Stadium"])) .ratio()
        if score > best_score:
            best_score = score
            best = s
    if best_score >= 0.85:
        return best
    # fallback: city-only match
    if city_norm:
        candidates = [s for s in stadiums if normalize_name(s["City"]) == city_norm]
        if candidates:
            def cap_val(item):
                try:
                    return float(item.get("Capacity") or 0)
                except Exception:
                    return 0.0

            return sorted(candidates, key=cap_val, reverse=True)[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clubs", default="data/raw/openfootball_clubs")
    ap.add_argument("--stadiums", default="data/raw/stadiums/SoccerStadiums.json")
    ap.add_argument("--out", default="data/processed/team_stadiums.csv")
    args = ap.parse_args()

    clubs_root = Path(args.clubs)
    # find extracted repo folder
    repo_dirs = [p for p in clubs_root.iterdir() if p.is_dir()]
    if not repo_dirs:
        print("Openfootball clubs not found. Run download_openfootball_clubs.py", file=sys.stderr)
        return 1

    stadiums_path = Path(args.stadiums)
    if not stadiums_path.exists():
        print("Stadiums dataset missing. Run download_stadiums.py", file=sys.stderr)
        return 1

    clubs = parse_clubs(repo_dirs[0])
    stadiums = load_stadiums(stadiums_path)

    # Build mapping records
    rows = []
    for c in clubs:
        match = best_stadium_match(c["Stadium"], c["City"], stadiums)
        rows.append(
            {
                "TeamName": c["Name"],
                "City": c["City"],
                "Country": c["Country"],
                "Stadium": c["Stadium"],
                "Latitude": match.get("Latitude") if match else None,
                "Longitude": match.get("Longitude") if match else None,
                "Capacity": match.get("Capacity") if match else None,
            }
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
