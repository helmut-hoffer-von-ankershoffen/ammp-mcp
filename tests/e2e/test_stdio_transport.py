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

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _stdio_env(isolated_tree: Path) -> dict[str, str]:
    """Build a clean env that points the subprocess at the test fixtures."""
    return {
        **os.environ,
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
        # over stdio must match the HTTP wire surface — five Mentoring ops.
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "ListPlaybooks",
            "GetPlaybook",
            "SearchPlaybooks",
            "AskMentor",
            "EscalateToHuman",
        }, names

        # ListPlaybooks — pepe has two playbooks under the fixture corpus.
        listed = _payload(await client.call_tool("ListPlaybooks", {"mentor": "pepe"}))
        assert listed["mentor"] == "pepe"
        assert listed["count"] == 2
        ids = {pb["id"] for pb in listed["playbooks"]}
        assert ids == {"auth", "intro"}, ids

        # GetPlaybook — single doc body.
        got = _payload(await client.call_tool("GetPlaybook", {"id": "intro", "mentor": "pepe"}))
        assert got["id"] == "intro"
        assert "Welcome to Pepe" in got["body"]

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
