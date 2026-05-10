"""Typer-based CLI for housekeeping.

Commands:
    list-mentors         — show registered mentors and their corpus size
    list-playbooks       — list playbooks for a mentor
    show-playbook        — print one playbook to stdout
    list-mentees         — show the mentee allowlist (slugs only)
    add-mentee           — add a mentee; mints + prints a fresh API key
    remove-mentee        — drop a mentee from the allowlist
    rotate-mentee-key    — issue a fresh API key for an existing mentee
    capability           — print /.well-known/agent.json offline
    serve                — start the HTTP MCP server (same as `ammp-server`)
"""

from __future__ import annotations

import json
import secrets

import typer
from rich.console import Console
from rich.table import Table

from .models import Mentee
from .playbooks import load_corpus, safe_id
from .registries import (
    find_mentee_by_api_key,
    get_mentor,
    hash_api_key,
    load_mentees,
    load_mentors,
    save_mentees,
)
from .settings import get_settings

app = typer.Typer(
    name="ammp",
    help="Housekeeping CLI for the AMMP Mentoring-track reference server.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


# ─── Mentors ──────────────────────────────────────────────────────────────


@app.command("list-mentors")
def list_mentors() -> None:
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
    table.add_column("playbook_dir", style="dim")
    for slug, m in mentors.items():
        corpus = load_corpus(m.playbook_dir)
        marker = " (default)" if slug == s.default_mentor else ""
        table.add_row(slug + marker, m.name, str(len(corpus)), f"{m.confidence_threshold:.2f}", str(m.playbook_dir))
    console.print(table)


@app.command("list-playbooks")
def list_playbooks(mentor: str = typer.Option("", help="Mentor slug. Empty → server default.")) -> None:
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


@app.command("show-playbook")
def show_playbook(
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


# ─── Mentees ──────────────────────────────────────────────────────────────


@app.command("list-mentees")
def list_mentees() -> None:
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


@app.command("add-mentee")
def add_mentee(
    slug: str = typer.Argument(..., help="Mentee slug, e.g. 'claude-cowork-sandra'."),
    operator: str = typer.Option(..., help="Operator, e.g. 'human:sandra'."),
    runtime: str = typer.Option(..., help="Runtime, e.g. 'claude-cowork', 'claude-ai', 'claude-code'."),
    rate_limit: int = typer.Option(60, help="Per-minute request budget."),
) -> None:
    """Add a mentee. Mints a fresh API key, prints it ONCE, stores only the hash."""
    s = get_settings()
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    if slug in mentees:
        console.print(f"[red]Mentee {slug!r} already exists. Use rotate-mentee-key to issue a new key.[/red]")
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


@app.command("remove-mentee")
def remove_mentee(slug: str = typer.Argument(..., help="Mentee slug to remove.")) -> None:
    """Remove a mentee from the allowlist."""
    s = get_settings()
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    if slug not in mentees:
        console.print(f"[red]Mentee {slug!r} not found.[/red]")
        raise typer.Exit(code=1)
    del mentees[slug]
    save_mentees(s.mentees_file, mentees)
    console.print(f"[green]Removed mentee {slug!r}.[/green]")


@app.command("rotate-mentee-key")
def rotate_mentee_key(slug: str = typer.Argument(..., help="Mentee slug.")) -> None:
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


@app.command("check-key")
def check_key(api_key: str = typer.Argument(..., help="Plaintext key to test.")) -> None:
    """Resolve a plaintext API key to a mentee — useful for debugging auth."""
    s = get_settings()
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    found = find_mentee_by_api_key(mentees, api_key)
    if not found:
        console.print("[red]No mentee matches that key.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Match: {found.slug} (operator={found.operator}, runtime={found.runtime})[/green]")


# ─── Inspection / serve ───────────────────────────────────────────────────


@app.command("capability")
def capability() -> None:
    """Print the AMMP capability advertisement (offline render)."""
    from . import __ammp_draft__, __version__

    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    summaries = []
    for slug, m in mentors.items():
        corpus = load_corpus(m.playbook_dir)
        summaries.append({"slug": slug, "name": m.name, "playbookCount": len(corpus)})
    payload = {
        "name": "ammp-mcp",
        "version": __version__,
        "url": s.public_url,
        "ammp": {
            "draft": __ammp_draft__,
            "tracks": ["mentoring"],
            "mentors": summaries,
            "privacyPosture": {
                "retention": "no-retention",
                "auditLog": "hash-only",
                "crossCompartmentEscalation": "prohibited",
                "requireAuth": s.require_auth,
            },
            "bindings": ["mcp"],
            "invariants": ["compartmentalisation", "human-gated-escalation"],
        },
        "operations": [
            "ListPlaybooks",
            "GetPlaybook",
            "SearchPlaybooks",
            "AskMentor",
            "EscalateToHuman",
        ],
    }
    console.print_json(json.dumps(payload))


@app.command("serve")
def serve(
    host: str = typer.Option("", help="Override bind host (default from settings)."),
    port: int = typer.Option(0, help="Override bind port (default from settings)."),
) -> None:
    """Start the MCP server. Same as `ammp-server` console script."""
    import logging

    from .server import create_server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    server = create_server(s)
    server.run(transport="http", host=host or s.host, port=port or s.port)


if __name__ == "__main__":
    app()
