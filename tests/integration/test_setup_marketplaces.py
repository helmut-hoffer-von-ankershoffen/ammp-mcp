"""Tests for `ammp setup`'s marketplace auto-clone (`bootstrap_marketplaces`).

A fresh host has the mentors corpus (metadata) but not the marketplace
caches that hold the skill bodies. `bootstrap_marketplaces` closes that
gap: it clones every marketplace the corpus references and has a
configured repo URL.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ammp_mcp.settings import Settings
from ammp_mcp.system._setup_service import bootstrap_marketplaces


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
    (pb_dir / "playbook.json").write_text(
        json.dumps({"name": "PB1", "plugin": plugin_ref}), encoding="utf-8"
    )


def test_bootstrap_marketplaces_clones_referenced(tmp_path: Path) -> None:
    """A corpus-referenced marketplace with a configured URL is cloned; idempotent."""
    src = tmp_path / "src-marketplace"
    _make_git_repo(src)

    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "p1@test-mp")

    s = Settings(
        _env_file=None,
        ammp_dir=tmp_path / "ammp",
        mentors_root=mentors_root,
        marketplaces_root=tmp_path / "ammp" / "marketplaces",
        marketplace_repos={"test-mp": f"file://{src}"},
    )

    assert bootstrap_marketplaces(s) is True
    assert (s.marketplaces_root / "test-mp" / "MARKER.md").is_file()

    # Second call: marketplace already on disk → nothing cloned.
    assert bootstrap_marketplaces(s) is False


def test_bootstrap_marketplaces_skips_unmapped(tmp_path: Path) -> None:
    """A referenced marketplace with no configured URL is skipped, not fatal."""
    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "p1@unmapped-mp")

    s = Settings(
        _env_file=None,
        ammp_dir=tmp_path / "ammp",
        mentors_root=mentors_root,
        marketplaces_root=tmp_path / "ammp" / "marketplaces",
        marketplace_repos={},
    )

    assert bootstrap_marketplaces(s) is False
    assert not (s.marketplaces_root / "unmapped-mp").exists()


def test_bootstrap_marketplaces_no_references(tmp_path: Path) -> None:
    """A corpus with no `@marketplace` plugin references clones nothing."""
    mentors_root = tmp_path / "mentors"
    _write_playbook(mentors_root, "")  # no plugin reference

    s = Settings(
        _env_file=None,
        ammp_dir=tmp_path / "ammp",
        mentors_root=mentors_root,
        marketplaces_root=tmp_path / "ammp" / "marketplaces",
    )

    assert bootstrap_marketplaces(s) is False
