#!/usr/bin/env python3
"""Download EPL CSVs from football-data.co.uk based on config.yaml."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    print("Missing dependency: PyYAML. Install with: pip install pyyaml", file=sys.stderr)
    raise

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    print("Missing dependency: requests. Install with: pip install requests", file=sys.stderr)
    raise


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"skip (exists): {dest}")
        return
    print(f"downloading: {url}")
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} for {url}")
    dest.write_bytes(resp.content)
    print(f"saved: {dest}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    base_url = cfg["sources"]["football_data"]["base_url"].rstrip("/")
    league_code = cfg["sources"]["football_data"]["league_code"]
    seasons = cfg["seasons"]["football_data_codes"]
    raw_dir = Path(cfg["paths"]["raw"]) / "football_data"

    for code in seasons:
        code_str = str(code)
        url = f"{base_url}/{code_str}/{league_code}.csv"
        dest = raw_dir / f"{league_code}_{code_str}.csv"
        download(url, dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
