from __future__ import annotations

from pathlib import Path

import pytest

from ammp_mcp.registries import (
    find_mentee_by_api_key,
    get_mentor,
    hash_api_key,
    load_mentees,
    load_mentors,
    save_mentees,
)

pytestmark = pytest.mark.unit


def test_load_mentors_discovers_subdirs(isolated_tree: Path) -> None:
    mentors = load_mentors(isolated_tree / "mentors")
    assert set(mentors) == {"pepe", "strict", "stubmentor"}
    assert mentors["pepe"].name == "Pepe Arturo"
    assert mentors["strict"].confidence_threshold == pytest.approx(0.9)


def test_load_mentors_missing_root_returns_empty(tmp_path: Path) -> None:
    assert load_mentors(tmp_path / "nope") == {}


def test_get_mentor_falls_back_to_default(isolated_tree: Path) -> None:
    mentors = load_mentors(isolated_tree / "mentors")
    assert get_mentor(mentors, "", "pepe") is mentors["pepe"]
    assert get_mentor(mentors, None, "pepe") is mentors["pepe"]


def test_get_mentor_unknown_returns_none(isolated_tree: Path) -> None:
    mentors = load_mentors(isolated_tree / "mentors")
    assert get_mentor(mentors, "ghost", "pepe") is None


def test_hash_api_key_is_deterministic() -> None:
    assert hash_api_key("abc") == hash_api_key("abc")
    assert hash_api_key("abc") != hash_api_key("abd")


def test_find_mentee_by_api_key_resolves(isolated_tree: Path) -> None:
    mentees = load_mentees(isolated_tree / "mentees.json")
    found = find_mentee_by_api_key(mentees, "ammp-test-key-1")
    assert found is not None
    assert found.slug == "claude-cowork-sandra"


def test_find_mentee_by_api_key_rejects_wrong_key(isolated_tree: Path) -> None:
    mentees = load_mentees(isolated_tree / "mentees.json")
    assert find_mentee_by_api_key(mentees, "wrong") is None


def test_save_and_reload_round_trip(isolated_tree: Path) -> None:
    mentees = load_mentees(isolated_tree / "mentees.json")
    out = isolated_tree / "out.json"
    save_mentees(out, mentees)
    again = load_mentees(out)
    assert set(mentees) == set(again)
    assert mentees["claude-cowork-sandra"].api_key_hash == again["claude-cowork-sandra"].api_key_hash


def test_load_mentees_rejects_non_list(tmp_path: Path) -> None:
    bad = tmp_path / "mentees.json"
    bad.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON list"):
        load_mentees(bad)
