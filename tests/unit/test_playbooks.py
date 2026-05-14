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


# ─── AgentSkills format + plugin-aware loader ─────────────────────────────


def _write_skill_md(skill_dir: Path, body: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_load_skill_md_picks_up_frontmatter_description_and_title(tmp_path: Path) -> None:
    """AgentSkills `skills/<id>/SKILL.md` loads with frontmatter description and metadata.order."""
    root = tmp_path / "playbooks"
    pb = root / "demo"
    pb.mkdir(parents=True)
    (pb / "playbook.json").write_text('{"name": "Demo", "description": "d"}', encoding="utf-8")
    _write_skill_md(
        pb / "skills" / "alpha",
        '---\nname: alpha\ndescription: "Alpha skill summary."\nlicense: CC-BY-4.0\nmetadata:\n  order: 2\n---\n# Alpha skill title\n\nBody.\n',
    )
    _write_skill_md(
        pb / "skills" / "beta",
        '---\nname: beta\ndescription: "Beta summary."\nmetadata:\n  order: 1\n---\n# Beta title\n\nBody.\n',
    )
    corpus = load_playbooks(root)
    pbk = corpus[0]
    # Ordering follows metadata.order (beta=1 before alpha=2).
    assert [sk.id for sk in pbk.instructions] == ["beta", "alpha"]
    alpha = next(sk for sk in pbk.instructions if sk.id == "alpha")
    assert alpha.title == "Alpha skill title"
    assert alpha.summary == "Alpha skill summary."
    assert alpha.order == 2


def test_plugin_backed_playbook_loads_from_marketplace_clone(tmp_path: Path) -> None:
    """A playbook with `plugin: "<n>@<m>"` resolves to the marketplace clone path."""
    root = tmp_path / "playbooks"
    pb = root / "demo"
    pb.mkdir(parents=True)
    (pb / "playbook.json").write_text(
        '{"name": "Demo", "description": "d", "plugin": "demo-plugin@demo-market"}',
        encoding="utf-8",
    )
    mr = tmp_path / "marketplaces"
    plugin_dir = mr / "demo-market" / "plugins" / "demo-plugin"
    _write_skill_md(
        plugin_dir / "skills" / "alpha",
        '---\nname: alpha\ndescription: "From marketplace."\n---\n# From plugin\n\nx\n',
    )
    corpus = load_playbooks(root, marketplaces_root=mr)
    pbk = corpus[0]
    assert pbk.plugin_ref == ("demo-plugin", "demo-market")
    assert [sk.id for sk in pbk.instructions] == ["alpha"]
    assert pbk.instructions[0].summary == "From marketplace."


def test_plugin_ref_with_missing_marketplaces_root_falls_back_to_local(tmp_path: Path) -> None:
    """If `plugin` is set but `marketplaces_root` isn't given, fall back to local skills."""
    root = tmp_path / "playbooks"
    pb = root / "demo"
    pb.mkdir(parents=True)
    (pb / "playbook.json").write_text(
        '{"name": "Demo", "description": "d", "plugin": "p@m"}', encoding="utf-8"
    )
    (pb / "01-local.md").write_text("# Local\n\nbody\n", encoding="utf-8")
    corpus = load_playbooks(root)  # no marketplaces_root
    assert corpus[0].plugin_ref == ("p", "m")
    assert [sk.id for sk in corpus[0].instructions] == ["01-local"]


def test_plugin_ref_pointing_at_missing_dir_falls_back_to_local(tmp_path: Path) -> None:
    """A `plugin:` ref whose marketplace clone doesn't exist falls back to local skills."""
    root = tmp_path / "playbooks"
    pb = root / "demo"
    pb.mkdir(parents=True)
    (pb / "playbook.json").write_text(
        '{"name": "Demo", "description": "d", "plugin": "missing@absent"}', encoding="utf-8"
    )
    (pb / "01-local.md").write_text("# Local\n\nbody\n", encoding="utf-8")
    corpus = load_playbooks(root, marketplaces_root=tmp_path / "marketplaces")
    assert corpus[0].plugin_ref == ("missing", "absent")
    assert [sk.id for sk in corpus[0].instructions] == ["01-local"]


def test_malformed_plugin_ref_is_ignored(tmp_path: Path) -> None:
    """An unparseable `plugin:` value (no `@`) is treated as absent, loader falls back to local."""
    root = tmp_path / "playbooks"
    pb = root / "demo"
    pb.mkdir(parents=True)
    (pb / "playbook.json").write_text(
        '{"name": "Demo", "description": "d", "plugin": "bad-no-marketplace"}',
        encoding="utf-8",
    )
    (pb / "01-local.md").write_text("# Local\n\nbody\n", encoding="utf-8")
    corpus = load_playbooks(root, marketplaces_root=tmp_path / "marketplaces")
    assert corpus[0].plugin_ref is None
    assert [sk.id for sk in corpus[0].instructions] == ["01-local"]


def test_skills_subdir_wins_over_legacy_nn_files(tmp_path: Path) -> None:
    """When both layouts coexist locally, `skills/<id>/SKILL.md` wins over `NN-*.md`."""
    root = tmp_path / "playbooks"
    pb = root / "demo"
    pb.mkdir(parents=True)
    (pb / "playbook.json").write_text('{"name": "Demo", "description": "d"}', encoding="utf-8")
    _write_skill_md(
        pb / "skills" / "alpha",
        '---\nname: alpha\ndescription: "AgentSkills body."\n---\n# Alpha\n\nx\n',
    )
    (pb / "01-legacy.md").write_text("# Legacy\n\nbody\n", encoding="utf-8")
    corpus = load_playbooks(root)
    assert [sk.id for sk in corpus[0].instructions] == ["alpha"]


def test_plugin_with_empty_skills_dir_returns_no_skills(tmp_path: Path) -> None:
    """A plugin clone with an empty `skills/` subdir yields zero skills."""
    root = tmp_path / "playbooks"
    pb = root / "demo"
    pb.mkdir(parents=True)
    (pb / "playbook.json").write_text(
        '{"name": "Demo", "description": "d", "plugin": "p@m"}', encoding="utf-8"
    )
    mr = tmp_path / "marketplaces"
    (mr / "m" / "plugins" / "p" / "skills").mkdir(parents=True)
    corpus = load_playbooks(root, marketplaces_root=mr)
    assert corpus[0].plugin_ref == ("p", "m")
    assert corpus[0].instructions == []


def test_plugin_with_no_skills_subdir_returns_no_skills(tmp_path: Path) -> None:
    """A plugin clone without a `skills/` directory at all yields zero skills (no crash)."""
    root = tmp_path / "playbooks"
    pb = root / "demo"
    pb.mkdir(parents=True)
    (pb / "playbook.json").write_text(
        '{"name": "Demo", "description": "d", "plugin": "p@m"}', encoding="utf-8"
    )
    mr = tmp_path / "marketplaces"
    (mr / "m" / "plugins" / "p").mkdir(parents=True)
    corpus = load_playbooks(root, marketplaces_root=mr)
    assert corpus[0].instructions == []


def test_start_prompt_for_plugin_backed_playbook_mentions_get_plugin_archive(tmp_path: Path) -> None:
    """`_build_mentor_playbook_prompt` with a `plugin_ref` opens with the install step."""
    from ammp_mcp.mentor import Mentor
    from ammp_mcp.server import _build_mentor_playbook_prompt

    m = Mentor(
        slug="pepe",
        name="Pepe Arturo",
        persona="calm",
        playbook_dir=tmp_path,
        mentor_dir=tmp_path,
    )
    prompt = _build_mentor_playbook_prompt(
        public_url="https://mcp.helmguild.com/ammp",
        mentor=m,
        playbook_id="x",
        playbook_name="X",
        playbook_description="desc",
        instruction_count=4,
        plugin_ref=("pepe-x", "helmguild-plugins"),
    )
    # The install step uses GetPluginArchive (not /plugin marketplace add) so
    # mentees can install plugins from private marketplaces.
    assert "GetPluginArchive" in prompt
    assert "pepe-x" in prompt
    assert "/plugin marketplace add" not in prompt
    # The standard 4-step body still follows after the install step.
    assert "ListPlaybooks" in prompt
    assert "GetPlaybook" in prompt
    assert "AskMentor" in prompt
