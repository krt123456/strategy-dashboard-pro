#!/usr/bin/env python3
"""Daily strategy evolution — appends NEW expert versions, never removes old ones.

Each run reads the latest graded results, applies expert analysis, and creates a
dated batch of new versioned strategies that are appended to the stable library
(data/strategy_versions_library.json). The library is append-only: original
strategies and every prior version stay untouched, so the candidate pool only
grows — exactly what the selection phase needs.

The runners (multi_strategy_agent, cross_source_strategy_runner) load the whole
library so every accumulated version competes live, and the daily resolver +
report rank them. Over weeks the best versions surface from a large field.

Naming: {concept}_v{N}_{YYYYMMDD} so each version is traceable to its birth date
and the analysis that created it.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List

PROJECT_DIR = Path(__file__).resolve().parent.parent
LIBRARY_PATH = PROJECT_DIR / "data" / "strategy_versions_library.json"
ANALYSIS_LOG = PROJECT_DIR / "reports" / "strategy_intelligence.md"


def _load_library() -> List[dict]:
    if not LIBRARY_PATH.exists():
        return []
    return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))


def _existing_names(lib: List[dict]) -> set:
    return {v["name"] for v in lib}


def _append(versions: List[dict], batch_tag: str) -> int:
    lib = _load_library()
    have = _existing_names(lib)
    added = 0
    for v in versions:
        if v["name"] in have:
            continue
        v["created"] = batch_tag
        lib.append(v)
        added += 1
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_PATH.write_text(json.dumps(lib, indent=2), encoding="utf-8")
    return added


# ---------------------------------------------------------------------------
# Expert version batches. Each batch is the product of analysing the latest
# graded results. Add a new batch function per analysis pass; never edit an old
# batch (the library is append-only history).
# ---------------------------------------------------------------------------

def batch_20260620() -> List[dict]:
    """Pass 2 — born from the 2026-06-19 graded results (234 picks).

    Confirmed live: coinflip-home edge is real (v5 67%, premium 56% vs 50%
    baseline); mid-favorite zone loses (moderate_home 40%); pure_elo is the
    strongest basketball signal (76%). These versions explore adjacent bands and
    combine the two winning signals (ELO + coinflip), plus a trap-avoiding
    favourite filter drawn from the vig analysis.
    """
    return [
        {
            "name": "coinflip_home_v1_20260620", "base": "contrarian_home_coinflip",
            "rule": "home_band", "params": {"lo": 0.50, "hi": 0.60},
            "rationale": "v5 (0.48-0.58) confirmed +1.39 live; nudge upper edge toward slight-favourite",
        },
        {
            "name": "coinflip_home_v2_20260620", "base": "contrarian_home_coinflip",
            "rule": "home_band", "params": {"lo": 0.46, "hi": 0.56},
            "rule_home": True, "params2": {"lo": 0.46, "hi": 0.56},
            "rationale": "explore lower coinflip band (pure underdog-coinflip) for the durable edge",
        },
        {
            "name": "elo_coinflip_combo_v1_20260620", "base": "pure_elo",
            "rule": "elo_and_coinflip", "params": {"elo_min_home": 0.52, "band_lo": 0.45, "band_hi": 0.58},
            "rationale": "combine the two strongest live signals: ELO favours home AND market is a coinflip. Highest-conviction candidate.",
        },
        {
            "name": "trapfree_favorite_v1_20260620", "base": "clear_favorite",
            "rule": "favorite_exclude_trap", "params": {"margin": 0.25, "trap_lo": 1.5, "trap_hi": 1.8},
            "rationale": "vig analysis: odds 1.5-1.8 lost -82 live; bet clear favourites but EXCLUDE the trap zone",
        },
        {
            "name": "pure_elo_strict_v1_20260620", "base": "pure_elo",
            "rule": "elo_strong_margin", "params": {"elo_diff_min": 100},
            "rationale": "pure_elo is best (76%); restrict to large ELO gaps for high-conviction picks",
        },
    ]


def get_active_versions() -> List[dict]:
    """All versions in the library (every accumulated candidate competes live)."""
    return _load_library()


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Append a new expert version batch to the strategy library.")
    ap.add_argument("--batch", default="20260620", help="batch tag / function name to run")
    args = ap.parse_args()

    batches = {"20260620": batch_20260620}
    fn = batches.get(args.batch)
    if fn is None:
        print(f"Unknown batch '{args.batch}'. Known: {list(batches)}")
        return 1
    new_versions = fn()
    added = _append(new_versions, args.batch)
    lib = _load_library()
    print(f"Appended {added} new versions (batch {args.batch}). Library now holds {len(lib)} versions.")
    for v in new_versions:
        print(f"  + {v['name']:<34} [{v['rule']}] {v['rationale'][:60]}")
    print(f"\nLibrary → {LIBRARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
