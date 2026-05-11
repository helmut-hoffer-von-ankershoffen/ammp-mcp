"""In-memory integration tests for the FastMCP server.

Uses fastmcp.Client(server) — no socket bind, no TLS — so the test exercises
the same code path real MCP clients hit, while running offline. The LLM is
left in stub mode (no API key); AskMentor returns a deterministic low-confidence
answer so we can validate the mentor-triggered escalation path without a
network round-trip.
"""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from ammp_mcp.server import create_server
from ammp_mcp.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def server(settings: Settings):
    return create_server(settings)


async def test_list_mentors_returns_all_mentors(server) -> None:
    """Server-side extension over AMMP-01: a mentee can discover slugs without an out-of-band capability fetch."""
    async with Client(server) as c:
        result = await c.call_tool("ListMentors", {})
    assert result.data["count"] == 3
    assert result.data["default_mentor"] == "pepe"
    by_slug = {m["slug"]: m for m in result.data["mentors"]}
    assert set(by_slug) == {"pepe", "strict", "stubmentor"}
    # Pepe is the configured default (per conftest fixture).
    assert by_slug["pepe"]["is_default"] is True
    assert by_slug["strict"]["is_default"] is False
    # Pepe's confidence threshold matches the fixture's mentor.json.
    assert by_slug["pepe"]["confidence_threshold"] == pytest.approx(0.6)
    assert by_slug["strict"]["confidence_threshold"] == pytest.approx(0.9)
    # Every mentor advertises its config kind — the same value the operator
    # wrote in mentor.json (`anthropic`/`openclaw`/`stub`). Mentors with no
    # backend block fall through to the global Anthropic fallback.
    for m in result.data["mentors"]:
        assert m["backend_kind"] in {"anthropic", "openclaw", "stub"}, m
    by_slug = {m["slug"]: m for m in result.data["mentors"]}
    # `stubmentor` declares `backend.kind = stub`. `pepe` and `strict` have
    # no backend block, so they report the global fallback (`anthropic`).
    assert by_slug["stubmentor"]["backend_kind"] == "stub"
    assert by_slug["pepe"]["backend_kind"] == "anthropic"


async def test_list_mentors_advertised_in_capability(server) -> None:
    """`ListMentors` shows up in the capability JSON's `operations` array."""
    async with Client(server) as c:
        # Tool surface includes the new tool.
        tools = await c.list_tools()
        assert "ListMentors" in {t.name for t in tools}


async def test_list_playbooks_default_mentor(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {})
    assert result.data["mentor"] == "pepe"
    assert result.data["count"] == 2
    ids = {p["id"] for p in result.data["playbooks"]}
    assert ids == {"intro", "auth"}
    assert "readme" not in ids


async def test_list_playbooks_explicit_mentor(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {"mentor": "strict"})
    assert result.data["mentor"] == "strict"
    assert result.data["count"] == 1


async def test_list_playbooks_unknown_mentor(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {"mentor": "ghost"})
    assert result.data["error"] == "unknown_mentor"


async def test_get_playbook_returns_body(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "intro"})
    assert result.data["id"] == "intro"
    assert "Welcome" in result.data["body"]


async def test_get_playbook_path_traversal_rejected(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "../etc/passwd"})
    assert result.data["error"] == "invalid_id"


async def test_get_playbook_not_found(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "nope"})
    assert result.data["error"] == "not_found"


async def test_search_returns_ranked_matches(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("SearchPlaybooks", {"query": "playbook"})
    assert result.data["count"] >= 1
    assert all("rank" in m for m in result.data["matches"])


async def test_search_empty_query_rejected(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("SearchPlaybooks", {"query": "  "})
    assert result.data["error"] == "empty_query"


async def test_ask_mentor_stub_triggers_escalation(server) -> None:
    """LLM stub returns confidence 0.2 < threshold 0.6 → mentor-triggered escalation."""
    async with Client(server) as c:
        result = await c.call_tool(
            "AskMentor",
            {"question": "How should I handle an OAuth callback that times out?"},
        )
    assert result.data["mentor"] == "pepe"
    assert result.data["escalation_recommended"] is True
    assert result.data["suggested_message_to_your_operator"] is not None
    assert "operator" in result.data["suggested_message_to_your_operator"].lower()
    assert result.data["confidence"] < 0.6


async def test_ask_mentor_empty_question_rejected(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("AskMentor", {"question": "  "})
    assert result.data["error"] == "empty_question"


async def test_escalate_to_human_returns_phrasing(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool(
            "EscalateToHuman",
            {"situation": "I cannot decide whether to ship this.", "why_stuck": "Stakes unclear."},
        )
    assert result.data["mentor"] == "pepe"
    assert "operator" in result.data["guidance"].lower()
    assert "I won't act" in result.data["suggested_message_to_your_operator"]


async def test_audit_log_records_no_plaintext(server, settings: Settings) -> None:
    secret_q = "secret-payload-do-not-log-7f3a"
    async with Client(server) as c:
        await c.call_tool("AskMentor", {"question": secret_q})
    log_text = settings.audit_log_path.read_text(encoding="utf-8")
    assert "op=AskMentor" in log_text
    assert secret_q not in log_text


async def test_capability_route(server) -> None:
    """The /.well-known/agent.json AMMP capability advertisement."""
    async with Client(server) as c:
        await c.list_tools()  # warm-up
    # Pull capability via the underlying http app instead — exercise the route directly.
    from starlette.testclient import TestClient

    app = server.http_app(path="/")
    with TestClient(app) as http:
        r = http.get("/.well-known/agent.json")
    assert r.status_code == 200
    payload = r.json()
    assert payload["name"] == "ammp-mcp"
    assert payload["ammp"]["tracks"] == ["mentoring"]
    slugs = {m["slug"] for m in payload["ammp"]["mentors"]}
    assert slugs == {"pepe", "strict", "stubmentor"}
    # Each mentor advertises its backend in the new shape.
    backends = {m["slug"]: m["backend"] for m in payload["ammp"]["mentors"]}
    assert backends["stubmentor"] == "stub"
    assert payload["ammp"]["privacyPosture"]["crossCompartmentEscalation"] == "prohibited"
    # Operations array must include the six MCP tools — five §5 baseline plus
    # the ListMentors server-side extension. Pinned explicitly: this assertion
    # has caught the offline/live capability builders drifting apart before.
    assert set(payload["operations"]) == {
        "ListMentors",
        "ListPlaybooks",
        "GetPlaybook",
        "SearchPlaybooks",
        "AskMentor",
        "EscalateToHuman",
    }


def test_offline_and_live_capability_operations_match(settings: Settings) -> None:
    """`ammp capability` (offline) and `/.well-known/agent.json` (live) must
    agree on the operations array, or mentees that probe one and act on the
    other hit phantom tools.
    """
    from ammp_mcp import __ammp_draft__, __version__
    from ammp_mcp.server import _build_capability_payload, build_context
    from ammp_mcp.system._capability_service import build_offline_capability

    live = _build_capability_payload(build_context(settings))
    offline = build_offline_capability(settings, __version__, __ammp_draft__)
    assert set(live["operations"]) == set(offline["operations"])


# ─── Auth-on flow ─────────────────────────────────────────────────────────


async def test_auth_required_rejects_missing_key(settings: Settings) -> None:
    settings_auth = settings.model_copy(update={"require_auth": True})
    server = create_server(settings_auth)
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {})
    assert result.data["error"] == "auth_failed"
    assert result.data["detail"] == "api_key_required"


async def test_auth_required_accepts_valid_key(settings: Settings) -> None:
    settings_auth = settings.model_copy(update={"require_auth": True})
    server = create_server(settings_auth)
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {"api_key": "ammp-test-key-1"})
    assert result.data["mentor"] == "pepe"


async def test_auth_required_rejects_wrong_key(settings: Settings) -> None:
    settings_auth = settings.model_copy(update={"require_auth": True})
    server = create_server(settings_auth)
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {"api_key": "wrong"})
    assert result.data["error"] == "auth_failed"
    assert result.data["detail"] == "api_key_invalid"


def test_capability_advertises_auth_setting(settings: Settings) -> None:
    # Sync — TestClient handles its own event loop; no `await` needed here.
    settings_auth = settings.model_copy(update={"require_auth": True})
    server = create_server(settings_auth)
    from starlette.testclient import TestClient

    app = server.http_app(path="/")
    with TestClient(app) as http:
        r = http.get("/.well-known/agent.json")
    assert r.json()["ammp"]["privacyPosture"]["requireAuth"] is True


async def test_tools_listed_match_ammp_operations(server) -> None:
    async with Client(server) as c:
        tools = await c.list_tools()
    names = {t.name for t in tools}
    assert names == {"ListMentors", "ListPlaybooks", "GetPlaybook", "SearchPlaybooks", "AskMentor", "EscalateToHuman"}


async def test_response_envelopes_match_pydantic_schema(server) -> None:
    """Sanity check: each tool response round-trips through its model_dump shape."""
    async with Client(server) as c:
        lp = await c.call_tool("ListPlaybooks", {})
    # Catches forgotten fields when models change
    payload = json.dumps(lp.data)
    assert "track" in payload
    assert "mentor" in payload


async def test_ask_mentor_routes_to_explicit_stub_backend(server) -> None:
    """A mentor whose mentor.json declares backend.kind=stub goes through StubBackend."""
    async with Client(server) as c:
        result = await c.call_tool(
            "AskMentor",
            {"question": "anything", "mentor": "stubmentor"},
        )
    assert result.data["mentor"] == "stubmentor"
    # Stub returns confidence 0.2 → escalation_recommended True at default threshold 0.6.
    assert result.data["escalation_recommended"] is True
    assert "stub backend" in result.data["answer"].lower()


async def test_capability_advertises_per_mentor_backends(server) -> None:
    from starlette.testclient import TestClient

    app = server.http_app(path="/")
    with TestClient(app) as http:
        r = http.get("/.well-known/agent.json")
    payload = r.json()
    backends_by_slug = {m["slug"]: m["backend"] for m in payload["ammp"]["mentors"]}
    # stubmentor was declared with backend.kind=stub.
    assert backends_by_slug["stubmentor"] == "stub"
    # pepe and strict have no backend block — fall through to anthropic-direct
    # (which is `is_live=False` here because no API key is configured).
    assert backends_by_slug["pepe"] == "anthropic-direct"
    assert backends_by_slug["strict"] == "anthropic-direct"
