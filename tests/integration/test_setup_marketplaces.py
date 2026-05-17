"""Tests for `ammp setup`'s marketplace auto-clone (`bootstrap_marketplaces`).

A fresh host has the mentors corpus (metadata) but not the marketplace
caches that hold the skill bodies. `bootstrap_marketplaces` closes that
gap: it clones every marketplace the corpus references and has a
configured repo URL.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ammp_mcp.settings import Settings
from ammp_mcp.system._setup_service import bootstrap_ammp_dir, bootstrap_marketplaces

pytestmark = pytest.mark.integration


def _make_git_repo(path: Path) -> None:
    """Init a one-commit git repo at `path` to stand in for a marketplace."""
    path.mkdir(parents=True)
    (path / "MARKER.md").write_text("fake marketplace\n", encoding="utf-8")
    for argv in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
    ):
        subprocess.run(argv, cwd=path, check=True, capture_output=True)


def _write_playbook(mentors_root: Path, plugin_ref: str) -> None:
    """Write a minimal playbook.json under `mentors_root` referencing `plugin_ref`."""
    pb_dir = mentors_root / "m1" / "playbooks" / "pb1"
    pb_dir.mkdir(parents=True)
    (pb_dir / "playbook.json").write_text(json.dumps({"name": "PB1", "plugin": plugin_ref}), encoding="utf-8")


def _settings(tmp_path: Path, mentors_root: Path, **extra: object) -> Settings:
    """Build a hermetic Settings bound to `tmp_path` for marketplace tests."""
    return Settings(
        _env_file=None,
        ammp_dir=tmp_path / "ammp",
        mentors_root=mentors_root,
        marketplaces_root=tmp_path / "ammp" / "marketplaces",
        **extra,  # type: ignore[arg-type]
    )


def test_bootstrap_marketplaces_clones_referenced(tmp_path: Path) -> None:
    """A corpus-referenced marketplace with a configured URL is cloned; idempotent."""
    src = tmp_path / "src-marketplace"
    _make_git_repo(src)

    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "p1@test-mp")
    s = _settings(tmp_path, mentors_root, marketplace_repos={"test-mp": f"file://{src}"})

    assert bootstrap_marketplaces(s) is True
    assert (s.marketplaces_root / "test-mp" / "MARKER.md").is_file()

    # Second call: marketplace already on disk → nothing cloned.
    assert bootstrap_marketplaces(s) is False


def test_bootstrap_marketplaces_skips_unmapped(tmp_path: Path) -> None:
    """A referenced marketplace with no configured URL is skipped, not fatal."""
    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "p1@unmapped-mp")
    s = _settings(tmp_path, mentors_root, marketplace_repos={})

    assert bootstrap_marketplaces(s) is False
    assert not (s.marketplaces_root / "unmapped-mp").exists()


def test_bootstrap_marketplaces_no_references(tmp_path: Path) -> None:
    """A corpus with no `@marketplace` plugin references clones nothing."""
    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "")  # no plugin reference
    s = _settings(tmp_path, mentors_root)

    assert bootstrap_marketplaces(s) is False


def test_bootstrap_marketplaces_clone_failure_is_not_fatal(tmp_path: Path) -> None:
    """A configured URL that fails to clone logs a warning and is skipped."""
    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "p1@bad-mp")
    s = _settings(
        tmp_path,
        mentors_root,
        marketplace_repos={"bad-mp": f"file://{tmp_path}/does-not-exist"},
    )

    assert bootstrap_marketplaces(s) is False


def test_bootstrap_marketplaces_malformed_playbook_json(tmp_path: Path) -> None:
    """A playbook.json that isn't valid JSON is skipped, not fatal."""
    mentors_root = tmp_path / "mentors"
    pb_dir = mentors_root / "m1" / "playbooks" / "pb1"
    pb_dir.mkdir(parents=True)
    (pb_dir / "playbook.json").write_text("{ not valid json", encoding="utf-8")
    s = _settings(tmp_path, mentors_root)

    assert bootstrap_marketplaces(s) is False


def test_bootstrap_marketplaces_git_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When `git` is not on PATH, the clone is skipped with a warning, not a crash."""
    src = tmp_path / "src-marketplace"
    _make_git_repo(src)
    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "p1@test-mp")
    s = _settings(tmp_path, mentors_root, marketplace_repos={"test-mp": f"file://{src}"})

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert bootstrap_marketplaces(s) is False
    assert not (s.marketplaces_root / "test-mp").exists()


def test_bootstrap_ammp_dir_runs_marketplace_step(tmp_path: Path) -> None:
    """`bootstrap_ammp_dir` scaffolds the tree AND clones referenced marketplaces."""
    src = tmp_path / "src-marketplace"
    _make_git_repo(src)
    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "p1@test-mp")
    s = _settings(tmp_path, mentors_root, marketplace_repos={"test-mp": f"file://{src}"})

    # First call: writes config.env + clones the marketplace.
    assert bootstrap_ammp_dir(s, quiet=True) is True
    assert (s.ammp_dir / "config.env").is_file()
    assert (s.marketplaces_root / "test-mp" / "MARKER.md").is_file()

    # Second call: fully set up → nothing to do.
    assert bootstrap_ammp_dir(s, quiet=True) is False
