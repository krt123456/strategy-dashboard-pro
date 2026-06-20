#!/usr/bin/env python3
"""Download openfootball/clubs dataset (zip) and extract."""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from zipfile import ZipFile

try:
    import requests  # type: ignore
except Exception:
    print("Missing dependency: requests. Install with: pip install requests", file=sys.stderr)
    raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw/openfootball_clubs")
    ap.add_argument("--url", default="https://github.com/openfootball/clubs/archive/refs/heads/master.zip")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Skip if already extracted
    marker = out_dir / "_extracted.marker"
    if marker.exists():
        print(f"skip (already extracted): {out_dir}")
        return 0

    print(f"downloading: {args.url}")
    resp = requests.get(args.url, timeout=60)
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}", file=sys.stderr)
        return 1

    with ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(out_dir)

    marker.write_text("ok", encoding="utf-8")
    print(f"extracted: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
