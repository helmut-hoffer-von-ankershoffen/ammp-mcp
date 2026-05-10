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
    """Print one playbook body to stdout."""
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
