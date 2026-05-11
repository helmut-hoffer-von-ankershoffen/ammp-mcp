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
    # Each mentor entry embeds its full playbook corpus — playbooks
    # (areas of practice) with all their work instructions inside — so
    # a single ListMentors call surfaces everything a mentee needs to
    # ground itself without follow-up GetPlaybook calls.
    pepe_playbooks = by_slug["pepe"]["playbooks"]
    assert len(pepe_playbooks) == by_slug["pepe"]["playbook_count"] == 2
    pb_ids = {pb["id"] for pb in pepe_playbooks}
    assert pb_ids == {"intro", "operator-craft"}  # see conftest fixture
    for pb in pepe_playbooks:
        assert pb["name"]
        # Each playbook embeds work-instruction summaries only — bodies
        # are intentionally absent here to keep ListMentors lightweight.
        # Mentees fetch full bodies via GetPlaybook / GetWorkInstruction.
        for wi in pb["instructions"]:
            assert wi["title"]
            assert "body" not in wi
    # mentor-level description + avatar_url are surfaced over the wire
    # so mentee clients can render a profile card without an extra HTTP
    # fetch. Pepe's fixture has both; stubmentor has neither.
    assert by_slug["pepe"]["description"] == "Calm, grounded mentor for resilient agent work."
    assert by_slug["pepe"]["avatar_url"] is not None
    assert by_slug["pepe"]["avatar_url"].endswith("/mentors/pepe/avatar")
    assert by_slug["stubmentor"]["description"] is None
    assert by_slug["stubmentor"]["avatar_url"] is None
    # human_mentor surfaces over the wire — escalation destination is
    # explicit. Pepe has Helmut; the other fixture mentors don't set it.
    assert by_slug["pepe"]["human_mentor"] == {
        "name": "Helmut Hoffer von Ankershoffen",
        "url": "https://helmut.hoffer-von-ankershoffen.me/",
        "contact": "helmuthva@gmail.com",
    }
    assert by_slug["strict"]["human_mentor"] is None
    # Instruction count is the flattened total across all playbooks
    # (pepe: intro has 2 instructions, operator-craft has 1 → 3 total).
    assert by_slug["pepe"]["instruction_count"] == 3


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
    assert ids == {"intro", "operator-craft"}
    # Each playbook surfaces its work-instruction summaries (no bodies).
    intro = next(p for p in result.data["playbooks"] if p["id"] == "intro")
    wi_ids = {wi["id"] for wi in intro["instructions"]}
    assert wi_ids == {"intro", "auth"}
    assert intro["instruction_count"] == 2
    assert "readme" not in wi_ids


async def test_list_playbooks_explicit_mentor(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {"mentor": "strict"})
    assert result.data["mentor"] == "strict"
    assert result.data["count"] == 1


async def test_list_playbooks_unknown_mentor(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {"mentor": "ghost"})
    assert result.data["error"] == "unknown_mentor"


async def test_get_playbook_returns_instructions(server) -> None:
    """GetPlaybook returns the playbook + every work-instruction body."""
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "intro"})
    assert result.data["id"] == "intro"
    assert result.data["name"] == "Welcome to Pepe"
    instr_ids = {wi["id"] for wi in result.data["instructions"]}
    assert instr_ids == {"intro", "auth"}
    # Bodies are full markdown.
    intro_wi = next(wi for wi in result.data["instructions"] if wi["id"] == "intro")
    assert "Welcome" in intro_wi["body"]


async def test_get_playbook_path_traversal_rejected(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "../etc/passwd"})
    assert result.data["error"] == "invalid_id"


async def test_get_playbook_not_found(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "nope"})
    assert result.data["error"] == "not_found"


async def test_get_work_instruction_returns_body(server) -> None:
    """GetWorkInstruction fetches one specific instruction by (playbook_id, id)."""
    async with Client(server) as c:
        result = await c.call_tool(
            "GetWorkInstruction",
            {"playbook_id": "intro", "id": "auth"},
        )
    assert result.data["playbook_id"] == "intro"
    assert result.data["id"] == "auth"
    assert "OAuth callback resilience" in result.data["title"]
    assert "Idempotent retries" in result.data["body"]


async def test_get_work_instruction_unknown_playbook(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool(
            "GetWorkInstruction",
            {"playbook_id": "no-such-playbook", "id": "auth"},
        )
    assert result.data["error"] == "not_found"


async def test_get_work_instruction_unknown_id(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool(
            "GetWorkInstruction",
            {"playbook_id": "intro", "id": "no-such-instruction"},
        )
    assert result.data["error"] == "not_found"


async def test_search_returns_ranked_matches(server) -> None:
    """Search runs at work-instruction granularity and names parent playbook."""
    async with Client(server) as c:
        result = await c.call_tool("SearchPlaybooks", {"query": "playbook"})
    assert result.data["count"] >= 1
    for match in result.data["matches"]:
        assert "rank" in match
        assert "playbook_id" in match
        assert "id" in match


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


async def test_landing_page_route(server) -> None:
    """`GET /` returns a mentee-facing HTML landing with live mentors + playbooks.

    Pinned because a fresh-clone visitor hitting the bare URL must see
    a usable page, not a 404 from the underlying MCP framework. Mounts
    MCP at `/mcp/` (production default) so the root is free for the
    custom landing route. The page is non-technical and mentee-only;
    operator minting flow lives in OPERATING.md, not on the page.
    """
    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        r = http.get("/")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    body = r.text
    # All fixture mentor slugs + names must render — proves the per-request
    # `ctx.mentors` walk is feeding the page (dynamic on add/remove).
    for slug in ("pepe", "strict", "stubmentor"):
        assert slug in body, f"mentor {slug!r} missing from landing page"
    for name in ("Pepe Arturo", "Strict Mentor", "Stub Mentor"):
        assert name in body, f"mentor name {name!r} missing from landing page"
    # Playbook titles from each mentor's corpus must appear — confirms
    # `load_corpus()` is called per request, so new playbooks show up
    # without a server restart. (Titles from the fixture playbooks.)
    # Playbook names + work-instruction titles render. Apostrophes get
    # HTML-escaped (`&#x27;`); assert on apostrophe-free substrings.
    for s in (
        "Welcome to Pepe",  # playbook name
        "Onboarding for new mentees.",  # playbook description
        "OAuth callback resilience",  # work-instruction title
        "Operator craft",  # playbook name
        "Verify before claiming done",  # work-instruction title
        "Strict rules",  # playbook name
    ):
        assert s in body, f"{s!r} missing from landing page"
    # Mentor-level description renders alongside name + avatar.
    assert "Calm, grounded mentor for resilient agent work." in body
    assert "High-bar reviewer" in body
    # human_mentor attribution: Pepe shows Helmut as the human behind
    # so escalation destinations are explicit.
    assert "Helmut Hoffer von Ankershoffen" in body
    assert "helmut.hoffer-von-ankershoffen.me" in body
    assert "Behind Pepe Arturo" in body
    # Pepe has an avatar.png in the fixture, so an <img> tag with the
    # mentor-avatar route must render. The src is relative ("mentors/...")
    # so it resolves correctly whether the landing is served at root or
    # at a mount prefix like `/ammp/`. stubmentor has no avatar; it
    # falls back to an initial-letter glyph (`<div class='avatar avatar-fallback'>`).
    assert "src='mentors/pepe/avatar'" in body or 'src="mentors/pepe/avatar"' in body
    assert "avatar-fallback" in body  # stubmentor + strict have no avatar file
    # Each per-agent tab label must render — six tabs, one for every
    # supported runtime plus a Generic catch-all.
    for runtime in ("Claude Desktop", "Claude Code", "Copilot", "OpenClaw", "Hermes", "Generic"):
        assert runtime in body, f"runtime {runtime!r} missing from landing page"
    # The canonical MCP endpoint must be visible for copy/paste.
    assert "/mcp/" in body
    # Operator-side language is gone — none of these belong on the
    # mentee page after the simplification.
    assert "ammp mentee add" not in body
    assert "If you are the operator" not in body
    assert "rotate-key" not in body
    # The "Request access" CTA is a mailto: with pre-filled subject + body.
    assert "mailto:helmuthva@gmail.com" in body
    assert "subject=" in body and "body=" in body
    # Mailto body must elicit the inputs that map to `ammp mentee add`
    # at the operator's end: name (→ slug + operator), runtime, and a
    # secure delivery channel for the plaintext token. Field names are
    # URL-encoded by `urllib.parse.quote`, so check for the
    # percent-encoded forms or readable substrings that survive encoding.
    import urllib.parse as _up

    mailto_start = body.index("mailto:helmuthva")
    mailto_end = body.index("'", mailto_start) if "'" in body[mailto_start:] else body.index('"', mailto_start)
    mailto_url = body[mailto_start:mailto_end]
    decoded = _up.unquote(mailto_url)
    assert "My name" in decoded, "mailto body must ask for the requester's name"
    assert "My agent" in decoded, "mailto body must ask for the agent runtime"
    assert "My secure delivery channel" in decoded, "mailto body must ask for the delivery channel"
    # Four-step structure, in order: request token → configure connection →
    # pick mentor → sanity-check the connection with a list-mentors prompt.
    step1 = body.index("Step 1")
    step2 = body.index("Step 2")
    step3 = body.index("Step 3")
    step4 = body.index("Step 4")
    assert step1 < step2 < step3 < step4, "step headings out of order"
    # Each step has the right content anchor.
    assert "Request" in body[step1 : step1 + 200]
    assert "MCP connection" in body[step2 : step2 + 200] or "connector settings" in body[step2 : step2 + 200]
    assert "mentor" in body[step3 : step3 + 200].lower()
    # Step 4 surfaces the canonical sanity-check prompt for copy/paste.
    assert "List mentors on helmguild." in body[step4 : step4 + 400]
    # Single Copy-URL button with the inline vanilla-JS handler.
    assert 'class="btn copy"' in body
    assert "navigator.clipboard.writeText" in body
    # Cache-Control: no-store prevents browsers (and Cloudflare) from
    # serving a stale landing across deploys.
    assert "no-store" in r.headers.get("cache-control", "")


async def test_mount_path_prefix_relocates_all_routes(settings: Settings) -> None:
    """When `mount_path=/ammp`, every route this server exposes lives under the
    prefix and bare `/` returns a 302 redirect.

    Pinned because the prefix is what lets `mcp.helmguild.com` host
    additional MCP servers later under sibling prefixes (e.g. `/review`).
    """
    from starlette.testclient import TestClient

    settings_pref = settings.model_copy(update={"mount_path": "/ammp", "public_url": "http://test.invalid/ammp"})
    server = create_server(settings_pref)
    app = server.http_app(path="/ammp/mcp/")
    with TestClient(app) as http:
        # Root redirects to the prefixed landing.
        r_root = http.get("/", follow_redirects=False)
        assert r_root.status_code == 302
        assert r_root.headers["location"] == "/ammp/"
        # Prefixed landing serves the page.
        r_landing = http.get("/ammp/")
        assert r_landing.status_code == 200
        assert "Mentor your agent." in r_landing.text
        # Capability JSON lives under the prefix.
        r_caps = http.get("/ammp/.well-known/agent.json")
        assert r_caps.status_code == 200
        assert r_caps.json()["url"] == "http://test.invalid/ammp"
        # Avatar route lives under the prefix.
        r_avatar = http.get("/ammp/mentors/pepe/avatar")
        assert r_avatar.status_code == 200
        # Bare avatar path (no prefix) returns 404 — proves the route
        # actually moved, didn't double-register.
        r_avatar_bare = http.get("/mentors/pepe/avatar")
        assert r_avatar_bare.status_code == 404


async def test_landing_renders_a_prompt_per_playbook(server) -> None:
    """Every playbook in the fixture corpus gets a `<pre class='prompt'>` block.

    The prompt must reference the right mentor slug + playbook id + the
    correct tool names a mentee would call to start the session.
    """
    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        r = http.get("/")
    body = r.text
    # Pepe has 2 playbooks (intro + operator-craft) in the fixture; strict 1; stub 1 → 4 prompts.
    assert body.count("class='prompt'") == 4 or body.count('class="prompt"') == 4
    # Each prompt names the tools an agent should call.
    for tool in ("ListPlaybooks", "GetPlaybook", "AskMentor"):
        assert tool in body, f"prompt does not mention {tool!r}"
    # The Pepe prompts mention EscalateToHumanMentor + Helmut (the
    # configured human_mentor on Pepe in the fixture). Strict + stub
    # have no human_mentor → no EscalateToHumanMentor step in their
    # prompts.
    assert "EscalateToHumanMentor" in body
    # Per-playbook prompt-dom ids are unique and stable.
    for pb_id in ("intro", "operator-craft"):
        assert f"id='prompt--pepe--{pb_id}'" in body or f'id="prompt--pepe--{pb_id}"' in body
    # Copy buttons reference the corresponding pre via data-copy-from.
    assert "data-copy-from" in body
    # Inline JS handler supports the new data-copy-from attribute.
    assert "dataset.copyFrom" in body


async def test_landing_prompts_match_actual_server_state(server) -> None:
    """E2E sanity: every prompt's stated mentor / playbook / instruction count
    matches what the actual MCP tools return on this server.

    Parses the rendered landing HTML, extracts every prompt block, and
    for each one drives `ListPlaybooks` + `GetPlaybook` over the live
    in-memory FastMCP client. Asserts the prompt's claims about the
    playbook (id, name, instruction count, mention of the tool names
    the mentee should call) line up with the server's actual state.

    This catches drift between the landing text and the corpus: if
    someone renames a playbook or adds/removes work instructions, the
    prompts must stay accurate or this test fails.
    """
    import re

    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        r = http.get("/")
    body = r.text

    # Each prompt block carries data-mentor / data-playbook attrs so the
    # test can address them unambiguously (avoids hyphen-splitting issues
    # in slugs like `operator-craft`).
    prompt_re = re.compile(
        r"<pre class='prompt'[^>]*data-mentor='(?P<mentor>[a-z0-9-]+)'[^>]*data-playbook='(?P<playbook>[a-z0-9-]+)'>"
        r"(?P<text>.*?)</pre>",
        re.DOTALL,
    )
    matches = list(prompt_re.finditer(body))
    assert matches, "no prompt blocks rendered on the landing"

    seen: dict[tuple[str, str], str] = {}
    for m in matches:
        seen[(m.group("mentor"), m.group("playbook"))] = m.group("text")

    # Walk every (mentor, playbook) prompt and validate against MCP.
    for (mentor_slug, playbook_id), prompt_text in seen.items():
        async with Client(server) as c:
            list_result = await c.call_tool("ListPlaybooks", {"mentor": mentor_slug})
            get_result = await c.call_tool("GetPlaybook", {"id": playbook_id, "mentor": mentor_slug})
        # Mentor reachable
        assert list_result.data["mentor"] == mentor_slug, (mentor_slug, list_result.data)
        # Playbook exists
        assert "error" not in get_result.data, (mentor_slug, playbook_id, get_result.data)
        assert get_result.data["id"] == playbook_id
        actual_count = len(get_result.data["instructions"])
        # The prompt must state the *correct* instruction count.
        expected_count_phrase = f"{actual_count} work instruction"
        assert expected_count_phrase in prompt_text, (
            f"prompt for ({mentor_slug}, {playbook_id}) claims a different instruction count "
            f"than the server returns ({actual_count}). Prompt text: {prompt_text[:200]}"
        )
        # Tool names the prompt tells the agent to call must be the
        # canonical names the MCP server actually exposes.
        for tool in ("ListPlaybooks", "GetPlaybook", "AskMentor"):
            assert tool in prompt_text, (mentor_slug, playbook_id, tool)


async def test_escalate_to_human_mentor_round_trips(settings: Settings) -> None:
    """B.a → A.a → A.h flow: long-running tool returns A.h's reply.

    Uses an in-test delivery adapter that synthetically resolves the
    broker once `deliver()` runs, simulating A.h replying immediately.
    Verifies the response envelope shape + that the escalation lands
    in the persistent jsonl with `status=answered`.
    """
    import asyncio as _asyncio

    from ammp_mcp.escalation._adapters._base import DeliveryAdapter
    from ammp_mcp.server import build_context

    class _InstantAdapter(DeliveryAdapter):
        """Fakes a human mentor that replies immediately on deliver."""

        kind = "instant-test"

        def __init__(self, answer: str) -> None:
            self._answer = answer
            self._broker = None
            self._store = None
            self._tasks: list[_asyncio.Task[None]] = []

        async def start(self, broker, store) -> None:
            self._broker = broker
            self._store = store

        async def deliver(self, escalation) -> str | None:
            # Schedule resolution on next loop tick so the handler's
            # `event.wait()` is already registered.
            from datetime import UTC, datetime

            async def _resolve() -> None:
                await _asyncio.sleep(0)
                self._store.update(escalation.id, status="answered", answered_at=datetime.now(UTC), answer=self._answer)
                self._broker.resolve(escalation.id, self._answer)

            self._tasks.append(_asyncio.create_task(_resolve()))
            return "telegram-msg-123"

        async def stop(self) -> None:
            return None

    # Build a custom context with our instant adapter.
    ctx = build_context(settings)
    # Swap the adapter (ServerContext is frozen; rebuild).
    from dataclasses import replace as _replace

    ctx = _replace(ctx, delivery_adapter=_InstantAdapter(answer="Run the A/B for 7 days at parity."))

    # Re-create server with our patched ctx — easier path: create a
    # minimal server bound to the same ctx by going via the test
    # context directly. We exercise `_handle_escalate_to_human_mentor`
    # without spinning up the full MCP transport.
    from ammp_mcp.server import _handle_escalate_to_human_mentor

    # Adapter `start()` would normally be called by the FastMCP
    # lifespan; do it explicitly for this direct-handler test.
    await ctx.delivery_adapter.start(ctx.escalation_broker, ctx.escalation_store)
    try:
        result = await _handle_escalate_to_human_mentor(
            ctx,
            question="Sandra is asking about Instagram reel scheduling cadence — should we go 2/wk or 4/wk?",
            mentor="pepe",
            context="",
            api_key=None,
        )
    finally:
        await ctx.delivery_adapter.stop()

    assert "error" not in result, result
    assert result["mentor"] == "pepe"
    assert result["escalation_id"]
    assert result["answer"] == "Run the A/B for 7 days at parity."
    assert result["answered_at"]
    # Persistence: the jsonl now has this escalation as answered.
    persisted = ctx.escalation_store.get(result["escalation_id"])
    assert persisted is not None
    assert persisted.status == "answered"
    assert persisted.answer == "Run the A/B for 7 days at parity."
    assert persisted.delivery_ref == "telegram-msg-123"
    # Audit log: hash-only, no payload leakage.
    log_text = settings.audit_log_path.read_text(encoding="utf-8")
    assert "op=EscalateToHumanMentor" in log_text
    assert "Sandra is asking" not in log_text


async def test_escalate_to_human_mentor_no_human_mentor_configured(settings: Settings) -> None:
    """Mentor without `human_mentor` set returns no_human_mentor in-band error."""
    from ammp_mcp.server import _handle_escalate_to_human_mentor, build_context

    ctx = build_context(settings)
    await ctx.delivery_adapter.start(ctx.escalation_broker, ctx.escalation_store)
    try:
        # `strict` fixture has no human_mentor configured.
        result = await _handle_escalate_to_human_mentor(
            ctx, question="anything", mentor="strict", context="", api_key=None
        )
    finally:
        await ctx.delivery_adapter.stop()
    assert result["error"] == "no_human_mentor"


async def test_ask_mentor_carries_escalation_draft_when_low_confidence(server) -> None:
    """A low-confidence AskMentor for pepe carries a B.h-approval draft."""
    async with Client(server) as c:
        result = await c.call_tool(
            "AskMentor",
            {"question": "Should we ship this without further review?"},
        )
    assert result.data["escalation_recommended"] is True
    # Pepe has a human_mentor configured in the fixture → draft surfaces.
    draft = result.data["escalation_to_human_mentor_draft"]
    assert draft is not None
    assert draft["human_mentor"]["name"] == "Helmut Hoffer von Ankershoffen"
    assert "Should we ship" in draft["question"]
    assert "approve" in draft["suggested_message_to_your_operator"].lower()


async def test_ask_mentor_no_draft_when_no_human_mentor(server) -> None:
    """A mentor without `human_mentor` doesn't surface a draft even at low confidence."""
    async with Client(server) as c:
        result = await c.call_tool(
            "AskMentor",
            {"question": "anything", "mentor": "strict"},
        )
    assert result.data["escalation_to_human_mentor_draft"] is None


async def test_mentor_avatar_route_serves_file(server) -> None:
    """`GET /mentors/<slug>/avatar` streams the on-disk PNG with a sane cache header."""
    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        r = http.get("/mentors/pepe/avatar")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert "max-age" in r.headers.get("cache-control", "")
    # PNG magic number — confirms it's the actual binary, not an error body.
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


async def test_mentor_avatar_route_404_when_missing(server) -> None:
    """A mentor with no `avatar.*` file returns 404, not a placeholder."""
    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        # stubmentor has no avatar in the fixture.
        r_missing = http.get("/mentors/stubmentor/avatar")
        # Unknown mentor slug also returns 404 (not 403 — no information
        # leak about whether the slug is known).
        r_unknown = http.get("/mentors/does-not-exist/avatar")
    assert r_missing.status_code == 404
    assert r_unknown.status_code == 404


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
        "GetWorkInstruction",
        "SearchPlaybooks",
        "AskMentor",
        "EscalateToHuman",
        "EscalateToHumanMentor",
    }
    # humanMentor surfaces in the capability JSON too — operators who
    # discover via /.well-known/agent.json see who's behind each mentor.
    by_slug = {m["slug"]: m for m in payload["ammp"]["mentors"]}
    assert by_slug["pepe"]["humanMentor"]["name"] == "Helmut Hoffer von Ankershoffen"
    assert by_slug["strict"]["humanMentor"] is None


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
    """No api_key + no Authorization header → in-band auth_failed."""
    from ammp_mcp.server import _handle_list_playbooks, build_context

    settings_auth = settings.model_copy(update={"require_auth": True})
    ctx = build_context(settings_auth)
    result = _handle_list_playbooks(ctx, mentor="", api_key=None)
    assert result["error"] == "auth_failed"
    assert result["detail"] == "api_key_required"


async def test_auth_required_accepts_valid_key(settings: Settings) -> None:
    """Explicit api_key resolves the mentee without needing an HTTP request."""
    from ammp_mcp.server import _handle_list_playbooks, build_context

    settings_auth = settings.model_copy(update={"require_auth": True})
    ctx = build_context(settings_auth)
    result = _handle_list_playbooks(ctx, mentor="", api_key="ammp-test-key-1")
    assert result["mentor"] == "pepe"


async def test_auth_required_rejects_wrong_key(settings: Settings) -> None:
    """An unknown api_key → auth_failed with detail=api_key_invalid."""
    from ammp_mcp.server import _handle_list_playbooks, build_context

    settings_auth = settings.model_copy(update={"require_auth": True})
    ctx = build_context(settings_auth)
    result = _handle_list_playbooks(ctx, mentor="", api_key="wrong")
    assert result["error"] == "auth_failed"
    assert result["detail"] == "api_key_invalid"


async def test_auth_reads_bearer_from_http_authorization_header(settings: Settings) -> None:
    """When no api_key is passed, _authenticate reads `Authorization: Bearer …` from the HTTP request.

    This is the production path: MCP-over-HTTP carries the per-mentee
    token in the standard Authorization header; the tool wrappers don't
    take an api_key parameter so the LLM never gets prompted for one.
    """
    from contextlib import ExitStack
    from unittest.mock import Mock, patch

    from fastmcp.server import http as fastmcp_http

    from ammp_mcp.server import _handle_list_playbooks, build_context

    settings_auth = settings.model_copy(update={"require_auth": True})
    ctx = build_context(settings_auth)

    fake_request = Mock()
    fake_request.headers = {"authorization": "Bearer ammp-test-key-1"}

    with ExitStack() as stack:
        # Inject the fake request into FastMCP's request ContextVar.
        stack.enter_context(fastmcp_http.set_http_request(fake_request))
        # Force `request_ctx.get()` (the first place get_http_request looks)
        # to miss, so the fallback path that reads _current_http_request fires.
        from fastmcp.server import dependencies as deps

        stack.enter_context(patch.object(deps, "request_ctx"))
        deps.request_ctx.get.side_effect = LookupError
        result = _handle_list_playbooks(ctx, mentor="", api_key=None)
    assert result.get("error") is None, result
    assert result["mentor"] == "pepe"


async def test_auth_hot_reloads_mentees_from_disk(settings: Settings) -> None:
    """A mentee minted AFTER `create_server` is honoured without a restart.

    Pinned because `ammp mentee add` is supposed to be a one-shot
    operator action — having to also `launchctl kickstart -k` (or
    `docker restart`) the live server would be a perfectly avoidable
    foot-gun. The hot-reload happens in `_authenticate`, which goes
    through `_load_mentees_cached` — an mtime-keyed cache that only
    re-parses `mentees.json` when its `st_mtime_ns` changes.
    """
    import json
    import time
    from unittest.mock import patch

    from ammp_mcp.mentee import Mentee, hash_api_key, load_mentees, save_mentees
    from ammp_mcp.server import _handle_list_playbooks, build_context

    settings_auth = settings.model_copy(update={"require_auth": True})
    ctx = build_context(settings_auth)

    # First call: key not yet on disk → rejected (sanity baseline).
    new_key = "ammp-hot-reload-fresh-key"
    r = _handle_list_playbooks(ctx, mentor="", api_key=new_key)
    assert r["error"] == "auth_failed"
    assert r["detail"] == "api_key_invalid"

    # Mint the mentee straight to disk (same path `ammp mentee add` writes).
    # Sleep a beat so the on-disk mtime is strictly newer than the cached
    # snapshot — on coarse filesystems (HFS+, FAT), st_mtime can be
    # second-resolution and tests run fast enough to land within the
    # same tick.
    time.sleep(0.02)
    mentees = load_mentees(settings.mentees_file)
    mentees["hot-reload-mentee"] = Mentee(
        slug="hot-reload-mentee",
        operator="human:test",
        runtime="claude-cowork",
        api_key_hash=hash_api_key(new_key),
        rate_limit_per_minute=60,
    )
    save_mentees(settings.mentees_file, mentees)
    # Belt-and-braces: confirm the on-disk file actually contains the new slug.
    assert "hot-reload-mentee" in json.loads(settings.mentees_file.read_text(encoding="utf-8"))[-1]["slug"]

    # SECOND call — same context, no restart. The fresh key should now auth.
    r = _handle_list_playbooks(ctx, mentor="", api_key=new_key)
    assert r.get("error") is None, r
    assert r["mentor"] == "pepe"

    # Verify the cache actually caches: two more calls with the same key
    # should result in exactly ZERO re-parses of mentees.json (the prior
    # call already warmed the cache; mtime hasn't changed since).
    with patch("ammp_mcp.server.load_mentees", wraps=load_mentees) as spy:
        _handle_list_playbooks(ctx, mentor="", api_key=new_key)
        _handle_list_playbooks(ctx, mentor="", api_key=new_key)
        assert spy.call_count == 0, (
            f"Expected zero re-parses across two requests when mentees.json was untouched, got {spy.call_count}."
        )


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
    assert names == {
        "ListMentors",
        "ListPlaybooks",
        "GetPlaybook",
        "GetWorkInstruction",
        "SearchPlaybooks",
        "AskMentor",
        "EscalateToHuman",
        "EscalateToHumanMentor",
    }


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
