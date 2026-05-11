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
# round-trip + tool dispatch. 20 is more than the model needs for the
# current doc surface (typically converges in 6-10).
MAX_TOOL_ITERATIONS = 20

# Per-command timeout. The longest legitimate read-only command
# (`ammp playbook list`) finishes in well under a second on a warm shell.
COMMAND_TIMEOUT_SECONDS = 20

HAIKU_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
You are auditing the documentation of `ammp-mcp` against the CLI it documents.

Your job: read the docs in the user message, extract concrete *verifiable*
claims, exercise each claim using the `run_command` tool, and report any
mismatch via `report_inconsistency`. When you are done, call `finish`.

A *verifiable* claim is something the CLI itself reveals: a default value
shown by `--help`, an env var rendered by `capability`, a playbook id
listed by `playbook list`, the existence of a subcommand, the shape of
help output, etc.

Skip claims you cannot verify from the CLI surface (PyPI URLs, GitHub
links, deployment topology, narrative text). Skip wording-level
differences — only report behaviour mismatches.

The `run_command` tool takes a list of argv tokens that are passed to
`python -m ammp_mcp …`. So to run `ammp --help` you call run_command
with args=["--help"]. To run `ammp playbook list --mentor pepe` you
call run_command with args=["playbook", "list", "--mentor", "pepe"].

The tool runs from a clean working directory with no .env loaded, so
`capability` will reflect the *built-in* defaults from settings.py — the
exact thing the env-var tables in the docs claim. Use that to verify
documented defaults.

Severity scale for `report_inconsistency`:
- "high": documented command does not run, or documented default
  value is wrong (a user following the docs hits a bug).
- "low": cosmetic / out-of-date narrative that won't trip a reader.

Be efficient. Do not exhaustively enumerate every help string — pick the
claims most likely to rot: env-var defaults, command names, flag names,
default values shown in help. 6–12 probes is plenty.
"""


def _filter_env() -> dict[str, str]:
    """Strip AMMP_* from the env so settings.py defaults take effect.

    The doc walker tests *what the docs say the defaults are*, which
    means the subprocess must see no overrides — no .env, no exported
    AMMP_* vars. Everything else (PATH etc.) carries through.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("AMMP_")}


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

    Output is truncated to keep tool-result tokens bounded — the model
    only needs the first / last bytes to decide whether the doc claim
    holds. A truncation marker tells the model it's seeing a slice.
    """
    refusal = _is_dangerous(args)
    if refusal:
        return {"refused": refusal, "exit_code": None}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ammp_mcp", *args],
            cwd=str(tmp_cwd),
            env=_filter_env(),
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


def _docs_payload() -> str:
    """Concatenate the three docs the walker audits."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    installation = (REPO_ROOT / "INSTALLATION.md").read_text(encoding="utf-8")
    cli_ref = (REPO_ROOT / "docs" / "CLI_REFERENCE.md").read_text(encoding="utf-8")
    return (
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
    """
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

    for _iter in range(MAX_TOOL_ITERATIONS):
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
                    "content": json.dumps(result),
                }
            )

        messages.append({"role": "user", "content": tool_results})
        if finished_summary is not None:
            break

    high = [f for f in findings if f.get("severity") == "high"]
    low = [f for f in findings if f.get("severity") == "low"]

    if low:
        # Surface but don't fail — the build owner can decide whether to fix.
        print("\nDoc walker — low-severity findings:")
        print(json.dumps(low, indent=2))

    if high:
        pytest.fail(
            f"Doc walker found {len(high)} high-severity inconsistency/ies "
            f"(summary: {finished_summary!r}):\n{json.dumps(high, indent=2)}"
        )

    # If the model burned the whole iteration budget without calling
    # finish, that's a soft failure — we want the contract to be "the
    # walker terminates cleanly". A non-finishing run usually means the
    # tool surface or the prompt drifted.
    assert finished_summary is not None, (
        f"Doc walker did not call finish within {MAX_TOOL_ITERATIONS} iterations. "
        f"Findings so far: {json.dumps(findings, indent=2)}"
    )
