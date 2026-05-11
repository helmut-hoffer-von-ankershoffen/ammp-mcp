"""CLI integration tests via Typer's CliRunner.

We override settings via env vars (AMMP_*) so the CLI's get_settings()
call resolves to the isolated tree. This is the same surface a real user hits.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ammp_mcp import settings as settings_module
from ammp_mcp.cli import app
from ammp_mcp.mentee import find_mentee_by_api_key, load_mentees

pytestmark = pytest.mark.integration


@pytest.fixture
def runner(isolated_tree: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("AMMP_DIR", str(isolated_tree))
    monkeypatch.setenv("AMMP_MENTORS_ROOT", str(isolated_tree / "mentors"))
    monkeypatch.setenv("AMMP_MENTEES_FILE", str(isolated_tree / "mentees.json"))
    monkeypatch.setenv("AMMP_AUDIT_LOG_PATH", str(isolated_tree / "audit.log"))
    monkeypatch.setenv("AMMP_PUBLIC_URL", "http://test.invalid")
    # The fixture's primary mentor is `pepe`; pin explicitly because the
    # package default flipped to `example` and `example` is not in the tree.
    monkeypatch.setenv("AMMP_DEFAULT_MENTOR", "pepe")
    # Rich truncates table cells to terminal width; force wide so slugs render in full.
    monkeypatch.setenv("COLUMNS", "200")
    settings_module.reset_settings_for_testing()
    yield CliRunner()
    settings_module.reset_settings_for_testing()


def test_list_mentors(runner: CliRunner) -> None:
    r = runner.invoke(app, ["mentor", "list"])
    assert r.exit_code == 0, r.output
    assert "pepe" in r.output
    assert "strict" in r.output


def test_list_mentors_json_matches_wire_envelope(runner: CliRunner) -> None:
    """`ammp mentor list --json` returns the same shape as the MCP `ListMentors` tool."""
    r = runner.invoke(app, ["mentor", "list", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["track"] == "mentoring"
    assert payload["default_mentor"] == "pepe"
    by_slug = {m["slug"]: m for m in payload["mentors"]}
    assert set(by_slug) == {"pepe", "strict", "stubmentor"}
    assert by_slug["pepe"]["is_default"] is True
    assert by_slug["pepe"]["playbook_count"] == 2  # fixture's pepe has 2 playbooks
    # Wire envelope has no operator-facing fields like `playbook_dir`.
    for m in payload["mentors"]:
        assert "playbook_dir" not in m


def test_list_playbooks_default(runner: CliRunner) -> None:
    r = runner.invoke(app, ["playbook", "list"])
    assert r.exit_code == 0, r.output
    assert "intro" in r.output
    assert "auth" in r.output


def test_list_playbooks_unknown_mentor(runner: CliRunner) -> None:
    r = runner.invoke(app, ["playbook", "list", "--mentor", "ghost"])
    assert r.exit_code == 2


def test_show_playbook(runner: CliRunner) -> None:
    r = runner.invoke(app, ["playbook", "show", "intro"])
    assert r.exit_code == 0
    assert "Welcome" in r.output


def test_show_playbook_traversal_rejected(runner: CliRunner) -> None:
    r = runner.invoke(app, ["playbook", "show", "../etc/passwd"])
    assert r.exit_code == 2


def test_list_mentees(runner: CliRunner) -> None:
    r = runner.invoke(app, ["mentee", "list"])
    assert r.exit_code == 0
    assert "claude-cowork-sandra" in r.output


def test_add_mentee_round_trip(runner: CliRunner, isolated_tree: Path) -> None:
    r = runner.invoke(
        app,
        [
            "mentee",
            "add",
            "claude-ai-sandra",
            "--operator",
            "human:sandra",
            "--runtime",
            "claude-ai",
        ],
    )
    assert r.exit_code == 0, r.output
    # CLI prints the plaintext key once
    m = re.search(r"(ammp-[A-Za-z0-9_\-]+)", r.output)
    assert m, f"expected an ammp- key in output, got: {r.output}"
    plaintext = m.group(1)
    # Reload from disk and resolve the new key
    mentees = load_mentees(isolated_tree / "mentees.json")
    assert "claude-ai-sandra" in mentees
    found = find_mentee_by_api_key(mentees, plaintext)
    assert found is not None
    assert found.runtime == "claude-ai"


def test_add_mentee_duplicate_rejected(runner: CliRunner) -> None:
    r = runner.invoke(
        app,
        [
            "mentee",
            "add",
            "claude-cowork-sandra",
            "--operator",
            "human:sandra",
            "--runtime",
            "claude-cowork",
        ],
    )
    assert r.exit_code == 2
    assert "already exists" in r.output


def test_remove_mentee(runner: CliRunner, isolated_tree: Path) -> None:
    r = runner.invoke(app, ["mentee", "remove", "claude-cowork-sandra"])
    assert r.exit_code == 0
    mentees = load_mentees(isolated_tree / "mentees.json")
    assert "claude-cowork-sandra" not in mentees


def test_rotate_mentee_key(runner: CliRunner, isolated_tree: Path) -> None:
    before = load_mentees(isolated_tree / "mentees.json")
    old_hash = before["claude-cowork-sandra"].api_key_hash

    r = runner.invoke(app, ["mentee", "rotate-key", "claude-cowork-sandra"])
    assert r.exit_code == 0
    m = re.search(r"(ammp-[A-Za-z0-9_\-]+)", r.output)
    assert m, r.output

    after = load_mentees(isolated_tree / "mentees.json")
    new_hash = after["claude-cowork-sandra"].api_key_hash
    assert new_hash != old_hash


def test_check_key_match(runner: CliRunner) -> None:
    r = runner.invoke(app, ["mentee", "check-key", "ammp-test-key-1"])
    assert r.exit_code == 0
    assert "claude-cowork-sandra" in r.output


def test_check_key_no_match(runner: CliRunner) -> None:
    r = runner.invoke(app, ["mentee", "check-key", "definitely-wrong"])
    assert r.exit_code == 1


def test_capability_command(runner: CliRunner) -> None:
    r = runner.invoke(app, ["capability"])
    assert r.exit_code == 0
    assert "ammp-mcp" in r.output
    assert "draft-ammp" in r.output


# ─── Setup / status / usage ───────────────────────────────────────────────


def test_setup_writes_backend_block_and_first_mentee(runner: CliRunner, isolated_tree: Path) -> None:
    cwd_before = os.getcwd()
    os.chdir(isolated_tree)
    try:
        r = runner.invoke(
            app,
            [
                "setup",
                "--yes",
                "--mentor-slug",
                "pepe",
                "--backend",
                "openclaw",
                "--openclaw-url",
                "https://test.invalid/ammp/ask",
                "--auth-bearer-env",
                "MY_TEST_BEARER",
                "--no-mint-first-mentee",  # skip mentee in test, less file noise
            ],
        )
    finally:
        os.chdir(cwd_before)
    assert r.exit_code == 0, r.output
    # mentor.json now has the openclaw backend block
    pepe_mj = json.loads((isolated_tree / "mentors" / "pepe" / "mentor.json").read_text(encoding="utf-8"))
    assert pepe_mj["backend"]["kind"] == "openclaw"
    assert pepe_mj["backend"]["url"] == "https://test.invalid/ammp/ask"
    assert pepe_mj["backend"]["auth_bearer_env"] == "MY_TEST_BEARER"
    # config.env scaffold written into AMMP_DIR (not cwd)
    config_env = isolated_tree / "config.env"
    assert config_env.exists()
    body = config_env.read_text(encoding="utf-8")
    assert "AMMP_REQUIRE_AUTH=true" in body
    assert "MY_TEST_BEARER=" in body


def test_setup_rejects_unknown_mentor(runner: CliRunner, isolated_tree: Path) -> None:
    cwd_before = os.getcwd()
    os.chdir(isolated_tree)
    try:
        r = runner.invoke(app, ["setup", "--yes", "--mentor-slug", "ghost"])
    finally:
        os.chdir(cwd_before)
    assert r.exit_code == 2
    assert "No mentor directory" in r.output


def test_setup_rejects_unknown_backend(runner: CliRunner, isolated_tree: Path) -> None:
    cwd_before = os.getcwd()
    os.chdir(isolated_tree)
    try:
        # Pin mentor-slug to the fixture's `pepe` so the wizard reaches backend
        # validation rather than failing earlier on a missing `mentors/example/`.
        r = runner.invoke(app, ["setup", "--yes", "--mentor-slug", "pepe", "--backend", "ghost"])
    finally:
        os.chdir(cwd_before)
    assert r.exit_code == 2
    assert "Unknown backend kind" in r.output


def test_status_reports_ok_with_warnings(runner: CliRunner) -> None:
    r = runner.invoke(app, ["status"])
    # mentees.json exists in the fixture (no AMMP_ANTHROPIC key) → OK with warnings.
    assert r.exit_code == 0
    assert "Settings loaded" in r.output
    assert "mentor pepe" in r.output


def test_status_fails_when_mentors_root_missing(runner: CliRunner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AMMP_MENTORS_ROOT", str(tmp_path / "definitely_not_a_dir"))
    from ammp_mcp import settings as settings_module

    settings_module.reset_settings_for_testing()
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 1
    assert "Mentors root" in r.output
    assert "directory does not exist" in r.output


def test_usage_handles_missing_log(runner: CliRunner) -> None:
    r = runner.invoke(app, ["usage"])
    assert r.exit_code == 0
    assert "does not exist yet" in r.output


def test_usage_aggregates_audit_log(runner: CliRunner, isolated_tree: Path) -> None:
    import datetime as dt

    audit_log = isolated_tree / "audit.log"
    now = dt.datetime.now(dt.UTC).isoformat()
    audit_log.write_text(
        f"{now} op=ListPlaybooks mentor=pepe mentee=anonymous hash=—\n"
        f"{now} op=AskMentor mentor=pepe mentee=alice hash=abc12345\n"
        f"{now} op=AskMentor mentor=strict mentee=alice hash=def00000\n"
        f"{now} op=EscalateToHuman mentor=pepe mentee=bob hash=ffffffff\n",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["usage", "--days", "0"])
    assert r.exit_code == 0
    assert "AskMentor" in r.output
    assert "pepe" in r.output
    assert "alice" in r.output


# ─── system subgroup + aliases + health ──────────────────────────────────


def test_system_status_resolves_via_subgroup(runner: CliRunner) -> None:
    """`ammp system status` and the top-level alias `ammp status` route to the same callable."""
    r1 = runner.invoke(app, ["status"])
    r2 = runner.invoke(app, ["system", "status"])
    assert r1.exit_code == r2.exit_code == 0
    assert "Settings loaded" in r1.output
    assert "Settings loaded" in r2.output


def test_system_capability_resolves_via_subgroup(runner: CliRunner) -> None:
    r = runner.invoke(app, ["system", "capability"])
    assert r.exit_code == 0
    assert "ammp-mcp" in r.output
    assert "draft-ammp" in r.output


def test_system_health_fails_when_unreachable(runner: CliRunner) -> None:
    """`ammp system health` against a closed port should exit 1."""
    r = runner.invoke(app, ["system", "health", "--url", "http://127.0.0.1:1", "--no-probe-backends"])
    assert r.exit_code == 1
    assert "unreachable" in r.output.lower() or "connection" in r.output.lower()


def test_health_alias_works(runner: CliRunner) -> None:
    r = runner.invoke(app, ["health", "--url", "http://127.0.0.1:1", "--no-probe-backends"])
    assert r.exit_code == 1


# ─── CLI parity with AMMP wire protocol ───────────────────────────────────
#
# `ammp mentor ask`, `ammp mentor escalate`, `ammp playbook search` invoke
# the same in-process handlers the MCP server uses. These tests pin the
# CLI surface to the protocol — both happy and error paths.


def test_mentor_ask_with_stub_backend(runner: CliRunner) -> None:
    """`ammp mentor ask` against a stub-backend mentor returns a deterministic answer."""
    r = runner.invoke(app, ["mentor", "ask", "what should I do?", "--mentor", "stubmentor"])
    assert r.exit_code == 0, r.output
    assert "AskMentor" in r.output
    assert "answer" in r.output


def test_mentor_ask_unknown_mentor_errors(runner: CliRunner) -> None:
    """Unknown mentor slug → exit 1 with a helpful error."""
    r = runner.invoke(app, ["mentor", "ask", "anything", "--mentor", "ghost"])
    assert r.exit_code == 1
    assert "failed" in r.output.lower() or "unknown" in r.output.lower()


def test_mentor_ask_json_output(runner: CliRunner) -> None:
    """`--json` emits parseable JSON with the expected handler shape."""
    r = runner.invoke(app, ["mentor", "ask", "hello", "--mentor", "stubmentor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["mentor"] == "stubmentor"
    assert "answer" in payload
    assert "confidence" in payload


def test_mentor_escalate_happy_path(runner: CliRunner) -> None:
    """`ammp mentor escalate` returns suggested phrasing for the operator."""
    r = runner.invoke(
        app,
        ["mentor", "escalate", "two playbooks contradict", "--mentor", "stubmentor", "--why-stuck", "unsure"],
    )
    assert r.exit_code == 0, r.output
    assert "EscalateToHuman" in r.output


def test_mentor_escalate_unknown_mentor_errors(runner: CliRunner) -> None:
    r = runner.invoke(app, ["mentor", "escalate", "anything", "--mentor", "ghost"])
    assert r.exit_code == 1


def test_playbook_search_finds_match(runner: CliRunner) -> None:
    """`ammp playbook search` substring-searches the corpus."""
    r = runner.invoke(app, ["playbook", "search", "callback", "--mentor", "pepe"])
    assert r.exit_code == 0, r.output
    # The auth.md playbook mentions "OAuth callback resilience".
    assert "auth" in r.output


def test_playbook_search_unknown_mentor_errors(runner: CliRunner) -> None:
    r = runner.invoke(app, ["playbook", "search", "anything", "--mentor", "ghost"])
    assert r.exit_code == 1


def test_playbook_search_json_output(runner: CliRunner) -> None:
    r = runner.invoke(app, ["playbook", "search", "callback", "--mentor", "pepe", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["mentor"] == "pepe"
    assert payload["query"] == "callback"
    assert payload["count"] >= 1


# ─── Help / discoverability invariants ──────────────────────────────────────


# Every leaf command in the public CLI surface. Update this list when a new
# command is added — the loop tests below assert both `<cmd> --help` works
# AND that the bare subgroup prints help cleanly (exit 0, not exit 2).
_LEAF_COMMANDS: list[list[str]] = [
    ["mentor", "list"],
    ["mentor", "ask", "what is grounding?"],
    ["mentor", "escalate", "I'm stuck"],
    ["mentee", "list"],
    ["mentee", "add", "x", "--operator", "human:x", "--runtime", "claude-cowork"],
    ["mentee", "remove", "x"],
    ["mentee", "rotate-key", "x"],
    ["mentee", "check-key", "ammp-x"],
    ["playbook", "list"],
    ["playbook", "show", "intro"],
    ["playbook", "search", "x"],
    ["system", "capability"],
    ["system", "setup"],
    ["system", "status"],
    ["system", "health"],
    ["system", "usage"],
    # serve is destructive (boots a server) — `serve --help` only.
    ["system", "serve"],
]


@pytest.mark.parametrize("cmd", _LEAF_COMMANDS, ids=lambda c: " ".join(c))
def test_every_leaf_command_supports_help_flag(runner: CliRunner, cmd: list[str]) -> None:
    """`<cmd> --help` exits 0 with a Usage: banner — for every leaf."""
    r = runner.invoke(app, [*cmd[:2], "--help"])  # subject + verb + --help
    assert r.exit_code == 0, (cmd, r.output)
    assert "Usage:" in r.output


@pytest.mark.parametrize(
    "subgroup",
    [[], ["mentor"], ["mentee"], ["playbook"], ["system"]],
    ids=lambda s: "ammp" if not s else f"ammp {s[0]}",
)
def test_bare_invocation_shows_help_and_exits_zero(runner: CliRunner, subgroup: list[str]) -> None:
    """`ammp` and every subgroup print help on no-args and exit 0.

    Typer's default `no_args_is_help=True` exits 2 (Click's usage-error
    convention). Modern CLIs (kubectl, gh, helm, AWS CLI) treat help as
    a first-class feature — bare invocation succeeds. Pinned via the
    `wire_help_on_no_args` shim; regression-tested here.
    """
    r = runner.invoke(app, subgroup)
    assert r.exit_code == 0, (subgroup, r.exit_code, r.output)
    assert "Usage:" in r.output


def test_root_help_lists_every_subject(runner: CliRunner) -> None:
    """`ammp --help` lists every subject and top-level alias."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for subject in ("mentor", "mentee", "playbook", "system"):
        assert subject in r.output, f"subject {subject!r} missing from root help"
    for alias in ("serve", "setup", "status", "health", "usage", "capability"):
        assert alias in r.output, f"alias {alias!r} missing from root help"
