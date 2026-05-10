"""`ammp mentee …` subcommands."""

from __future__ import annotations

import secrets

import typer
from rich.console import Console
from rich.table import Table

from ..settings import get_settings
from ._models import Mentee
from ._service import (
    find_mentee_by_api_key,
    hash_api_key,
    load_mentees,
    save_mentees,
)

console = Console()

mentee_app = typer.Typer(
    name="mentee",
    help="Manage the mentee allowlist (add / remove / rotate-key / list / check-key).",
    no_args_is_help=True,
    add_completion=False,
)


@mentee_app.command("list")
def mentee_list() -> None:
    """Show the mentee allowlist. Plaintext keys are NEVER shown — only hashes."""
    s = get_settings()
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    if not mentees:
        console.print(f"[yellow]No mentees in {s.mentees_file}[/yellow]")
        raise typer.Exit(code=0)
    table = Table(title=f"Mentees ({len(mentees)})", show_lines=False)
    table.add_column("slug", style="cyan")
    table.add_column("operator", style="white")
    table.add_column("runtime", style="green")
    table.add_column("rate/min", justify="right")
    table.add_column("api_key_hash (first 12)", style="dim")
    for slug, m in mentees.items():
        table.add_row(slug, m.operator, m.runtime, str(m.rate_limit_per_minute), m.api_key_hash[:12] + "…")
    console.print(table)


@mentee_app.command("add")
def mentee_add(
    slug: str = typer.Argument(..., help="Mentee slug, e.g. 'claude-cowork-sandra'."),
    operator: str = typer.Option(..., help="Operator, e.g. 'human:sandra'."),
    runtime: str = typer.Option(..., help="Runtime, e.g. 'claude-cowork', 'claude-ai', 'claude-code'."),
    rate_limit: int = typer.Option(60, help="Per-minute request budget."),
) -> None:
    """Add a mentee. Mints a fresh API key, prints it ONCE, stores only the hash."""
    s = get_settings()
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    if slug in mentees:
        console.print(f"[red]Mentee {slug!r} already exists. Use `ammp mentee rotate-key` to issue a new key.[/red]")
        raise typer.Exit(code=2)
    api_key = "ammp-" + secrets.token_urlsafe(32)
    mentees[slug] = Mentee(
        slug=slug,
        operator=operator,
        runtime=runtime,
        api_key_hash=hash_api_key(api_key),
        rate_limit_per_minute=rate_limit,
    )
    save_mentees(s.mentees_file, mentees)
    console.print(f"[green]Added mentee {slug!r}.[/green]")
    console.print()
    console.print("[bold yellow]API KEY — copy now, you will not see it again:[/bold yellow]")
    console.print(f"  {api_key}")
    console.print()
    console.print(f"[dim]Stored hash: {hash_api_key(api_key)[:12]}… in {s.mentees_file}[/dim]")


@mentee_app.command("remove")
def mentee_remove(slug: str = typer.Argument(..., help="Mentee slug to remove.")) -> None:
    """Remove a mentee from the allowlist."""
    s = get_settings()
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    if slug not in mentees:
        console.print(f"[red]Mentee {slug!r} not found.[/red]")
        raise typer.Exit(code=1)
    del mentees[slug]
    save_mentees(s.mentees_file, mentees)
    console.print(f"[green]Removed mentee {slug!r}.[/green]")


@mentee_app.command("rotate-key")
def mentee_rotate_key(slug: str = typer.Argument(..., help="Mentee slug.")) -> None:
    """Issue a fresh API key, replace the stored hash. Prints the new key once."""
    s = get_settings()
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    if slug not in mentees:
        console.print(f"[red]Mentee {slug!r} not found.[/red]")
        raise typer.Exit(code=1)
    api_key = "ammp-" + secrets.token_urlsafe(32)
    existing = mentees[slug]
    mentees[slug] = existing.model_copy(update={"api_key_hash": hash_api_key(api_key)})
    save_mentees(s.mentees_file, mentees)
    console.print(f"[green]Rotated key for {slug!r}.[/green]")
    console.print()
    console.print("[bold yellow]NEW API KEY — copy now, you will not see it again:[/bold yellow]")
    console.print(f"  {api_key}")


@mentee_app.command("check-key")
def mentee_check_key(api_key: str = typer.Argument(..., help="Plaintext key to test.")) -> None:
    """Resolve a plaintext API key to a mentee — useful for debugging auth."""
    s = get_settings()
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    found = find_mentee_by_api_key(mentees, api_key)
    if not found:
        console.print("[red]No mentee matches that key.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Match: {found.slug} (operator={found.operator}, runtime={found.runtime})[/green]")
