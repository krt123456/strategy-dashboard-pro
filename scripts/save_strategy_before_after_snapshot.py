#!/usr/bin/env python3
"""Save a before/after strategy snapshot package and optional comparison."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
COMPARE_SCRIPT = BASE_DIR / "scripts" / "build_before_after_strategy_comparison.py"


def _copy_many(paths: Iterable[str], target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for raw in paths:
        if not raw:
            continue
        src = Path(raw)
        if not src.exists():
            continue
        dst = target_dir / src.name
        try:
            if src.resolve() == dst.resolve():
                continue
        except Exception:
            pass
        shutil.copy2(src, dst)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save before/after strategy snapshots and comparison files.")
    parser.add_argument("--label", required=True, help="Comparison package label, e.g. watch_lab_2026-05-01")
    parser.add_argument("--before-files", nargs="*", default=[])
    parser.add_argument("--after-files", nargs="*", default=[])
    parser.add_argument("--before-advisor", default="")
    parser.add_argument("--after-advisor", default="")
    parser.add_argument("--before-guard", default="")
    parser.add_argument("--after-guard", default="")
    parser.add_argument("--before-football", default="")
    parser.add_argument("--after-football", default="")
    parser.add_argument("--before-other-sports", default="")
    parser.add_argument("--after-other-sports", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = REPORTS_DIR / "comparisons" / args.label
    before_dir = root / "before"
    after_dir = root / "after"
    _copy_many(args.before_files, before_dir)
    _copy_many(args.after_files, after_dir)

    if args.before_advisor and args.after_advisor:
        cmd = [
            sys.executable,
            str(COMPARE_SCRIPT),
            "--before-advisor",
            args.before_advisor,
            "--after-advisor",
            args.after_advisor,
            "--out-csv",
            str(root / "before_after_strategy_comparison.csv"),
            "--out-md",
            str(root / "before_after_strategy_comparison.md"),
            "--out-json",
            str(root / "before_after_strategy_comparison.json"),
        ]
        if args.before_guard:
            cmd.extend(["--before-guard", args.before_guard])
        if args.after_guard:
            cmd.extend(["--after-guard", args.after_guard])
        if args.before_football:
            cmd.extend(["--before-football", args.before_football])
        if args.after_football:
            cmd.extend(["--after-football", args.after_football])
        if args.before_other_sports:
            cmd.extend(["--before-other-sports", args.before_other_sports])
        if args.after_other_sports:
            cmd.extend(["--after-other-sports", args.after_other_sports])
        subprocess.run(cmd, cwd=BASE_DIR, check=True)

    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
