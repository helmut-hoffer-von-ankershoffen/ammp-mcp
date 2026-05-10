"""End-to-end tests against real external services.

Skipped by default. Run with:
    pytest -m e2e -- requires AMMP_ANTHROPIC_API_KEY in env

These exercise the actual Anthropic Messages API, so they cost money and
take seconds rather than milliseconds. Keep them small and assertion-rich
about the protocol contract, not the model's exact wording.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastmcp import Client

from ammp_mcp.server import create_server
from ammp_mcp.settings import Settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("AMMP_ANTHROPIC_API_KEY"),
        reason="AMMP_ANTHROPIC_API_KEY not set",
    ),
]


@pytest.fixture
def live_settings(isolated_tree: Path) -> Settings:
    return Settings(
        mentors_root=isolated_tree / "mentors",
        mentees_file=isolated_tree / "mentees.json",
        audit_log_path=isolated_tree / "audit.log",
        require_auth=False,
        anthropic_api_key=os.environ["AMMP_ANTHROPIC_API_KEY"],
        public_url="http://test.invalid",
    )


async def test_ask_mentor_against_real_claude(live_settings: Settings) -> None:
    server = create_server(live_settings)
    async with Client(server) as c:
        result = await c.call_tool(
            "AskMentor",
            {
                "question": "Should I retry an OAuth callback that times out, and how often?",
                "mentor": "pepe",
            },
        )
    assert result.data["mentor"] == "pepe"
    assert result.data["answer"]
    assert 0.0 <= result.data["confidence"] <= 1.0
    # The corpus directly covers OAuth callbacks, so confidence should be respectable.
    # We don't pin it tightly — the model varies — just sanity-check the shape.
