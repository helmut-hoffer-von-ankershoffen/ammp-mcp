"""`ammp playbook …` and `ammp instruction …` subcommands.

The corpus is a two-level hierarchy — playbooks (areas of practice)
contain skills (markdown files). ``ammp playbook`` operates
on the area level; ``ammp instruction`` operates on individual
instructions.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from .._cli_utils import wire_help_on_no_args
from ..mentor import get_mentor, load_mentors
from ..settings import get_settings
from ._service import load_playbooks, safe_id

console = Console()

playbook_app = typer.Typer(
    name="playbook",
    help="Inspect and read a mentor's playbooks (areas of practice).",
    add_completion=False,
)
wire_help_on_no_args(playbook_app)


instruction_app = typer.Typer(
    name="instruction",
    help="Inspect and read individual skills inside a mentor's playbooks.",
    add_completion=False,
)
wire_help_on_no_args(instruction_app)


@playbook_app.command("list")
def playbook_list(mentor: str = typer.Option("", help="Mentor slug. Empty → server default.")) -> None:
    """List the playbooks (areas of practice) for a mentor."""
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    m = get_mentor(mentors, mentor, s.default_mentor)
    if not m:
        console.print(f"[red]Unknown mentor: {mentor or s.default_mentor!r}[/red]")
        raise typer.Exit(code=2)
    corpus = load_playbooks(m.playbook_dir)
    table = Table(title=f"{m.name} — playbooks ({len(corpus)})")
    table.add_column("id", style="cyan")
    table.add_column("name", style="white")
    table.add_column("description", style="dim")
    table.add_column("instr", justify="right", style="magenta")
    for pb in corpus:
        table.add_row(pb.id, pb.name, pb.description[:80], str(len(pb.instructions)))
    console.print(table)


@playbook_app.command("show")
def playbook_show(
    playbook_id: str = typer.Argument(..., help="Playbook id (directory name)."),
    mentor: str = typer.Option("", help="Mentor slug."),
) -> None:
    """Print one playbook's metadata + its skill list."""
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    m = get_mentor(mentors, mentor, s.default_mentor)
    if not m:
        console.print(f"[red]Unknown mentor: {mentor or s.default_mentor!r}[/red]")
        raise typer.Exit(code=2)
    clean = safe_id(playbook_id)
    if not clean:
        console.print(f"[red]Invalid playbook id: {playbook_id!r}[/red]")
        raise typer.Exit(code=2)
    corpus = load_playbooks(m.playbook_dir)
    pb = next((p for p in corpus if p.id == clean), None)
    if pb is None:
        console.print(f"[red]Not found: playbook id={clean!r} for mentor {m.slug!r}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold]{pb.name}[/bold]  [dim]({pb.id})[/dim]")
    if pb.description:
        console.print(pb.description)
    console.print()
    table = Table(title=f"skills ({len(pb.instructions)})")
    table.add_column("id", style="cyan")
    table.add_column("title", style="white")
    table.add_column("summary", style="dim")
    for wi in pb.instructions:
        table.add_row(wi.id, wi.title, wi.summary[:80])
    console.print(table)


@instruction_app.command("list")
def instruction_list(
    playbook_id: str = typer.Option(..., "--playbook", "-p", help="Playbook id (directory name)."),
    mentor: str = typer.Option("", help="Mentor slug. Empty → server default."),
) -> None:
    """List the skills inside a playbook."""
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    m = get_mentor(mentors, mentor, s.default_mentor)
    if not m:
        console.print(f"[red]Unknown mentor: {mentor or s.default_mentor!r}[/red]")
        raise typer.Exit(code=2)
    clean_pb = safe_id(playbook_id)
    if not clean_pb:
        console.print(f"[red]Invalid playbook id: {playbook_id!r}[/red]")
        raise typer.Exit(code=2)
    corpus = load_playbooks(m.playbook_dir)
    pb = next((p for p in corpus if p.id == clean_pb), None)
    if pb is None:
        console.print(f"[red]Not found: playbook id={clean_pb!r} for mentor {m.slug!r}[/red]")
        raise typer.Exit(code=1)
    table = Table(title=f"{m.name} · {pb.name} — skills ({len(pb.instructions)})")
    table.add_column("id", style="cyan")
    table.add_column("title", style="white")
    table.add_column("summary", style="dim")
    for wi in pb.instructions:
        table.add_row(wi.id, wi.title, wi.summary[:80])
    console.print(table)


@instruction_app.command("show")
def instruction_show(
    instruction_id: str = typer.Argument(..., help="Skill id (folder name or filename stem)."),
    playbook_id: str = typer.Option(..., "--playbook", "-p", help="Playbook id (directory name)."),
    mentor: str = typer.Option("", help="Mentor slug."),
) -> None:
    """Print one skill body to stdout (CLI parity with ``GetSkill``)."""
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    m = get_mentor(mentors, mentor, s.default_mentor)
    if not m:
        console.print(f"[red]Unknown mentor: {mentor or s.default_mentor!r}[/red]")
        raise typer.Exit(code=2)
    clean_pb = safe_id(playbook_id)
    clean_id = safe_id(instruction_id)
    if not clean_pb or not clean_id:
        console.print("[red]Invalid id[/red]")
        raise typer.Exit(code=2)
    target = m.playbook_dir / clean_pb / f"{clean_id}.md"
    if not target.is_file():
        console.print(f"[red]Not found: {target}[/red]")
        raise typer.Exit(code=1)
    console.print(target.read_text(encoding="utf-8"))


@playbook_app.command("search")
def playbook_search(
    query: str = typer.Argument(..., help="Substring to search for across the mentor's work-instruction corpus."),
    mentor: str = typer.Option("", "--mentor", "-m", help="Mentor slug. Empty → server default."),
    limit: int = typer.Option(5, "--limit", "-n", min=1, max=50, help="Maximum number of matches to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON (machine-readable) instead of a Rich table."),
) -> None:
    """Substring-search a mentor's corpus (CLI parity with AMMP ``SearchPlaybooks``).

    Calls the same handler the MCP server uses. Search runs at
    work-instruction granularity; each match names its parent playbook.
    The hash-only audit log records the call.
    """
    import json as _json

    # Local CLI context — auth bypassed (operator already has filesystem access).
    from ..server import _handle_search_playbooks, build_context

    local_settings = get_settings().model_copy(update={"require_auth": False})
    ctx = build_context(local_settings)
    payload = _handle_search_playbooks(ctx, query, mentor, limit, api_key=None)
    if as_json:
        typer.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        if payload.get("error"):
            raise typer.Exit(code=1)
        return
    err = payload.get("error")
    if isinstance(err, str):
        console.print(f"[red]SearchPlaybooks failed: {err}[/red]")
        raise typer.Exit(code=1)
    matches = payload.get("matches", [])
    table = Table(
        title=f"{payload.get('mentor', '?')} — search '{payload.get('query', query)}' ({payload.get('count', 0)} matches)"
    )
    table.add_column("rank", justify="right", style="magenta")
    table.add_column("playbook", style="green")
    table.add_column("id", style="cyan")
    table.add_column("title", style="white")
    table.add_column("snippet", style="dim")
    for m_ in matches:
        table.add_row(
            str(m_["rank"]),
            m_.get("playbook_id", ""),
            m_["id"],
            m_["title"],
            m_["snippet"][:100],
        )
    console.print(table)
