"""First-run setup helpers — backing the `ammp system setup` wizard.

Split out as discrete helpers so the orchestrating command (`setup` in
`_cli.py`) stays well under SonarCloud's S3776 cognitive-complexity
limit (15).
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from ..mentee import Mentee, hash_api_key, load_mentees, save_mentees
from ..mentor import Mentor, load_mentors
from ..settings import Settings

console = Console()

_ICON_OK = "[green]✓[/green]"
_VALID_BACKEND_KINDS = {"openclaw", "anthropic", "stub"}


def _setup_print_header(s: Settings, repo_root: Path) -> None:
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
    mentor_json_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
    s: Settings, backend: str, auth_bearer_env: str, require_auth: bool, existing: dict[str, str]
) -> dict[str, str]:
    desired = {
        "AMMP_REQUIRE_AUTH": "true" if require_auth else "false",
        "AMMP_MENTORS_ROOT": str(s.mentors_root),
        "AMMP_MENTEES_FILE": str(s.mentees_file),
        "AMMP_AUDIT_LOG_PATH": str(s.audit_log_path),
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
