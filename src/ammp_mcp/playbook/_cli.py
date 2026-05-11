"""`ammp playbook …` subcommands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..mentor import get_mentor, load_mentors
from ..settings import get_settings
from ._service import load_corpus, safe_id

console = Console()

playbook_app = typer.Typer(
    name="playbook",
    help="Inspect and read the playbook corpus of a mentor.",
    no_args_is_help=True,
    add_completion=False,
)


@playbook_app.command("list")
def playbook_list(mentor: str = typer.Option("", help="Mentor slug. Empty → server default.")) -> None:
    """List the playbook corpus for a mentor."""
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    m = get_mentor(mentors, mentor, s.default_mentor)
    if not m:
        console.print(f"[red]Unknown mentor: {mentor or s.default_mentor!r}[/red]")
        raise typer.Exit(code=2)
    corpus = load_corpus(m.playbook_dir)
    table = Table(title=f"{m.name} — playbooks ({len(corpus)})")
    table.add_column("id", style="cyan")
    table.add_column("title", style="white")
    table.add_column("summary", style="dim")
    for pb in corpus:
        table.add_row(pb.id, pb.title, pb.summary[:80])
    console.print(table)


@playbook_app.command("show")
def playbook_show(
    playbook_id: str = typer.Argument(..., help="Playbook id (filename stem)."),
    mentor: str = typer.Option("", help="Mentor slug."),
) -> None:
    """Print one playbook body to stdout (CLI parity with AMMP ``GetPlaybook``)."""
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
    target = m.playbook_dir / f"{clean}.md"
    if not target.is_file():
        console.print(f"[red]Not found: {target}[/red]")
        raise typer.Exit(code=1)
    console.print(target.read_text(encoding="utf-8"))


@playbook_app.command("search")
def playbook_search(
    query: str = typer.Argument(..., help="Substring to search for across the mentor's playbook corpus."),
    mentor: str = typer.Option("", "--mentor", "-m", help="Mentor slug. Empty → server default."),
    limit: int = typer.Option(5, "--limit", "-n", min=1, max=50, help="Maximum number of matches to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON (machine-readable) instead of a Rich table."),
) -> None:
    """Substring-search a mentor's playbook corpus (CLI parity with AMMP ``SearchPlaybooks``).

    Calls the same handler the MCP server uses. Returns ranked matches with
    surrounding snippet context. The hash-only audit log records the call.
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
    table.add_column("id", style="cyan")
    table.add_column("title", style="white")
    table.add_column("snippet", style="dim")
    for m_ in matches:
        table.add_row(str(m_["rank"]), m_["id"], m_["title"], m_["snippet"][:100])
    console.print(table)
