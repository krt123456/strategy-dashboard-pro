#!/usr/bin/env python3
"""Check sports API key pools without exposing secret values.

This is a continuity guard, not an account-creation bypass.  It verifies the
currently configured key pools, writes a redacted report, and can fail the
pipeline when every key for a provider is exhausted or invalid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = BASE_DIR.parent.parent


def _first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]


@dataclass
class KeyCheck:
    provider: str
    key_id: str
    source_file: str
    ok: bool
    status: int | None
    category: str
    records: int | None
    remaining: str | None
    note: str


def _extract_keys(text: str, min_len: int) -> list[str]:
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


def _load_key_files(path: Path, min_len: int) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    files = [path] if path.is_file() else sorted(p for p in path.iterdir() if p.is_file())
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for file in files:
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for key in _extract_keys(text, min_len):
            if key in seen:
                continue
            seen.add(key)
            out.append((file.name, key))
    return out


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _category(status: int | None) -> str:
    if status == 200:
        return "ok"
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "invalid_or_forbidden"
    if status is None:
        return "network_error"
    return "http_error"


def _records(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("data", "sports", "events", "fixtures"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def _probe_oddsapi(source_file: str, key: str) -> KeyCheck:
    url = "https://api.odds-api.io/v3/events"
    params = {"apiKey": key, "sport": "football", "limit": "1"}
    try:
        resp = requests.get(url, params=params, timeout=20)
        status = resp.status_code
        payload = resp.json() if status == 200 else None
        records = _records(payload)
        remaining = resp.headers.get("x-ratelimit-remaining") or resp.headers.get("X-RateLimit-Remaining")
        note = "events sample" if status == 200 else resp.text[:120]
    except Exception as exc:
        status = None
        records = None
        remaining = None
        note = type(exc).__name__
    cat = _category(status)
    return KeyCheck("Odds-API.io", _key_id(key), source_file, status == 200, status, cat, records, remaining, note)


def _probe_oddspapi(source_file: str, key: str) -> KeyCheck:
    url = "https://api.oddspapi.io/v4/sports"
    params = {"apiKey": key}
    try:
        resp = requests.get(url, params=params, timeout=20)
        status = resp.status_code
        payload = resp.json() if status == 200 else None
        records = _records(payload)
        remaining = resp.headers.get("x-ratelimit-remaining") or resp.headers.get("X-RateLimit-Remaining")
        note = "sports catalogue" if status == 200 else resp.text[:120]
    except Exception as exc:
        status = None
        records = None
        remaining = None
        note = type(exc).__name__
    cat = _category(status)
    return KeyCheck("OddsPapi", _key_id(key), source_file, status == 200, status, cat, records, remaining, note)


def _write_report(checks: list[KeyCheck], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    json_path = out_dir / f"key_pool_health_{stamp}.json"
    md_path = out_dir / f"key_pool_health_{stamp}.md"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "checks": [asdict(item) for item in checks],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Key Pool Health",
        f"- Generated: {payload['generated_at']}",
        "- Secret values are not written here.",
        "",
        "| Provider | Key ID | File | OK | Status | Category | Records | Remaining | Note |",
        "| --- | --- | --- | --- | ---: | --- | ---: | --- | --- |",
    ]
    for item in checks:
        note = item.note.replace("\n", " ")[:100]
        lines.append(
            f"| {item.provider} | {item.key_id} | {item.source_file} | "
            f"{'yes' if item.ok else 'no'} | {item.status or ''} | {item.category} | "
            f"{item.records if item.records is not None else ''} | {item.remaining or ''} | {note} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oddsapi-path", default=str(WORKSPACE_DIR / "odds-api"))
    parser.add_argument(
        "--oddspapi-path",
        default=str(_first_existing(WORKSPACE_DIR / "api" / "oddspapi-pool", WORKSPACE_DIR / "api" / "OddsPapi.txt")),
    )
    parser.add_argument("--out-dir", default=str(BASE_DIR / "reports"))
    parser.add_argument("--max-keys-per-provider", type=int, default=5)
    parser.add_argument("--all-keys", action="store_true", help="Probe every discovered key. This can consume significant quota.")
    parser.add_argument("--fail-on-exhausted", action="store_true")
    args = parser.parse_args()

    checks: list[KeyCheck] = []
    oddsapi_keys = _load_key_files(Path(args.oddsapi_path), 16)
    oddspapi_keys = _load_key_files(Path(args.oddspapi_path), 8)
    if not args.all_keys:
        limit = max(1, args.max_keys_per_provider)
        oddsapi_keys = oddsapi_keys[:limit]
        oddspapi_keys = oddspapi_keys[:limit]
    for source_file, key in oddsapi_keys:
        checks.append(_probe_oddsapi(source_file, key))
    for source_file, key in oddspapi_keys:
        checks.append(_probe_oddspapi(source_file, key))

    json_path, md_path = _write_report(checks, Path(args.out_dir))
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")

    if not args.fail_on_exhausted:
        return 0
    providers = sorted({item.provider for item in checks})
    exhausted = [provider for provider in providers if not any(item.ok for item in checks if item.provider == provider)]
    if exhausted:
        print("Exhausted providers: " + ", ".join(exhausted))
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
