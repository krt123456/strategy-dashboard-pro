#!/usr/bin/env python3
"""Create or optionally send a Strategy Dashboard review packet to Codex.

By default this only writes a local packet. Running Codex is intentionally
opt-in because language output is advisory, not the final match decision gate.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.brain_ops import build_brain_state, render_codex_advisor_packet, write_codex_advisor_packet  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-codex", action="store_true", help="Send the packet to a configured Codex command.")
    args = ap.parse_args()

    packet_path = write_codex_advisor_packet(PROJECT_DIR)
    print(packet_path)

    if not args.run_codex:
        return 0
    if os.environ.get("STRATEGY_DASHBOARD_ENABLE_CODEX") != "1":
        raise SystemExit("Refusing to run Codex unless STRATEGY_DASHBOARD_ENABLE_CODEX=1 is set.")
    cmd_text = os.environ.get("STRATEGY_DASHBOARD_CODEX_CMD")
    if not cmd_text:
        raise SystemExit("Set STRATEGY_DASHBOARD_CODEX_CMD to the exact Codex command you want to run.")
    packet = render_codex_advisor_packet(PROJECT_DIR, build_brain_state(PROJECT_DIR))
    proc = subprocess.run(shlex.split(cmd_text), input=packet, text=True, cwd=PROJECT_DIR, check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
