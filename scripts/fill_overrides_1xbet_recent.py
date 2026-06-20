#!/usr/bin/env python3
import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from difflib import SequenceMatcher

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.engine import (
    compute_range,
    compute_range_basketball,
    compute_range_hockey,
    compute_range_tennis,
    NAME_OVERRIDES_1XBET,
)


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _norm(text: str) -> str:
    text = _strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _choose_display(raw: str, variants: dict[str, int]) -> str:
    items = list(variants.items())
    if not items:
        return raw
    raw_norm = _norm(raw)

    def score(item):
        name, count = item
        name_norm = _norm(name)
        sim = SequenceMatcher(None, raw_norm, name_norm).ratio() if raw_norm and name_norm else 0.0
        return (-count, -sim, len(name))

    items.sort(key=score)
    return items[0][0]


def _collect_variant_counts(df: pd.DataFrame, sport: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if df is None or df.empty:
        return counts
    for row in df.to_dict("records"):
        for role in ("Home", "Away", "Pred"):
            raw = row.get(role)
            if raw is None:
                continue
            raw = str(raw).strip()
            if not raw:
                continue
            display = row.get(f"{role}Display") or raw
            display = str(display).strip()
            counts[raw][display] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill 1XBet name overrides from recent picks.")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--season", default="2526", help="Football season code")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=90)
    if start > end:
        start, end = end, start

    results = {
        "football": compute_range(start, end, auto_update_future=False, season_code=args.season).picks,
        "basketball": compute_range_basketball(start, end).picks,
        "tennis": compute_range_tennis(start, end).picks,
        "hockey": compute_range_hockey(start, end).picks,
    }

    overrides: dict[str, dict[str, str]] = {"football": {}, "basketball": {}, "tennis": {}, "hockey": {}}
    if NAME_OVERRIDES_1XBET.exists():
        try:
            payload = json.loads(NAME_OVERRIDES_1XBET.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            for sport, mapping in payload.items():
                if isinstance(mapping, dict):
                    overrides[str(sport).strip().lower()] = {str(k): str(v) for k, v in mapping.items()}

    added = {"football": 0, "basketball": 0, "tennis": 0, "hockey": 0}
    updated = {"football": 0, "basketball": 0, "tennis": 0, "hockey": 0}

    for sport, df in results.items():
        counts = _collect_variant_counts(df, sport)
        for raw, variants in counts.items():
            choice = _choose_display(raw, variants)
            if not choice:
                continue
            if choice == raw:
                continue
            existing = overrides.get(sport, {}).get(raw)
            if existing is None:
                overrides.setdefault(sport, {})[raw] = choice
                added[sport] += 1
            elif existing != choice:
                overrides.setdefault(sport, {})[raw] = choice
                updated[sport] += 1

    if args.dry_run:
        print("dry run: no file changes")
        print("added", added)
        print("updated", updated)
        return 0

    NAME_OVERRIDES_1XBET.parent.mkdir(parents=True, exist_ok=True)
    NAME_OVERRIDES_1XBET.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"updated {NAME_OVERRIDES_1XBET}")
    print("added", added)
    print("updated", updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
