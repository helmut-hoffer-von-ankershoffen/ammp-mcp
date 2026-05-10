#!/usr/bin/env python3
"""Regenerate ``docs/CLI_REFERENCE.md`` from the live Typer app.

Usage:
    python scripts/generate_cli_reference.py

Wraps ``typer ammp_mcp.cli utils docs --name ammp`` so the regen path is
single-step and discoverable. The committed file should stay in sync with
the CLI surface — re-run this whenever you add/remove/rename a Typer
command or change a help string.

Mirrors the doc-generation step in ``Aignostics/python-sdk``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "docs" / "CLI_REFERENCE.md"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "typer",
        "ammp_mcp.cli",
        "utils",
        "docs",
        "--name",
        "ammp",
        "--output",
        str(OUTPUT),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=REPO_ROOT)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        sys.exit(proc.returncode)
    print(f"Wrote {OUTPUT}.")


if __name__ == "__main__":
    main()
