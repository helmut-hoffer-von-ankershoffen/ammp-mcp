"""CLI integration tests via Typer's CliRunner.

We override settings via env vars (AMMP_*) so the CLI's get_settings()
call resolves to the isolated tree. This is the same surface a real user hits.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ammp_mcp import settings as settings_module
from ammp_mcp.cli import app
from ammp_mcp.registries import find_mentee_by_api_key, load_mentees

pytestmark = pytest.mark.integration


@pytest.fixture
def runner(isolated_tree: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("AMMP_MENTORS_ROOT", str(isolated_tree / "mentors"))
    monkeypatch.setenv("AMMP_MENTEES_FILE", str(isolated_tree / "mentees.json"))
    monkeypatch.setenv("AMMP_AUDIT_LOG_PATH", str(isolated_tree / "audit.log"))
    monkeypatch.setenv("AMMP_PUBLIC_URL", "http://test.invalid")
    # Rich truncates table cells to terminal width; force wide so slugs render in full.
    monkeypatch.setenv("COLUMNS", "200")
    settings_module.reset_settings_for_testing()
    yield CliRunner()
    settings_module.reset_settings_for_testing()


def test_list_mentors(runner: CliRunner) -> None:
    r = runner.invoke(app, ["list-mentors"])
    assert r.exit_code == 0, r.output
    assert "pepe" in r.output
    assert "strict" in r.output


def test_list_playbooks_default(runner: CliRunner) -> None:
    r = runner.invoke(app, ["list-playbooks"])
    assert r.exit_code == 0, r.output
    assert "intro" in r.output
    assert "auth" in r.output


def test_list_playbooks_unknown_mentor(runner: CliRunner) -> None:
    r = runner.invoke(app, ["list-playbooks", "--mentor", "ghost"])
    assert r.exit_code == 2


def test_show_playbook(runner: CliRunner) -> None:
    r = runner.invoke(app, ["show-playbook", "intro"])
    assert r.exit_code == 0
    assert "Welcome" in r.output


def test_show_playbook_traversal_rejected(runner: CliRunner) -> None:
    r = runner.invoke(app, ["show-playbook", "../etc/passwd"])
    assert r.exit_code == 2


def test_list_mentees(runner: CliRunner) -> None:
    r = runner.invoke(app, ["list-mentees"])
    assert r.exit_code == 0
    assert "claude-cowork-sandra" in r.output


def test_add_mentee_round_trip(runner: CliRunner, isolated_tree: Path) -> None:
    r = runner.invoke(
        app,
        [
            "add-mentee",
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
            "add-mentee",
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
    r = runner.invoke(app, ["remove-mentee", "claude-cowork-sandra"])
    assert r.exit_code == 0
    mentees = load_mentees(isolated_tree / "mentees.json")
    assert "claude-cowork-sandra" not in mentees


def test_rotate_mentee_key(runner: CliRunner, isolated_tree: Path) -> None:
    before = load_mentees(isolated_tree / "mentees.json")
    old_hash = before["claude-cowork-sandra"].api_key_hash

    r = runner.invoke(app, ["rotate-mentee-key", "claude-cowork-sandra"])
    assert r.exit_code == 0
    m = re.search(r"(ammp-[A-Za-z0-9_\-]+)", r.output)
    assert m, r.output

    after = load_mentees(isolated_tree / "mentees.json")
    new_hash = after["claude-cowork-sandra"].api_key_hash
    assert new_hash != old_hash


def test_check_key_match(runner: CliRunner) -> None:
    r = runner.invoke(app, ["check-key", "ammp-test-key-1"])
    assert r.exit_code == 0
    assert "claude-cowork-sandra" in r.output


def test_check_key_no_match(runner: CliRunner) -> None:
    r = runner.invoke(app, ["check-key", "definitely-wrong"])
    assert r.exit_code == 1


def test_capability_command(runner: CliRunner) -> None:
    r = runner.invoke(app, ["capability"])
    assert r.exit_code == 0
    assert "ammp-mcp" in r.output
    assert "draft-arturo-ammp" in r.output
