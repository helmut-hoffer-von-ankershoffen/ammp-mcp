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
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

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


def _authenticate(ctx: ServerContext, api_key: str | None) -> str:
    """Resolve a request to a mentee slug.

    Returns ``'anonymous'`` when ``require_auth`` is off; raises
    ``ValueError`` with a stable error code when auth is required and
    the key is missing or unrecognised. Callers translate the error
    code into an in-band ``{"error": "auth_failed"}`` response.
    """
    if not ctx.settings.require_auth:
        return "anonymous"
    if not api_key:
        raise ValueError("api_key_required")
    m = find_mentee_by_api_key(ctx.mentees, api_key)
    if not m:
        raise ValueError("api_key_invalid")
    return m.slug


def _resolve_mentor(ctx: ServerContext, slug: str) -> Mentor | None:
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
    return (
        f'To your operator: "My mentor returned an answer at confidence '
        f"{confidence:.2f}, below their threshold of "
        f"{threshold:.2f}, on this question — could you take a look "
        f'before I act on it? Question: {question[:300]}"'
    )


async def _handle_ask_mentor(
    ctx: ServerContext, question: str, mentor: str, context: str, api_key: str | None
) -> dict[str, Any]:
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

    return mcp
