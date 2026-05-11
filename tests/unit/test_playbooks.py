from __future__ import annotations

from pathlib import Path

import pytest

from ammp_mcp.playbook import (
    flatten_instructions,
    keyword_rank,
    load_playbooks,
    safe_id,
    search,
)

pytestmark = pytest.mark.unit


def test_load_playbooks_picks_up_each_subdir(isolated_tree: Path) -> None:
    corpus = load_playbooks(isolated_tree / "mentors" / "pepe" / "playbooks")
    ids = {pb.id for pb in corpus}
    assert ids == {"intro", "operator-craft"}


def test_load_playbooks_excludes_readme_in_instructions(isolated_tree: Path) -> None:
    corpus = load_playbooks(isolated_tree / "mentors" / "pepe" / "playbooks")
    intro = next(pb for pb in corpus if pb.id == "intro")
    instr_ids = {wi.id for wi in intro.instructions}
    assert "readme" not in instr_ids
    assert {"intro", "auth"} <= instr_ids


def test_load_playbooks_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_playbooks(tmp_path / "nope") == []


def test_load_playbook_reads_metadata(isolated_tree: Path) -> None:
    corpus = load_playbooks(isolated_tree / "mentors" / "pepe" / "playbooks")
    intro = next(pb for pb in corpus if pb.id == "intro")
    assert intro.name == "Welcome to Pepe"
    assert intro.description == "Onboarding for new mentees."


def test_load_instruction_extracts_title_and_summary(isolated_tree: Path) -> None:
    corpus = load_playbooks(isolated_tree / "mentors" / "pepe" / "playbooks")
    intro = next(pb for pb in corpus if pb.id == "intro")
    intro_wi = next(wi for wi in intro.instructions if wi.id == "intro")
    assert intro_wi.title == "Welcome to Pepe's Playbooks"
    assert "operational" in intro_wi.summary.lower()
    # Every instruction knows which playbook it belongs to.
    assert intro_wi.playbook_id == "intro"


def test_load_playbooks_skips_dir_without_playbook_json(tmp_path: Path) -> None:
    """A subdir without playbook.json is not a playbook — skipped, not crashed."""
    root = tmp_path / "playbooks"
    valid = root / "ok"
    valid.mkdir(parents=True)
    (valid / "playbook.json").write_text('{"name": "OK", "description": ""}', encoding="utf-8")
    (valid / "i.md").write_text("# i\n\nbody\n", encoding="utf-8")
    # Subdir without playbook.json — should be skipped (warning), not crash.
    invalid = root / "missing-meta"
    invalid.mkdir()
    (invalid / "x.md").write_text("# x\n\nbody\n", encoding="utf-8")
    corpus = load_playbooks(root)
    assert [pb.id for pb in corpus] == ["ok"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("intro", "intro"),
        ("  auth  ", "auth"),
        ("../etc/passwd", None),
        ("foo\\bar", None),
        (".secret", None),
        ("", None),
        ("a" * 200, None),
    ],
)
def test_safe_id_validation(raw: str, expected: str | None) -> None:
    assert safe_id(raw) == expected


def test_search_returns_work_instructions_with_playbook_id(isolated_tree: Path) -> None:
    corpus = load_playbooks(isolated_tree / "mentors" / "pepe" / "playbooks")
    rows = search(corpus, "playbook", limit=5)
    assert rows
    for wi, rank, _ in rows:
        assert rank >= 1
        # Each search hit knows its parent playbook.
        assert wi.playbook_id


def test_search_empty_query_returns_empty(isolated_tree: Path) -> None:
    corpus = load_playbooks(isolated_tree / "mentors" / "pepe" / "playbooks")
    assert search(corpus, "") == []
    assert search(corpus, "   ") == []


def test_keyword_rank_strips_stopwords_returns_instructions(isolated_tree: Path) -> None:
    corpus = load_playbooks(isolated_tree / "mentors" / "pepe" / "playbooks")
    rows = keyword_rank(corpus, "what should I do about the OAuth callback?")
    assert rows
    # The "auth" instruction lives in the "intro" playbook in the fixture.
    assert rows[0][0].id == "auth"


def test_flatten_instructions(isolated_tree: Path) -> None:
    corpus = load_playbooks(isolated_tree / "mentors" / "pepe" / "playbooks")
    flat = flatten_instructions(corpus)
    # pepe has 2 playbooks: intro (2 instructions) + operator-craft (1).
    assert len(flat) == 3
