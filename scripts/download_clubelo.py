#!/usr/bin/env python3
"""Download ClubElo ratings (current EPL snapshot)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:
    print("Missing dependency: PyYAML. Install with: pip install pyyaml", file=sys.stderr)
    raise

try:
    import requests  # type: ignore
except Exception:
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
    api_base = cfg["sources"]["clubelo"]["api_base"].rstrip("/")
    raw_dir = Path(cfg["paths"]["raw"]) / "clubelo"

    # Current EPL snapshot
    url = f"{api_base}/EPL"
    download(url, raw_dir / "EPL_current.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
