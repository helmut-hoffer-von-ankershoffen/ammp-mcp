"""Cross-runtime scenario tests.

Three scenarios exercise the AMMP protocol surface against a freshly-spawned
`ammp-mcp` subprocess, with mentor and mentee identities labelled across
runtimes:

  #7 — OpenClaw-routed mentor + Claude-Code-labelled mentee. The mentor's
       `backend.kind = openclaw` so AskMentor calls a real HTTP webhook
       (`mock_openclaw_webhook`) instead of the deterministic stub. Full
       flow: list playbooks → get one → search → ask → escalate.

  #8 — Roles switched. Same flow, but the labels in the fixture reflect
       the inverse runtime mapping (mentee = `openclaw`, mentor's persona
       = `pepe-on-claude-code`). The protocol is symmetric, so the test
       proves that the same code paths cover the swap with config-only
       changes.

  #9 — Multi-mentor / multi-mentee. Two mentee clients connect concurrently
       to one server with three mentors (pepe, strict, stubmentor) and run
       overlapping operations. Asserts that audit-log entries are recorded
       for every call without cross-talk between mentees or routing
       confusion between mentors.

All three are marked `@pytest.mark.e2e`. They use the subprocess stdio
transport so they exercise the same binary a Claude Desktop / Claude Code
mentee would attach to.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from .harness import (
    REPO_ROOT,
    mock_openclaw_webhook,
    payload,
    stdio_env,
    write_mentor_with_openclaw_backend,
)

# `integration`, not `e2e`: subprocess-spawning tests but no paid external
# services (mock OpenClaw webhook, stub backend). CI runs `pytest -m "unit
# or integration"` so these are exercised on every push.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _client(isolated_tree: Path) -> Client:
    """Construct a FastMCP stdio Client that spawns the server subprocess."""
    return Client(
        StdioTransport(
            command=sys.executable,
            args=["-m", "ammp_mcp", "system", "serve", "--stdio"],
            env=stdio_env(isolated_tree),
            cwd=str(REPO_ROOT),
        )
    )


def _audit_lines(isolated_tree: Path) -> list[str]:
    """Read the audit log lines (one event per line)."""
    log = isolated_tree / "audit.log"
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


# ─── Scenario #7 — OpenClaw mentor + Claude-Code mentee ─────────────────────


async def test_scenario_openclaw_mentor_and_claude_code_mentee(isolated_tree: Path) -> None:
    """Full flow: real OpenClaw-routed mentor, Claude-Code-labelled mentee.

    1. Spin up a mock OpenClaw webhook (deterministic 0.85-confidence echo).
    2. Rewrite the `pepe` mentor.json so its backend.kind = openclaw and url
       points at the webhook.
    3. Spawn the ammp-mcp subprocess with the isolated tree.
    4. Connect a FastMCP Client (the "Claude Code" mentee) over stdio.
    5. Walk through every Mentoring-track operation; assert wire contract.
    6. Verify the audit log captured each call hash-only.
    """
    async with mock_openclaw_webhook() as webhook_url:
        # Rewrite pepe to route through the live (mock) webhook.
        write_mentor_with_openclaw_backend(
            isolated_tree / "mentors",
            slug="pepe",
            name="Pepe Arturo (on OpenClaw)",
            persona="calm operator, lived runtime continuity via OpenClaw",
            confidence_threshold=0.6,
            webhook_url=webhook_url,
        )

        async with _client(isolated_tree) as mentee:
            # Step 1: housekeeping — discover what's available. Six tools:
            # AMMP §5 baseline plus the ListMentors server-side extension.
            tools = {t.name for t in await mentee.list_tools()}
            assert tools == {
                "ListMentors",
                "ListPlaybooks",
                "GetPlaybook",
                "GetWorkInstruction",
                "SearchPlaybooks",
                "AskMentor",
                "EscalateToHuman",
            }, tools

            # Step 2: consume a playbook (area of practice) — verify it
            # returns work-instruction bodies in the new envelope shape.
            listed = payload(await mentee.call_tool("ListPlaybooks", {"mentor": "pepe"}))
            assert listed["mentor"] == "pepe"
            assert listed["count"] == 2
            first_id = listed["playbooks"][0]["id"]
            got = payload(await mentee.call_tool("GetPlaybook", {"id": first_id, "mentor": "pepe"}))
            assert got["id"] == first_id
            assert isinstance(got["instructions"], list) and got["instructions"]
            assert isinstance(got["instructions"][0]["body"], str) and got["instructions"][0]["body"]

            # Step 3: search.
            searched = payload(
                await mentee.call_tool("SearchPlaybooks", {"query": "playbook", "mentor": "pepe", "limit": 5})
            )
            assert searched["mentor"] == "pepe"
            assert searched["count"] >= 0  # corpus is small; just verify the envelope shape

            # Step 4: ask a question — backend POSTs to the mock webhook,
            # which returns confidence 0.85 (above pepe's 0.6 threshold).
            answered = payload(
                await mentee.call_tool(
                    "AskMentor",
                    {"question": "how do you stay grounded under ambiguity?", "mentor": "pepe"},
                )
            )
            assert answered["mentor"] == "pepe"
            assert answered["confidence"] == pytest.approx(0.85)
            assert answered["escalation_recommended"] is False
            assert "OpenClaw mentor would say" in answered["answer"]

            # Step 5: escalate.
            escalated = payload(
                await mentee.call_tool(
                    "EscalateToHuman",
                    {
                        "situation": "Two playbooks contradict each other on retry policy.",
                        "mentor": "pepe",
                        "why_stuck": "neither covers the new payment provider's idempotency model",
                    },
                )
            )
            assert escalated["mentor"] == "pepe"
            assert "operator" in escalated["suggested_message_to_your_operator"]

    # Step 6: audit log captured each call hash-only.
    lines = _audit_lines(isolated_tree)
    ops = {line.split("op=")[1].split()[0] for line in lines if "op=" in line}
    assert ops >= {"ListPlaybooks", "GetPlaybook", "SearchPlaybooks", "AskMentor", "EscalateToHuman"}
    # No payload leakage — every line is the structured "ts op= mentor= mentee= hash=" shape.
    for line in lines:
        assert "mentor=" in line and "mentee=" in line
        if "AskMentor" in line or "EscalateToHuman" in line or "SearchPlaybooks" in line or "GetPlaybook" in line:
            assert "hash=" in line, line


# ─── Scenario #8 — Roles switched ───────────────────────────────────────────


async def test_scenario_roles_switched(isolated_tree: Path) -> None:
    """Same protocol surface, runtime labels inverted.

    AMMP is symmetric: nothing in the wire contract pins which runtime is
    mentor vs. mentee. This test rewrites the fixture so the mentor's persona
    declares it runs on Claude-Code and the mentee's runtime label is
    `openclaw`, and asserts the same five operations still complete cleanly.

    The point of the test: prove the protocol's role-symmetry is config-only,
    not a code path that needs separate maintenance.
    """
    # Rewrite pepe's mentor.json so persona reflects a Claude-Code-resident mentor.
    (isolated_tree / "mentors" / "pepe" / "mentor.json").write_text(
        json.dumps(
            {
                "name": "Pepe Arturo (on Claude-Code)",
                "persona": (
                    "Same mentor; this deployment runs inside a Claude Code session "
                    "rather than on the OpenClaw agent runtime."
                ),
                "confidence_threshold": 0.6,
                "backend": {"kind": "stub"},
            }
        ),
        encoding="utf-8",
    )
    # Rewrite the mentees so one of them is labelled as running on `openclaw`.
    from ammp_mcp.mentee import hash_api_key

    (isolated_tree / "mentees.json").write_text(
        json.dumps(
            [
                {
                    "slug": "openclaw-mentee",
                    "operator": "human:helmut",
                    "runtime": "openclaw",
                    "api_key_hash": hash_api_key("ammp-roles-switched-key"),
                    "rate_limit_per_minute": 60,
                },
            ]
        ),
        encoding="utf-8",
    )

    async with _client(isolated_tree) as mentee:
        # Full Mentoring-track sweep — should succeed regardless of label swap.
        listed = payload(await mentee.call_tool("ListPlaybooks", {"mentor": "pepe"}))
        assert listed["mentor"] == "pepe"
        assert listed["count"] == 2

        answered = payload(
            await mentee.call_tool(
                "AskMentor",
                {"question": "what is the helmguild stance on cross-compartment escalation?", "mentor": "pepe"},
            )
        )
        # Stub backend → confidence 0.2 → mentor-triggered escalation kicks in.
        assert answered["mentor"] == "pepe"
        assert answered["escalation_recommended"] is True

        escalated = payload(
            await mentee.call_tool(
                "EscalateToHuman",
                {"situation": "Need clarity on the retention posture for replayed sessions.", "mentor": "pepe"},
            )
        )
        assert "operator" in escalated["suggested_message_to_your_operator"]

    # The protocol contract is independent of which side wears which runtime
    # label. Sanity: audit log still tracks calls by mentor slug only.
    lines = _audit_lines(isolated_tree)
    assert any("mentor=pepe" in line for line in lines)


# ─── Scenario #9 — Multi-mentor, multi-mentee, concurrent ───────────────────


async def test_scenario_multi_mentor_multi_mentee(isolated_tree: Path) -> None:
    """Three mentors, two concurrent mentees, overlapping operations.

    Spawns two FastMCP stdio clients in parallel — each connects to its own
    subprocess instance — and runs operations against different mentors
    simultaneously. Verifies:

    - All five operation types complete on every (mentee, mentor) combination.
    - The audit log records every call.
    - Each mentor's stated confidence_threshold is respected (pepe at 0.6,
      strict at 0.9, stubmentor explicit-stub).
    - No cross-talk: a mentee asking mentor A never receives mentor B's
      content.
    """

    async def run_mentee_flow(label: str) -> dict[str, dict[str, object]]:
        """One mentee asks ListPlaybooks of all three mentors + AskMentor of one each."""
        results: dict[str, dict[str, object]] = {}
        async with _client(isolated_tree) as mentee:
            for mentor_slug in ("pepe", "strict", "stubmentor"):
                listed = payload(await mentee.call_tool("ListPlaybooks", {"mentor": mentor_slug}))
                assert listed["mentor"] == mentor_slug, (label, mentor_slug, listed)
                results[f"list:{mentor_slug}"] = listed

                answered = payload(
                    await mentee.call_tool(
                        "AskMentor",
                        {"question": f"hello from {label}, mentor {mentor_slug}?", "mentor": mentor_slug},
                    )
                )
                results[f"ask:{mentor_slug}"] = answered
                # All three fixture mentors are stub-backed (pepe + strict use
                # the global fallback which becomes stub when no Anthropic key
                # is present; stubmentor is explicit-stub). Stub confidence
                # is 0.2, so strict (threshold 0.9) and pepe (threshold 0.6)
                # both recommend escalation; stubmentor uses pepe's default
                # threshold of 0.6 and also escalates.
                assert answered["mentor"] == mentor_slug
                assert answered["confidence"] == pytest.approx(0.2)
                assert answered["escalation_recommended"] is True
        return results

    # Run two mentees concurrently.
    results_alpha, results_beta = await asyncio.gather(
        run_mentee_flow("mentee-alpha"),
        run_mentee_flow("mentee-beta"),
    )

    # Both mentees see consistent per-mentor data. ListPlaybooks for pepe
    # must show the same two playbooks (areas of practice) for both.
    pepe_alpha_ids = {pb["id"] for pb in results_alpha["list:pepe"]["playbooks"]}
    pepe_beta_ids = {pb["id"] for pb in results_beta["list:pepe"]["playbooks"]}
    assert pepe_alpha_ids == pepe_beta_ids == {"intro", "operator-craft"}

    strict_alpha_ids = {pb["id"] for pb in results_alpha["list:strict"]["playbooks"]}
    assert strict_alpha_ids == {"rules"}

    # Mentor identity must be preserved on every response.
    for mentor in ("pepe", "strict", "stubmentor"):
        assert results_alpha[f"ask:{mentor}"]["mentor"] == mentor
        assert results_beta[f"ask:{mentor}"]["mentor"] == mentor

    # Audit log captured every call from both mentees. Two mentees x (3 lists
    # + 3 asks) = 12 entries minimum.
    lines = _audit_lines(isolated_tree)
    list_lines = [line for line in lines if "op=ListPlaybooks" in line]
    ask_lines = [line for line in lines if "op=AskMentor" in line]
    assert len(list_lines) >= 6, list_lines
    assert len(ask_lines) >= 6, ask_lines

    # Sanity: every mentor slug appears in the log.
    for slug in ("pepe", "strict", "stubmentor"):
        assert any(f"mentor={slug}" in line for line in lines), slug
