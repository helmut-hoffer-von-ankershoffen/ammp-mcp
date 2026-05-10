"""System module — install-wide commands (setup, status, health, usage, capability, serve).

Package-private services live behind the `_cli.py` Typer surface. The
public CLI surface is `system_app` and the individual command callables
(re-exported by `ammp_mcp.cli` for top-level aliases).
"""

from __future__ import annotations
