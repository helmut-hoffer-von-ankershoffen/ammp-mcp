"""`ammp playbook …`, `ammp skill …`, and `ammp plugin …` subcommands.

The corpus is a two-level hierarchy — playbooks (areas of practice)
contain skills (markdown files). ``ammp playbook`` operates on the
area level; ``ammp skill`` operates on individual skills;
``ammp plugin`` exposes the local equivalent of the ``GetPluginArchive``
MCP tool so an operator on the host can resolve a plugin to its
download URL + install instructions without an MCP round-trip.
"""

from __future__ import annotations

import json as _json

import typer
from rich.console import Console
from rich.table import Table

from .._cli_utils import wire_help_on_no_args
from ..mentor import get_mentor, load_mentors
from ..settings import get_settings
from ._service import (
    build_plugin_archive_response,
    enumerate_plugin_refs,
    load_playbooks,
    safe_id,
)

console = Console()

playbook_app = typer.Typer(
    name="playbook",
    help="Inspect and read a mentor's playbooks (areas of practice).",
    add_completion=False,
)
wire_help_on_no_args(playbook_app)


skill_app = typer.Typer(
    name="skill",
    help="Inspect and read individual skills inside a mentor's playbooks.",
    add_completion=False,
)
wire_help_on_no_args(skill_app)


plugin_app = typer.Typer(
    name="plugin",
    help="Resolve marketplace plugins (CLI parity with the GetPluginArchive MCP tool).",
    add_completion=False,
)
wire_help_on_no_args(plugin_app)


@plugin_app.command("list")
def plugin_list() -> None:
    """List every (plugin, marketplace) referenced by this server's playbooks."""
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    known = enumerate_plugin_refs(mentors, s.marketplaces_root)
    if not known:
        console.print("[dim]No playbook on this server references a plugin.[/dim]")
        return
    table = Table(title=f"Plugin refs ({len(known)})")
    table.add_column("plugin", style="cyan")
    table.add_column("marketplace", style="green")
    table.add_column("clone path", style="dim")
    for plugin, (_, marketplace, plugin_dir) in sorted(known.items()):
        table.add_row(plugin, marketplace, str(plugin_dir))
    console.print(table)


@plugin_app.command("archive")
def plugin_archive(
    plugin: str = typer.Argument(..., help="Plugin name (kebab-case slug)."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a Rich table."),
) -> None:
    """Resolve a plugin name to its zip URL + install instructions.

    CLI parity with the ``GetPluginArchive`` MCP tool — same in-process
    service-layer helper (:func:`build_plugin_archive_response`), so
    the wire and the shell stay in lockstep.
    """
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    known = enumerate_plugin_refs(mentors, s.marketplaces_root)
    payload = build_plugin_archive_response(s.public_url, plugin, known)
    if as_json:
        typer.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        if "error" in payload:
            raise typer.Exit(code=1)
        return
    if "error" in payload:
        console.print(f"[red]{payload['error']}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold]{payload['plugin']}[/bold] @ {payload['marketplace']}")
    console.print(f"URL: {payload['archive_url']}")
    console.print()
    console.print(payload["install_instructions"])


@playbook_app.command("list")
def playbook_list(
    mentor: str = typer.Option("", help="Mentor slug. Empty → server default."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a Rich table."),
) -> None:
    """List the playbooks (areas of practice) for a mentor.

    Default output is a Rich table; ``--json`` emits a machine-readable
    envelope with shape ``{mentor, count, playbooks: [{id, name,
    description, requires, skill_count}]}`` (matches the MCP
    ``ListPlaybooks`` envelope minus the embedded skill bodies).
    """
    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    m = get_mentor(mentors, mentor, s.default_mentor)
    if not m:
        console.print(f"[red]Unknown mentor: {mentor or s.default_mentor!r}[/red]")
        raise typer.Exit(code=2)
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=s.marketplaces_root)
    if as_json:
        payload = {
            "mentor": m.slug,
            "count": len(corpus),
            "playbooks": [
                {
                    "id": pb.id,
                    "name": pb.name,
                    "description": pb.description,
                    "requires": list(pb.requires),
                    "skill_count": len(pb.skills),
                }
                for pb in corpus
            ],
        }
        typer.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return
    table = Table(title=f"{m.name} — playbooks ({len(corpus)})")
    table.add_column("id", style="cyan")
    table.add_column("name", style="white")
    table.add_column("description", style="dim")
    table.add_column("skills", justify="right", style="magenta")
    for pb in corpus:
        table.add_row(pb.id, pb.name, pb.description[:80], str(len(pb.skills)))
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
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=s.marketplaces_root)
    pb = next((p for p in corpus if p.id == clean), None)
    if pb is None:
        console.print(f"[red]Not found: playbook id={clean!r} for mentor {m.slug!r}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold]{pb.name}[/bold]  [dim]({pb.id})[/dim]")
    if pb.description:
        console.print(pb.description)
    console.print()
    table = Table(title=f"skills ({len(pb.skills)})")
    table.add_column("id", style="cyan")
    table.add_column("title", style="white")
    table.add_column("summary", style="dim")
    for sk in pb.skills:
        table.add_row(sk.id, sk.title, sk.summary[:80])
    console.print(table)


@skill_app.command("list")
def skill_list(
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
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=s.marketplaces_root)
    pb = next((p for p in corpus if p.id == clean_pb), None)
    if pb is None:
        console.print(f"[red]Not found: playbook id={clean_pb!r} for mentor {m.slug!r}[/red]")
        raise typer.Exit(code=1)
    table = Table(title=f"{m.name} · {pb.name} — skills ({len(pb.skills)})")
    table.add_column("id", style="cyan")
    table.add_column("title", style="white")
    table.add_column("summary", style="dim")
    for sk in pb.skills:
        table.add_row(sk.id, sk.title, sk.summary[:80])
    console.print(table)


@skill_app.command("show")
def skill_show(
    skill_id: str = typer.Argument(..., help="Skill id (folder name or filename stem)."),
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
    clean_id = safe_id(skill_id)
    if not clean_pb or not clean_id:
        console.print("[red]Invalid id[/red]")
        raise typer.Exit(code=2)
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=s.marketplaces_root)
    pb = next((p for p in corpus if p.id == clean_pb), None)
    if pb is None:
        console.print(f"[red]Not found: playbook id={clean_pb!r} for mentor {m.slug!r}[/red]")
        raise typer.Exit(code=1)
    sk = next((s_ for s_ in pb.skills if s_.id == clean_id), None)
    if sk is None:
        console.print(f"[red]Not found: skill id={clean_id!r} in playbook {clean_pb!r}[/red]")
        raise typer.Exit(code=1)
    console.print(sk.body)


@playbook_app.command("search")
def playbook_search(
    query: str = typer.Argument(..., help="Substring to search for across the mentor's skill corpus."),
    mentor: str = typer.Option("", "--mentor", "-m", help="Mentor slug. Empty → server default."),
    limit: int = typer.Option(5, "--limit", "-n", min=1, max=50, help="Maximum number of matches to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON (machine-readable) instead of a Rich table."),
) -> None:
    """Substring-search a mentor's corpus (CLI parity with AMMP ``SearchPlaybooks``).

    Calls the same handler the MCP server uses. Search runs at skill
    granularity; each match names its parent playbook. The hash-only
    audit log records the call.
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


@playbook_app.command("validate")
def playbook_validate(
    playbook_id: str = typer.Argument(..., help="Playbook id to validate."),
    mentor: str = typer.Option("", "--mentor", "-m", help="Mentor slug. Empty → server default."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a Rich summary."),
    behavioral: bool = typer.Option(
        False,
        "--behavioral",
        help="Run the Layer 2 behavioral validator (real claude plugin install + filesystem side-effect assertions) instead of Layer 1 comprehension-only.",
    ),
    goal: bool = typer.Option(
        False,
        "--goal",
        help="Run the Layer 3 goal-level validator: producer mentee delivers the playbook's promised outcome + consumer mentee verifies the workdir is consumable cold (no AMMP wire).",
    ),
    plugin: str = typer.Option(
        "",
        "--plugin",
        help="Plugin id for --behavioral mode (e.g. pepe-knowledge-management). Required when --behavioral.",
    ),
    marketplace_repo: str = typer.Option(
        "",
        "--marketplace-repo",
        help="GitHub owner/repo of the marketplace for --behavioral mode. Required when --behavioral.",
    ),
) -> None:
    """Run the playbook's validation prompts as a Sub-process LLM mentee.

    Reads the playbook's ``validation.prompts[]`` from ``playbook.json``,
    spawns a fresh ``claude -p`` mentee for each prompt with the AMMP
    wire pre-loaded (the mentee gets the canonical start-prompt
    referencing this playbook), then runs each ``expect`` rule against
    the mentee's response.

    Rules supported (mentee must satisfy all that apply):

    * ``must_mention_skill: <skill-id>`` — response references the named
      skill explicitly (case-insensitive substring of the skill id).
    * ``must_contain: ["str", "str|alt", ...]`` — every listed string
      appears in the response. Pipe-separated alternates accepted.
    * ``must_contain_pattern: "regex"`` — Python ``re.search`` against
      the response matches.
    * ``must_invoke_or_name: ["script.sh", ...]`` — at least one of the
      named scripts is named in the response (proxy for "the mentee
      knew to invoke this tool"; the validator doesn't actually run
      the scripts here, that's the e2e harness's job).

    The CLI is the planning layer; the heavy lifting (subprocess
    spawning + Claude Code invocation) lives in
    ``scripts/e2e-playbook-validation.sh``. This CLI is what an
    operator + CI invoke; the script is what does the work.
    """
    import os
    import subprocess
    import sys

    s = get_settings()
    mentors = load_mentors(s.mentors_root)
    m = get_mentor(mentors, mentor, s.default_mentor)
    if not m:
        console.print(f"[red]Unknown mentor: {mentor or s.default_mentor!r}[/red]")
        raise typer.Exit(code=2)
    clean_id = safe_id(playbook_id)
    if not clean_id:
        console.print(f"[red]Invalid playbook id: {playbook_id!r}[/red]")
        raise typer.Exit(code=2)
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=s.marketplaces_root)
    pb = next((p for p in corpus if p.id == clean_id), None)
    if pb is None:
        console.print(f"[red]Not found: playbook id={clean_id!r} for mentor {m.slug!r}[/red]")
        raise typer.Exit(code=1)

    prompts = pb.validation.get("prompts", []) if isinstance(pb.validation, dict) else []
    if not prompts:
        console.print(f"[yellow]Playbook {clean_id!r} declares no validation prompts. Nothing to verify.[/yellow]")
        raise typer.Exit(code=2)

    # Delegate to the bundled subprocess harness. The harness lives in
    # the ammp-mcp repo; this CLI passes the playbook spec to it via
    # env vars so the harness has everything to spawn the mentee.
    # _cli.py lives at <repo>/src/ammp_mcp/playbook/_cli.py — 4 dirnames
    # up gets the repo root.
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if goal:
        if not plugin or not marketplace_repo:
            console.print("[red]--goal requires --plugin and --marketplace-repo[/red]")
            raise typer.Exit(code=2)
        if not isinstance(pb.validation, dict) or not isinstance(pb.validation.get("goal"), dict):
            console.print(
                f"[yellow]Playbook {clean_id!r} declares no `validation.goal` block. Nothing to verify at goal level.[/yellow]"
            )
            raise typer.Exit(code=2)
        harness = os.path.join(repo_root, "scripts", "e2e-playbook-validation-goal.sh")
    elif behavioral:
        if not plugin or not marketplace_repo:
            console.print(
                "[red]--behavioral requires --plugin and --marketplace-repo (e.g. "
                "--plugin pepe-knowledge-management --marketplace-repo "
                "helmut-hoffer-von-ankershoffen/helmguild-plugins-public)[/red]"
            )
            raise typer.Exit(code=2)
        harness = os.path.join(repo_root, "scripts", "e2e-playbook-validation-behavioral.sh")
    else:
        harness = os.path.join(repo_root, "scripts", "e2e-playbook-validation.sh")
    if not os.path.isfile(harness):
        console.print(
            f"[red]Validator harness not found at {harness!r}. "
            f"This CLI requires scripts/e2e-playbook-validation.sh in the ammp-mcp checkout.[/red]"
        )
        raise typer.Exit(code=2)

    # Pass the playbook spec as env vars + JSON on stdin (avoids a
    # 100-arg command-line).
    env = os.environ.copy()
    env["AMMP_VALIDATE_MENTOR"] = m.slug
    env["AMMP_VALIDATE_PLAYBOOK"] = clean_id
    env["AMMP_VALIDATE_PLAYBOOK_NAME"] = pb.name
    env["AMMP_VALIDATE_OUTPUT_FORMAT"] = "json" if as_json else "text"
    if behavioral or goal:
        env["AMMP_VALIDATE_PLUGIN"] = plugin
        env["AMMP_VALIDATE_MARKETPLACE_REPO"] = marketplace_repo
    if goal:
        spec_json = _json.dumps({"goal": pb.validation.get("goal", {})}, ensure_ascii=False)
    else:
        spec_json = _json.dumps({"prompts": prompts}, ensure_ascii=False)
    proc = subprocess.run(
        ["bash", harness],
        env=env,
        input=spec_json,
        text=True,
        capture_output=True,
    )
    # Pass through the harness's stdout + stderr verbatim.
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    raise typer.Exit(code=proc.returncode)
