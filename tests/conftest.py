"""Shared fixtures.

Builds a fully-isolated mentor+mentee tree per test under a tmp_path so unit
and integration tests never touch the real `mentors/` or `mentees.json`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from ammp_mcp import audit
from ammp_mcp import settings as settings_module
from ammp_mcp.mentee import hash_api_key
from ammp_mcp.settings import Settings


@pytest.fixture
def isolated_tree(tmp_path: Path) -> Path:
    """Lay down a tiny but realistic mentors/ + mentees.json under tmp_path.

    Two mentors so multi-mentor routing tests have something to compare
    against; one with a confidence threshold of 0.6, one at 0.9 so we can
    exercise mentor-triggered escalation."""
    mentors_root = tmp_path / "mentors"
    # Pepe: two playbooks (areas of practice). The first has two work
    # instructions, the second one — exercises multi-playbook + multi-
    # instruction routing.
    pepe_intro = mentors_root / "pepe" / "playbooks" / "intro"
    pepe_intro.mkdir(parents=True)
    (pepe_intro / "playbook.json").write_text(
        json.dumps({"name": "Welcome to Pepe", "description": "Onboarding for new mentees."}, ensure_ascii=False),
        encoding="utf-8",
    )
    (pepe_intro / "intro.md").write_text(
        "# Welcome to Pepe's Playbooks\n\n"
        "Calm, operational tone — no emojis, no filler.\n\n"
        "## When to escalate\n"
        "If you cannot reach grounded coverage, escalate to your operator.\n",
        encoding="utf-8",
    )
    (pepe_intro / "auth.md").write_text(
        "# OAuth callback resilience\n\nTreat the callback as an unreliable handoff. Idempotent retries.\n",
        encoding="utf-8",
    )
    (pepe_intro / "README.md").write_text(
        "# Index — not an instruction\n\nThis file is excluded from the corpus.\n",
        encoding="utf-8",
    )
    pepe_craft = mentors_root / "pepe" / "playbooks" / "operator-craft"
    pepe_craft.mkdir(parents=True)
    (pepe_craft / "playbook.json").write_text(
        json.dumps(
            {"name": "Operator craft", "description": "Cross-cutting craft principles."},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pepe_craft / "verify.md").write_text(
        "# Verify before claiming done\n\nDone means verified done.\n",
        encoding="utf-8",
    )
    (mentors_root / "pepe" / "mentor.json").write_text(
        json.dumps(
            {
                "name": "Pepe Arturo",
                "description": "Calm, grounded mentor for resilient agent work.",
                "profile_url": "https://www.helmguild.com/pepe-arturo-ai/",
                "human_mentor": {
                    "name": "Helmut Hoffer von Ankershoffen",
                    "url": "https://helmut.hoffer-von-ankershoffen.me/",
                    "contact": "helmuthva@gmail.com",
                },
                "persona": "calm operator",
                "confidence_threshold": 0.6,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # Drop a tiny avatar so the /mentors/<slug>/avatar route has something
    # to serve in tests. 1x1 transparent PNG (smallest valid PNG).
    _avatar_png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
    )
    (mentors_root / "pepe" / "avatar.png").write_bytes(_avatar_png)

    # Strict mentor: one playbook with one instruction.
    strict_pb = mentors_root / "strict" / "playbooks" / "rules"
    strict_pb.mkdir(parents=True)
    (strict_pb / "playbook.json").write_text(
        json.dumps({"name": "Strict rules", "description": "High-bar review rules."}, ensure_ascii=False),
        encoding="utf-8",
    )
    (strict_pb / "rule.md").write_text(
        "# Strict Mentor\n\nHigh confidence threshold. Escalates often.\n",
        encoding="utf-8",
    )
    (mentors_root / "strict" / "mentor.json").write_text(
        json.dumps(
            {
                "name": "Strict Mentor",
                "description": "High-bar reviewer who escalates whenever the evidence is thin.",
                "persona": "high-bar operator",
                "confidence_threshold": 0.9,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Third mentor — exercises the explicit stub-backend config path so
    # integration tests can confirm multi-mentor routing with mixed
    # backend kinds.
    stub_pb = mentors_root / "stubmentor" / "playbooks" / "anything"
    stub_pb.mkdir(parents=True)
    (stub_pb / "playbook.json").write_text(
        json.dumps({"name": "Anything", "description": "Stub-backed playbook."}, ensure_ascii=False),
        encoding="utf-8",
    )
    (stub_pb / "any.md").write_text("# Any\n\nstub-backed mentor for tests.\n", encoding="utf-8")
    (mentors_root / "stubmentor" / "mentor.json").write_text(
        json.dumps(
            {
                "name": "Stub Mentor",
                "persona": "deterministic",
                "backend": {"kind": "stub"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    mentees_file = tmp_path / "mentees.json"
    mentees_file.write_text(
        json.dumps(
            [
                {
                    "slug": "claude-cowork-sandra",
                    "operator": "human:sandra",
                    "runtime": "claude-cowork",
                    "api_key_hash": hash_api_key("ammp-test-key-1"),
                    "rate_limit_per_minute": 60,
                },
                {
                    "slug": "claude-code-sandra",
                    "operator": "human:sandra",
                    "runtime": "claude-code",
                    "api_key_hash": hash_api_key("ammp-test-key-2"),
                    "rate_limit_per_minute": 60,
                },
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def settings(isolated_tree: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Build a Settings bound to the isolated tree and clear the singleton."""
    from ammp_mcp.server import _mentees_cache_reset_for_testing

    settings_module.reset_settings_for_testing()
    audit.reset_salt_for_testing()
    _mentees_cache_reset_for_testing()
    monkeypatch.delenv("AMMP_ANTHROPIC_API_KEY", raising=False)
    # `_env_file=None` disables loading from ~/.ammp/config.env or .env so
    # tests stay hermetic regardless of what's on the dev machine.
    s = Settings(
        _env_file=None,
        ammp_dir=isolated_tree,
        mentors_root=isolated_tree / "mentors",
        mentees_file=isolated_tree / "mentees.json",
        audit_log_path=isolated_tree / "audit.log",
        require_auth=False,
        anthropic_api_key=None,
        public_url="http://test.invalid",
        # Fixture's primary mentor is `pepe` (see isolated_tree); pin
        # explicitly because the package default flipped to `example`.
        default_mentor="pepe",
    )
    yield s
    settings_module.reset_settings_for_testing()
