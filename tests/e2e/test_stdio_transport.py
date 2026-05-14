"""End-to-end test for the stdio MCP transport.

Spawns ``python -m ammp_mcp system serve --stdio`` as a subprocess, connects
via FastMCP's stdio client, exercises the protocol surface (initialize →
tools/list → tools/call ListPlaybooks → GetPlaybook → SearchPlaybooks →
EscalateToHuman), and verifies the responses match the in-process contract
the integration tests already cover.

This is the canonical "does the subprocess transport actually work" test —
the kind a Claude Desktop / Claude Code mentee would exercise on first
connect.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

# Marked `integration` (not `e2e`) because no paid external services are
# touched — the subprocess uses the stub backend with no Anthropic key. CI
# runs `pytest -m "unit or integration"` so these are exercised on every push.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _stdio_env(isolated_tree: Path) -> dict[str, str]:
    """Build a clean env that points the subprocess at the test fixtures."""
    return {
        **os.environ,
        # Pin AMMP_DIR so `ammp serve`'s auto-bootstrap writes config.env
        # under the tmp tree, never into the developer's real `~/.ammp/`.
        "AMMP_DIR": str(isolated_tree),
        "AMMP_MENTORS_ROOT": str(isolated_tree / "mentors"),
        "AMMP_MENTEES_FILE": str(isolated_tree / "mentees.json"),
        "AMMP_AUDIT_LOG_PATH": str(isolated_tree / "audit.log"),
        "AMMP_DEFAULT_MENTOR": "pepe",
        "AMMP_REQUIRE_AUTH": "false",
        # Force stub backend everywhere so AskMentor returns deterministic
        # output without needing a real Anthropic key in CI.
        "AMMP_ANTHROPIC_API_KEY": "",
    }


def _payload(result: object) -> dict[str, object]:
    """Unwrap a FastMCP CallToolResult into the structured dict the tool returned.

    FastMCP wraps tool outputs in a ``CallToolResult`` whose ``data`` attribute
    (when present) holds the parsed model_dump dict, and whose ``content`` list
    holds the JSON-serialised string envelope. Tests want the dict.
    """
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return json.loads(text)
    raise AssertionError(f"could not extract dict payload from {result!r}")


async def test_stdio_transport_round_trip(isolated_tree: Path) -> None:
    """Spawn the server in stdio mode and run every Mentoring-track op."""
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "ammp_mcp", "system", "serve", "--stdio"],
        env=_stdio_env(isolated_tree),
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )

    async with Client(transport) as client:
        # Handshake completes inside __aenter__. The list of tools advertised
        # over stdio must match the HTTP wire surface — five Mentoring §5
        # operations plus the ListMentors + GetSkill server-side extensions.
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "ListMentors",
            "ListPlaybooks",
            "GetPlaybook",
            "GetSkill",
            "SearchPlaybooks",
            "AskMentor",
            "EscalateToHuman",
            "EscalateToHumanMentor",
            "GetEscalation",
            "GetPluginArchive",
            "GetSystemInfo",
        }, names

        # ListPlaybooks — pepe has two playbooks (areas of practice).
        listed = _payload(await client.call_tool("ListPlaybooks", {"mentor": "pepe"}))
        assert listed["mentor"] == "pepe"
        assert listed["count"] == 2
        ids = {pb["id"] for pb in listed["playbooks"]}
        assert ids == {"intro", "operator-craft"}, ids

        # GetPlaybook — returns the playbook with every work-instruction body.
        got = _payload(await client.call_tool("GetPlaybook", {"id": "intro", "mentor": "pepe"}))
        assert got["id"] == "intro"
        wi_ids = {wi["id"] for wi in got["skills"]}
        assert wi_ids == {"intro", "auth"}
        intro_sk = next(wi for wi in got["skills"] if wi["id"] == "intro")
        assert "Welcome to Pepe" in intro_sk["body"]

        # SearchPlaybooks — substring match.
        searched = _payload(
            await client.call_tool(
                "SearchPlaybooks",
                {"query": "escalate", "mentor": "pepe", "limit": 3},
            )
        )
        assert searched["mentor"] == "pepe"
        assert searched["count"] >= 1
        assert any(m["id"] == "intro" for m in searched["matches"])

        # EscalateToHuman — protocol contract: returns suggested phrasing
        # the mentee hands to *its own* operator. The mentor never reaches
        # across compartments (AMMP §3.4).
        escalated = _payload(
            await client.call_tool(
                "EscalateToHuman",
                {
                    "situation": "I cannot reach grounded coverage on this question.",
                    "mentor": "pepe",
                    "why_stuck": "playbooks contradict each other",
                },
            )
        )
        assert escalated["mentor"] == "pepe"
        assert "operator" in escalated["suggested_message_to_your_operator"]
        assert escalated["invariant"] == "Human-Gated Escalation (AMMP §3.4)"

        # AskMentor — stub backend (no Anthropic key) returns confidence 0.2,
        # which is below pepe's 0.6 threshold, so the response must recommend
        # escalation with suggested phrasing.
        answered = _payload(
            await client.call_tool(
                "AskMentor",
                {"question": "how do you handle ambiguous escalations?", "mentor": "pepe"},
            )
        )
        assert answered["mentor"] == "pepe"
        assert 0.0 <= answered["confidence"] <= 1.0
        assert answered["escalation_recommended"] is True
        assert answered["suggested_message_to_your_operator"] is not None


async def test_stdio_transport_rejects_unknown_mentor(isolated_tree: Path) -> None:
    """A bogus mentor slug should come back as a structured in-band error."""
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "ammp_mcp", "system", "serve", "--stdio"],
        env=_stdio_env(isolated_tree),
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )
    async with Client(transport) as client:
        out = _payload(await client.call_tool("ListPlaybooks", {"mentor": "does-not-exist"}))
        assert out.get("error") == "unknown_mentor"


async def test_stdio_transport_rejects_empty_query(isolated_tree: Path) -> None:
    """`SearchPlaybooks` with an empty query → in-band ``empty_query`` error."""
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "ammp_mcp", "system", "serve", "--stdio"],
        env=_stdio_env(isolated_tree),
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )
    async with Client(transport) as client:
        out = _payload(await client.call_tool("SearchPlaybooks", {"query": "   ", "mentor": "pepe"}))
        assert out.get("error") == "empty_query"


async def test_stdio_transport_with_require_auth_rejects_when_no_http_request(isolated_tree: Path) -> None:
    """With ``AMMP_REQUIRE_AUTH=true`` and no HTTP request in scope, every stdio
    call returns ``auth_failed``.

    Stdio mode has no HTTP transport, so there's no ``Authorization``
    header to read; the new auth path reads the Bearer token from the
    HTTP request via FastMCP's request-scoped ContextVar (see
    ``_authenticate``). Pinned here so operators understand the
    contract: production auth requires the HTTP transport. The stdio
    transport is for trusted-parent contexts (Claude Code, Desktop
    subprocess) where the parent controls who can speak to the server
    — turn ``require_auth`` off when running in stdio mode.
    """
    env = {
        **_stdio_env(isolated_tree),
        "AMMP_REQUIRE_AUTH": "true",
    }
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "ammp_mcp", "system", "serve", "--stdio"],
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
    )
    async with Client(transport) as client:
        out = _payload(await client.call_tool("ListPlaybooks", {"mentor": "pepe"}))
        assert out.get("error") == "auth_failed"
        assert "api_key_required" in str(out.get("detail", ""))
