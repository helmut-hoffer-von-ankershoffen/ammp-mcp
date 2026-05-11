"""FastMCP server exposing AMMP's Mentoring track.

Six operations: the AMMP §5 baseline (ListPlaybooks, GetPlaybook,
SearchPlaybooks, AskMentor, EscalateToHuman) plus a server-side
extension (ListMentors) so mentees can enumerate available mentors over
the same wire they use for everything else. Multi-mentor: each call
takes a `mentor: str` slug and the server routes to the right corpus.
Multi-mentee: when `require_auth` is enabled, requests must carry a
Bearer API key matching an entry in the mentee allowlist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from . import __ammp_draft__, __version__
from .audit import log_event, short_hash
from .backends import LLMAnswer, MentorBackend, build_backend
from .mentee import Mentee, find_mentee_by_api_key, load_mentees
from .mentor import Mentor, get_mentor, load_mentors
from .models import (
    AskMentorResponse,
    EscalateToHumanResponse,
    GetPlaybookResponse,
    ListMentorsResponse,
    ListPlaybooksResponse,
    MentorSummary,
    PlaybookEntry,
    PlaybookSummary,
    SearchMatch,
    SearchPlaybooksResponse,
)
from .playbook import keyword_rank, load_corpus, safe_id, search
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)


# ─── Per-server runtime context ───────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class ServerContext:
    """Everything a tool handler needs to do its work.

    Built once at boot by ``create_server``; passed by reference to every
    handler so the factory function itself stays small enough that
    SonarCloud's cognitive-complexity check (S3776) doesn't trip.
    """

    settings: Settings
    mentors: dict[str, Mentor]
    mentees: dict[str, Mentee]
    backends: dict[str, MentorBackend]


# Module-level mtime cache for the mentee allowlist. `_load_mentees_cached`
# only re-parses `mentees.json` when its mtime changes — so the steady-state
# auth path is one `stat()` per request, not a full JSON parse. The cache
# is keyed by path, so multiple Settings (e.g. concurrent tests) don't
# stomp on each other. `_mentees_cache_reset_for_testing` is the seam
# tests use to start each run with no cached state.
_mentees_cache: dict[Path, tuple[int, dict[str, Mentee]]] = {}


def _load_mentees_cached(path: Path) -> dict[str, Mentee]:
    """Load mentees with mtime-based invalidation.

    Args:
        path: Filesystem path to the JSON allowlist.

    Returns:
        The current mentees registry. A new parse fires only when
        ``path`` is freshly written (different ``st_mtime_ns``);
        unchanged files yield the cached value.
    """
    if not path.exists():
        _mentees_cache.pop(path, None)
        return {}
    mtime = path.stat().st_mtime_ns
    cached = _mentees_cache.get(path)
    if cached is None or cached[0] != mtime:
        _mentees_cache[path] = (mtime, load_mentees(path))
    return _mentees_cache[path][1]


def _mentees_cache_reset_for_testing() -> None:
    """Drop every cached mentees-snapshot. Test-only seam."""
    _mentees_cache.clear()


def _authenticate(ctx: ServerContext, api_key: str | None) -> str:
    """Resolve a request to a mentee slug.

    Returns ``'anonymous'`` when ``require_auth`` is off; raises
    ``ValueError`` with a stable error code when auth is required and
    the key is missing or unrecognised. Callers translate the error
    code into an in-band ``{"error": "auth_failed"}`` response.

    The mentee allowlist is hot-reloaded via :func:`_load_mentees_cached`
    — `ammp mentee add` / `rotate-key` / `remove` are visible to the
    running server on their next request, with no restart. Steady-state
    cost is one ``stat()`` per request; the JSON parse only re-fires
    when the file actually changed.

    Playbooks already hot-reload via :func:`load_corpus`. Mentor.json
    + backend instances stay cached in ``ctx`` because their lifecycle
    involves sockets / semaphores — that's a separate refactor.
    """
    if not ctx.settings.require_auth:
        return "anonymous"
    if not api_key:
        raise ValueError("api_key_required")
    mentees = _load_mentees_cached(ctx.settings.mentees_file)
    m = find_mentee_by_api_key(mentees, api_key)
    if not m:
        raise ValueError("api_key_invalid")
    return m.slug


def _resolve_mentor(ctx: ServerContext, slug: str) -> Mentor | None:
    """Resolve a caller-supplied slug to a :class:`Mentor` registry entry.

    Args:
        ctx: The per-request server context.
        slug: The mentor slug supplied by the caller. Empty string falls
            through to ``ctx.settings.default_mentor``.

    Returns:
        The matching mentor, or ``None`` when neither the explicit slug
        nor the configured default resolves to a registered mentor.
    """
    return get_mentor(ctx.mentors, slug, ctx.settings.default_mentor)


# ─── Tool handlers (pure functions; testable without FastMCP) ─────────────


def _handle_list_mentors(ctx: ServerContext, api_key: str | None) -> dict[str, Any]:
    """Enumerate all mentors hosted by this server.

    Server-side extension beyond AMMP-01's five operations. The same
    per-mentor data is also published in the capability JSON, but
    surfacing it as an MCP tool lets a mentee discover mentors over the
    same wire it uses for the other five ops.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    log_event(ctx.settings.audit_log_path, "ListMentors", mentee=mentee_slug)
    summaries: list[MentorSummary] = []
    for slug, m in ctx.mentors.items():
        corpus = load_corpus(m.playbook_dir)
        backend = ctx.backends.get(slug)
        # Surface the config-level kind (`anthropic`/`openclaw`/`stub`) — what
        # the docs say and what the operator wrote in mentor.json — rather
        # than the runtime mode label (`anthropic-direct` etc.). When the
        # mentor has no `backend` block it falls through to the global
        # Anthropic backend; report that as `anthropic` here.
        backend_kind = m.backend.kind if m.backend else "anthropic"
        summaries.append(
            MentorSummary(
                slug=slug,
                name=m.name,
                playbook_count=len(corpus),
                confidence_threshold=m.confidence_threshold,
                backend_kind=backend_kind,
                backend_live=backend.is_live if backend else False,
                is_default=(slug == ctx.settings.default_mentor),
                playbooks=[PlaybookEntry(id=pb.id, title=pb.title, summary=pb.summary, body=pb.body) for pb in corpus],
            )
        )
    return ListMentorsResponse(
        count=len(summaries),
        default_mentor=ctx.settings.default_mentor,
        mentors=summaries,
    ).model_dump()


def _handle_list_playbooks(ctx: ServerContext, mentor: str, api_key: str | None) -> dict[str, Any]:
    """Handle the ``ListPlaybooks`` MCP tool call (AMMP §5.1).

    Args:
        ctx: The per-request server context.
        mentor: Mentor slug. Empty string falls through to the
            configured default mentor.
        api_key: Caller-supplied Bearer key, or ``None``. Required when
            ``ctx.settings.require_auth`` is set.

    Returns:
        A JSON-serialisable dict — either a
        :class:`ListPlaybooksResponse` envelope on success, or an
        in-band error envelope ``{"error": ..., "detail": ...}`` on
        auth failure or unknown mentor.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    m = _resolve_mentor(ctx, mentor)
    if not m:
        return {"error": "unknown_mentor", "detail": f"slug={mentor or ctx.settings.default_mentor!r}"}
    log_event(ctx.settings.audit_log_path, "ListPlaybooks", mentor=m.slug, mentee=mentee_slug)
    corpus = load_corpus(m.playbook_dir)
    return ListPlaybooksResponse(
        mentor=m.slug,
        count=len(corpus),
        playbooks=[PlaybookSummary(id=pb.id, title=pb.title, summary=pb.summary) for pb in corpus],
    ).model_dump()


def _handle_get_playbook(ctx: ServerContext, playbook_id: str, mentor: str, api_key: str | None) -> dict[str, Any]:
    """Handle the ``GetPlaybook`` MCP tool call (AMMP §5.2).

    Args:
        ctx: The per-request server context.
        playbook_id: Filename-stem identifier of the requested playbook.
            Sanitised via :func:`ammp_mcp.playbook.safe_id` before
            filesystem access; path-traversal attempts return ``invalid_id``.
        mentor: Mentor slug. Empty string falls through to the
            configured default mentor.
        api_key: Caller-supplied Bearer key, or ``None``. Required when
            ``ctx.settings.require_auth`` is set.

    Returns:
        A JSON-serialisable dict — either a
        :class:`GetPlaybookResponse` envelope on success, or an in-band
        error envelope ``{"error": ..., "detail": ...}`` on auth
        failure, unknown mentor, invalid id, or not-found.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    m = _resolve_mentor(ctx, mentor)
    if not m:
        return {"error": "unknown_mentor"}
    clean_id = safe_id(playbook_id)
    if not clean_id:
        return {"error": "invalid_id"}
    log_event(
        ctx.settings.audit_log_path,
        "GetPlaybook",
        mentor=m.slug,
        mentee=mentee_slug,
        request_hash=short_hash(clean_id),
    )
    target = m.playbook_dir / f"{clean_id}.md"
    if not target.is_file():
        return {"error": "not_found", "detail": f"id={clean_id}"}
    from .playbook._service import _load_one  # lazy import to avoid widening the public surface

    pb = _load_one(target)
    return GetPlaybookResponse(mentor=m.slug, id=pb.id, title=pb.title, body=pb.body).model_dump()


def _handle_search_playbooks(
    ctx: ServerContext, query: str, mentor: str, limit: int, api_key: str | None
) -> dict[str, Any]:
    """Handle the ``SearchPlaybooks`` MCP tool call (AMMP §5.3).

    Args:
        ctx: The per-request server context.
        query: Substring-search query. Whitespace-only queries return
            an ``empty_query`` error.
        mentor: Mentor slug. Empty string falls through to the
            configured default mentor.
        limit: Maximum number of matches to return.
        api_key: Caller-supplied Bearer key, or ``None``. Required when
            ``ctx.settings.require_auth`` is set.

    Returns:
        A JSON-serialisable dict — either a
        :class:`SearchPlaybooksResponse` envelope on success, or an
        in-band error envelope ``{"error": ..., "detail": ...}``.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    m = _resolve_mentor(ctx, mentor)
    if not m:
        return {"error": "unknown_mentor"}
    if not query or not query.strip():
        return {"error": "empty_query"}
    log_event(
        ctx.settings.audit_log_path,
        "SearchPlaybooks",
        mentor=m.slug,
        mentee=mentee_slug,
        request_hash=short_hash(query),
    )
    corpus = load_corpus(m.playbook_dir)
    rows = search(corpus, query, limit=limit)
    return SearchPlaybooksResponse(
        mentor=m.slug,
        query=query,
        count=len(rows),
        matches=[SearchMatch(id=pb.id, title=pb.title, rank=rank, snippet=snippet) for pb, rank, snippet in rows],
    ).model_dump()


def _build_escalation_prompt(question: str, confidence: float, threshold: float) -> str:
    """Format the ``suggested_message_to_your_operator`` for mentor-triggered escalation.

    Built when AskMentor's self-reported confidence falls below the
    mentor's threshold; the mentee uses the returned phrasing to
    surface the situation to its own operator. Question is truncated
    to 300 characters to bound the response payload.

    Args:
        question: The original mentee question (truncated to 300 chars).
        confidence: The mentor's self-reported confidence in ``[0, 1]``.
        threshold: The mentor's configured escalation threshold.

    Returns:
        A first-person string the mentee can quote verbatim to its
        operator.
    """
    return (
        f'To your operator: "My mentor returned an answer at confidence '
        f"{confidence:.2f}, below their threshold of "
        f"{threshold:.2f}, on this question — could you take a look "
        f'before I act on it? Question: {question[:300]}"'
    )


async def _handle_ask_mentor(
    ctx: ServerContext, question: str, mentor: str, context: str, api_key: str | None
) -> dict[str, Any]:
    """Handle the ``AskMentor`` MCP tool call (AMMP §5.4).

    Routes the question through the mentor's configured backend,
    appending the top-3 keyword-ranked playbooks as grounding context.
    When the backend's self-reported confidence falls below the
    mentor's threshold, the response also carries a mentor-triggered
    escalation recommendation with suggested operator-facing phrasing.

    Args:
        ctx: The per-request server context.
        question: The free-form question. Empty / whitespace-only
            returns an ``empty_question`` error.
        mentor: Mentor slug. Empty string falls through to the
            configured default mentor.
        context: Optional context the mentee chooses to share; appended
            to the question for the backend.
        api_key: Caller-supplied Bearer key, or ``None``. Required when
            ``ctx.settings.require_auth`` is set.

    Returns:
        A JSON-serialisable dict — either an :class:`AskMentorResponse`
        envelope on success (with optional escalation recommendation),
        or an in-band error envelope. On backend failure, returns
        ``{"error": "llm_failed", "relevant_playbooks": [...]}`` so the
        mentee can fall back to ``GetPlaybook`` directly.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    m = _resolve_mentor(ctx, mentor)
    if not m:
        return {"error": "unknown_mentor"}
    q = (question or "").strip()
    if not q:
        return {"error": "empty_question"}

    log_event(
        ctx.settings.audit_log_path,
        "AskMentor",
        mentor=m.slug,
        mentee=mentee_slug,
        request_hash=short_hash(q),
    )

    corpus = load_corpus(m.playbook_dir)
    ranked = keyword_rank(corpus, q + " " + context, limit=3)
    relevant = [PlaybookSummary(id=pb.id, title=pb.title, summary=pb.summary) for pb, _ in ranked]
    playbook_bodies = [(pb.title, pb.body) for pb, _ in ranked]

    backend = ctx.backends.get(m.slug)
    if backend is None:
        return {"error": "unknown_mentor"}
    try:
        llm_answer: LLMAnswer = await backend.ask(
            mentor_name=m.name,
            persona=m.persona,
            question=q if not context else f"{q}\n\nContext:\n{context}",
            playbook_bodies=playbook_bodies,
        )
    except Exception as e:
        logger.warning("AskMentor backend call failed (%s): %s", backend.mode_label, e)
        return {
            "error": "llm_failed",
            "detail": "the mentor is currently unable to synthesise an answer; try GetPlaybook on a relevant id",
            "relevant_playbooks": [r.model_dump() for r in relevant],
        }

    threshold = m.confidence_threshold
    escalation_recommended = llm_answer.confidence < threshold
    suggested = _build_escalation_prompt(q, llm_answer.confidence, threshold) if escalation_recommended else None

    return AskMentorResponse(
        mentor=m.slug,
        question=q[:500],
        answer=llm_answer.answer,
        confidence=llm_answer.confidence,
        relevant_playbooks=relevant,
        escalation_recommended=escalation_recommended,
        suggested_message_to_your_operator=suggested,
    ).model_dump()


def _handle_escalate_to_human(
    ctx: ServerContext, situation: str, mentor: str, why_stuck: str, api_key: str | None
) -> dict[str, Any]:
    """Handle the ``EscalateToHuman`` MCP tool call (AMMP §5.5).

    Returns first-person guidance the mentee can hand to *its own*
    operator. The server never reaches across compartments — that's
    the Human-Gated Escalation Invariant (AMMP §3.4).

    Args:
        ctx: The per-request server context.
        situation: One-paragraph description of why the mentee is
            stuck. Empty / whitespace-only returns ``empty_situation``.
        mentor: Mentor slug. Empty string falls through to the
            configured default mentor.
        why_stuck: Optional note on what is making the mentee uncertain.
        api_key: Caller-supplied Bearer key, or ``None``. Required when
            ``ctx.settings.require_auth`` is set.

    Returns:
        A JSON-serialisable dict — either an
        :class:`EscalateToHumanResponse` envelope on success, or an
        in-band error envelope.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    m = _resolve_mentor(ctx, mentor)
    if not m:
        return {"error": "unknown_mentor"}
    s_situation = (situation or "").strip()
    if not s_situation:
        return {"error": "empty_situation"}
    log_event(
        ctx.settings.audit_log_path,
        "EscalateToHuman",
        mentor=m.slug,
        mentee=mentee_slug,
        request_hash=short_hash(s_situation),
    )
    suggested = (
        f"To your operator: \"I'm stuck on the following situation and "
        f"I think it needs your call. Situation: {s_situation[:300]}"
        + (f" — what's making me unsure: {why_stuck[:200]}" if why_stuck else "")
        + ". Could you take a look and tell me how to proceed? I won't "
        'act until you respond."'
    )
    return EscalateToHumanResponse(
        mentor=m.slug,
        guidance=(
            "The mentor cannot reach your operator. Use the suggested "
            "phrasing below to ask them yourself. Wait for their decision "
            "before acting."
        ),
        suggested_message_to_your_operator=suggested,
    ).model_dump()


# ─── Capability advertisement helper ──────────────────────────────────────


def _build_capability_payload(ctx: ServerContext) -> dict[str, Any]:
    """Assemble the ``/.well-known/agent.json`` AMMP capability payload.

    The live capability payload includes per-mentor backend liveness
    (which depends on backend instances held in ``ctx.backends``), so
    this function lives next to the FastMCP server. The offline
    counterpart for the ``ammp capability`` CLI is
    :func:`ammp_mcp.system._capability_service.build_offline_capability`;
    both MUST agree on the ``operations`` list and the per-mentor
    schema.

    Args:
        ctx: The boot-time server context.

    Returns:
        A JSON-serialisable dict — the full AMMP capability
        advertisement (AMMP §10).
    """
    mentor_summaries = []
    for slug, m in ctx.mentors.items():
        corpus = load_corpus(m.playbook_dir)
        backend = ctx.backends.get(slug)
        mentor_summaries.append(
            {
                "slug": slug,
                "name": m.name,
                "playbookCount": len(corpus),
                "backend": backend.mode_label if backend else "unknown",
                "backendLive": backend.is_live if backend else False,
            }
        )
    any_live = any(b.is_live for b in ctx.backends.values())
    return {
        "name": "ammp-mcp",
        "version": __version__,
        "url": ctx.settings.public_url,
        "description": (
            "Reference AMMP server (Mentoring track). Multi-mentor: each "
            "tool call routes to the named mentor's corpus. Privacy "
            "posture: no-retention; hash-only audit log; "
            "cross-compartment escalation prohibited."
        ),
        "ammp": {
            "draft": __ammp_draft__,
            "tracks": ["mentoring"],
            "mentors": mentor_summaries,
            "mentoring": {
                "askMentorMode": "per-mentor" if any_live else "structured-pointer",
                "askMentorMaxConcurrent": ctx.settings.llm_max_concurrent,
                "playbookLanguages": ["en"],
            },
            "privacyPosture": {
                "retention": "no-retention",
                "auditLog": "hash-only",
                "crossCompartmentEscalation": "prohibited",
                "requireAuth": ctx.settings.require_auth,
            },
            "bindings": ["mcp"],
            "invariants": ["compartmentalisation", "human-gated-escalation"],
        },
        "operations": [
            "ListMentors",
            "ListPlaybooks",
            "GetPlaybook",
            "SearchPlaybooks",
            "AskMentor",
            "EscalateToHuman",
        ],
    }


# ─── Human-facing landing page ────────────────────────────────────────────


def _render_landing(ctx: ServerContext) -> str:
    """Render the operator-facing landing page served at ``GET /``.

    Self-contained HTML (inline CSS + JS, no external assets). Lists
    the live mentors from ``ctx`` so a visitor immediately sees what
    this deployment exposes; then walks through how to connect as a
    mentee from each of the supported runtimes (Claude.ai, Claude
    Cowork, Claude Code, OpenClaw, Hermes) plus the bash-plus-CLI path.
    URLs have a Copy-to-clipboard button (clipboard.writeText, vanilla
    JS). The "Request a token" button is a `mailto:` with URL-encoded
    subject + body so the visitor's mail client opens pre-filled.

    Args:
        ctx: The boot-time server context — used for the live mentor
            list, the public URL, and the capability JSON link.

    Returns:
        A complete HTML document as a string, ready for ``HTMLResponse``.
    """
    from urllib.parse import quote as _q

    base = ctx.settings.public_url.rstrip("/")
    mcp_url = f"{base}/mcp/"
    claude_code_cmd = f'claude mcp add --scope user ammp-pepe {mcp_url} --header "Authorization: Bearer ammp-…"'

    # Pre-fill the operator's inbox with the three things the access flow
    # needs. The visitor edits the placeholders in the body.
    mailto_subject = "ammp-mcp — mentee token request"
    mailto_body = (
        "Hi Helmut,\n\n"
        f"I'd like to connect a mentee to {base}.\n\n"
        "  Mentee slug      : <kebab-case, e.g. claude-cowork-sandra>\n"
        "  Runtime          : <claude-ai | claude-cowork | claude-code | openclaw | hermes>\n"
        "  Delivery channel : <Signal / iMessage / Telegram + number>\n\n"
        "Thanks!\n"
    )
    mailto = f"mailto:helmuthva@gmail.com?subject={_q(mailto_subject)}&body={_q(mailto_body)}"

    mentor_rows = "".join(
        f'<li><span class="slug">{slug}</span> <span class="name">{m.name}</span>'
        f'<span class="count">{len(load_corpus(m.playbook_dir))} playbook(s)</span></li>'
        for slug, m in ctx.mentors.items()
    )
    if not mentor_rows:
        mentor_rows = '<li><span class="count">No mentors loaded.</span></li>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ammp-mcp · {base.replace("https://", "").replace("http://", "")}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="AMMP Mentoring-track server. Connect a mentee agent (Claude.ai, Claude Cowork, Claude Code, OpenClaw, Hermes) over MCP, or use the CLI.">
<style>
:root {{
  --bg-hi:#F2EDE2; --bg:#ECE6D9; --bg-lo:#E2DBC8;
  --ink:#1A1F2C; --ink-soft:#535868; --rule:#CFC8B6;
  --accent:#2E4F6B; --accent-hover:#3B6488;
  --serif:"Iowan Old Style","Apple Garamond","Palatino Linotype",Palatino,Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Inter","SF Pro Text","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg-hi:#1A1F2C; --bg:#161B25; --bg-lo:#11161F;
           --ink:#E8E3D6; --ink-soft:#98A0B2; --rule:#3A4150;
           --accent:#9FC5E8; --accent-hover:#BBD5F2; }}
}}
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:linear-gradient(180deg,var(--bg-hi),var(--bg) 50%,var(--bg-lo));background-attachment:fixed;color:var(--ink);font-family:var(--sans);font-size:17px;line-height:1.7;-webkit-font-smoothing:antialiased}}
main{{max-width:44rem;margin:0 auto;padding:3.5rem 1.75rem 3rem}}
h1{{font-family:var(--serif);font-weight:600;font-size:2.25rem;margin:0 0 .35rem;letter-spacing:-.01em}}
h2{{font-family:var(--serif);font-size:.82rem;font-weight:600;text-transform:uppercase;letter-spacing:.16em;color:var(--ink-soft);margin:2.75rem 0 1rem}}
h3{{font-family:var(--serif);font-size:1.05rem;margin:0 0 .35rem;font-weight:600}}
.lede{{color:var(--ink-soft);font-style:italic;margin:0 0 .5rem}}
.meta{{font-family:var(--mono);font-size:.82rem;color:var(--ink-soft);margin:1.25rem 0 0}}
a{{color:var(--accent);text-decoration:none;border-bottom:1px solid color-mix(in srgb,var(--accent) 30%,transparent);padding-bottom:1px}}
a:hover{{color:var(--accent-hover);border-bottom-color:var(--accent-hover)}}
code,kbd,pre{{font-family:var(--mono);font-size:.92em}}
pre{{background:rgba(0,0,0,.045);border:1px solid var(--rule);border-radius:6px;padding:.9rem 1rem;overflow-x:auto;font-size:.85rem;line-height:1.55;margin:.75rem 0 0}}
@media (prefers-color-scheme: dark) {{ pre{{background:rgba(255,255,255,.04)}} }}
.integration{{border-top:1px solid var(--rule);padding:1.25rem 0}}
.integration:last-of-type{{border-bottom:1px solid var(--rule)}}
.integration .lead{{color:var(--ink-soft);margin:.15rem 0 .35rem;font-size:.95rem}}
.mentor-list{{margin:0;padding:0;list-style:none;border-top:1px solid var(--rule)}}
.mentor-list li{{display:flex;gap:1rem;align-items:baseline;border-bottom:1px solid var(--rule);padding:.55rem 0;font-family:var(--mono);font-size:.92rem}}
.mentor-list .slug{{color:var(--accent);min-width:8rem}}
.mentor-list .name{{color:var(--ink)}}
.mentor-list .count{{color:var(--ink-soft);font-size:.85em;margin-left:auto}}
footer{{color:var(--ink-soft);font-size:.82rem;margin-top:3rem;padding-top:1.5rem;border-top:1px solid var(--rule)}}
footer a{{color:var(--ink-soft);border-bottom-color:var(--rule)}}
button{{font:inherit}}
.btn{{display:inline-flex;align-items:center;gap:.4rem;padding:.35rem .75rem;margin:.5rem .5rem 0 0;border:1px solid var(--accent);border-radius:5px;background:transparent;color:var(--accent);font-family:var(--sans);font-size:.85rem;cursor:pointer;text-decoration:none}}
.btn:hover{{background:var(--accent);color:var(--bg)}}
.btn.copy{{font-family:var(--mono);font-size:.78rem;padding:.25rem .55rem;margin-left:.5rem;vertical-align:middle}}
.btn.copy[data-copied="1"]{{border-color:#3a8a3a;color:#3a8a3a;background:transparent}}
.btn.primary{{background:var(--accent);color:var(--bg);border-color:var(--accent)}}
.btn.primary:hover{{background:var(--accent-hover);border-color:var(--accent-hover)}}
.url-row{{font-family:var(--mono);font-size:.85rem;margin:.4rem 0}}
.url-row .label{{color:var(--ink-soft);display:inline-block;min-width:5.5rem}}
.url-row code{{background:rgba(0,0,0,.045);border:1px solid var(--rule);border-radius:4px;padding:.1rem .4rem;font-size:.92em}}
@media (prefers-color-scheme: dark) {{ .url-row code{{background:rgba(255,255,255,.04)}} }}
</style>
</head>
<body>
<main>

<h1>ammp-mcp</h1>
<p class="lede">AMMP Mentoring-track server — multi-mentor, multi-mentee, privacy-preserving.</p>
<p class="meta">{base} · <a href="{base}/.well-known/agent.json">capability JSON</a> · <a href="https://www.helmguild.com/rfc/ammp/">RFC draft-ammp-01</a> · <a href="https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp">source</a></p>

<h2>Mentors hosted here</h2>
<ul class="mentor-list">
{mentor_rows}
</ul>

<h2>Requesting access</h2>
<p>Every mentee call carries a per-mentee Bearer token. There is no self-service mint endpoint — gatekept by design (AMMP's allowlist is what makes <em>multi-mentee</em> a privacy posture, not a wishful default).</p>
<p>
  <a class="btn primary" href="{mailto}">📧 Request a token</a>
</p>
<p><strong>If you want to connect:</strong> the button opens your mail client pre-filled with the three pieces of info the operator needs — the mentee <code>slug</code> you want (kebab-case, e.g. <code>claude-cowork-sandra</code>), the runtime you'll connect from (<code>claude-ai</code>, <code>claude-cowork</code>, <code>claude-code</code>, <code>openclaw</code>, or <code>hermes</code>), and a secure channel (Signal / iMessage / Telegram) to receive the token on. The operator mints + sends it; the token is shown to them <strong>once</strong> and never re-derivable.</p>
<p><strong>If you are the operator:</strong> on the host where ammp-mcp runs, mint a mentee and copy the plaintext token straight into a secure channel (Signal, 1Password share, encrypted email — never plain email / Slack / SMS). The token format is <code>ammp-&lt;32 url-safe bytes&gt;</code>; only its SHA-256 lands on disk.</p>
<pre>$ ammp mentee add claude-cowork-sandra \\
    --operator human:sandra --runtime claude-cowork
✓ Minted first mentee claude-cowork-sandra.
API KEY for the first mentee — copy now, you will not see it again:
  ammp-zxV3l8_Z_cXY1cRVr22cQY3RTOrWZ2E7EpOVldb_YrE</pre>
<p>If a token leaks, rotate it: <code>ammp mentee rotate-key &lt;slug&gt;</code> prints a fresh one and invalidates the old hash on disk. To revoke entirely, <code>ammp mentee remove &lt;slug&gt;</code>.</p>

<h2>Connect as a mentee</h2>

<div class="integration">
<h3>Claude.ai <span class="meta">runtime: <code>claude-ai</code></span></h3>
<p class="lead">Settings → Connectors → Custom. Paste the MCP URL and Bearer token.</p>
<div class="url-row"><span class="label">URL:</span> <code>{mcp_url}</code> <button class="btn copy" data-copy="{mcp_url}">Copy</button></div>
<div class="url-row"><span class="label">Header:</span> <code>Authorization: Bearer ammp-…</code></div>
</div>

<div class="integration">
<h3>Claude Cowork <span class="meta">runtime: <code>claude-cowork</code></span></h3>
<p class="lead">Add as a custom MCP connector. The Bearer key is per-mentee — request one above.</p>
<div class="url-row"><span class="label">URL:</span> <code>{mcp_url}</code> <button class="btn copy" data-copy="{mcp_url}">Copy</button></div>
<div class="url-row"><span class="label">Header:</span> <code>Authorization: Bearer ammp-…</code></div>
</div>

<div class="integration">
<h3>Claude Code <span class="meta">runtime: <code>claude-code</code></span></h3>
<p class="lead">One CLI call registers the server at user scope so every cwd sees it. Swap <code>ammp-…</code> for the token you received.</p>
<div class="url-row"><code>{claude_code_cmd}</code> <button class="btn copy" data-copy='{claude_code_cmd}'>Copy</button></div>
</div>

<div class="integration">
<h3>OpenClaw <span class="meta">runtime: <code>openclaw</code></span></h3>
<p class="lead">OpenClaw natively speaks MCP. Add the server via your runtime's connector UI or its config file with the same URL + Bearer header.</p>
<div class="url-row"><span class="label">URL:</span> <code>{mcp_url}</code> <button class="btn copy" data-copy="{mcp_url}">Copy</button></div>
<div class="url-row"><span class="label">Header:</span> <code>Authorization: Bearer ammp-…</code></div>
</div>

<div class="integration">
<h3>Hermes <span class="meta">runtime: <code>hermes</code></span></h3>
<p class="lead">Hermes connects to MCP servers through its standard tool-server registry. Point it at the same URL + Bearer; no Hermes-specific handshake.</p>
<div class="url-row"><span class="label">URL:</span> <code>{mcp_url}</code> <button class="btn copy" data-copy="{mcp_url}">Copy</button></div>
<div class="url-row"><span class="label">Header:</span> <code>Authorization: Bearer ammp-…</code></div>
</div>

<h2>Or use via CLI</h2>
<p>Every wire-level operation has a matching <code>ammp</code> subcommand. Useful for shell-plus-Bash agents that prefer not to speak MCP.</p>
<pre>uvx ammp-mcp                                              # install + run
ammp mentor list                                          # who is hosted here
ammp playbook list --mentor pepe                          # the corpus
ammp playbook show 01-cite-or-decline --mentor pepe       # one playbook body
ammp mentor ask "how do you stay grounded?" --mentor pepe # AskMentor parity
ammp mentor escalate "two playbooks contradict" --mentor pepe \\
  --why-stuck "neither covers idempotency"                # EscalateToHuman parity</pre>

<h2>Privacy posture</h2>
<p>No-retention. Mentor never accumulates a profile of the mentee. Audit log is hash-only — operation, mentor slug, mentee slug, opaque 8-hex hash, never any payload. Cross-compartment escalation is prohibited: <code>EscalateToHuman</code> returns guidance text the mentee hands to <em>its own</em> operator. See <a href="https://www.helmguild.com/rfc/ammp/">draft-ammp-01 §6.2 / §3.4</a> for the normative wording.</p>

<footer>
<p>Reference implementation of the <a href="https://www.helmguild.com/rfc/ammp/">Agentic Mentor-Mentee Protocol</a>. MIT-licensed. Operator: <a href="https://helmut.hoffer-von-ankershoffen.me/">Helmut Hoffer von Ankershoffen</a>.</p>
</footer>

</main>

<script>
// Tiny copy-to-clipboard handler. Falls back gracefully on browsers that
// don't allow async clipboard access (rare, mostly old Safari over HTTP).
document.querySelectorAll('button.btn.copy').forEach(function(b) {{
  b.addEventListener('click', async function() {{
    try {{
      await navigator.clipboard.writeText(b.dataset.copy);
      var prev = b.textContent;
      b.textContent = 'Copied ✓';
      b.setAttribute('data-copied', '1');
      setTimeout(function() {{
        b.textContent = prev;
        b.removeAttribute('data-copied');
      }}, 1500);
    }} catch (e) {{
      b.textContent = 'Press ⌘C';
    }}
  }});
}});
</script>
</body>
</html>"""


# ─── Server factory ──────────────────────────────────────────────────────


def build_context(settings: Settings | None = None) -> ServerContext:
    """Build a :class:`ServerContext` from settings.

    Loads mentors, the mentee allowlist, and per-mentor backends — the same
    state ``create_server`` initialises. Exposed so the CLI surface (which
    invokes handlers directly without spinning up the MCP server) can build
    the same context the in-process handlers expect.

    Args:
        settings: Settings instance. ``None`` uses the env-derived
            singleton from :func:`ammp_mcp.settings.get_settings`.

    Returns:
        A fully-populated :class:`ServerContext` ready to pass to any of the
        ``_handle_*`` functions in this module.
    """
    s = settings or get_settings()
    mentors = load_mentors(s.mentors_root)
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    backends: dict[str, MentorBackend] = {slug: build_backend(m.backend, s) for slug, m in mentors.items()}
    return ServerContext(settings=s, mentors=mentors, mentees=mentees, backends=backends)


def create_server(settings: Settings | None = None) -> FastMCP:
    """Build a FastMCP server bound to ``settings``.

    Loads mentors, the mentee allowlist, and per-mentor backends; then
    registers the AMMP Mentoring-track tools (five from §5 plus the
    ``ListMentors`` server-side extension) and the capability
    advertisement endpoint.

    Args:
        settings: Settings instance. ``None`` uses the env-derived
            singleton from :func:`ammp_mcp.settings.get_settings`.

    Returns:
        A configured FastMCP server ready to ``.run()``.
    """
    ctx = build_context(settings)

    logger.info(
        "boot: %d mentors loaded (%s), %d mentees in allowlist, backends={%s}",
        len(ctx.mentors),
        ",".join(ctx.mentors) or "none",
        len(ctx.mentees),
        ", ".join(f"{slug}:{b.mode_label}({'live' if b.is_live else 'stub'})" for slug, b in ctx.backends.items())
        or "none",
    )
    mcp: FastMCP[Any] = FastMCP(
        name="ammp-mcp",
        instructions=(
            "AMMP Mentoring track reference implementation. Each tool takes a "
            "`mentor` slug routing to the relevant playbook corpus. Privacy "
            "posture: no-retention; the mentor never accumulates a profile of "
            "the mentee. EscalateToHuman returns guidance text the mentee "
            "hands to its own operator — the mentor never reaches across "
            "compartments."
        ),
    )

    # MCP tool functions are deliberately PascalCase — the function name
    # becomes the on-the-wire AMMP operation name. They are thin wrappers
    # that delegate to the module-level handlers above so the cognitive
    # complexity of `create_server` stays well under SonarCloud's S3776 limit.
    # Five of the six are AMMP §5 baseline; `ListMentors` is a server-side
    # extension over the draft (mirrors the capability JSON's mentor block).

    @mcp.tool
    def ListMentors(api_key: str | None = None) -> dict[str, Any]:
        """List the mentors this server hosts.

        Each entry includes slug, display name, playbook count, confidence
        threshold, backend kind, whether the backend is live, and whether
        this mentor is the configured default. Use the returned slugs in
        the other five operations.
        """
        return _handle_list_mentors(ctx, api_key)

    @mcp.tool
    def ListPlaybooks(mentor: str = "", api_key: str | None = None) -> dict[str, Any]:
        """Enumerate the curated playbook corpus for the given mentor."""
        return _handle_list_playbooks(ctx, mentor, api_key)

    @mcp.tool
    def GetPlaybook(id: str, mentor: str = "", api_key: str | None = None) -> dict[str, Any]:
        """Retrieve a single playbook from the given mentor's corpus."""
        return _handle_get_playbook(ctx, id, mentor, api_key)

    @mcp.tool
    def SearchPlaybooks(query: str, mentor: str = "", limit: int = 5, api_key: str | None = None) -> dict[str, Any]:
        """Substring-search the mentor's corpus. Returns ranked matches."""
        return _handle_search_playbooks(ctx, query, mentor, limit, api_key)

    @mcp.tool
    async def AskMentor(
        question: str,
        mentor: str = "",
        context: str = "",
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Ask the mentor a free-form question.

        The mentor synthesises an answer from its playbook corpus +
        general operational knowledge, and returns a self-reported
        confidence in ``[0, 1]``. When confidence is below the mentor's
        threshold, the response also recommends ``EscalateToHuman``
        with suggested phrasing — *mentor-triggered* escalation, in
        addition to the mentee being free to call ``EscalateToHuman``
        directly.
        """
        return await _handle_ask_mentor(ctx, question, mentor, context, api_key)

    @mcp.tool
    def EscalateToHuman(
        situation: str,
        mentor: str = "",
        why_stuck: str = "",
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Mentee-triggered escalation to the mentee's own operator.

        Returns suggested phrasing the mentee hands to its own
        operator. The mentor does not page or message anyone — that's
        the Human-Gated Escalation Invariant (AMMP §3.4).
        """
        return _handle_escalate_to_human(ctx, situation, mentor, why_stuck, api_key)

    @mcp.custom_route("/.well-known/agent.json", methods=["GET"])
    async def agent_card(_request: Request) -> JSONResponse:
        """AMMP capability advertisement. AMMP §10."""
        return JSONResponse(_build_capability_payload(ctx))

    @mcp.custom_route("/", methods=["GET"])
    async def landing(_request: Request) -> HTMLResponse:
        """Human-facing landing page — how to connect a mentee + CLI usage."""
        return HTMLResponse(
            _render_landing(ctx),
            # The mentor list is rendered from live ctx; the integration
            # cards quote the live public URL. Cache should never serve
            # a stale version of either. `no-store` plus the long-form
            # `no-cache, must-revalidate` belt-and-braces tells every
            # browser + intermediate (Cloudflare, Caddy) not to hold it.
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    return mcp
