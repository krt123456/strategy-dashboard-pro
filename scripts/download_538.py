#!/usr/bin/env python3
"""Download FiveThirtyEight SPI datasets (matches + rankings)."""
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


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if value:
        return [str(value)]
    return []


def download(urls, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"skip (exists): {dest}")
        return
    headers = {"User-Agent": "Mozilla/5.0 (compatible; EPL-Model/1.0)"}
    last_error = None
    for url in _as_list(urls):
        print(f"downloading: {url}")
        try:
            resp = requests.get(url, timeout=30, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code} for {url}")
            content = resp.content
            if content.lstrip().startswith(b"<!doctype html") or b"<html" in content[:500].lower():
                raise RuntimeError(f"Unexpected HTML response for {url}.")
            dest.write_bytes(content)
            print(f"saved: {dest}")
            return
        except Exception as exc:
            last_error = exc
            print(f"failed: {url} ({exc})")

    raise RuntimeError(f"All downloads failed for {dest}: {last_error}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    raw_dir = Path(cfg["paths"]["raw"]) / "fivethirtyeight"
    matches_url = cfg["sources"]["fivethirtyeight"]["spi_matches_url"]
    rankings_url = cfg["sources"]["fivethirtyeight"]["spi_rankings_url"]

    download(matches_url, raw_dir / "spi_matches.csv")
    download(rankings_url, raw_dir / "spi_global_rankings.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
