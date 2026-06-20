#!/usr/bin/env python3
"""Run the local Strategy Dashboard brain cycle.

The cycle mirrors the useful parts of the user's brain workflow: rebuild memory,
validate gates, refresh health, persist lessons, and produce a next-action map.
It does not call external AI or fetch network data.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.brain_ops import build_brain_state, update_brain_memory, write_codex_advisor_packet  # noqa: E402


def _run(label: str, cmd: list[str]) -> tuple[str, int, str]:
    proc = subprocess.run(cmd, cwd=PROJECT_DIR, text=True, capture_output=True, check=False)
    output = (proc.stdout + proc.stderr).strip()
    return label, proc.returncode, output


def _render_report(results: list[tuple[str, int, str]], memory_path: Path, packet_path: Path | None) -> str:
    state = build_brain_state(PROJECT_DIR)
    lines = [
        "# Strategy Dashboard Brain Cycle",
        f"- Date: {date.today().isoformat()}",
        "- Network: not used",
        "- Mode: local strict-precision evolution",
        "",
        "## Pass Results",
        "",
    ]
    for label, code, output in results:
        status = "PASS" if code == 0 else "FAIL"
        lines.extend([f"### {label}", f"- Status: {status}", ""])
        if output:
            lines.extend(["```text", output[-4000:], "```", ""])
    lines.extend(
        [
            "## Memory",
            "",
            f"- Updated: `{memory_path}`",
        ]
    )
    if packet_path:
        lines.append(f"- Codex advisor packet: `{packet_path}`")
    lines.extend(["", "## Next Actions", ""])
    for action in state.get("next_actions", []):
        lines.append(f"- {action}")
    lines.extend(["", "## Hard Rules", ""])
    for rule in state.get("hard_rules", []):
        lines.append(f"- {rule}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex-packet", action="store_true", help="Also generate a Codex advisor packet.")
    args = ap.parse_args()

    python = sys.executable
    results = [
        _run("Pass 1 - rebuild decision profile", [python, "scripts/build_decision_brain_profile.py"]),
        _run("Pass 2 - validate decision gates", [python, "scripts/validate_decision_brain.py"]),
        _run("Pass 3 - refresh model health", [python, "scripts/model_health_report.py"]),
    ]
    state = build_brain_state(PROJECT_DIR)
    memory_path = update_brain_memory(PROJECT_DIR, state)
    packet_path = write_codex_advisor_packet(PROJECT_DIR) if args.codex_packet else None
    out = PROJECT_DIR / "reports" / f"brain_cycle_{date.today().isoformat()}.md"
    out.write_text(_render_report(results, memory_path, packet_path), encoding="utf-8")
    print(out)
    return 0 if all(code == 0 for _, code, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
