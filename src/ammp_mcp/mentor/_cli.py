"""`ammp mentor …` subcommands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..playbook import load_corpus
from ..settings import get_settings
from ._service import load_mentors

console = Console()

mentor_app = typer.Typer(
    name="mentor",
    help="Inspect registered mentors.",
    no_args_is_help=True,
    add_completion=False,
)


@mentor_app.command("list")
def mentor_list() -> None:
    """List registered mentors and their corpus sizes."""
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    if not mentors:
        console.print(f"[yellow]No mentors found under {s.mentors_root}[/yellow]")
        raise typer.Exit(code=0)
    table = Table(title=f"Mentors ({len(mentors)})", show_lines=False)
    table.add_column("slug", style="cyan")
    table.add_column("name", style="white")
    table.add_column("playbooks", justify="right", style="green")
    table.add_column("threshold", justify="right", style="magenta")
    table.add_column("backend", style="white")
    table.add_column("playbook_dir", style="dim")
    for slug, m in mentors.items():
        corpus = load_corpus(m.playbook_dir)
        marker = " (default)" if slug == s.default_mentor else ""
        backend = m.backend.kind if m.backend else "fallback"
        table.add_row(
            slug + marker,
            m.name,
            str(len(corpus)),
            f"{m.confidence_threshold:.2f}",
            backend,
            str(m.playbook_dir),
        )
    console.print(table)
