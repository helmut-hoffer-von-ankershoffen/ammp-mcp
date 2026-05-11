"""`ammp system …` subcommands — install-wide operations.

`setup / status / health / usage / capability / serve`. The individual
command callables are also re-exported through `ammp_mcp.cli` so they
can be wired as top-level aliases (`ammp status`, `ammp serve`, etc.).
"""

from __future__ import annotations

import datetime as dt
import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .._cli_utils import wire_help_on_no_args
from ..mentor import load_mentors
from ..settings import get_settings
from ._capability_service import build_offline_capability
from ._health_service import _health_probe_capability, _health_probe_one_backend
from ._setup_service import (
    _setup_apply_backend,
    _setup_compose_env,
    _setup_load_existing_env,
    _setup_mint_mentee_if_needed,
    _setup_print_header,
    _setup_print_next_steps,
    _setup_resolve_openclaw_args,
    _setup_validate,
    _setup_write_env,
)
from ._status_service import (
    _status_check_anthropic_key,
    _status_check_audit_log,
    _status_check_mentees,
    _status_check_mentors,
    _StatusReporter,
)
from ._usage_service import (
    _usage_parse_audit,
    _usage_render_breakdown,
)

console = Console()

_ICON_OK = "[green]✓[/green]"

system_app = typer.Typer(
    name="system",
    help="Operate the install as a whole (setup, status, health, usage, capability, serve).",
    add_completion=False,
)
wire_help_on_no_args(system_app)


# ─── ammp system capability ──────────────────────────────────────────────


@system_app.command("capability")
def capability() -> None:
    """Print the AMMP capability advertisement (offline render)."""
    from .. import __ammp_draft__, __version__

    s = get_settings()
    payload = build_offline_capability(s, __version__, __ammp_draft__)
    console.print_json(json.dumps(payload))


# ─── ammp system serve ───────────────────────────────────────────────────


@system_app.command("serve")
def serve(
    stdio: bool = typer.Option(
        False,
        "--stdio",
        help=(
            "Speak MCP JSON-RPC over stdin/stdout (subprocess transport). "
            "Wins over --host/--port and AMMP_TRANSPORT=http when set. "
            "Use this for Claude Desktop / Claude Code subprocess MCP integration."
        ),
    ),
    host: str = typer.Option("", help="Override bind host (HTTP transport only)."),
    port: int = typer.Option(0, help="Override bind port (HTTP transport only)."),
) -> None:
    """Start the MCP server.

    Auto-bootstraps `~/.ammp/` (creates the dir, copies the shipped
    example mentor, writes a `config.env` scaffold) on first run so a
    fresh install can `ammp serve` immediately. Re-runs are no-ops.

    Default transport is HTTP (Streamable-HTTP at `/mcp/`). Use ``--stdio`` for
    subprocess transport (Claude Desktop, Claude Code stdio integrations). The
    transport can also be set via ``AMMP_TRANSPORT={http,stdio}``; the
    ``--stdio`` flag wins when both are present.
    """
    import logging

    from ..server import create_server
    from ..settings import reset_settings_for_testing
    from ._setup_service import bootstrap_ammp_dir

    s = get_settings()
    # Auto-bootstrap is quiet by default so the boot log stays clean; if it
    # had to do work, reset the singleton so the freshly written
    # config.env is picked up on the next get_settings() call.
    if bootstrap_ammp_dir(s, quiet=stdio):
        reset_settings_for_testing()
        s = get_settings()
    transport = "stdio" if stdio else s.transport
    if transport == "stdio":
        # Stdio MCP frames go on stdin/stdout; log to stderr so the wire stays clean.
        import sys

        logging.basicConfig(
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )
        server = create_server(s)
        server.run(transport="stdio")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = create_server(s)
    server.run(transport="http", host=host or s.host, port=port or s.port)


# ─── ammp system setup ───────────────────────────────────────────────────


@system_app.command("setup")
def setup(
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept all defaults; no prompts."),
    mentor_slug: str = typer.Option(
        "example",
        help="Slug of the default mentor to configure (matches a directory under mentors/).",
    ),
    backend: str = typer.Option(
        "openclaw",
        help="Backend kind for the mentor: 'openclaw' (live agent runtime), 'anthropic' (stateless), or 'stub'.",
    ),
    openclaw_url: str = typer.Option(
        "https://openclaw.helmguild.local/ammp/ask",
        help="OpenClaw webhook URL (when backend=openclaw).",
    ),
    auth_bearer_env: str = typer.Option(
        "OPENCLAW_BEARER",
        help="Env var name holding the Bearer token sent to the OpenClaw webhook.",
    ),
    require_auth: bool = typer.Option(True, help="Enforce Bearer-key auth on incoming MCP calls."),
    mint_first_mentee: bool = typer.Option(
        True,
        help="Mint a first mentee (claude-cowork-helmut) so you can hit the server immediately.",
    ),
) -> None:
    """First-run installation wizard.

    Configures the chosen mentor's backend block, optionally mints a first
    mentee, writes a .env scaffold with the required env vars, and prints
    the next-step commands. Idempotent.
    """
    from ..settings import reset_settings_for_testing
    from ._setup_service import bootstrap_ammp_dir

    s = get_settings()
    bootstrap_ammp_dir(s)
    # If bootstrap wrote a fresh config.env, re-read settings so the
    # wizard works against the new tree from this point on.
    reset_settings_for_testing()
    s = get_settings()
    _setup_print_header(s)
    _setup_validate(s.mentors_root, mentor_slug, backend)
    if backend == "openclaw":
        openclaw_url, auth_bearer_env = _setup_resolve_openclaw_args(yes, openclaw_url, auth_bearer_env)
    _setup_apply_backend(s.mentors_root / mentor_slug / "mentor.json", backend, openclaw_url, auth_bearer_env)
    console.print(f"{_ICON_OK} Updated [bold]{mentor_slug}/mentor.json[/bold] → backend = [cyan]{backend}[/cyan]")

    api_key = _setup_mint_mentee_if_needed(s.mentees_file) if mint_first_mentee else None

    config_env = s.ammp_dir / "config.env"
    existing = _setup_load_existing_env(config_env)
    merged = _setup_compose_env(s, backend, auth_bearer_env, require_auth, existing)
    _setup_write_env(config_env, merged, auth_bearer_env)
    _setup_print_next_steps(backend, auth_bearer_env)

    if api_key:
        console.print()
        console.print("[bold yellow]API KEY for the first mentee — copy now, you will not see it again:[/bold yellow]")
        console.print(f"  {api_key}")


# ─── ammp system status ──────────────────────────────────────────────────


def _status_emit_summary(r: _StatusReporter) -> None:
    if r.problems:
        console.print()
        console.print(f"[bold red]{len(r.problems)} problem(s) — installation is broken:[/bold red]")
        for p in r.problems:
            console.print(f"  • {p}")
        raise typer.Exit(code=1)
    if r.notes:
        console.print()
        console.print(f"[yellow]{len(r.notes)} warning(s) — non-fatal but worth checking:[/yellow]")
        for n in r.notes:
            console.print(f"  • {n}")
        console.print()
        console.print("[green]Status: OK with warnings.[/green]")
    else:
        console.print()
        console.print("[bold green]Status: OK.[/bold green]")


@system_app.command("status")
def status() -> None:
    """Validate the current installation. Exits non-zero if anything is broken."""
    s = get_settings()
    table = Table(title="ammp-mcp installation status", show_lines=False)
    table.add_column("check", style="white", no_wrap=True)
    table.add_column("result", justify="left")
    table.add_column("detail", style="dim")
    r = _StatusReporter(table=table, problems=[], notes=[])

    r.ok(
        "Settings loaded",
        f"public_url={s.public_url}, host={s.host}, port={s.port}, require_auth={s.require_auth}",
    )
    mentors = _status_check_mentors(s.mentors_root, r)
    _status_check_mentees(s.mentees_file, r)
    _status_check_audit_log(s.audit_log_path, r)
    _status_check_anthropic_key(s, mentors, r)

    console.print(table)
    _status_emit_summary(r)


# ─── ammp system usage ───────────────────────────────────────────────────


@system_app.command("usage")
def usage(
    days: int = typer.Option(7, help="Aggregation window — last N days. 0 = all-time."),
    by_mentee: bool = typer.Option(True, help="Show per-mentee breakdown."),
    by_mentor: bool = typer.Option(True, help="Show per-mentor breakdown."),
) -> None:
    """Aggregate usage statistics from the hash-only audit log.

    The audit log records *only* operation + mentor + mentee + opaque
    request hash — never any payload — so this view tells you who used
    what without leaking anything.
    """
    s = get_settings()
    if not s.audit_log_path.exists():
        console.print(f"[yellow]Audit log does not exist yet: {s.audit_log_path}[/yellow]")
        raise typer.Exit(code=0)

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days) if days > 0 else None
    agg = _usage_parse_audit(s.audit_log_path, cutoff)

    window = f"last {days}d" if days > 0 else "all time"
    if not agg.parsed:
        console.print(f"[yellow]No audit entries in window ({window}).[/yellow]")
        raise typer.Exit(code=0)

    range_line = ""
    if agg.earliest:
        latest_str = agg.latest.isoformat(timespec="seconds") if agg.latest else "—"
        range_line = f"\nrange: {agg.earliest.isoformat(timespec='seconds')} → {latest_str}"
    skipped_note = f" (skipped {agg.skipped} unparsable)" if agg.skipped else ""
    console.print(
        Panel.fit(
            f"[bold]ammp-mcp usage[/bold] · window: [cyan]{window}[/cyan]\n"
            f"entries: [bold]{agg.parsed}[/bold]{skipped_note}{range_line}",
            border_style="cyan",
        )
    )

    console.print(_usage_render_breakdown("By operation", agg.op_counts, "operation", "cyan"))
    if by_mentor:
        console.print(_usage_render_breakdown("By mentor", agg.mentor_counts, "mentor", "green"))
    if by_mentee:
        console.print(_usage_render_breakdown("By mentee", agg.mentee_counts, "mentee", "magenta"))


# ─── ammp system health ──────────────────────────────────────────────────


@system_app.command("health")
def health(
    url: str = typer.Option("", help="Override the URL to probe. Default: configured public_url, fallback localhost."),
    timeout: float = typer.Option(5.0, help="Per-probe timeout in seconds."),
    probe_backends: bool = typer.Option(True, help="Probe each mentor's openclaw webhook URL too."),
) -> None:
    """Active runtime probe.

    `status` validates the install statically (paths, files, env vars).
    `health` validates that the running thing actually answers — it
    GETs `/.well-known/agent.json` from the server and (optionally)
    HEADs each openclaw mentor backend's webhook URL.
    """
    s = get_settings()
    # `s.public_url` always has a value (settings default is the loopback
    # URL); dropping the per-call f-string fallback removes a duplicate
    # `http://` literal that SonarCloud flagged as a security hotspot.
    target = (url or s.public_url).rstrip("/")
    agent_url = f"{target}/.well-known/agent.json"

    table = Table(title=f"ammp-mcp runtime health · target={target}", show_lines=False)
    table.add_column("probe", style="white", no_wrap=True)
    table.add_column("result", justify="left")
    table.add_column("detail", style="dim")
    problems: list[str] = []

    _health_probe_capability(agent_url, timeout, table, problems)
    if probe_backends:
        for slug, m in load_mentors(s.mentors_root).items():
            _health_probe_one_backend(slug, m, timeout, table, problems)

    console.print(table)
    console.print()
    if problems:
        console.print(f"[bold red]{len(problems)} health problem(s):[/bold red]")
        for p in problems:
            console.print(f"  • {p}")
        raise typer.Exit(code=1)
    console.print("[bold green]Health: OK.[/bold green]")
