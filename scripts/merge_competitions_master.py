#!/usr/bin/env python3
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_PATH = BASE_DIR / "reports" / "primary_strategy_all_competitions.csv"
LOOKUP_PATH = BASE_DIR / "reports" / "competition_api_lookup.csv"
OUT_PATH = BASE_DIR / "reports" / "competitions_master.csv"
OUT_MISSING_PATH = BASE_DIR / "reports" / "competitions_missing_from_api.csv"


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    text = _strip_accents(text.lower())
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def main() -> int:
    if not LOCAL_PATH.exists():
        print(f"Missing local competitions file: {LOCAL_PATH}")
        return 1
    if not LOOKUP_PATH.exists():
        print(f"Missing Sportradar lookup file: {LOOKUP_PATH}")
        return 1

    local = pd.read_csv(LOCAL_PATH)
    local = local.rename(columns={"Code": "LocalCode", "League": "LocalLeague"})
    local = local[["LocalCode", "LocalLeague", "Source", "Status"]].copy()
    local["Origin"] = "local"
    local["CompetitionId"] = ""
    local["Category"] = ""
    local["InputName"] = ""
    local["LookupStatus"] = ""
    local["LookupScore"] = ""
    local["LocalMatchCode"] = local["LocalCode"]
    local["LocalMatchLeague"] = local["LocalLeague"]
    local["InLocal"] = True

    lookup = pd.read_csv(LOOKUP_PATH)
    lookup = lookup[lookup["status"].isin(["matched", "ambiguous"])].copy()
    if lookup.empty:
        print("No matched competitions in lookup file.")
        return 1

    local_norm = local["LocalLeague"].fillna("").map(_normalize)
    local_names = local["LocalLeague"].fillna("").tolist()
    local_codes = local["LocalCode"].fillna("").tolist()

    def best_local_match(name: str) -> tuple[str, str, float]:
        name_norm = _normalize(name)
        best_score = 0.0
        best_name = ""
        best_code = ""
        for code, lname, lnorm in zip(local_codes, local_names, local_norm):
            score = _similarity(name_norm, lnorm)
            if score > best_score:
                best_score = score
                best_name = lname
                best_code = code
        return best_code, best_name, best_score

    rows = []
    for _, row in lookup.iterrows():
        best_code, best_name, best_score = best_local_match(str(row.get("best_match", "")))
        rows.append(
            {
                "Origin": "sportradar",
                "LocalCode": "",
                "LocalLeague": "",
                "Source": "sportradar",
                "Status": "",
                "CompetitionId": row.get("competition_id", ""),
                "Category": row.get("category", ""),
                "InputName": row.get("input", ""),
                "LookupStatus": row.get("status", ""),
                "LookupScore": row.get("score", ""),
                "LocalMatchCode": best_code,
                "LocalMatchLeague": best_name,
                "InLocal": bool(best_score >= 0.86),
            }
        )

    sr_df = pd.DataFrame(rows)
    master = pd.concat([local, sr_df], ignore_index=True)
    master.to_csv(OUT_PATH, index=False)

    missing = pd.read_csv(LOOKUP_PATH)
    missing = missing[missing["status"] == "not_found"].copy()
    if not missing.empty:
        missing.to_csv(OUT_MISSING_PATH, index=False)

    print(f"Wrote master list: {OUT_PATH}")
    if not missing.empty:
        print(f"Wrote missing list: {OUT_MISSING_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
