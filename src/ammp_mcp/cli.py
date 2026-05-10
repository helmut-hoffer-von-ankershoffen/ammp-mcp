"""Typer-based CLI for housekeeping.

Subject-then-action layout (mirrors `git remote list`, `kubectl get pods`):

    ammp mentor list
    ammp mentee list / add / remove / rotate-key / check-key
    ammp playbook list / show
    ammp system setup / status / health / usage / capability / serve

Top-level shortcuts for the most common system verbs:

    ammp serve       → ammp system serve
    ammp setup       → ammp system setup
    ammp status      → ammp system status
    ammp health      → ammp system health
    ammp usage       → ammp system usage
    ammp capability  → ammp system capability
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .models import Mentee, Mentor
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

# ─── Constants reused across commands ────────────────────────────────────

_ICON_OK = "[green]✓[/green]"
_ICON_FAIL = "[red]✗[/red]"
_ICON_WARN = "[yellow]![/yellow]"
_CAP_LABEL = "Capability advertisement"
_VALID_BACKEND_KINDS = {"openclaw", "anthropic", "stub"}

mentor_app = typer.Typer(
    name="mentor",
    help="Inspect registered mentors.",
    no_args_is_help=True,
    add_completion=False,
)
mentee_app = typer.Typer(
    name="mentee",
    help="Manage the mentee allowlist (add / remove / rotate-key / list / check-key).",
    no_args_is_help=True,
    add_completion=False,
)
playbook_app = typer.Typer(
    name="playbook",
    help="Inspect and read the playbook corpus of a mentor.",
    no_args_is_help=True,
    add_completion=False,
)
system_app = typer.Typer(
    name="system",
    help="Operate the install as a whole (setup, status, health, usage, capability, serve).",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(mentor_app)
app.add_typer(mentee_app)
app.add_typer(playbook_app)
app.add_typer(system_app)


# ─── ammp mentor … ────────────────────────────────────────────────────────


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


# ─── ammp playbook … ──────────────────────────────────────────────────────


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


# ─── ammp mentee … ────────────────────────────────────────────────────────


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


# ─── ammp system … (act on the install as a whole) ───────────────────────


@system_app.command("capability")
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


@system_app.command("serve")
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


# ─── ammp system setup ────────────────────────────────────────────────────
#
# `setup` is split into a thin command function that calls a sequence of
# private helpers. Keeps cognitive complexity well under SonarCloud's
# S3776 limit of 15.


def _setup_print_header(s: object, repo_root: Path) -> None:
    console.print(
        Panel.fit(
            f"[bold]ammp-mcp setup[/bold]\n"
            f"[dim]repo:[/dim] {repo_root}\n"
            f"[dim]mentors_root:[/dim] {s.mentors_root}\n"  # type: ignore[attr-defined]
            f"[dim]mentees_file:[/dim] {s.mentees_file}\n"  # type: ignore[attr-defined]
            f"[dim]audit_log:[/dim] {s.audit_log_path}",  # type: ignore[attr-defined]
            title="Step 0 — environment",
            border_style="cyan",
        )
    )


def _setup_validate(mentors_root: Path, mentor_slug: str, backend: str) -> dict[str, Mentor]:
    mentors = load_mentors(mentors_root)
    if mentor_slug not in mentors:
        console.print(
            f"[red]No mentor directory found at {mentors_root / mentor_slug}.[/red]\n"
            "Create one with `mentor.json` + `playbooks/*.md` first, then re-run setup."
        )
        raise typer.Exit(code=2)
    if backend not in _VALID_BACKEND_KINDS:
        kinds = ", ".join(sorted(_VALID_BACKEND_KINDS))
        console.print(f"[red]Unknown backend kind: {backend!r}. Choose one of: {kinds}.[/red]")
        raise typer.Exit(code=2)
    return mentors


def _setup_resolve_openclaw_args(yes: bool, openclaw_url: str, auth_bearer_env: str) -> tuple[str, str]:
    if yes:
        return openclaw_url, auth_bearer_env
    url = Prompt.ask("OpenClaw webhook URL", default=openclaw_url, console=console)
    bearer = Prompt.ask("Env var holding the OpenClaw Bearer token", default=auth_bearer_env, console=console)
    return url, bearer


def _setup_apply_backend(mentor_json_path: Path, backend: str, openclaw_url: str, auth_bearer_env: str) -> None:
    backend_block: dict[str, object] = {"kind": backend}
    if backend == "openclaw":
        backend_block.update(
            {"url": openclaw_url, "auth_bearer_env": auth_bearer_env, "timeout_seconds": 60, "max_concurrent": 10}
        )
    raw = json.loads(mentor_json_path.read_text(encoding="utf-8"))
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    raw["backend"] = backend_block
    mentor_json_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def _setup_mint_mentee_if_needed(mentees_file: Path) -> str | None:
    """Mint `claude-cowork-helmut` if absent; return the plaintext key (or None when skipped)."""
    mentees = load_mentees(mentees_file) if mentees_file.exists() else {}
    first_slug = "claude-cowork-helmut"
    if first_slug in mentees:
        console.print(f"[yellow]·[/yellow] Mentee [bold]{first_slug}[/bold] already exists — skipped.")
        return None
    api_key = "ammp-" + secrets.token_urlsafe(32)
    mentees[first_slug] = Mentee(
        slug=first_slug,
        operator="human:helmut",
        runtime="claude-cowork",
        api_key_hash=hash_api_key(api_key),
        rate_limit_per_minute=60,
    )
    save_mentees(mentees_file, mentees)
    console.print(f"{_ICON_OK} Minted first mentee [bold]{first_slug}[/bold].")
    return api_key


def _setup_load_existing_env(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _setup_compose_env(
    s: object, backend: str, auth_bearer_env: str, require_auth: bool, existing: dict[str, str]
) -> dict[str, str]:
    desired = {
        "AMMP_REQUIRE_AUTH": "true" if require_auth else "false",
        "AMMP_MENTORS_ROOT": str(s.mentors_root),  # type: ignore[attr-defined]
        "AMMP_MENTEES_FILE": str(s.mentees_file),  # type: ignore[attr-defined]
        "AMMP_AUDIT_LOG_PATH": str(s.audit_log_path),  # type: ignore[attr-defined]
    }
    if backend == "anthropic":
        desired.setdefault("AMMP_ANTHROPIC_API_KEY", existing.get("AMMP_ANTHROPIC_API_KEY", ""))
    if backend == "openclaw":
        desired.setdefault(auth_bearer_env, existing.get(auth_bearer_env, ""))
    return {**desired, **existing}


def _setup_write_env(env_path: Path, merged: dict[str, str], auth_bearer_env: str) -> None:
    lines = [
        "# ammp-mcp environment - written by `ammp setup`. Edit freely.",
        f"# Generated {dt.datetime.now(dt.UTC).isoformat()}.",
        "",
    ]
    for k in sorted(merged):
        v = merged[k]
        comment = ""
        if k == auth_bearer_env and not v:
            comment = "  # <-- fill in the Bearer token before booting"
        if k == "AMMP_ANTHROPIC_API_KEY" and not v:
            comment = "  # <-- fill in if you choose the anthropic backend"
        lines.append(f"{k}={v}{comment}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"{_ICON_OK} Wrote [bold].env[/bold] scaffold ({len(merged)} keys).")


def _setup_print_next_steps(backend: str, auth_bearer_env: str) -> None:
    next_secret = auth_bearer_env if backend == "openclaw" else "AMMP_ANTHROPIC_API_KEY"
    console.print()
    console.print(
        Panel.fit(
            "[bold]Next steps[/bold]\n\n"
            f"  1. Edit [cyan].env[/cyan] and fill in the [cyan]{next_secret}[/cyan] value.\n"
            "  2. [cyan]uv run ammp status[/cyan] — verify the install is healthy.\n"
            "  3. [cyan]uv run ammp serve[/cyan] — boot the MCP server.\n"
            "  4. [cyan]curl http://127.0.0.1:8765/.well-known/agent.json[/cyan] — confirm the capability advertisement.",
            title="Setup complete",
            border_style="green",
        )
    )


@system_app.command("setup")
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
    the next-step commands. Idempotent.
    """
    s = get_settings()
    repo_root = Path.cwd()
    _setup_print_header(s, repo_root)
    _setup_validate(s.mentors_root, mentor_slug, backend)
    if backend == "openclaw":
        openclaw_url, auth_bearer_env = _setup_resolve_openclaw_args(yes, openclaw_url, auth_bearer_env)
    _setup_apply_backend(s.mentors_root / mentor_slug / "mentor.json", backend, openclaw_url, auth_bearer_env)
    console.print(f"{_ICON_OK} Updated [bold]{mentor_slug}/mentor.json[/bold] → backend = [cyan]{backend}[/cyan]")

    api_key = _setup_mint_mentee_if_needed(s.mentees_file) if mint_first_mentee else None

    env_path = repo_root / ".env"
    existing = _setup_load_existing_env(env_path)
    merged = _setup_compose_env(s, backend, auth_bearer_env, require_auth, existing)
    _setup_write_env(env_path, merged, auth_bearer_env)
    _setup_print_next_steps(backend, auth_bearer_env)

    if api_key:
        console.print()
        console.print("[bold yellow]API KEY for the first mentee — copy now, you will not see it again:[/bold yellow]")
        console.print(f"  {api_key}")


# ─── ammp system status ───────────────────────────────────────────────────


@dataclass
class _StatusReporter:
    """Collects status-check rows + outcome lists. One closure-free unit per run."""

    table: Table
    problems: list[str]
    notes: list[str]

    def ok(self, name: str, detail: str = "") -> None:
        self.table.add_row(name, _ICON_OK, detail)

    def warn(self, name: str, detail: str) -> None:
        self.table.add_row(name, _ICON_WARN, detail)
        self.notes.append(f"{name}: {detail}")

    def fail(self, name: str, detail: str) -> None:
        self.table.add_row(name, _ICON_FAIL, detail)
        self.problems.append(f"{name}: {detail}")


def _status_check_one_mentor(slug: str, m: Mentor, r: _StatusReporter) -> None:
    corpus = load_corpus(m.playbook_dir)
    backend_label = m.backend.kind if m.backend else "fallback"
    if not corpus:
        r.warn(f"  mentor {slug}", f"playbook_dir has no *.md: {m.playbook_dir}")
    else:
        r.ok(f"  mentor {slug}", f"{len(corpus)} playbook(s), backend={backend_label}")
    if m.backend and m.backend.kind == "openclaw":
        env_name = m.backend.auth_bearer_env
        if env_name and not os.environ.get(env_name):
            r.warn(f"    {slug}.backend env", f"{env_name} is not set in the environment")
        if not re.match(r"^https?://", m.backend.url):
            r.fail(f"    {slug}.backend url", f"not http(s): {m.backend.url}")


def _status_check_mentors(mentors_root: Path, r: _StatusReporter) -> dict[str, Mentor]:
    if not mentors_root.is_dir():
        r.fail("Mentors root", f"directory does not exist: {mentors_root}")
        return {}
    try:
        mentors = load_mentors(mentors_root)
    except Exception as e:
        r.fail("Mentor registry", f"failed to load: {e}")
        return {}
    if not mentors:
        r.warn("Mentor registry", f"no mentors under {mentors_root}")
    for slug, m in mentors.items():
        _status_check_one_mentor(slug, m, r)
    return mentors


def _status_check_mentees(mentees_file: Path, r: _StatusReporter) -> None:
    if not mentees_file.exists():
        r.warn("Mentees file", f"does not exist yet: {mentees_file} (run `ammp mentee add` to mint one)")
        return
    try:
        mentees = load_mentees(mentees_file)
        r.ok("Mentees allowlist", f"{len(mentees)} mentee(s)")
    except Exception as e:
        r.fail("Mentees allowlist", f"failed to load: {e}")


def _status_check_audit_log(audit_log_path: Path, r: _StatusReporter) -> None:
    try:
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        probe = audit_log_path.parent / ".ammp-status-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        r.ok("Audit log writable", str(audit_log_path))
    except Exception as e:
        r.fail("Audit log writable", f"{audit_log_path}: {e}")


def _status_check_anthropic_key(s: object, mentors: dict[str, Mentor], r: _StatusReporter) -> None:
    has_anthropic_mentor = any(m.backend is None or m.backend.kind == "anthropic" for m in mentors.values())
    if not has_anthropic_mentor:
        return
    if not s.anthropic_api_key:  # type: ignore[attr-defined]
        r.warn(
            "AMMP_ANTHROPIC_API_KEY",
            "unset; AskMentor will return a deterministic stub for any anthropic-backed mentor",
        )
    else:
        r.ok("AMMP_ANTHROPIC_API_KEY", "set")


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


# ─── ammp system usage ────────────────────────────────────────────────────


_AUDIT_LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+op=(?P<op>\S+)\s+mentor=(?P<mentor>\S+)\s+mentee=(?P<mentee>\S+)\s+hash=(?P<hash>\S+)"
)


@dataclass
class _AuditAggregate:
    op_counts: Counter[str] = field(default_factory=Counter)
    mentor_counts: Counter[str] = field(default_factory=Counter)
    mentee_counts: Counter[str] = field(default_factory=Counter)
    parsed: int = 0
    skipped: int = 0
    earliest: dt.datetime | None = None
    latest: dt.datetime | None = None


def _usage_parse_one_line(line: str, cutoff: dt.datetime | None, agg: _AuditAggregate) -> None:
    m = _AUDIT_LINE_RE.match(line)
    if not m:
        agg.skipped += 1
        return
    try:
        ts = dt.datetime.fromisoformat(m.group("ts"))
    except ValueError:
        agg.skipped += 1
        return
    if cutoff and ts < cutoff:
        return
    agg.parsed += 1
    agg.op_counts[m.group("op")] += 1
    agg.mentor_counts[m.group("mentor")] += 1
    agg.mentee_counts[m.group("mentee")] += 1
    if agg.earliest is None or ts < agg.earliest:
        agg.earliest = ts
    if agg.latest is None or ts > agg.latest:
        agg.latest = ts


def _usage_parse_audit(path: Path, cutoff: dt.datetime | None) -> _AuditAggregate:
    agg = _AuditAggregate()
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            _usage_parse_one_line(line, cutoff, agg)
    return agg


def _usage_render_breakdown(title: str, counts: Counter[str], col_label: str, style: str) -> Table:
    t = Table(title=title)
    t.add_column(col_label, style="white")
    t.add_column("count", justify="right", style=style)
    for k, n in counts.most_common():
        t.add_row(k, str(n))
    return t


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


# ─── ammp system health ───────────────────────────────────────────────────


def _health_probe_capability(agent_url: str, timeout: float, table: Table, problems: list[str]) -> None:
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(agent_url, headers={"User-Agent": "ammp-health/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as e:
        table.add_row(_CAP_LABEL, _ICON_FAIL, f"GET {agent_url} failed: {e}")
        problems.append(f"server unreachable at {agent_url} — boot it with `ammp serve`")
        return
    except Exception as e:
        table.add_row(_CAP_LABEL, _ICON_FAIL, f"unexpected error: {e}")
        problems.append(f"capability probe error: {e}")
        return
    ammp_block = body.get("ammp", {})
    mentor_count = len(ammp_block.get("mentors", []))
    live_count = sum(1 for m in ammp_block.get("mentors", []) if m.get("backendLive"))
    table.add_row(
        _CAP_LABEL,
        _ICON_OK,
        f"name={body.get('name')} v{body.get('version')} · {mentor_count} mentor(s), {live_count} live",
    )


def _health_probe_one_backend(slug: str, m: Mentor, timeout: float, table: Table, problems: list[str]) -> None:
    import urllib.error
    import urllib.request

    label = f"  backend {slug} (openclaw)"
    if m.backend is None or m.backend.kind != "openclaw":
        return
    try:
        req = urllib.request.Request(m.backend.url, method="HEAD", headers={"User-Agent": "ammp-health/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            table.add_row(label, _ICON_OK, f"HEAD {m.backend.url} → {resp.status}")
    except urllib.error.HTTPError as e:
        # 405 Method Not Allowed is fine — server is up, just rejects HEAD.
        if e.code in {405, 501}:
            table.add_row(label, _ICON_OK, f"reachable (HEAD rejected: {e.code})")
        else:
            table.add_row(label, _ICON_WARN, f"{m.backend.url} → {e}")
    except urllib.error.URLError as e:
        table.add_row(label, _ICON_FAIL, f"unreachable: {e.reason}")
        problems.append(f"openclaw backend for {slug!r} unreachable: {m.backend.url}")
    except Exception as e:
        table.add_row(label, _ICON_FAIL, f"unexpected error: {e}")
        problems.append(f"openclaw backend probe error for {slug!r}: {e}")


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
    target = (url or s.public_url or f"http://{s.host}:{s.port}").rstrip("/")
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


# ─── Top-level aliases ────────────────────────────────────────────────────
# Common system verbs are also exposed at the top level for muscle memory.
# Canonical home stays on `ammp system <verb>`; these are just shortcuts.

app.command(
    "serve",
    help="Alias for `ammp system serve`.",
)(serve)
app.command(
    "setup",
    help="Alias for `ammp system setup`.",
)(setup)
app.command(
    "status",
    help="Alias for `ammp system status`.",
)(status)
app.command(
    "health",
    help="Alias for `ammp system health`.",
)(health)
app.command(
    "usage",
    help="Alias for `ammp system usage`.",
)(usage)
app.command(
    "capability",
    help="Alias for `ammp system capability`.",
)(capability)


if __name__ == "__main__":
    app()
