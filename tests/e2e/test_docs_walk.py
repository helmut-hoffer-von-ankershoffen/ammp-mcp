"""Doc-walker: Claude Haiku reads the docs and runs the documented commands.

The job: catch the kind of README rot where a doc says one thing and the
CLI does another (wrong env-var default, renamed subcommand, stale flag).

Marked ``e2e`` because it costs a small number of Haiku tokens per run.
Self-skips when ``AMMP_ANTHROPIC_API_KEY`` is missing, so it's free to
keep in the suite locally and only fires in CI where the secret is wired.

Why Haiku-with-tool-use, not a hand-rolled regex over the docs:

- Hand-rolled regexes are fragile to wording (`default: 8765` vs `8765 by
  default`). The LLM extracts claims semantically.
- The LLM also picks *which* claims are verifiable from the CLI surface
  and which (PyPI URLs, "the AMMP draft says…") are not.
- The CLI surface is small and read-only safe (--help, capability,
  playbook list/show/search, mentor/mentee list, status). The tool
  allowlist enforces that no destructive command can be invoked even if
  the model tries.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Anthropic SDK is already a runtime dep (used by the anthropic backend).
from anthropic import Anthropic
from anthropic.types import ToolUseBlock

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("AMMP_ANTHROPIC_API_KEY"),
        reason="AMMP_ANTHROPIC_API_KEY not set",
    ),
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Read-only subcommands the doc walker may exercise. Anything that mints
# keys, writes files, or starts a server is blocked at the gate. The
# allowlist is on the *leading* subject token (or top-level command) so
# `ammp playbook list` is OK but `ammp playbook list --evil-flag` is also
# OK — the flag goes to argparse, not to us. Destructive verbs go through
# a separate blocklist below.
ALLOWED_LEADING_TOKENS = frozenset(
    {
        # subjects
        "mentor",
        "mentee",
        "playbook",
        "system",
        # top-level read-only verbs (aliases of `ammp system <verb>`)
        "capability",
        "status",
        "usage",
        "health",
        # universal help flag
        "--help",
        "-h",
    }
)

# Inside the subject namespace, these verbs flip state and must be refused
# even if argparse would accept them. Keep this list aggressive — a false
# block is cheap (the model just picks a different probe), a false allow
# would let the doc walker mint API keys or boot a server.
DESTRUCTIVE_TOKENS = frozenset(
    {
        "add",
        "remove",
        "rotate-key",
        "setup",
        "serve",
    }
)

# Hard ceiling on the agentic loop. Each iteration is one Anthropic
# round-trip + tool dispatch. Haiku converges in 6-10 turns on the
# current doc surface; 12 leaves headroom without burning tokens.
MAX_TOOL_ITERATIONS = 12

# Nudge the model to wrap up before it exhausts the iteration budget.
# Once we hit this many iterations without a `finish` call, every
# subsequent tool_result carries an explicit "call finish next turn"
# instruction. This is how we convert a soft cap into a soft deadline
# the model actually respects.
FINISH_NUDGE_AFTER_ITERATION = 6

# Per-command timeout. The longest legitimate read-only command
# (`ammp playbook list`) finishes in well under a second on a warm shell.
COMMAND_TIMEOUT_SECONDS = 20

HAIKU_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
You are auditing the documentation of `ammp-mcp` against the CLI it documents.

Your job: read the docs in the user message, extract concrete verifiable
claims, exercise each claim using the `run_command` tool, and report any
mismatch via `report_inconsistency`. When you are done, call `finish`.

A verifiable claim is something the CLI itself reveals: a runtime
default rendered by `capability` JSON, a playbook id listed by
`playbook list`, the existence of a subcommand, an env var the docs say
defaults to X.

DO NOT report these — they look like inconsistencies but are not:

1. Typer override sentinels. `[default: 0]` on `--port`, `[default: ""]`
   on `--host`, etc. are flag-override sentinels meaning "no override —
   use the settings value". They are NOT the runtime defaults. The
   runtime defaults are what `capability` JSON shows.

2. The `--require-auth` default on `ammp setup`. That is what the setup
   wizard writes into `.env` so production starts with auth on. It is
   NOT the package's runtime default. The runtime default is shown by
   `capability` and is `false`.

3. Wording differences, narrative tone, future tense, out-of-scope
   sections, PyPI URLs, GitHub links, deployment topology.

When in doubt: only report it if a user copy-pasting a value from the
docs would hit a real bug. An env-var table claiming default X when
`capability` shows Y is a real bug. A wizard flag default differing
from a runtime default is NOT.

The `run_command` tool takes a list of argv tokens passed to
`python -m ammp_mcp ...`. To run `ammp --help` use args=["--help"]. To
run `ammp playbook list --mentor pepe` use
args=["playbook","list","--mentor","pepe"].

The tool runs from a clean working directory with no .env loaded, so
`capability` reflects the built-in defaults from settings.py — the
exact thing the env-var tables in the docs claim.

Severity:
- "high": documented runtime default is wrong, or a documented
  command/flag does not exist. A user copy-pasting hits a bug.
- "low": stale narrative (e.g. "v0.2 has X" while package is v0.3).

Workflow: 4 to 8 probes total, then call `finish`. Do NOT keep probing
after the verifiable surface is covered. Call `finish` even if your
findings list is empty — an empty list is a valid "everything matches"
result. The iteration budget is small; thrashing exhausts it.
"""


def _filter_env(ammp_dir: Path | None = None) -> dict[str, str]:
    """Strip AMMP_* from the env, optionally pinning ``AMMP_DIR``.

    The doc walker tests *what the docs say the defaults are*, so the
    subprocess must see no leftover overrides — no .env, no exported
    AMMP_* vars. Everything else (PATH etc.) carries through.

    When ``ammp_dir`` is supplied, ``AMMP_DIR`` is set to that path so
    bootstrap writes / reads into the test's tmp tree rather than the
    developer's real ``~/.ammp/``. This mirrors a fresh install on a
    clean machine.

    Args:
        ammp_dir: Optional tmp directory to use as the bootstrapped
            ``AMMP_DIR``. ``None`` leaves the env var unset so the
            subprocess hits the packaged default (``~/.ammp/``).

    Returns:
        A copy of the current environment with all ``AMMP_*`` keys
        stripped and ``AMMP_DIR`` optionally re-added.
    """
    out = {k: v for k, v in os.environ.items() if not k.startswith("AMMP_")}
    if ammp_dir is not None:
        out["AMMP_DIR"] = str(ammp_dir)
    return out


def _is_dangerous(args: list[str]) -> str | None:
    """Return a refusal reason if `args` looks destructive; None if safe."""
    if not args:
        return "empty args"
    leading = args[0]
    if leading not in ALLOWED_LEADING_TOKENS:
        return f"leading token {leading!r} not in read-only allowlist"
    for tok in args:
        if tok in DESTRUCTIVE_TOKENS:
            return f"destructive token {tok!r} blocked"
    return None


def _run_command_tool(args: list[str], tmp_cwd: Path) -> dict[str, object]:
    """Execute one CLI probe in a clean env. Returns stdout/stderr/exit_code.

    ``tmp_cwd`` doubles as the subprocess's ``AMMP_DIR`` so the model
    sees a freshly-bootstrapped tree (example mentor copied, config.env
    written) rather than the developer's real ``~/.ammp/``. The
    bootstrap happens once on first invocation and is idempotent.

    Output is truncated to keep tool-result tokens bounded — the model
    only needs the first / last bytes to decide whether the doc claim
    holds. A truncation marker tells the model it's seeing a slice.

    Args:
        args: argv tokens passed verbatim after ``python -m ammp_mcp``.
        tmp_cwd: Test-isolated tmp directory used as both the
            subprocess cwd and the bootstrap target (``AMMP_DIR``).

    Returns:
        A JSON-serialisable dict — ``{exit_code, stdout, stderr}`` on
        a real subprocess run, or ``{refused, exit_code: None}`` when
        the args fail the safety allowlist.
    """
    refusal = _is_dangerous(args)
    if refusal:
        return {"refused": refusal, "exit_code": None}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ammp_mcp", *args],
            cwd=str(tmp_cwd),
            env=_filter_env(ammp_dir=tmp_cwd),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"refused": None, "exit_code": -1, "stdout": "", "stderr": "timeout"}

    def _trim(s: str, limit: int = 6000) -> str:
        if len(s) <= limit:
            return s
        return s[: limit // 2] + f"\n…[truncated {len(s) - limit} chars]…\n" + s[-limit // 2 :]

    return {
        "refused": None,
        "exit_code": proc.returncode,
        "stdout": _trim(proc.stdout),
        "stderr": _trim(proc.stderr),
    }


def _tools_schema() -> list[dict[str, object]]:
    return [
        {
            "name": "run_command",
            "description": (
                "Run `python -m ammp_mcp <args>` from a clean directory with no .env "
                "loaded. Returns {exit_code, stdout, stderr}. Read-only commands only "
                "(--help, capability, playbook/mentor/mentee list, etc.). Destructive "
                "verbs are blocked at the gate and return a `refused` reason."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "argv tokens passed after `ammp_mcp` (e.g. ['playbook', 'list', '--mentor', 'pepe']).",
                    },
                },
                "required": ["args"],
            },
        },
        {
            "name": "report_inconsistency",
            "description": (
                "Record one mismatch between what the docs claim and what the CLI "
                "actually does. Severity 'high' fails the test."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "documented_claim": {
                        "type": "string",
                        "description": "Quote or paraphrase the doc claim, including which file it appears in (README, INSTALLATION, CLI_REFERENCE).",
                    },
                    "actual_behavior": {
                        "type": "string",
                        "description": "What the CLI actually does, with the command you ran to verify.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["high", "low"],
                        "description": "high = will trip a user following the docs; low = cosmetic / stale narrative.",
                    },
                },
                "required": ["documented_claim", "actual_behavior", "severity"],
            },
        },
        {
            "name": "finish",
            "description": "Signal you have finished auditing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "One-line summary of what you found.",
                    }
                },
                "required": ["summary"],
            },
        },
    ]


def _project_scripts_block() -> str:
    """Extract the `[project.scripts]` table from pyproject.toml.

    The walker can't probe console-script entry points via `python -m
    ammp_mcp` (those run a separate binary, not a CLI subcommand). So
    we hand the script registry to the model as ground truth — any
    doc referencing a console script is verifiable against this list
    without needing a tool probe.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lines = pyproject.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if line.strip().startswith("[project.scripts]"):
            inside = True
        elif inside and line.strip().startswith("[") and "scripts" not in line:
            break
        if inside:
            out.append(line)
    return "\n".join(out) if out else "(no [project.scripts] block found)"


def _docs_payload() -> str:
    """Concatenate the three docs the walker audits, plus ground-truth context."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    installation = (REPO_ROOT / "INSTALLATION.md").read_text(encoding="utf-8")
    cli_ref = (REPO_ROOT / "docs" / "CLI_REFERENCE.md").read_text(encoding="utf-8")
    scripts = _project_scripts_block()
    return (
        "=== GROUND TRUTH: console scripts registered in pyproject.toml ===\n"
        "These binaries are INSTALLED alongside `ammp` and are real entry "
        "points. They are NOT subcommands of `ammp` and cannot be probed "
        "via `python -m ammp_mcp ...`. Treat their existence as verified.\n\n"
        f"{scripts}\n\n"
        "=== README.md ===\n"
        f"{readme}\n\n"
        "=== INSTALLATION.md ===\n"
        f"{installation}\n\n"
        "=== docs/CLI_REFERENCE.md ===\n"
        f"{cli_ref}\n"
    )


@pytest.mark.timeout(240)
def test_docs_walk_matches_cli_behaviour(tmp_path: Path) -> None:
    """Haiku reads the docs, runs the documented commands, reports drift.

    Fails on any 'high' severity inconsistency. 'low' severity findings
    are surfaced to the test output but don't fail the build — they're
    the cosmetic kind (e.g. "narrative paragraph still says v0.2").

    Pre-bootstraps ``tmp_path`` as the test's ``AMMP_DIR`` so the
    walker sees the same starting state a fresh-install operator
    would: example mentor present, config.env written. Without this,
    `ammp playbook list` (default mentor = example) would fail and
    Haiku would correctly report that as an inconsistency.

    Args:
        tmp_path: Pytest's per-test tmp directory; doubles as the
            subprocess ``AMMP_DIR`` for the walker.
    """
    # Pre-bootstrap so commands the walker invokes see a working tree.
    # Done in-process for speed (no subprocess) and reset_settings_for_testing
    # to make sure the bootstrap honours our explicit AMMP_DIR pin.
    from ammp_mcp.settings import Settings, reset_settings_for_testing
    from ammp_mcp.system._setup_service import bootstrap_ammp_dir

    bootstrap_ammp_dir(Settings(_env_file=None, ammp_dir=tmp_path), quiet=True)
    reset_settings_for_testing()

    client = Anthropic(api_key=os.environ["AMMP_ANTHROPIC_API_KEY"])
    tools = _tools_schema()
    findings: list[dict[str, object]] = []
    finished_summary: str | None = None

    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": (
                "Audit the following documentation against the CLI. Report any "
                "behaviour mismatch via `report_inconsistency`, then call `finish`.\n\n"
                f"{_docs_payload()}"
            ),
        }
    ]

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=tools,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        iterations_left = MAX_TOOL_ITERATIONS - iteration - 1
        nudge = (
            f"\n\n[{iterations_left} iterations remaining. Call `finish` next "
            f"turn unless you have one specific verifiable claim left to test.]"
            if iteration >= FINISH_NUDGE_AFTER_ITERATION
            else ""
        )

        tool_results: list[dict[str, object]] = []
        for block in response.content:
            if not isinstance(block, ToolUseBlock):
                continue
            payload = block.input if isinstance(block.input, dict) else {}
            if block.name == "run_command":
                args_raw = payload.get("args", [])
                args = [str(a) for a in args_raw] if isinstance(args_raw, list) else []
                result: dict[str, object] = _run_command_tool(args, tmp_path)
            elif block.name == "report_inconsistency":
                findings.append(dict(payload))
                result = {"recorded": True}
            elif block.name == "finish":
                finished_summary = str(payload.get("summary", ""))
                result = {"acknowledged": True}
            else:
                result = {"error": f"unknown tool {block.name!r}"}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result) + nudge,
                }
            )

        messages.append({"role": "user", "content": tool_results})
        if finished_summary is not None:
            break

    high = [f for f in findings if f.get("severity") == "high"]
    low = [f for f in findings if f.get("severity") == "low"]

    if finished_summary is None:
        # Haiku burned the iteration budget without calling `finish`.
        # That is a warning, not a failure: a thorough audit that ran
        # out of budget still surfaced its findings. If those findings
        # are non-empty we report them; if not, the test still passes
        # but prints a notice so prompt drift is visible.
        print(
            f"\nDoc walker NOTICE — did not call finish within "
            f"{MAX_TOOL_ITERATIONS} iterations. Findings extracted: "
            f"{len(findings)}."
        )
    else:
        print(
            f"\nDoc walker finished: {finished_summary!r}. "
            f"Findings: {len(findings)} ({len(high)} high, {len(low)} low)."
        )

    if low:
        print("\nDoc walker — low-severity findings:")
        print(json.dumps(low, indent=2))

    if high:
        pytest.fail(
            f"Doc walker found {len(high)} high-severity inconsistency/ies "
            f"(summary: {finished_summary!r}):\n{json.dumps(high, indent=2)}"
        )
