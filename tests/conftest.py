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
    pepe = mentors_root / "pepe" / "playbooks"
    pepe.mkdir(parents=True)
    (pepe / "intro.md").write_text(
        "# Welcome to Pepe's Playbooks\n\n"
        "Calm, operational tone — no emojis, no filler.\n\n"
        "## When to escalate\n"
        "If you cannot reach grounded coverage, escalate to your operator.\n",
        encoding="utf-8",
    )
    (pepe / "auth.md").write_text(
        "# OAuth callback resilience\n\nTreat the callback as an unreliable handoff. Idempotent retries.\n",
        encoding="utf-8",
    )
    (pepe / "README.md").write_text(
        "# Index — not a playbook\n\nThis file is excluded from the corpus.\n",
        encoding="utf-8",
    )
    (mentors_root / "pepe" / "mentor.json").write_text(
        json.dumps(
            {
                "name": "Pepe Arturo",
                "persona": "calm operator",
                "confidence_threshold": 0.6,
            }
        ),
        encoding="utf-8",
    )

    strict = mentors_root / "strict" / "playbooks"
    strict.mkdir(parents=True)
    (strict / "rule.md").write_text(
        "# Strict Mentor\n\nHigh confidence threshold. Escalates often.\n",
        encoding="utf-8",
    )
    (mentors_root / "strict" / "mentor.json").write_text(
        json.dumps(
            {
                "name": "Strict Mentor",
                "persona": "high-bar operator",
                "confidence_threshold": 0.9,
            }
        ),
        encoding="utf-8",
    )

    # Third mentor — exercises the explicit stub-backend config path so
    # integration tests can confirm multi-mentor routing with mixed
    # backend kinds.
    stubmentor = mentors_root / "stubmentor" / "playbooks"
    stubmentor.mkdir(parents=True)
    (stubmentor / "any.md").write_text("# Any\n\nstub-backed mentor for tests.\n", encoding="utf-8")
    (mentors_root / "stubmentor" / "mentor.json").write_text(
        json.dumps(
            {
                "name": "Stub Mentor",
                "persona": "deterministic",
                "backend": {"kind": "stub"},
            }
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
