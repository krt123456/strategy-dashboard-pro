#!/usr/bin/env python3
"""Discover local sports-data API key candidates without exposing secrets."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = BASE_DIR.parent.parent
COMMANDS_DIR = Path("/home/krt/Desktop/أوامر التشغيل")

EXCLUDE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    ".venv_dashboard",
    "__pycache__",
    "node_modules",
    "site-packages",
}
EXTENSIONS = {".env", ".json", ".txt", ".yaml", ".yml", ""}
SPORT_HINTS = (
    "odds",
    "oddspapi",
    "papi",
    "sport",
    "sportradar",
    "sportdevs",
    "betstack",
    "bet365",
    "api",
)
PROVIDER_HINTS = {
    "Odds-API.io": ("odds-api", "oddsapi", "api.odds-api.io"),
    "OddsPapi": ("oddspapi", "papi", "api.oddspapi.io"),
    "SportDevs": ("sportdevs",),
    "Sportradar": ("sportradar",),
    "Betstack": ("betstack",),
    "SportsGame": ("sportsgame", "sportsgame", "sports game"),
    "Generic Sports API": ("sport", "api"),
}


@dataclass
class Candidate:
    provider_guess: str
    key_id: str
    source_file: str
    bytes: int
    modified: str
    token_length: int


def _is_excluded(path: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in path.parts)


def _has_hint(path: Path) -> bool:
    text = str(path).lower()
    return any(hint in text for hint in SPORT_HINTS)


def _provider_guess(path: Path, text: str) -> str:
    blob = f"{path} {text[:2000]}".lower()
    for provider, hints in PROVIDER_HINTS.items():
        if any(hint in blob for hint in hints):
            return provider
    return "Unknown"


def _extract_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    # Keep this intentionally conservative to avoid harvesting report prose.
    for raw in re.split(r"[\s,'\"`=:+|]+", text):
        token = raw.strip()
        if len(token) < 16 or len(token) > 160:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            continue
        if token.lower() in {"authorization", "bearer", "apikey", "api_key"}:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _key_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _iter_files(roots: Iterable[Path], *, max_size: int) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if _is_excluded(path):
                continue
            if path.suffix.lower() not in EXTENSIONS:
                continue
            if not _has_hint(path):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > max_size:
                continue
            yield path


def discover(roots: list[Path], *, max_size: int) -> list[Candidate]:
    out: list[Candidate] = []
    seen_tokens: set[str] = set()
    for path in _iter_files(roots, max_size=max_size):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            stat = path.stat()
        except OSError:
            continue
        tokens = _extract_tokens(text)
        if not tokens:
            continue
        provider = _provider_guess(path, text)
        for token in tokens:
            token_hash = _key_id(token)
            if token_hash in seen_tokens:
                continue
            seen_tokens.add(token_hash)
            out.append(
                Candidate(
                    provider_guess=provider,
                    key_id=token_hash,
                    source_file=str(path),
                    bytes=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    token_length=len(token),
                )
            )
    return out


def write_reports(candidates: list[Candidate], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    json_path = out_dir / f"sports_key_discovery_{stamp}.json"
    md_path = out_dir / f"جرد كل مفاتيح الرياضة Strategy Dashboard Pro {stamp}.md"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_count": len(candidates),
        "candidates": [asdict(item) for item in candidates],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# جرد كل مفاتيح الرياضة Strategy Dashboard Pro",
        "",
        f"آخر تحديث: {payload['generated_at']}",
        "",
        "لا يحتوي هذا الملف على قيم المفاتيح. يتم عرض hash قصير فقط لتمييز المفاتيح والمكررات.",
        "",
        f"- عدد المفاتيح المرشحة غير المكررة: {len(candidates)}",
        "",
        "| Provider | Key ID | Length | File | Bytes | Modified |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for item in sorted(candidates, key=lambda c: (c.provider_guess, c.source_file, c.key_id)):
        lines.append(
            f"| {item.provider_guess} | {item.key_id} | {item.token_length} | "
            f"`{item.source_file}` | {item.bytes} | {item.modified} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--max-size", type=int, default=20_000)
    parser.add_argument("--broad", action="store_true", help="Also scan the full project and Desktop; may include many false positives.")
    parser.add_argument("--out-dir", default=str(COMMANDS_DIR))
    args = parser.parse_args()

    roots = [Path(p).expanduser() for p in args.root] if args.root else [
        WORKSPACE_DIR / "api",
        WORKSPACE_DIR / "odds-api",
        WORKSPACE_DIR / "odds-api (Copy)",
        WORKSPACE_DIR / "odds-api (Copy 2)",
        Path("/home/krt/Desktop") / "api",
        Path("/home/krt/Desktop") / "odds-api",
    ]
    if args.broad:
        roots.extend([BASE_DIR, Path("/home/krt/Desktop")])
    candidates = discover(roots, max_size=max(128, args.max_size))
    json_path, md_path = write_reports(candidates, Path(args.out_dir))
    print(json.dumps({"candidates": len(candidates), "json": str(json_path), "md": str(md_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
