"""Shared CLI helpers — re-used by the root app and every Typer subgroup.

Lives at the package root so each `<domain>/_cli.py` can import from it
without pulling the whole `ammp_mcp.cli` import graph.
"""

from __future__ import annotations

import typer


def wire_help_on_no_args(app: typer.Typer) -> None:
    """Make `<app>` (with no subcommand) print help and exit 0.

    Typer's built-in ``no_args_is_help=True`` exits with code 2 (the
    Click convention for "usage error"). For modern CLIs (`kubectl`,
    `gh`, `helm`, AWS CLI) help is a feature, not an error — bare
    invocation succeeds and prints help. This shim picks the modern
    convention.

    Call once per Typer app after the app is constructed and before
    any commands or sub-apps are attached.
    """

    @app.callback(invoke_without_command=True)
    def _root(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            typer.echo(ctx.get_help())
            raise typer.Exit(0)
