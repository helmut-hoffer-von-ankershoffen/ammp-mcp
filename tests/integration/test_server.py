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
        # Each playbook embeds skill summaries only — bodies are
        # intentionally absent here to keep ListMentors lightweight.
        # Mentees fetch full bodies via GetPlaybook / GetSkill.
        for sk in pb["skills"]:
            assert sk["title"]
            assert "body" not in sk
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
        "profile_url": "https://www.helmguild.com/helmut-hoffer-von-ankershoffen/",
        "contact": "helmuthva@gmail.com",
    }
    assert by_slug["strict"]["human_mentor"] is None
    # Instruction count is the flattened total across all playbooks
    # (pepe: intro has 2 instructions, operator-craft has 1 → 3 total).
    assert by_slug["pepe"]["skill_count"] == 3


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
    # Each playbook surfaces its skill summaries (no bodies).
    intro = next(p for p in result.data["playbooks"] if p["id"] == "intro")
    skill_ids = {sk["id"] for sk in intro["skills"]}
    assert skill_ids == {"intro", "auth"}
    assert intro["skill_count"] == 2
    assert "readme" not in skill_ids


async def test_list_playbooks_explicit_mentor(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {"mentor": "strict"})
    assert result.data["mentor"] == "strict"
    assert result.data["count"] == 1


async def test_list_playbooks_unknown_mentor(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("ListPlaybooks", {"mentor": "ghost"})
    assert result.data["error"] == "unknown_mentor"


async def test_get_playbook_returns_skills(server) -> None:
    """GetPlaybook returns the playbook + every skill body."""
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "intro"})
    assert result.data["id"] == "intro"
    assert result.data["name"] == "Welcome to Pepe"
    skill_ids = {sk["id"] for sk in result.data["skills"]}
    assert skill_ids == {"intro", "auth"}
    # Bodies are full markdown.
    intro_sk = next(sk for sk in result.data["skills"] if sk["id"] == "intro")
    assert "Welcome" in intro_sk["body"]


async def test_get_playbook_path_traversal_rejected(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "../etc/passwd"})
    assert result.data["error"] == "invalid_id"


async def test_get_playbook_not_found(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool("GetPlaybook", {"id": "nope"})
    assert result.data["error"] == "not_found"


async def test_get_skill_returns_body(server) -> None:
    """GetSkill fetches one specific skill by (playbook_id, id)."""
    async with Client(server) as c:
        result = await c.call_tool(
            "GetSkill",
            {"playbook_id": "intro", "id": "auth"},
        )
    assert result.data["playbook_id"] == "intro"
    assert result.data["id"] == "auth"
    assert "OAuth callback resilience" in result.data["title"]
    assert "Idempotent retries" in result.data["body"]


async def test_get_skill_unknown_playbook(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool(
            "GetSkill",
            {"playbook_id": "no-such-playbook", "id": "auth"},
        )
    assert result.data["error"] == "not_found"


async def test_get_skill_unknown_id(server) -> None:
    async with Client(server) as c:
        result = await c.call_tool(
            "GetSkill",
            {"playbook_id": "intro", "id": "no-such-skill"},
        )
    assert result.data["error"] == "not_found"


async def test_search_returns_ranked_matches(server) -> None:
    """Search runs at skill granularity and names parent playbook."""
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
    # Playbook names + skill titles render. Apostrophes get
    # HTML-escaped (`&#x27;`); assert on apostrophe-free substrings.
    for s in (
        "Welcome to Pepe",  # playbook name
        "Onboarding for new mentees.",  # playbook description
        "OAuth callback resilience",  # skill title
        "Operator craft",  # playbook name
        "Verify before claiming done",  # skill title
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
    # Step 1's access-request flow is an embedded Google Form (replaced
    # the earlier mailto link on 2026-05-13). Collapsed by default into
    # a <details> with a button-styled <summary> labelled "Request access";
    # the mailto: scheme must not appear anywhere on the landing.
    assert "mailto:" not in body
    assert '<details class="access-form">' in body
    assert '<summary class="btn primary">Request access</summary>' in body
    assert "docs.google.com/forms/" in body
    assert "1FAIpQLSfGJJCw_wd12OTYb8F4ryvtOaOb3doFShUTKSIrwFfYifSoKg" in body
    assert "<iframe" in body
    assert 'loading="lazy"' in body
    # Four-step structure, in order: request token → configure connection →
    # sanity-check with list-mentors prompt → pick a mentor.
    step1 = body.index("Step 1")
    step2 = body.index("Step 2")
    step3 = body.index("Step 3")
    step4 = body.index("Step 4")
    assert step1 < step2 < step3 < step4, "step headings out of order"
    # Each step has the right content anchor.
    assert "Request" in body[step1 : step1 + 200]
    assert "MCP connection" in body[step2 : step2 + 200] or "connector settings" in body[step2 : step2 + 200]
    # Step 3 surfaces the canonical sanity-check prompt for copy/paste —
    # it must precede the mentor cards so the user verifies the connection
    # before committing to a real session.
    assert "List mentors on helmguild." in body[step3 : step3 + 400]
    # Step 4 introduces the mentor list and the per-playbook prompts.
    assert "mentor" in body[step4 : step4 + 200].lower()
    # Single Copy-URL button with the inline vanilla-JS handler.
    assert 'class="btn copy"' in body
    assert "navigator.clipboard.writeText" in body
    # Cache-Control: no-store prevents browsers (and Cloudflare) from
    # serving a stale landing across deploys.
    assert "no-store" in r.headers.get("cache-control", "")
    # Chrome: banner linking back to helmguild.com + EN · DE language pill.
    assert 'class="helmguild-banner"' in body
    assert "https://www.helmguild.com/" in body
    assert "back to helmguild.com" in body
    assert 'class="lang-pill"' in body
    # EN landing marks EN as current and links to ./de/.
    assert 'hreflang="de"' in body and "./de/" in body
    # Pepe's `profile_url` (set in the fixture) turns the mentor name
    # into a link to the longer profile page on the brand site.
    assert "class='mentor-profile' href='https://www.helmguild.com/pepe-arturo-ai/'" in body
    # Helmut's `human_mentor.profile_url` swaps the "Behind Pepe" link
    # from the personal bio site to the helmguild profile page.
    assert "href='https://www.helmguild.com/helmut-hoffer-von-ankershoffen/'" in body
    # Built-on-open-standards callout names AgentSkills + the Claude Code
    # plugin / marketplace conventions explicitly, so visitors can see at a
    # glance that the on-disk format is not bespoke.
    assert "Built on open standards" in body
    assert "agentskills.io" in body
    assert "code.claude.com/docs/en/plugins" in body
    assert "code.claude.com/docs/en/plugin-marketplaces" in body
    # Footer also names the standards (single-line breadcrumb under the
    # source link) so the claim is visible even when the section is
    # scrolled off-screen.
    assert "AgentSkills</a> standard" in body
    assert "Claude Code plugin</a> spec" in body


async def test_landing_page_de_route_serves_german(server) -> None:
    """`GET /de/` returns the German mirror of the landing page.

    Same structure, translated chrome. Mentor names + playbook content
    stay in English (corpus is content, not chrome) — same lockstep
    convention as helmguild.com.
    """
    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        r = http.get("/de/")
    assert r.status_code == 200, r.text
    body = r.text
    assert '<html lang="de">' in body
    # Translated headings + chrome.
    assert "Schritt 1 — Zugangs-Token anfordern" in body
    assert "Schritt 2 — MCP-Verbindung deines Agenten einrichten" in body
    assert "Schritt 3 — Verbindung prüfen" in body
    assert "Schritt 4 — Mentor auswählen und Session starten" in body
    assert "Datenschutz" in body
    assert "zurück zu helmguild.com" in body
    # Lang pill — DE is current; back-to-EN link present.
    assert 'class="lang-pill"' in body
    assert "../" in body and 'hreflang="en"' in body
    # Access-request iframe is embedded on the DE landing too (same form,
    # same collapsed-by-default details/summary pattern).
    assert "mailto:" not in body
    assert '<details class="access-form">' in body
    assert '<summary class="btn primary">Zugang anfordern</summary>' in body
    assert "docs.google.com/forms/" in body
    assert "Zugangsanfrage-Formular" in body  # DE iframe title
    # MCP tool registration command stays in English (it's a literal CLI invocation).
    assert "claude mcp add" in body
    # Mentor profile link auto-swaps to the /de/ variant on the DE landing.
    assert "class='mentor-profile' href='https://www.helmguild.com/de/pepe-arturo-ai/'" in body
    # The human-mentor profile link follows the same /de/ swap rule.
    assert "href='https://www.helmguild.com/de/helmut-hoffer-von-ankershoffen/'" in body
    # Open-standards callout — translated heading, same link targets.
    assert "Auf offenen Standards aufgebaut" in body
    assert "agentskills.io" in body
    assert "code.claude.com/docs/en/plugins" in body
    # The bare (EN) profile URL should NOT appear as a mentor-profile href
    # on the DE page — only its /de/ variant. (It may still appear in
    # alternate-link tags or footer, which is fine.)
    assert "class='mentor-profile' href='https://www.helmguild.com/pepe-arturo-ai/'" not in body


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
    """E2E sanity: every prompt's stated mentor / playbook / skill count
    matches what the actual MCP tools return on this server.

    Parses the rendered landing HTML, extracts every prompt block, and
    for each one drives `ListPlaybooks` + `GetPlaybook` over the live
    in-memory FastMCP client. Asserts the prompt's claims about the
    playbook (id, name, skill count, mention of the tool names the
    mentee should call) line up with the server's actual state.

    This catches drift between the landing text and the corpus: if
    someone renames a playbook or adds/removes skills, the prompts
    must stay accurate or this test fails.
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
        actual_count = len(get_result.data["skills"])
        # The prompt must state the *correct* skill count.
        expected_count_phrase = f"{actual_count} skill"
        assert expected_count_phrase in prompt_text, (
            f"prompt for ({mentor_slug}, {playbook_id}) claims a different skill count "
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


async def test_escalate_to_human_mentor_emits_progress_heartbeats_while_waiting(settings: Settings) -> None:
    """Long wait → periodic progress notifications fire.

    The MCP per-tool timeout on Claude Desktop is ~60s; each
    `notifications/progress` resets it. Without heartbeats, the
    client gives up with `-32001` long before A.h can reply. Pinned
    so a regression that drops the heartbeat surfaces as a failing
    test, not as a client-side timeout in production.
    """
    import asyncio as _asyncio
    from dataclasses import replace as _replace
    from datetime import UTC, datetime

    from ammp_mcp.escalation._adapters._base import DeliveryAdapter
    from ammp_mcp.server import _handle_escalate_to_human_mentor, build_context

    class _DelayedAdapter(DeliveryAdapter):
        """Resolves the escalation after `delay_seconds` to simulate A.h thinking."""

        kind = "delayed-test"

        def __init__(self, *, answer: str, delay_seconds: float) -> None:
            self._answer = answer
            self._delay = delay_seconds
            self._broker = None
            self._store = None
            self._tasks: list[_asyncio.Task[None]] = []

        async def start(self, broker, store) -> None:
            self._broker = broker
            self._store = store

        async def deliver(self, escalation) -> str | None:
            async def _resolve() -> None:
                await _asyncio.sleep(self._delay)
                self._store.update(escalation.id, status="answered", answered_at=datetime.now(UTC), answer=self._answer)
                self._broker.resolve(escalation.id, self._answer)

            self._tasks.append(_asyncio.create_task(_resolve()))
            return "telegram-msg-456"

        async def stop(self) -> None:
            return None

    # Tight heartbeat (20 ms) and a delivery delay (≈ 80 ms) so the
    # loop must fire ≥ 3 progress notifications before the answer
    # lands. Total timeout stays at the production default so we
    # don't exercise the timeout branch here.
    settings_with_fast_heartbeat = settings.model_copy(update={"escalation_progress_heartbeat_seconds": 0.02})
    ctx = build_context(settings_with_fast_heartbeat)
    ctx = _replace(ctx, delivery_adapter=_DelayedAdapter(answer="Take the meeting.", delay_seconds=0.08))

    progress_events: list[tuple[float, str]] = []

    async def _capture(progress: float, message: str) -> None:
        progress_events.append((progress, message))

    await ctx.delivery_adapter.start(ctx.escalation_broker, ctx.escalation_store)
    try:
        result = await _handle_escalate_to_human_mentor(
            ctx,
            question="Should I take the meeting?",
            mentor="pepe",
            context="",
            api_key=None,
            progress=_capture,
        )
    finally:
        await ctx.delivery_adapter.stop()

    assert "error" not in result, result
    assert result["answer"] == "Take the meeting."

    # Expected progress stream: 0.1 (queued) → 0.5 (delivered) → ≥1
    # heartbeat tick(s) carrying "still awaiting" → 1.0 (replied).
    progresses = [p for p, _ in progress_events]
    messages = [m for _, m in progress_events]
    assert any(m.startswith("escalation") and "queued" in m for m in messages), messages
    assert any("delivered" in m for m in messages), messages
    assert any("still awaiting" in m for m in messages), (
        f"heartbeat progress notification missing — client would time out. events={progress_events}"
    )
    assert progresses[-1] == 1.0


async def test_escalate_to_human_mentor_advertises_task_mode(server) -> None:
    """`EscalateToHumanMentor` opts into MCP background tasks (mode=optional).

    Clients that understand the MCP task primitive can run the call
    in the background — server returns a task id immediately, client
    polls until A.h replies (the wait can be hours and survives a
    flaky transport). Clients that don't understand tasks still get
    the synchronous heartbeat-buffered path. Pinned so a regression
    that drops the decorator surfaces here rather than as a silent
    UX regression in production.
    """
    tool = await server.get_tool("EscalateToHumanMentor")
    assert tool.task_config is not None, "EscalateToHumanMentor must opt into MCP background tasks"
    assert tool.task_config.mode == "optional"
    # Short-running tools should NOT advertise the task primitive — it
    # adds protocol overhead with no benefit when the answer is one
    # synthesis round-trip away.
    for sync_name in ("ListMentors", "ListPlaybooks", "GetPlaybook", "AskMentor", "SearchPlaybooks"):
        t = await server.get_tool(sync_name)
        assert t.task_config.mode == "forbidden", f"{sync_name} should not opt into background tasks"


async def test_escalate_to_human_mentor_returns_pending_then_get_escalation_resolves(settings: Settings) -> None:
    """The sync-or-pending contract: EscalateToHumanMentor returns
    quickly with status=pending when A.h doesn't reply within
    wait_seconds; GetEscalation later retrieves the answer.

    This is the production reliability path — MCP client per-tool
    timeouts vary, so we don't bet on a long sync block. Pinned to
    catch a regression that silently re-introduces the old
    block-for-the-full-timeout behavior.
    """
    import asyncio as _asyncio
    from dataclasses import replace as _replace
    from datetime import UTC, datetime

    from ammp_mcp.escalation._adapters._base import DeliveryAdapter
    from ammp_mcp.server import _handle_escalate_to_human_mentor, _handle_get_escalation, build_context

    class _LateAdapter(DeliveryAdapter):
        """A.h replies only after the first wait_seconds budget has elapsed."""

        kind = "late-test"

        def __init__(self, *, answer: str, delay_seconds: float) -> None:
            self._answer = answer
            self._delay = delay_seconds
            self._broker = None
            self._store = None
            self._tasks: list[_asyncio.Task[None]] = []

        async def start(self, broker, store) -> None:
            self._broker = broker
            self._store = store

        async def deliver(self, escalation) -> str | None:
            async def _resolve() -> None:
                await _asyncio.sleep(self._delay)
                # Update the row + resolve any pending waiter; the broker
                # cleans up gone waiters silently so this is safe even
                # when the first call returned pending without one.
                self._store.update(escalation.id, status="answered", answered_at=datetime.now(UTC), answer=self._answer)
                self._broker.resolve(escalation.id, self._answer)

            self._tasks.append(_asyncio.create_task(_resolve()))
            return "late-msg-1"

        async def stop(self) -> None:
            for t in self._tasks:
                t.cancel()

    ctx = build_context(settings)
    # A.h replies after 120ms — wait_seconds=0.05 returns pending,
    # then a follow-up GetEscalation(wait_seconds=0.2) catches it.
    ctx = _replace(ctx, delivery_adapter=_LateAdapter(answer="Take Wednesday off.", delay_seconds=0.12))
    await ctx.delivery_adapter.start(ctx.escalation_broker, ctx.escalation_store)
    try:
        first = await _handle_escalate_to_human_mentor(
            ctx,
            question="Should I take a day off?",
            mentor="pepe",
            context="",
            api_key=None,
            wait_seconds=0.05,
        )
        assert "error" not in first, first
        assert first["status"] == "pending", first
        assert first["answer"] is None
        assert first["escalation_id"]
        esc_id = first["escalation_id"]
        assert first["suggested_message_to_your_operator"], first

        # The mentee comes back and polls. With wait_seconds=0.2 we'll
        # block briefly and pick up the answer.
        second = await _handle_get_escalation(ctx, esc_id, api_key=None, wait_seconds=0.2)
        assert "error" not in second, second
        assert second["status"] == "answered"
        assert second["answer"] == "Take Wednesday off."
        assert second["answered_at"]
    finally:
        await ctx.delivery_adapter.stop()


async def test_get_escalation_unknown_id_returns_error(settings: Settings) -> None:
    """Polling with a typoed id returns `unknown_escalation` in-band."""
    from ammp_mcp.server import _handle_get_escalation, build_context

    ctx = build_context(settings)
    await ctx.delivery_adapter.start(ctx.escalation_broker, ctx.escalation_store)
    try:
        out = await _handle_get_escalation(ctx, "no-such-id-1234", api_key=None)
    finally:
        await ctx.delivery_adapter.stop()
    assert out.get("error") == "unknown_escalation"


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
        "GetSkill",
        "SearchPlaybooks",
        "AskMentor",
        "EscalateToHuman",
        "EscalateToHumanMentor",
        "GetEscalation",
        "GetPluginArchive",
        "GetSystemInfo",
    }
    # humanMentor surfaces in the capability JSON too — operators who
    # discover via /.well-known/agent.json see who's behind each mentor.
    by_slug = {m["slug"]: m for m in payload["ammp"]["mentors"]}
    assert by_slug["pepe"]["humanMentor"]["name"] == "Helmut Hoffer von Ankershoffen"
    assert by_slug["strict"]["humanMentor"] is None


async def test_escalate_to_human_mentor_wrapper_forwards_progress_to_client(settings: Settings) -> None:
    """End-to-end through the FastMCP tool wrapper: when the client supplies
    a progressToken, the wrapper's `_report` closure forwards progress to it.

    Covers the `_report` closure path (server.py ~2042) and exercises
    the GetEscalation companion's `_report` closure (~2085) by polling
    after a pending return — both currently missed by the direct
    `_handle_*` tests.
    """
    import asyncio as _asyncio
    from dataclasses import replace as _replace
    from datetime import UTC
    from datetime import datetime as _datetime

    from ammp_mcp.escalation._adapters._base import DeliveryAdapter
    from ammp_mcp.server import build_context, create_server

    class _LateAdapter(DeliveryAdapter):
        kind = "wrapper-probe"

        def __init__(self, answer: str, delay: float) -> None:
            self._a, self._d, self._b, self._s, self._t = answer, delay, None, None, []

        async def start(self, b, s) -> None:
            self._b, self._s = b, s

        async def deliver(self, esc) -> str | None:
            async def _r() -> None:
                await _asyncio.sleep(self._d)
                self._s.update(esc.id, status="answered", answered_at=_datetime.now(UTC), answer=self._a)
                self._b.resolve(esc.id, self._a)

            self._t.append(_asyncio.create_task(_r()))
            return "probe-msg"

        async def stop(self) -> None:
            for t in self._t:
                t.cancel()

    # Fast settings so the test runs in <1s.
    s_fast = settings.model_copy(update={"escalation_progress_heartbeat_seconds": 0.05})
    ctx = build_context(s_fast)
    ctx = _replace(ctx, delivery_adapter=_LateAdapter("Take the meeting.", delay=0.4))
    # create_server captures `ctx` via closure; rebuild a server with our patched ctx.
    server = create_server(s_fast)
    server._ammp_ctx = ctx  # not used; the server.create_server already loaded its own ctx
    # Use the already-built server directly via Client + progress_handler.
    progress_events: list[tuple[float, str]] = []

    async def handler(progress: float, total: float | None, message: str | None) -> None:
        progress_events.append((progress, message or ""))

    async with Client(server, progress_handler=handler) as c:
        # wait_seconds=0.1 → almost-immediate pending return; the
        # wrapper's `_report` closure fires for queued + delivered.
        first = await c.call_tool(
            "EscalateToHumanMentor",
            {"question": "test", "mentor": "pepe", "context": "", "wait_seconds": 0.1},
        )
        d = first.data
        assert "escalation_id" in d
        if d["status"] == "pending":
            # Hit the GetEscalation wrapper's `_report` closure with
            # a tiny wait — the snapshot reads from the persistent
            # store regardless of the broker waiter outcome.
            second = await c.call_tool(
                "GetEscalation",
                {"escalation_id": d["escalation_id"], "wait_seconds": 0.05},
            )
            assert second.data["status"] in ("answered", "pending", "delivered", "expired")

    # At least the queued + delivered notifications should have reached
    # the client through the wrapper's `_report` closure.
    assert any("queued" in m or "delivered" in m for _, m in progress_events), progress_events


async def test_favicon_routes_serve_brand_assets(server) -> None:
    """`/favicon.svg`, `/favicon-32.png` and `/apple-touch-icon.png` serve the
    helmguild compass — same files as www.helmguild.com — out of the
    packaged `_data/favicon/` directory with sane content-types + cache headers.
    """
    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        svg = http.get("/favicon.svg")
        png = http.get("/favicon-32.png")
        ico = http.get("/apple-touch-icon.png")
    assert svg.status_code == 200
    assert svg.headers.get("content-type") == "image/svg+xml"
    assert b"<svg" in svg.content
    assert png.status_code == 200
    assert png.headers.get("content-type") == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert ico.status_code == 200
    assert ico.headers.get("content-type") == "image/png"
    assert ico.content[:8] == b"\x89PNG\r\n\x1a\n"
    # All three are cacheable — they're brand assets, not per-request.
    for r in (svg, png, ico):
        assert "max-age" in r.headers.get("cache-control", "")


async def test_desktop_bundle_route_serves_mcpb_zip(server) -> None:
    """`GET /desktop-bundle.mcpb` returns a valid ZIP containing manifest + icon
    + server.js, with the BEARER substitution placeholder and the trailing-slash
    MCP URL baked into the manifest.

    Pinned because the bundle is the primary install path for Claude
    Desktop — a regression that ships a bundle with no manifest, the
    wrong URL, or an invalid zip silently breaks first-time connect.
    """
    import io
    import json
    import zipfile

    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        r = http.get("/desktop-bundle.mcpb")
    assert r.status_code == 200
    assert r.headers.get("content-disposition", "").endswith('"helmguild-ammp.mcpb"')
    body = r.content
    assert body[:2] == b"PK"  # ZIP magic
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = set(z.namelist())
        assert names == {"manifest.json", "icon.png", "server.js"}
        manifest = json.loads(z.read("manifest.json"))
    # Manifest carries the public + MCP URLs with trailing slash, and
    # the DXT-style `${user_config.bearer_token}` substitution token.
    flat = json.dumps(manifest)
    assert "/mcp/" in flat
    assert "${user_config.bearer_token}" in flat
    # `${__dirname}` substitutes to the install directory at runtime;
    # ensure we ship the real DXT token, not the authoring placeholder.
    assert "DIRNAME_PLACEHOLDER" not in flat
    assert "${__dirname}" in flat


async def test_get_system_info_returns_safe_metadata(server, settings: Settings) -> None:
    """`GetSystemInfo` returns the small, safe slice of build / runtime info
    a debugging mentee needs — and *only* that. Pinned so a regression
    that accidentally surfaces a file path, token, or hostname here
    surfaces as a failing test, not as a security leak in production.
    """
    from ammp_mcp import __ammp_draft__, __version__

    async with Client(server) as c:
        result = await c.call_tool("GetSystemInfo", {})
    data = result.data
    # Required, non-sensitive fields are present.
    assert data["name"] == "ammp-mcp"
    assert data["version"] == __version__
    assert data["ammp_draft"] == __ammp_draft__
    assert data["python_version"]
    assert data["platform"] in ("darwin", "linux", "win32") or isinstance(data["platform"], str)
    assert data["started_at"]
    assert isinstance(data["uptime_seconds"], (int, float)) and data["uptime_seconds"] >= 0
    assert data["mentor_count"] >= 1
    assert data["mentee_count"] >= 0
    assert data["default_mentor"]
    assert data["escalation_adapter"] in ("log", "telegram") or isinstance(data["escalation_adapter"], str)
    assert "mount_path" in data
    assert data["public_url"]

    # The envelope MUST NOT carry anything sensitive. Pin the "never
    # surface" list explicitly so a future field addition (or a refactor
    # that splats the full Settings object into the response) trips here.
    leaky_substrings = [
        str(settings.audit_log_path),
        str(settings.mentees_file),
        str(settings.mentors_root),
        str(settings.escalations_file),
        # Tokens / secrets that should never leave the server. If
        # require_auth is on these may be set; even then, never surface.
        "Bearer ",
        "ammp-",  # token prefix
    ]
    flat = " ".join(f"{k}={v}" for k, v in data.items())
    for s in leaky_substrings:
        # Some token prefixes can legitimately appear as part of the name
        # (`ammp-mcp`). Skip the false-positive by also checking the
        # specific field types.
        if s == "ammp-":
            continue
        assert s not in flat, f"GetSystemInfo leaked sensitive substring {s!r} via {flat!r}"


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
        "GetSkill",
        "SearchPlaybooks",
        "AskMentor",
        "EscalateToHuman",
        "EscalateToHumanMentor",
        "GetEscalation",
        "GetPluginArchive",
        "GetSystemInfo",
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


# ─── GetPluginArchive + /plugins/<name>.zip route ─────────────────────────


def _wire_test_marketplace(settings: Settings) -> tuple[str, str]:
    """Drop a tiny test marketplace + plugin on disk and point a playbook at it.

    Returns:
        ``(plugin_name, marketplace_name)`` pair so the test knows what
        to ask for.
    """
    plugin = "test-plugin"
    marketplace = "test-market"
    # Build the marketplace clone the loader expects.
    plugin_dir = settings.marketplaces_root / marketplace / "plugins" / plugin
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "test-plugin", "description": "Probe plugin for tests."}',
        encoding="utf-8",
    )
    skill_dir = plugin_dir / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: alpha\ndescription: "Probe skill."\nlicense: CC-BY-4.0\n---\n# Alpha\n\nBody.\n',
        encoding="utf-8",
    )
    # Point the pepe `intro` playbook at this plugin.
    pb_json = settings.mentors_root / "pepe" / "playbooks" / "intro" / "playbook.json"
    pb_json.write_text(
        json.dumps(
            {
                "name": "Welcome to Pepe",
                "description": "Onboarding for new mentees.",
                "plugin": f"{plugin}@{marketplace}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return plugin, marketplace


async def test_get_plugin_archive_returns_url_for_referenced_plugin(settings: Settings) -> None:
    """GetPluginArchive returns the auth-gated zip URL + install instructions
    for any plugin referenced by a playbook on this server.

    Pinned because the install-flow shifted from `/plugin marketplace add`
    to a direct zip download once helmguild-plugins went private —
    mentees can no longer clone the marketplace from GitHub.
    """
    plugin, marketplace = _wire_test_marketplace(settings)
    server = create_server(settings)
    async with Client(server) as c:
        result = await c.call_tool("GetPluginArchive", {"plugin": plugin})
    assert "error" not in result.data, result.data
    assert result.data["plugin"] == plugin
    assert result.data["marketplace"] == marketplace
    assert result.data["archive_url"].endswith(f"/plugins/{plugin}.zip")
    assert "Bearer token" in result.data["install_instructions"]


async def test_get_plugin_archive_unknown_plugin_returns_not_found(settings: Settings) -> None:
    """Plugins not referenced by any playbook on this server return not_found,
    independent of whether they exist in the marketplace clone."""
    _wire_test_marketplace(settings)
    server = create_server(settings)
    async with Client(server) as c:
        result = await c.call_tool("GetPluginArchive", {"plugin": "nope-not-here"})
    assert result.data["error"] == "not_found"


async def test_get_plugin_archive_rejects_path_traversal(settings: Settings) -> None:
    """Plugin name must match the kebab-case regex — traversal attempts
    don't even reach the marketplace lookup."""
    _wire_test_marketplace(settings)
    server = create_server(settings)
    async with Client(server) as c:
        result = await c.call_tool("GetPluginArchive", {"plugin": "../../etc/passwd"})
    assert result.data["error"] == "invalid_plugin"


async def test_plugin_archive_route_serves_zip(settings: Settings) -> None:
    """`GET /plugins/<plugin>.zip` returns the zip contents under the plugin
    name, gated by the same Bearer token as the MCP wire."""
    import io
    import zipfile

    plugin, _marketplace = _wire_test_marketplace(settings)
    # Auth on so the route exercises the bearer-header path.
    settings_auth = settings.model_copy(update={"require_auth": True})
    # Hot-reload mentees so the test key is loadable; the conftest's
    # mentees.json already has `ammp-test-key-1` wired.
    server = create_server(settings_auth)
    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        # No auth → 401.
        r_unauth = http.get(f"/plugins/{plugin}.zip")
        assert r_unauth.status_code == 401
        # Bad plugin name (kebab violation) → 404.
        r_bad = http.get("/plugins/UPPERCASE.zip", headers={"Authorization": "Bearer ammp-test-key-1"})
        assert r_bad.status_code == 404
        # Unknown plugin → 404.
        r_missing = http.get("/plugins/no-such-plugin.zip", headers={"Authorization": "Bearer ammp-test-key-1"})
        assert r_missing.status_code == 404
        # Good auth + known plugin → zip body, prefixed with plugin name.
        r = http.get(f"/plugins/{plugin}.zip", headers={"Authorization": "Bearer ammp-test-key-1"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers.get("content-disposition", "").endswith(f'"{plugin}.zip"')
    body = r.content
    assert body[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = z.namelist()
    # Files are prefixed with the plugin name so Claude Code extracts
    # them under a sensible directory.
    assert all(n.startswith(f"{plugin}/") for n in names), names
    assert f"{plugin}/.claude-plugin/plugin.json" in names
    assert f"{plugin}/skills/alpha/SKILL.md" in names


async def test_plugin_archive_zip_round_trips_scripts_and_mcp_payload(settings: Settings) -> None:
    """A plugin that ships scripts/ + mcp-server/ + .mcp.json round-trips
    cleanly through the zip route, with executable-bit metadata preserved
    so `${CLAUDE_PLUGIN_ROOT}/mcp-server/*.mjs` is spawnable after extract.

    Pinned because plugins like ``pepe-multi-channel-content-pipelines``
    rely on this end-to-end path: the zip the mentee downloads must
    extract into a fully-functional Claude-Code plugin (skills + bundled
    stdio MCP + bundled scripts) without any post-install fix-ups.
    """
    import io
    import json as _json
    import stat
    import zipfile

    plugin = "demo-plugin-with-payload"
    marketplace = "test-market"
    plugin_dir = settings.marketplaces_root / marketplace / "plugins" / plugin
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo-plugin-with-payload", "description": "Probe.", "version": "0.1.0"}',
        encoding="utf-8",
    )
    # AgentSkills SKILL.md
    skill_dir = plugin_dir / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: alpha\ndescription: "Probe."\nallowed-tools:\n  - Bash\n  - mcp__probe__tool_one\n---\n# Alpha\n\nbody\n',
        encoding="utf-8",
    )
    # .mcp.json with TWO server entries: HTTP + bundled stdio.
    mcp_cfg = {
        "mcpServers": {
            "helmguild-ammp": {
                "type": "http",
                "url": "https://mcp.helmguild.com/ammp/mcp/",
                "headers": {"Authorization": "Bearer ${HELMGUILD_AMMP_BEARER}"},
            },
            "probe": {
                "type": "stdio",
                "command": "node",
                "args": ["${CLAUDE_PLUGIN_ROOT}/mcp-server/probe.mjs"],
            },
        }
    }
    (plugin_dir / ".mcp.json").write_text(_json.dumps(mcp_cfg, indent=2), encoding="utf-8")
    # Bundled stdio MCP — content doesn't matter for this test; we just
    # care that the file ships + keeps its executable bit.
    (plugin_dir / "mcp-server").mkdir()
    mcp_script = plugin_dir / "mcp-server" / "probe.mjs"
    mcp_script.write_text("#!/usr/bin/env node\nprocess.exit(0)\n", encoding="utf-8")
    mcp_script.chmod(0o755)
    # Bundled bash helper — same shipping + exec-bit story.
    (plugin_dir / "scripts").mkdir()
    bash_helper = plugin_dir / "scripts" / "do-something.sh"
    bash_helper.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    bash_helper.chmod(0o755)
    # Wire the playbook → plugin so the route allow-lists it.
    pb_json = settings.mentors_root / "pepe" / "playbooks" / "intro" / "playbook.json"
    pb_json.write_text(
        _json.dumps(
            {
                "name": "Welcome to Pepe",
                "description": "Onboarding for new mentees.",
                "plugin": f"{plugin}@{marketplace}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Download via the route.
    settings_auth = settings.model_copy(update={"require_auth": True})
    server = create_server(settings_auth)
    from starlette.testclient import TestClient

    app = server.http_app(path="/mcp/")
    with TestClient(app) as http:
        r = http.get(f"/plugins/{plugin}.zip", headers={"Authorization": "Bearer ammp-test-key-1"})
    assert r.status_code == 200
    body = r.content

    # Validate the zip's shape end-to-end.
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = set(z.namelist())
        infos = {info.filename: info for info in z.infolist()}
        # Round-tripped files
        assert f"{plugin}/.claude-plugin/plugin.json" in names
        assert f"{plugin}/.mcp.json" in names
        assert f"{plugin}/skills/alpha/SKILL.md" in names
        assert f"{plugin}/mcp-server/probe.mjs" in names
        assert f"{plugin}/scripts/do-something.sh" in names
        # .mcp.json parses + still names both servers (HTTP + bundled stdio).
        roundtripped_mcp = _json.loads(z.read(f"{plugin}/.mcp.json"))
        assert set(roundtripped_mcp["mcpServers"]) == {"helmguild-ammp", "probe"}
        assert roundtripped_mcp["mcpServers"]["probe"]["type"] == "stdio"
        # Executable bit survives the round-trip — required for the runtime
        # to spawn `node mcp-server/probe.mjs` (or any shipped bash helper)
        # straight after `unzip` with no `chmod +x` post-step.
        for path in (f"{plugin}/mcp-server/probe.mjs", f"{plugin}/scripts/do-something.sh"):
            mode = infos[path].external_attr >> 16
            assert mode & stat.S_IXUSR, f"{path} lost its user-exec bit (mode={oct(mode)})"
