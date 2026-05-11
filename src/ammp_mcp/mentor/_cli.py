"""`ammp mentor …` subcommands.

CLI parity with the MCP protocol: the same operations a mentee agent can
invoke over the AMMP wire are also available from the shell here, so an
operator (or a bash-plus-CLI agent) can exercise the full Mentoring track
without speaking MCP.

  ammp mentor list                                — equivalent to listing mentors
  ammp mentor ask <question> [--mentor SLUG]      — AMMP `AskMentor`
  ammp mentor escalate <situation> [--mentor SLUG] — AMMP `EscalateToHuman`

`SearchPlaybooks` lives under the `playbook` subject (`ammp playbook search`);
`ListPlaybooks` / `GetPlaybook` likewise (`ammp playbook list` / `show`).
"""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .._cli_utils import wire_help_on_no_args
from ..playbook import load_playbooks
from ..server import ServerContext
from ..settings import get_settings
from ._service import load_mentors

console = Console()

mentor_app = typer.Typer(
    name="mentor",
    help="Inspect registered mentors; ask one a question; escalate to your operator.",
    add_completion=False,
)
wire_help_on_no_args(mentor_app)


def _local_context() -> ServerContext:
    """Build a :class:`ServerContext` with auth disabled for local CLI use.

    The CLI runs as the operator (filesystem access to mentees.json), so
    Bearer-key auth adds no value here.
    """
    from ..server import build_context

    local_settings = get_settings().model_copy(update={"require_auth": False})
    return build_context(local_settings)


def _render_response(payload: dict[str, object], *, as_json: bool, fallback_label: str) -> None:
    """Print a handler response — JSON when --json is set, Rich panel otherwise.

    In-band errors (the protocol's `{"error": "..."}` shape) are surfaced
    with exit code 1 so the CLI is shell-pipeable. Successful results print
    a Rich panel summarising the key fields plus the full JSON below.
    """
    if as_json:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        if payload.get("error"):
            raise typer.Exit(code=1)
        return
    err = payload.get("error")
    if isinstance(err, str):
        console.print(f"[red]{fallback_label} failed: {err}[/red]")
        detail = payload.get("detail")
        if isinstance(detail, str):
            console.print(f"[dim]{detail}[/dim]")
        raise typer.Exit(code=1)
    console.print(
        Panel.fit(json.dumps(payload, indent=2, ensure_ascii=False), title=fallback_label, border_style="cyan")
    )


@mentor_app.command("list")
def mentor_list(
    as_json: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit the same JSON envelope an MCP `ListMentors` call returns. "
            "Useful for scripting or for agents that prefer Bash+CLI to MCP."
        ),
    ),
) -> None:
    """List registered mentors and their corpus sizes.

    CLI parity with the MCP ``ListMentors`` extension. ``--json`` returns
    the wire envelope; default rendering is a Rich table with the
    additional ``playbook_dir`` column (operator-facing, not part of the
    wire shape).
    """
    s = get_settings()
    if as_json:
        from ..server import _handle_list_mentors, build_context

        local_settings = s.model_copy(update={"require_auth": False})
        ctx = build_context(local_settings)
        payload = _handle_list_mentors(ctx, api_key=None)
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    mentors = load_mentors(s.mentors_root)
    if not mentors:
        console.print(f"[yellow]No mentors found under {s.mentors_root}[/yellow]")
        raise typer.Exit(code=0)
    table = Table(title=f"Mentors ({len(mentors)})", show_lines=False)
    table.add_column("slug", style="cyan")
    table.add_column("name", style="white")
    table.add_column("playbooks", justify="right", style="green")
    table.add_column("instr.", justify="right", style="green")
    table.add_column("threshold", justify="right", style="magenta")
    table.add_column("backend", style="white")
    table.add_column("playbook_dir", style="dim")
    for slug, m in mentors.items():
        corpus = load_playbooks(m.playbook_dir)
        instruction_count = sum(len(pb.instructions) for pb in corpus)
        marker = " (default)" if slug == s.default_mentor else ""
        backend = m.backend.kind if m.backend else "fallback"
        table.add_row(
            slug + marker,
            m.name,
            str(len(corpus)),
            str(instruction_count),
            f"{m.confidence_threshold:.2f}",
            backend,
            str(m.playbook_dir),
        )
    console.print(table)


@mentor_app.command("ask")
def mentor_ask(
    question: str = typer.Argument(..., help="The free-form question to put to the mentor."),
    mentor: str = typer.Option("", "--mentor", "-m", help="Mentor slug. Empty → server default."),
    context: str = typer.Option("", "--context", "-c", help="Additional context to attach to the question."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON (machine-readable) instead of a Rich panel."),
) -> None:
    """Ask a mentor a free-form question (CLI parity with AMMP ``AskMentor``).

    Calls the same handler the MCP server uses. The mentor's backend
    synthesises an answer + confidence; when confidence falls below the
    mentor's threshold, the response also recommends ``EscalateToHuman``
    with suggested phrasing.
    """
    from ..server import _handle_ask_mentor

    ctx = _local_context()
    payload = asyncio.run(_handle_ask_mentor(ctx, question, mentor, context, api_key=None))
    _render_response(payload, as_json=as_json, fallback_label="AskMentor")


@mentor_app.command("escalate")
def mentor_escalate(
    situation: str = typer.Argument(..., help="One-paragraph description of the situation that needs the operator."),
    mentor: str = typer.Option("", "--mentor", "-m", help="Mentor slug. Empty → server default."),
    why_stuck: str = typer.Option("", "--why-stuck", help="Optional note on what's making you uncertain."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON (machine-readable) instead of a Rich panel."),
) -> None:
    """Request escalation phrasing to hand to your operator (AMMP ``EscalateToHuman``).

    Returns suggested phrasing the mentee hands to its own operator. The
    mentor does not page or message anyone — that's the Human-Gated
    Escalation Invariant (AMMP §3.4).
    """
    from ..server import _handle_escalate_to_human

    ctx = _local_context()
    payload = _handle_escalate_to_human(ctx, situation, mentor, why_stuck, api_key=None)
    _render_response(payload, as_json=as_json, fallback_label="EscalateToHuman")
