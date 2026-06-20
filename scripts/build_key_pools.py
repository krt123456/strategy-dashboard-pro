#!/usr/bin/env python3
"""Build local key-pool directories from existing key files.

The script never prints secret values.  It deduplicates by hash and writes
provider-specific pool files with restrictive permissions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = BASE_DIR.parent.parent


def _extract_keys(text: str, *, min_len: int) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for token in text.replace(",", " ").split():
        token = token.strip()
        if len(token) < min_len:
            continue
        if not all(ch.isalnum() or ch in "-_" for ch in token):
            continue
        if token in seen:
            continue
        seen.add(token)
        keys.append(token)
    return keys


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def build_oddspapi_pool(api_dir: Path) -> dict[str, object]:
    pool = api_dir / "oddspapi-pool"
    pool.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    files: list[str] = []
    for src in sorted(api_dir.glob("OddsPapi*.txt")):
        if not src.is_file():
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for key in _extract_keys(text, min_len=8):
            key_id = _key_id(key)
            if key_id in seen:
                continue
            seen.add(key_id)
            dst = pool / f"oddspapi_{key_id}.txt"
            dst.write_text(key + "\n", encoding="utf-8")
            dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
            files.append(dst.name)
    return {"provider": "OddsPapi", "pool": str(pool), "unique_keys": len(files), "files": files}


def build_oddsapi_pool(workspace_dir: Path) -> dict[str, object]:
    pool = workspace_dir / "odds-api"
    pool.mkdir(parents=True, exist_ok=True)
    roots = [
        workspace_dir / "api",
        workspace_dir / "odds-api",
        workspace_dir / "odds-api (Copy)",
        workspace_dir / "odds-api (Copy 2)",
    ]
    seen: set[str] = set()
    for existing in sorted(pool.glob("*.txt")):
        try:
            text = existing.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for key in _extract_keys(text, min_len=16):
            seen.add(_key_id(key))

    added: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for src in sorted(root.glob("*.txt")):
            lower_name = src.name.lower()
            if not src.is_file() or "oddspapi" in lower_name:
                continue
            if "odds-api" not in lower_name and "oddsapi" not in lower_name:
                continue
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for key in _extract_keys(text, min_len=16):
                key_id = _key_id(key)
                if key_id in seen:
                    continue
                seen.add(key_id)
                dst = pool / f"discovered_oddsapi_{key_id}.txt"
                dst.write_text(key + "\n", encoding="utf-8")
                dst.chmod(stat.S_IRUSR | stat.S_IWUSR)
                added.append(dst.name)
    return {"provider": "Odds-API.io", "pool": str(pool), "added_keys": len(added), "files": added}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-dir", default=str(WORKSPACE_DIR / "api"))
    parser.add_argument("--provider", choices=["oddsapi", "oddspapi", "all"], default="all")
    args = parser.parse_args()

    api_dir = Path(args.api_dir)
    results = []
    if args.provider in {"oddsapi", "all"}:
        results.append(build_oddsapi_pool(api_dir.parent))
    if args.provider in {"oddspapi", "all"}:
        results.append(build_oddspapi_pool(api_dir))
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
