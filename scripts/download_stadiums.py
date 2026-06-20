#!/usr/bin/env python3
"""Download stadiums dataset with coordinates/capacity."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import requests  # type: ignore
except Exception:
    print("Missing dependency: requests. Install with: pip install requests", file=sys.stderr)
    raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://raw.githubusercontent.com/sorrentmutie/WorldSoccerStadiums/master/SoccerStadiums.json")
    ap.add_argument("--out", default="data/raw/stadiums/SoccerStadiums.json")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"skip (exists): {out_path}")
        return 0

    print(f"downloading: {args.url}")
    resp = requests.get(args.url, timeout=60)
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}", file=sys.stderr)
        return 1

    out_path.write_bytes(resp.content)
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
