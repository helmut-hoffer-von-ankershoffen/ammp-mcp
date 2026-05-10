from __future__ import annotations

from pathlib import Path

import pytest

from ammp_mcp.playbook import (
    keyword_rank,
    load_corpus,
    safe_id,
    search,
)

pytestmark = pytest.mark.unit


def test_load_corpus_excludes_readme(isolated_tree: Path) -> None:
    corpus = load_corpus(isolated_tree / "mentors" / "pepe" / "playbooks")
    ids = {pb.id for pb in corpus}
    assert "readme" not in ids
    assert {"intro", "auth"} <= ids


def test_load_corpus_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_corpus(tmp_path / "nope") == []


def test_load_corpus_extracts_title_and_summary(isolated_tree: Path) -> None:
    corpus = load_corpus(isolated_tree / "mentors" / "pepe" / "playbooks")
    intro = next(pb for pb in corpus if pb.id == "intro")
    assert intro.title == "Welcome to Pepe's Playbooks"
    assert "operational" in intro.summary.lower()


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


def test_search_ranks_by_match_count(isolated_tree: Path) -> None:
    corpus = load_corpus(isolated_tree / "mentors" / "pepe" / "playbooks")
    rows = search(corpus, "playbook", limit=5)
    assert rows
    assert all(rank >= 1 for _, rank, _ in rows)


def test_search_empty_query_returns_empty(isolated_tree: Path) -> None:
    corpus = load_corpus(isolated_tree / "mentors" / "pepe" / "playbooks")
    assert search(corpus, "") == []
    assert search(corpus, "   ") == []


def test_keyword_rank_strips_stopwords(isolated_tree: Path) -> None:
    corpus = load_corpus(isolated_tree / "mentors" / "pepe" / "playbooks")
    rows = keyword_rank(corpus, "what should I do about the OAuth callback?")
    assert rows
    assert rows[0][0].id == "auth"
