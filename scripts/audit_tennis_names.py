#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Optional, Tuple

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
TENNIS_BASE_DIR = Path("/home/luna/tennis_predictor")
SGODDS_MATCHES = TENNIS_BASE_DIR / "data" / "processed" / "sgodds_tennis_matches.csv"
SGODDS_EVENTS_DIR = TENNIS_BASE_DIR / "data" / "sgodds"
REPORTS_DIR = BASE_DIR / "reports"


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    text = _strip_accents(str(name))
    text = text.lower().replace("'", " ").replace("-", " ")
    if "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) >= 2:
            text = f"{parts[1]} {parts[0]}"
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _token_set(name: str) -> set[str]:
    return set(_normalize_name(name).split())


def _tokens_match(name_a: str, name_b: str) -> bool:
    if not name_a or not name_b:
        return False
    a = _token_set(name_a)
    b = _token_set(name_b)
    if not a or not b:
        return False
    inter = a & b
    return len(inter) >= min(len(a), len(b))


def _iter_events(paths: Iterable[Path]):
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except FileNotFoundError:
            continue


def _parse_slug(url: Optional[str], home_hint: str, away_hint: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, None
    slug = url.split("?")[0].rstrip("/").split("/")[-1]
    slug = re.sub(r"^\d+-", "", slug)
    parts = [p for p in slug.split("-") if p]
    if not parts:
        return None, None
    if "vs" in parts:
        idx = parts.index("vs")
        left = parts[:idx]
        right = parts[idx + 1 :]
        return " ".join(left).strip(), " ".join(right).strip()

    home_tokens = _token_set(home_hint)
    away_tokens = _token_set(away_hint)

    def overlap_score(tokens: list[str], target: set[str]) -> int:
        return len(set(tokens) & target)

    best = None
    for i in range(1, len(parts)):
        left = parts[:i]
        right = parts[i:]
        score_lr = overlap_score(left, home_tokens) + overlap_score(right, away_tokens)
        score_rl = overlap_score(left, away_tokens) + overlap_score(right, home_tokens)
        if score_rl > score_lr:
            score = score_rl
            cand_home, cand_away = right, left
        else:
            score = score_lr
            cand_home, cand_away = left, right
        if best is None or score > best[0]:
            best = (score, cand_home, cand_away)
    if best is None:
        mid = len(parts) // 2
        return " ".join(parts[:mid]).strip(), " ".join(parts[mid:]).strip()
    return " ".join(best[1]).strip(), " ".join(best[2]).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit tennis player names vs bookmaker slugs.")
    parser.add_argument("--date", required=True, help="Date in YYYY-MM-DD")
    args = parser.parse_args()

    target_date = dt.date.fromisoformat(args.date)
    if not SGODDS_MATCHES.exists():
        raise SystemExit(f"missing {SGODDS_MATCHES}")

    matches = pd.read_csv(SGODDS_MATCHES)
    matches["date"] = pd.to_datetime(matches["date"], errors="coerce").dt.date
    day_matches = matches[matches["date"] == target_date].copy()
    if day_matches.empty:
        print("No matches for date.")
        return 0

    event_paths = list(SGODDS_EVENTS_DIR.glob("events_*.jsonl"))
    events = []
    for ev in _iter_events(event_paths):
        starts = (ev.get("status") or {}).get("startsAt")
        if not starts:
            continue
        try:
            ev_date = dt.datetime.fromisoformat(starts.replace("Z", "+00:00")).date()
        except Exception:
            continue
        if ev_date != target_date:
            continue
        teams = ev.get("teams") or {}
        home = (teams.get("home") or {}).get("names", {}).get("long")
        away = (teams.get("away") or {}).get("names", {}).get("long")
        links = (ev.get("links") or {}).get("bookmakers") or {}
        events.append(
            {
                "home": home,
                "away": away,
                "1xbet": links.get("1xbet"),
                "nordicbet": links.get("nordicbet"),
                "betsson": links.get("betsson"),
            }
        )

    # Index events by token sets (order insensitive).
    by_tokens: dict[tuple[frozenset[str], frozenset[str]], list[dict]] = {}
    for ev in events:
        key = (frozenset(_token_set(ev.get("home") or "")), frozenset(_token_set(ev.get("away") or "")))
        by_tokens.setdefault(key, []).append(ev)

    rows = []
    for _, row in day_matches.iterrows():
        home = row.get("player_home")
        away = row.get("player_away")
        key = (frozenset(_token_set(home)), frozenset(_token_set(away)))
        candidates = by_tokens.get(key)
        if not candidates:
            candidates = by_tokens.get((key[1], key[0]))
        ev = candidates[0] if candidates else {}

        one_url = ev.get("1xbet") if ev else None
        nb_url = ev.get("nordicbet") if ev else None
        if not nb_url:
            nb_url = ev.get("betsson") if ev else None
        one_home, one_away = _parse_slug(one_url, str(home or ""), str(away or ""))
        nb_home, nb_away = _parse_slug(nb_url, str(home or ""), str(away or ""))

        rows.append(
            {
                "home": home,
                "away": away,
                "1xbet_home": one_home,
                "1xbet_away": one_away,
                "nordicbet_home": nb_home,
                "nordicbet_away": nb_away,
                "1xbet_match": _tokens_match(home, one_home or "") and _tokens_match(away, one_away or ""),
                "nordicbet_match": _tokens_match(home, nb_home or "") and _tokens_match(away, nb_away or ""),
                "1xbet_url": one_url,
                "nordicbet_url": nb_url,
            }
        )

    out = pd.DataFrame(rows)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"tennis_name_audit_{target_date}.csv"
    out.to_csv(out_path, index=False)
    print(f"saved {out_path}")
    mismatches = out[(~out["1xbet_match"]) | (~out["nordicbet_match"])]
    if not mismatches.empty:
        print("mismatches:")
        print(mismatches[["home", "away", "1xbet_home", "1xbet_away", "nordicbet_home", "nordicbet_away"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
