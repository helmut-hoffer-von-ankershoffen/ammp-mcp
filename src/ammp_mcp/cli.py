"""Typer-based CLI for housekeeping.

Commands:
    setup                — interactive first-run wizard (mentor backend +
                           first mentee + .env scaffold)
    status               — validate the current installation: mentor.json
                           parses, playbook dirs exist, mentees allow-list
                           is well-formed, env vars set, OpenClaw webhook
                           reachable, audit log writable
    usage                — aggregate the audit log (per-operation,
                           per-mentor, per-mentee, last-N-days)
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

import datetime as dt
import json
import os
import re
import secrets
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
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


# ─── Setup wizard ─────────────────────────────────────────────────────────


@app.command("setup")
def setup(
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept all defaults; no prompts."),
    mentor_slug: str = typer.Option(
        "pepe",
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
    the next-step commands. Idempotent — re-running updates the same
    files without duplicating mentees.
    """
    s = get_settings()
    repo_root = Path.cwd()
    console.print(
        Panel.fit(
            f"[bold]ammp-mcp setup[/bold]\n"
            f"[dim]repo:[/dim] {repo_root}\n"
            f"[dim]mentors_root:[/dim] {s.mentors_root}\n"
            f"[dim]mentees_file:[/dim] {s.mentees_file}\n"
            f"[dim]audit_log:[/dim] {s.audit_log_path}",
            title="Step 0 — environment",
            border_style="cyan",
        )
    )

    # Step 1 — verify the mentor exists and patch its backend block.
    mentors = load_mentors(s.mentors_root)
    if mentor_slug not in mentors:
        console.print(
            f"[red]No mentor directory found at {s.mentors_root / mentor_slug}.[/red]\n"
            "Create one with `mentor.json` + `playbooks/*.md` first, then re-run setup."
        )
        raise typer.Exit(code=2)

    if backend not in {"openclaw", "anthropic", "stub"}:
        console.print(f"[red]Unknown backend kind: {backend!r}. Choose one of: openclaw, anthropic, stub.[/red]")
        raise typer.Exit(code=2)

    if backend == "openclaw" and not yes:
        openclaw_url = Prompt.ask(
            "OpenClaw webhook URL",
            default=openclaw_url,
            console=console,
        )
        auth_bearer_env = Prompt.ask(
            "Env var holding the OpenClaw Bearer token",
            default=auth_bearer_env,
            console=console,
        )

    backend_block: dict[str, object] = {"kind": backend}
    if backend == "openclaw":
        backend_block.update(
            {
                "url": openclaw_url,
                "auth_bearer_env": auth_bearer_env,
                "timeout_seconds": 60,
                "max_concurrent": 10,
            }
        )

    mentor_json_path = s.mentors_root / mentor_slug / "mentor.json"
    raw = json.loads(mentor_json_path.read_text(encoding="utf-8"))
    # Strip helper underscore keys we leave in for documentation; the new
    # block becomes authoritative.
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    raw["backend"] = backend_block
    mentor_json_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    console.print(f"[green]✓[/green] Updated [bold]{mentor_slug}/mentor.json[/bold] → backend = [cyan]{backend}[/cyan]")

    # Step 2 — mint a first mentee (idempotent on slug).
    api_key: str | None = None
    if mint_first_mentee:
        mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
        first_slug = "claude-cowork-helmut"
        if first_slug not in mentees:
            api_key = "ammp-" + secrets.token_urlsafe(32)
            mentees[first_slug] = Mentee(
                slug=first_slug,
                operator="human:helmut",
                runtime="claude-cowork",
                api_key_hash=hash_api_key(api_key),
                rate_limit_per_minute=60,
            )
            save_mentees(s.mentees_file, mentees)
            console.print(f"[green]✓[/green] Minted first mentee [bold]{first_slug}[/bold].")
        else:
            console.print(f"[yellow]·[/yellow] Mentee [bold]{first_slug}[/bold] already exists — skipped.")

    # Step 3 — write a .env scaffold (preserves existing values).
    env_path = repo_root / ".env"
    existing_env: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing_env[k.strip()] = v.strip()

    desired = {
        "AMMP_REQUIRE_AUTH": "true" if require_auth else "false",
        "AMMP_MENTORS_ROOT": str(s.mentors_root),
        "AMMP_MENTEES_FILE": str(s.mentees_file),
        "AMMP_AUDIT_LOG_PATH": str(s.audit_log_path),
    }
    if backend == "anthropic":
        desired.setdefault("AMMP_ANTHROPIC_API_KEY", existing_env.get("AMMP_ANTHROPIC_API_KEY", ""))
    if backend == "openclaw":
        desired.setdefault(auth_bearer_env, existing_env.get(auth_bearer_env, ""))

    merged = {**desired, **existing_env}  # existing wins, but desired keys are guaranteed present
    lines = [
        "# ammp-mcp environment — written by `ammp setup`. Edit freely.",
        f"# Generated {dt.datetime.now(dt.UTC).isoformat()}.",
        "",
    ]
    for k in sorted(merged):
        v = merged[k]
        comment = ""
        if k == auth_bearer_env and not v:
            comment = "  # ← fill in the Bearer token before booting"
        if k == "AMMP_ANTHROPIC_API_KEY" and not v:
            comment = "  # ← fill in if you choose the anthropic backend"
        lines.append(f"{k}={v}{comment}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]✓[/green] Wrote [bold].env[/bold] scaffold ({len(merged)} keys).")

    # Step 4 — print next-steps.
    console.print()
    console.print(
        Panel.fit(
            "[bold]Next steps[/bold]\n\n"
            f"  1. Edit [cyan].env[/cyan] and fill in the {auth_bearer_env if backend == 'openclaw' else 'AMMP_ANTHROPIC_API_KEY'} value.\n"
            "  2. [cyan]uv run ammp status[/cyan] — verify the install is healthy.\n"
            "  3. [cyan]uv run ammp serve[/cyan] — boot the MCP server.\n"
            "  4. [cyan]curl http://127.0.0.1:8765/.well-known/agent.json[/cyan] — confirm the capability advertisement.",
            title="Setup complete",
            border_style="green",
        )
    )

    if api_key:
        console.print()
        console.print("[bold yellow]API KEY for the first mentee — copy now, you will not see it again:[/bold yellow]")
        console.print(f"  {api_key}")


# ─── Status / health-check ────────────────────────────────────────────────


@app.command("status")
def status() -> None:
    """Validate the current installation. Exits non-zero if anything is broken.

    Checks: settings load, mentor.json files parse, playbook dirs exist,
    mentees allowlist is well-formed, audit log is writable, env vars
    referenced by OpenClaw backends are set, and the configured public
    URL is well-formed.
    """
    s = get_settings()
    problems: list[str] = []
    notes: list[str] = []

    table = Table(title="ammp-mcp installation status", show_lines=False)
    table.add_column("check", style="white", no_wrap=True)
    table.add_column("result", justify="left")
    table.add_column("detail", style="dim")

    def ok(name: str, detail: str = "") -> None:
        table.add_row(name, "[green]✓[/green]", detail)

    def warn(name: str, detail: str) -> None:
        table.add_row(name, "[yellow]![/yellow]", detail)
        notes.append(f"{name}: {detail}")

    def fail(name: str, detail: str) -> None:
        table.add_row(name, "[red]✗[/red]", detail)
        problems.append(f"{name}: {detail}")

    # Settings
    ok("Settings loaded", f"public_url={s.public_url}, host={s.host}, port={s.port}, require_auth={s.require_auth}")

    # Mentors
    if not s.mentors_root.is_dir():
        fail("Mentors root", f"directory does not exist: {s.mentors_root}")
    else:
        try:
            mentors = load_mentors(s.mentors_root)
        except Exception as e:
            fail("Mentor registry", f"failed to load: {e}")
            mentors = {}
        if not mentors:
            warn("Mentor registry", f"no mentors under {s.mentors_root}")
        for slug, m in mentors.items():
            corpus = load_corpus(m.playbook_dir)
            if not corpus:
                warn(f"  mentor {slug}", f"playbook_dir has no *.md: {m.playbook_dir}")
            else:
                ok(
                    f"  mentor {slug}",
                    f"{len(corpus)} playbook(s), backend={m.backend.kind if m.backend else 'fallback'}",
                )
            if m.backend and m.backend.kind == "openclaw":
                env_name = m.backend.auth_bearer_env
                if env_name and not os.environ.get(env_name):
                    warn(f"    {slug}.backend env", f"{env_name} is not set in the environment")
                if not re.match(r"^https?://", m.backend.url):
                    fail(f"    {slug}.backend url", f"not http(s): {m.backend.url}")

    # Mentees
    if not s.mentees_file.exists():
        warn("Mentees file", f"does not exist yet: {s.mentees_file} (run `ammp add-mentee` to mint one)")
    else:
        try:
            mentees = load_mentees(s.mentees_file)
            ok("Mentees allowlist", f"{len(mentees)} mentee(s)")
        except Exception as e:
            fail("Mentees allowlist", f"failed to load: {e}")

    # Audit log path is writable
    try:
        s.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        probe = s.audit_log_path.parent / ".ammp-status-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        ok("Audit log writable", str(s.audit_log_path))
    except Exception as e:
        fail("Audit log writable", f"{s.audit_log_path}: {e}")

    # Anthropic key (only relevant if any mentor uses anthropic backend)
    has_anthropic_mentor = s.mentors_root.is_dir() and any(
        m.backend is None or m.backend.kind == "anthropic" for m in load_mentors(s.mentors_root).values()
    )
    if has_anthropic_mentor:
        if not s.anthropic_api_key:
            warn(
                "AMMP_ANTHROPIC_API_KEY",
                "unset; AskMentor will return a deterministic stub for any anthropic-backed mentor",
            )
        else:
            ok("AMMP_ANTHROPIC_API_KEY", "set")

    console.print(table)

    if problems:
        console.print()
        console.print(f"[bold red]{len(problems)} problem(s) — installation is broken:[/bold red]")
        for p in problems:
            console.print(f"  • {p}")
        raise typer.Exit(code=1)
    if notes:
        console.print()
        console.print(f"[yellow]{len(notes)} warning(s) — non-fatal but worth checking:[/yellow]")
        for n in notes:
            console.print(f"  • {n}")
        console.print()
        console.print("[green]Status: OK with warnings.[/green]")
    else:
        console.print()
        console.print("[bold green]Status: OK.[/bold green]")


# ─── Usage statistics ─────────────────────────────────────────────────────


_AUDIT_LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+op=(?P<op>\S+)\s+mentor=(?P<mentor>\S+)\s+mentee=(?P<mentee>\S+)\s+hash=(?P<hash>\S+)"
)


@app.command("usage")
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

    cutoff: dt.datetime | None = None
    if days > 0:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

    op_counts: Counter[str] = Counter()
    mentor_counts: Counter[str] = Counter()
    mentee_counts: Counter[str] = Counter()
    op_x_mentor: Counter[tuple[str, str]] = Counter()
    parsed_lines = 0
    skipped_lines = 0
    earliest: dt.datetime | None = None
    latest: dt.datetime | None = None

    with s.audit_log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = _AUDIT_LINE_RE.match(line)
            if not m:
                skipped_lines += 1
                continue
            try:
                ts = dt.datetime.fromisoformat(m.group("ts"))
            except ValueError:
                skipped_lines += 1
                continue
            if cutoff and ts < cutoff:
                continue
            parsed_lines += 1
            op = m.group("op")
            mentor = m.group("mentor")
            mentee = m.group("mentee")
            op_counts[op] += 1
            mentor_counts[mentor] += 1
            mentee_counts[mentee] += 1
            op_x_mentor[(op, mentor)] += 1
            earliest = ts if earliest is None or ts < earliest else earliest
            latest = ts if latest is None or ts > latest else latest

    window = f"last {days}d" if days > 0 else "all time"
    if not parsed_lines:
        console.print(f"[yellow]No audit entries in window ({window}).[/yellow]")
        raise typer.Exit(code=0)

    console.print(
        Panel.fit(
            f"[bold]ammp-mcp usage[/bold] · window: [cyan]{window}[/cyan]\n"
            f"entries: [bold]{parsed_lines}[/bold]"
            + (f" (skipped {skipped_lines} unparsable)" if skipped_lines else "")
            + (
                f"\nrange: {earliest.isoformat(timespec='seconds') if earliest else '—'} → "
                f"{latest.isoformat(timespec='seconds') if latest else '—'}"
                if earliest
                else ""
            ),
            border_style="cyan",
        )
    )

    # Per-operation
    op_table = Table(title="By operation")
    op_table.add_column("operation", style="white")
    op_table.add_column("count", justify="right", style="cyan")
    for op, n in op_counts.most_common():
        op_table.add_row(op, str(n))
    console.print(op_table)

    if by_mentor:
        m_table = Table(title="By mentor")
        m_table.add_column("mentor", style="white")
        m_table.add_column("count", justify="right", style="green")
        for mentor, n in mentor_counts.most_common():
            m_table.add_row(mentor, str(n))
        console.print(m_table)

    if by_mentee:
        u_table = Table(title="By mentee")
        u_table.add_column("mentee", style="white")
        u_table.add_column("count", justify="right", style="magenta")
        for mentee, n in mentee_counts.most_common():
            u_table.add_row(mentee, str(n))
        console.print(u_table)


if __name__ == "__main__":
    app()
