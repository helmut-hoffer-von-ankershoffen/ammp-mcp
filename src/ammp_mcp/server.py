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

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.server.tasks import TaskConfig
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import __ammp_draft__, __version__
from .audit import log_event, short_hash
from .backends import LLMAnswer, MentorBackend, build_backend
from .escalation import EscalationBroker, EscalationStore
from .escalation._adapters import DeliveryAdapter, DeliveryError, LogDeliveryAdapter, TelegramDeliveryAdapter
from .escalation._service import expire_orphaned_pending, new_escalation
from .mentee import Mentee, find_mentee_by_api_key, load_mentees
from .mentor import Mentor, get_mentor, load_mentors
from .models import (
    AskMentorResponse,
    EscalateToHumanMentorResponse,
    EscalateToHumanResponse,
    EscalationToHumanMentorDraft,
    GetEscalationResponse,
    GetPlaybookResponse,
    GetSkillResponse,
    GetSystemInfoResponse,
    HumanMentorSummary,
    ListMentorsResponse,
    ListPlaybooksResponse,
    MentorSummary,
    PlaybookEntry,
    PlaybookSummary,
    SearchMatch,
    SearchPlaybooksResponse,
    SkillSummary,
)
from .playbook import (
    build_plugin_archive_response,
    enumerate_plugin_refs,
    keyword_rank,
    load_playbooks,
    safe_id,
    search,
)
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
    escalation_store: EscalationStore
    escalation_broker: EscalationBroker
    delivery_adapter: DeliveryAdapter
    started_at: datetime


def _build_delivery_adapter(s: Settings) -> DeliveryAdapter:
    """Construct the configured delivery adapter, or fall back to log-only.

    ``escalation_adapter=telegram`` requires both the bot token and
    chat id to be set; missing either degrades to the log adapter
    with a warning so the server still boots cleanly on a fresh
    install. Returns whichever adapter is appropriate.

    Args:
        s: The settings instance whose escalation-related fields
            drive adapter selection.

    Returns:
        A :class:`DeliveryAdapter` ready to ``start()``.
    """
    if s.escalation_adapter == "telegram":
        if not (s.escalation_telegram_bot_token and s.escalation_telegram_chat_id):
            logger.warning(
                "escalation_adapter=telegram but bot_token / chat_id not configured; falling back to log adapter"
            )
            return LogDeliveryAdapter()
        return TelegramDeliveryAdapter(
            bot_token=s.escalation_telegram_bot_token,
            chat_id=s.escalation_telegram_chat_id,
        )
    return LogDeliveryAdapter()


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
    # No explicit api_key supplied (the MCP tool wrappers don't ask the
    # LLM for one) — fall back to the HTTP `Authorization: Bearer <…>`
    # header carried by the request. FastMCP populates a request-scoped
    # ContextVar via its RequestContextMiddleware; we read it through
    # the public `get_http_request()` accessor. The CLI path passes
    # api_key explicitly and skips this fallback.
    if not api_key:
        try:
            from fastmcp.server.dependencies import get_http_request

            request = get_http_request()
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                api_key = auth_header[len("bearer ") :].strip() or None
        except Exception:
            # No HTTP request in scope (e.g. stdio mode or unit tests
            # invoking _handle_* directly without a transport). Fall
            # through to the missing-key error below.
            pass
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
        corpus = load_playbooks(m.playbook_dir, marketplaces_root=ctx.settings.marketplaces_root)
        backend = ctx.backends.get(slug)
        # Surface the config-level kind (`anthropic`/`openclaw`/`stub`) — what
        # the docs say and what the operator wrote in mentor.json — rather
        # than the runtime mode label (`anthropic-direct` etc.). When the
        # mentor has no `backend` block it falls through to the global
        # Anthropic backend; report that as `anthropic` here.
        backend_kind = m.backend.kind if m.backend else "anthropic"
        # Public URL is None when no avatar.* lives in the mentor dir.
        # The landing-side renderer also calls `m.avatar_path()` directly
        # for its <img> tag so it can fall back to an initial glyph; the
        # MCP envelope just exposes the URL so wire-level mentees can use
        # it however they like (display, profile card, none at all).
        avatar_url = f"{ctx.settings.public_url.rstrip('/')}/mentors/{slug}/avatar" if m.avatar_path() else None
        human_mentor = (
            HumanMentorSummary(
                name=m.human_mentor.name,
                url=m.human_mentor.url,
                profile_url=m.human_mentor.profile_url,
                contact=m.human_mentor.contact,
            )
            if m.human_mentor
            else None
        )
        # Embed skill summaries only — bodies are fetched on demand
        # via GetPlaybook / GetSkill. Without this, a corpus with a
        # few dozen skills inflates the response to 100+ KB which
        # trips Claude Cowork's MCP-transport timeout.
        playbook_entries = [
            PlaybookEntry(
                id=pb.id,
                name=pb.name,
                description=pb.description,
                skills=[SkillSummary(id=sk.id, title=sk.title, summary=sk.summary) for sk in pb.skills],
            )
            for pb in corpus
        ]
        summaries.append(
            MentorSummary(
                slug=slug,
                name=m.name,
                description=m.description,
                profile_url=m.profile_url,
                avatar_url=avatar_url,
                human_mentor=human_mentor,
                playbook_count=len(corpus),
                skill_count=sum(len(pb.skills) for pb in corpus),
                confidence_threshold=m.confidence_threshold,
                backend_kind=backend_kind,
                backend_live=backend.is_live if backend else False,
                is_default=(slug == ctx.settings.default_mentor),
                playbooks=playbook_entries,
            )
        )
    return ListMentorsResponse(
        count=len(summaries),
        default_mentor=ctx.settings.default_mentor,
        mentors=summaries,
    ).model_dump()


def _handle_list_playbooks(ctx: ServerContext, mentor: str, api_key: str | None) -> dict[str, Any]:
    """Handle the ``ListPlaybooks`` MCP tool call (AMMP §5.1).

    Returns the mentor's playbooks (areas of practice). Each playbook
    carries its name + description + skill summaries (id + title +
    one-line summary, no bodies). Fetch a full playbook's skill
    bodies with ``GetPlaybook(id)``, or one skill's body with
    ``GetSkill(playbook_id, id)``.

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
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=ctx.settings.marketplaces_root)
    return ListPlaybooksResponse(
        mentor=m.slug,
        count=len(corpus),
        playbooks=[
            PlaybookSummary(
                id=pb.id,
                name=pb.name,
                description=pb.description,
                requires=list(pb.requires),
                validation_prompt_count=len(pb.validation.get("prompts", []) or []),
                skill_count=len(pb.skills),
                skills=[SkillSummary(id=sk.id, title=sk.title, summary=sk.summary) for sk in pb.skills],
            )
            for pb in corpus
        ],
    ).model_dump()


def _handle_get_playbook(ctx: ServerContext, playbook_id: str, mentor: str, api_key: str | None) -> dict[str, Any]:
    """Handle the ``GetPlaybook`` MCP tool call (AMMP §5.2).

    Returns one playbook with the full body of every skill
    in it — a single round-trip for everything the mentee needs about
    one area of practice.

    Args:
        ctx: The per-request server context.
        playbook_id: Directory-name slug of the requested playbook.
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
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=ctx.settings.marketplaces_root)
    pb = next((p for p in corpus if p.id == clean_id), None)
    if pb is None:
        return {"error": "not_found", "detail": f"id={clean_id}"}
    return GetPlaybookResponse(
        mentor=m.slug,
        id=pb.id,
        name=pb.name,
        description=pb.description,
        requires=list(pb.requires),
        validation_prompt_count=len(pb.validation.get("prompts", []) or []),
        skills=[SkillSummary(id=sk.id, title=sk.title, summary=sk.summary) for sk in pb.skills],
    ).model_dump()


def _handle_get_skill(
    ctx: ServerContext, playbook_id: str, skill_id: str, mentor: str, api_key: str | None
) -> dict[str, Any]:
    """Handle the ``GetSkill`` MCP-extension tool call.

    Server-side extension beyond AMMP-01's five operations: fetch one
    specific skill by ``(playbook_id, skill_id)`` when the mentee
    already knows which one it wants, without round-tripping the
    whole playbook.

    Args:
        ctx: The per-request server context.
        playbook_id: Directory-name slug of the parent playbook.
        skill_id: Folder name (AgentSkills) or filename stem (legacy)
            of the requested skill.
        mentor: Mentor slug. Empty string falls through to the
            configured default mentor.
        api_key: Caller-supplied Bearer key, or ``None``. Required when
            ``ctx.settings.require_auth`` is set.

    Returns:
        A JSON-serialisable dict — either a :class:`GetSkillResponse`
        envelope on success, or an in-band error envelope on auth
        failure, unknown mentor, invalid id, or not-found.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    m = _resolve_mentor(ctx, mentor)
    if not m:
        return {"error": "unknown_mentor"}
    clean_pb = safe_id(playbook_id)
    clean_id = safe_id(skill_id)
    if not clean_pb or not clean_id:
        return {"error": "invalid_id"}
    log_event(
        ctx.settings.audit_log_path,
        "GetSkill",
        mentor=m.slug,
        mentee=mentee_slug,
        request_hash=short_hash(f"{clean_pb}/{clean_id}"),
    )
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=ctx.settings.marketplaces_root)
    pb = next((p for p in corpus if p.id == clean_pb), None)
    if pb is None:
        return {"error": "not_found", "detail": f"playbook_id={clean_pb}"}
    sk = next((s for s in pb.skills if s.id == clean_id), None)
    if sk is None:
        return {"error": "not_found", "detail": f"playbook_id={clean_pb} id={clean_id}"}
    return GetSkillResponse(
        mentor=m.slug,
        playbook_id=pb.id,
        id=sk.id,
        title=sk.title,
        summary=sk.summary,
        body=sk.body,
    ).model_dump()


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
    corpus = load_playbooks(m.playbook_dir, marketplaces_root=ctx.settings.marketplaces_root)
    rows = search(corpus, query, limit=limit)
    return SearchPlaybooksResponse(
        mentor=m.slug,
        query=query,
        count=len(rows),
        matches=[
            SearchMatch(
                playbook_id=wi.playbook_id,
                id=wi.id,
                title=wi.title,
                rank=rank,
                snippet=snippet,
            )
            for wi, rank, snippet in rows
        ],
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

    corpus = load_playbooks(m.playbook_dir, marketplaces_root=ctx.settings.marketplaces_root)
    ranked = keyword_rank(corpus, q + " " + context, limit=3)
    relevant = [SkillSummary(id=sk.id, title=sk.title, summary=sk.summary) for sk, _ in ranked]
    # Backends consume `(title, body)` pairs to assemble the grounding
    # block in the system prompt. We cite each skill by its full
    # ``<playbook> · <skill>`` path so the LLM can attribute back
    # accurately when synthesising.
    playbook_bodies = [(f"{sk.playbook_id} · {sk.title}", sk.body) for sk, _ in ranked]

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
            "detail": "the mentor is currently unable to synthesise an answer; try GetSkill on a relevant id",
            "relevant_skills": [r.model_dump() for r in relevant],
        }

    threshold = m.confidence_threshold
    escalation_recommended = llm_answer.confidence < threshold
    suggested = _build_escalation_prompt(q, llm_answer.confidence, threshold) if escalation_recommended else None
    # When confidence is low AND a human mentor is configured, also
    # surface a draft B.a can ask B.h to approve forwarding via
    # EscalateToHumanMentor. The draft text is the same question text;
    # B.a / B.h may edit before invoking the tool.
    escalation_draft: EscalationToHumanMentorDraft | None = None
    if escalation_recommended and m.human_mentor is not None:
        hm = m.human_mentor
        escalation_draft = EscalationToHumanMentorDraft(
            question=q,
            human_mentor=HumanMentorSummary(name=hm.name, url=hm.url, profile_url=hm.profile_url, contact=hm.contact),
            suggested_message_to_your_operator=(
                f"To your operator: \"My mentor isn't confident enough to answer this on its own. "
                f"They've offered to forward a B.h-approved version of the question to their human mentor, "
                f"{hm.name}. Could you review and approve the draft below before I forward it? "
                f"I won't forward anything until you say go."
                f'\n\nDraft to forward: {q[:600]}"'
            ),
        )

    return AskMentorResponse(
        mentor=m.slug,
        question=q[:500],
        answer=llm_answer.answer,
        confidence=llm_answer.confidence,
        relevant_skills=relevant,
        escalation_recommended=escalation_recommended,
        suggested_message_to_your_operator=suggested,
        escalation_to_human_mentor_draft=escalation_draft,
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


async def _wait_for_escalation_answer(
    ctx: ServerContext,
    esc_id: str,
    waiter: Any,
    human_mentor_name: str,
    wait_seconds: float,
    progress: Callable[[float, str], Awaitable[None]] | None,
    base_progress: float = 0.5,
) -> tuple[str, str | None]:
    """Block up to ``wait_seconds`` for the broker waiter to resolve.

    Shared by ``EscalateToHumanMentor`` (post-delivery wait) and
    ``GetEscalation`` (poll-with-wait). Caps the wait at
    ``wait_seconds`` rather than the full escalation timeout so the
    MCP client's per-tool deadline isn't exceeded — the escalation
    itself stays in ``delivered`` state and can be polled again.

    Args:
        ctx: Per-request server context.
        esc_id: Escalation id (used to revert state on cancellation).
        waiter: Broker waiter object with an ``event`` and ``answer`` slot.
        human_mentor_name: For the progress message text.
        wait_seconds: Max seconds to block. 0 returns immediately.
        progress: Optional MCP progress callback.
        base_progress: Lower bound of the progress range (0..1) used
            for heartbeat pings during this wait.

    Returns:
        ``(outcome, answer_or_reason)`` where ``outcome`` is one of
        ``"answered" | "pending"``. On ``"answered"``, the second
        element is the answer text. On ``"pending"``, it's ``None``.

    Raises:
        asyncio.CancelledError: Re-raised when the MCP client sends
            ``$/cancelRequest``; the escalation is marked
            ``cancelled`` before propagating.
    """
    if wait_seconds <= 0 or waiter.event.is_set():
        if waiter.event.is_set() and waiter.answer is not None:
            return ("answered", waiter.answer)
        return ("pending", None)

    heartbeat = ctx.settings.escalation_progress_heartbeat_seconds
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    deadline = started_at + wait_seconds
    try:
        while not waiter.event.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            chunk = min(heartbeat, remaining)
            try:
                await asyncio.wait_for(waiter.event.wait(), timeout=chunk)
            except TimeoutError:
                if waiter.event.is_set():
                    break
                if progress is not None:
                    elapsed = int(loop.time() - started_at)
                    p = base_progress + min(0.4, elapsed / max(wait_seconds, 1.0) * 0.4)
                    await progress(p, f"still awaiting {human_mentor_name}'s reply ({elapsed}s elapsed)")
    except asyncio.CancelledError:
        ctx.escalation_store.update(esc_id, status="cancelled", cancel_reason="mcp_cancelled")
        ctx.escalation_broker.cancel(esc_id, "mcp_cancelled")
        raise

    if waiter.event.is_set() and waiter.answer is not None:
        return ("answered", waiter.answer)
    return ("pending", None)


async def _handle_escalate_to_human_mentor(
    ctx: ServerContext,
    question: str,
    mentor: str,
    context: str,
    api_key: str | None,
    wait_seconds: float = 25.0,
    progress: Callable[[float, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Forward a B.h-approved question to A.h. Sync-or-pending semantics.

    The flow (B.a is the calling mentee, B.h is its operator, A.a is
    this server's mentor, A.h is the human behind A.a):

    1. B.a (after getting B.h's approval) calls this tool with the
       final question text X.
    2. The handler persists an :class:`Escalation`, opens a waiter on
       the in-memory broker, and hands X to the configured delivery
       adapter (Telegram bot, OpenClaw bridge, or log-only).
    3. The handler waits **up to** ``wait_seconds`` for A.h to reply.
       If A.h replies in time, the response carries ``status="answered"``
       and the answer text. If not, it returns
       ``status="pending"`` with the ``escalation_id`` — B.a can poll
       :func:`_handle_get_escalation` later to retrieve A.h's answer.
    4. The escalation row stays in ``delivered`` state when the call
       returns pending, so the broker continues to relay any inbound
       reply to whichever next poll arms a waiter.

    Why the sync-or-pending design: MCP clients vary in how long they
    hold a tool call open. Claude Cowork's per-tool deadline (~60s)
    is not always reset by ``notifications/progress``, so a long
    block-and-wait is unreliable. Short blocking (default 25s) buys
    one-shot UX for fast humans without betting on the client.

    Cancellation: if B.a's MCP client sends ``$/cancelRequest``
    *during the wait*, the escalation is marked ``cancelled`` and the
    cancellation propagates. Once the call returns pending, the
    escalation is no longer tied to the original call — the human
    can still reply via the delivery adapter and the answer is
    retrievable via ``GetEscalation``.

    Args:
        ctx: The per-request server context.
        question: The B.h-approved question text X. Empty / whitespace
            returns ``empty_question``.
        mentor: A.a's slug. Empty falls through to the configured
            default mentor.
        context: Optional context attached for A.h.
        api_key: B.a's Bearer key. Required when ``require_auth``.
        wait_seconds: Max seconds to block waiting for A.h's reply
            before returning pending. ``0`` = fire-and-forget. The
            default (25 s) sits below typical MCP per-tool timeouts.
        progress: Optional callback for MCP progress notifications.
            Called with ``(0..1 progress, "human readable")``.

    Returns:
        A JSON-serialisable dict — :class:`EscalateToHumanMentorResponse`
        with ``status="answered"`` or ``status="pending"``, or an
        in-band error envelope on auth failure, unknown mentor, no
        human mentor configured, or delivery failure. The
        ``$/cancelRequest`` path propagates ``CancelledError`` via the
        inner waiter helper.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    m = _resolve_mentor(ctx, mentor)
    if not m:
        return {"error": "unknown_mentor"}
    if m.human_mentor is None:
        return {
            "error": "no_human_mentor",
            "detail": f"mentor {m.slug!r} has no human_mentor configured; cannot relay",
        }
    q = (question or "").strip()
    if not q:
        return {"error": "empty_question"}

    esc = new_escalation(
        mentor_slug=m.slug,
        mentee_slug=mentee_slug,
        question=q,
        context=context.strip() if context else None,
    )
    ctx.escalation_store.append(esc)
    log_event(
        ctx.settings.audit_log_path,
        "EscalateToHumanMentor",
        mentor=m.slug,
        mentee=mentee_slug,
        request_hash=short_hash(q),
    )
    waiter = ctx.escalation_broker.open(esc.id)
    if progress is not None:
        await progress(0.1, f"escalation {esc.id[:8]} queued for delivery to {m.human_mentor.name}")

    try:
        delivery_ref = await ctx.delivery_adapter.deliver(esc)
    except DeliveryError as e:
        ctx.escalation_store.update(esc.id, status="cancelled", cancel_reason=f"delivery_failed: {e}")
        ctx.escalation_broker.cancel(esc.id, f"delivery_failed: {e}")
        return {"error": "delivery_failed", "detail": str(e)}
    ctx.escalation_store.update(esc.id, status="delivered", delivery_ref=delivery_ref)
    if progress is not None:
        await progress(0.5, f"delivered to {m.human_mentor.name}; awaiting reply")

    outcome, answer = await _wait_for_escalation_answer(
        ctx,
        esc.id,
        waiter,
        m.human_mentor.name,
        wait_seconds=wait_seconds,
        progress=progress,
    )

    if outcome == "answered":
        final = ctx.escalation_store.get(esc.id)
        answered_at = final.answered_at.isoformat() if final and final.answered_at else None
        if progress is not None:
            await progress(1.0, "human mentor replied")
        return EscalateToHumanMentorResponse(
            mentor=m.slug,
            escalation_id=esc.id,
            status="answered",
            answer=answer,
            answered_at=answered_at,
        ).model_dump()

    # Pending: hand back the id so B.a can poll later. Release the
    # broker waiter — the original MCP call is about to return, so
    # nothing's waiting on it; a later GetEscalation will arm a fresh
    # waiter when needed. If A.h replies in between, the Telegram
    # adapter still writes to the EscalationStore — the persisted row
    # is the source of truth.
    ctx.escalation_broker.release(esc.id)
    return EscalateToHumanMentorResponse(
        mentor=m.slug,
        escalation_id=esc.id,
        status="pending",
        answer=None,
        answered_at=None,
        suggested_message_to_your_operator=(
            f"I've forwarded the question to {m.human_mentor.name}. "
            f"They'll reply asynchronously — I'll check back for an answer "
            f"shortly (escalation id `{esc.id[:8]}…`)."
        ),
    ).model_dump()


async def _handle_get_escalation(
    ctx: ServerContext,
    escalation_id: str,
    api_key: str | None,
    wait_seconds: float = 0.0,
    progress: Callable[[float, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Look up an escalation and optionally block briefly for its answer.

    Companion to ``EscalateToHumanMentor`` for the sync-or-pending
    flow: when an escalation came back ``pending``, the mentee calls
    this to retrieve A.h's answer once it lands. With
    ``wait_seconds > 0`` the handler arms a fresh broker waiter and
    blocks for up to that many seconds — useful when the mentee
    expects an answer imminently (e.g. polling on user prompt).

    Auth: same Bearer requirement as the other tools. The handler
    does *not* enforce that the caller is the original creator of
    the escalation — any mentee can fetch any escalation by id. This
    is intentional for the v1 demo; tighten with per-mentee scoping
    once a second mentee starts using the server.

    Args:
        ctx: Per-request server context.
        escalation_id: The id returned by ``EscalateToHumanMentor``.
        api_key: B.a's Bearer key when ``require_auth``.
        wait_seconds: If >0, block up to that many seconds for the
            answer to arrive while the escalation is still
            ``delivered``. ``0`` (default) returns the current state
            immediately.
        progress: Optional progress callback.

    Returns:
        :class:`GetEscalationResponse` envelope dict, or an in-band
        ``auth_failed``/``unknown_escalation`` error.
    """
    try:
        _ = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}

    esc = ctx.escalation_store.get(escalation_id)
    if esc is None:
        return {"error": "unknown_escalation", "detail": f"no escalation with id {escalation_id!r}"}

    # If the escalation already has a final state, return it directly.
    if esc.status in ("answered", "cancelled", "expired"):
        return GetEscalationResponse(
            escalation_id=esc.id,
            status=esc.status,
            mentor=esc.mentor_slug,
            question=esc.question,
            answer=esc.answer,
            answered_at=esc.answered_at.isoformat() if esc.answered_at else None,
            cancel_reason=esc.cancel_reason,
        ).model_dump()

    # Pending (delivered or earlier). Optional short wait.
    if wait_seconds > 0:
        m = _resolve_mentor(ctx, esc.mentor_slug)
        human_name = m.human_mentor.name if (m and m.human_mentor) else "the human mentor"
        # The original EscalateToHumanMentor call released its waiter
        # when it returned pending; we're free to arm a fresh one.
        waiter = ctx.escalation_broker.open(esc.id)
        try:
            outcome, answer = await _wait_for_escalation_answer(
                ctx,
                esc.id,
                waiter,
                human_name,
                wait_seconds=wait_seconds,
                progress=progress,
            )
        finally:
            # If we time out without an answer, release the slot so a
            # subsequent poll can arm again. resolve()/cancel() already
            # pop the waiter, so release() is a no-op in those cases.
            ctx.escalation_broker.release(esc.id)
        if outcome == "answered":
            final = ctx.escalation_store.get(esc.id)
            return GetEscalationResponse(
                escalation_id=esc.id,
                status="answered",
                mentor=esc.mentor_slug,
                question=esc.question,
                answer=answer,
                answered_at=final.answered_at.isoformat() if (final and final.answered_at) else None,
            ).model_dump()

    # Still pending — return the current snapshot.
    refreshed = ctx.escalation_store.get(esc.id) or esc
    return GetEscalationResponse(
        escalation_id=refreshed.id,
        status=refreshed.status,
        mentor=refreshed.mentor_slug,
        question=refreshed.question,
        answer=refreshed.answer,
        answered_at=refreshed.answered_at.isoformat() if refreshed.answered_at else None,
        cancel_reason=refreshed.cancel_reason,
    ).model_dump()


def _handle_get_system_info(ctx: ServerContext, api_key: str | None) -> dict[str, Any]:
    """Return a small, safe slice of build / release / runtime metadata.

    The fields surface "what am I connected to?" for end-to-end
    debugging: software name + version, AMMP draft id, Python version,
    OS platform, boot timestamp + uptime, mentor / mentee counts,
    default mentor slug, active escalation adapter kind, mount path,
    public URL. Everything here is already publicly observable
    elsewhere (version in the repo, draft id in the capability JSON,
    counts in ``ListMentors``); the tool just bundles them.

    Deliberately omitted: file paths, env-var values, hostnames, PIDs,
    Bearer tokens, anything that could compromise security or leak
    operator state. See :class:`GetSystemInfoResponse` for the full
    "never surface" list.

    Args:
        ctx: Per-request server context.
        api_key: Bearer key when ``require_auth``. Auth-gated like
            every other AMMP tool.

    Returns:
        :class:`GetSystemInfoResponse` envelope dict, or an in-band
        ``auth_failed`` error envelope.
    """
    import platform as _platform
    import sys as _sys

    try:
        _ = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}

    now = datetime.now(UTC)
    uptime = (now - ctx.started_at).total_seconds()
    return GetSystemInfoResponse(
        name="ammp-mcp",
        version=__version__,
        ammp_draft=__ammp_draft__,
        python_version=_platform.python_version(),
        platform=_sys.platform,
        started_at=ctx.started_at.isoformat(),
        uptime_seconds=round(uptime, 1),
        mentor_count=len(ctx.mentors),
        mentee_count=len(ctx.mentees),
        default_mentor=ctx.settings.default_mentor,
        escalation_adapter=ctx.delivery_adapter.kind,
        mount_path=ctx.settings.mount_path,
        public_url=ctx.settings.public_url,
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
        corpus = load_playbooks(m.playbook_dir, marketplaces_root=ctx.settings.marketplaces_root)
        backend = ctx.backends.get(slug)
        human_mentor = (
            {
                "name": m.human_mentor.name,
                "url": m.human_mentor.url,
                "contact": m.human_mentor.contact,
            }
            if m.human_mentor
            else None
        )
        mentor_summaries.append(
            {
                "slug": slug,
                "name": m.name,
                "description": m.description,
                "humanMentor": human_mentor,
                "playbookCount": len(corpus),
                "skillCount": sum(len(pb.skills) for pb in corpus),
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
            "GetSkill",
            "SearchPlaybooks",
            "AskMentor",
            "EscalateToHuman",
            "EscalateToHumanMentor",
            "GetEscalation",
            "GetPluginArchive",
            "GetSystemInfo",
        ],
    }


# ─── Claude Cowork bundle (.mcpb) generation ─────────────────────────────


def _build_desktop_bundle(public_url: str, mcp_url: str) -> bytes:
    """Generate a Claude Cowork .mcpb bundle pointing at this server.

    The bundle is a zip containing a ``manifest.json`` (DXT/MCPB
    schema 0.1) plus an ``icon.png``. The manifest declares a
    ``user_config.bearer_token`` field so Claude Cowork prompts the
    visitor for their per-mentee token at install time and substitutes
    it into the ``mcp-remote`` invocation. No token ever lives inside
    the bundle itself — bundles can be shared freely.

    The bundle is generated per request because the URLs inside depend
    on the live ``public_url`` setting; if an operator changes the
    deployment URL, the next bundle download reflects that without a
    rebuild step.

    Args:
        public_url: The server's advertised URL (with mount prefix),
            used for the manifest's ``homepage`` field.
        mcp_url: The MCP transport URL the bundled ``mcp-remote``
            shim should call.

    Returns:
        The zipped ``.mcpb`` file as raw bytes, ready for an HTTP
        response body.
    """
    import io
    import zipfile

    from ._data import desktop_bundle_path

    bundle_root = desktop_bundle_path()
    template = (bundle_root / "manifest.json.template").read_text(encoding="utf-8")
    manifest = (
        template.replace("{public_url}", public_url)
        .replace("{mcp_url}", mcp_url)
        # DXT install-time substitution token — the template's
        # placeholder is `${user_config_token}` (with the dollar
        # already present), so we replace just the inner braced part
        # with the dotted DXT path.
        .replace("{user_config_token}", "{user_config.bearer_token}")
        # ${__dirname} is the DXT install-time substitution for the
        # extension's install directory. Authored as a placeholder so
        # our literal `.replace()` pass doesn't tangle with the dollar.
        .replace("DIRNAME_PLACEHOLDER", "${__dirname}")
    )
    icon_bytes = (bundle_root / "icon.png").read_bytes()
    server_js_bytes = (bundle_root / "server.js").read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", manifest)
        z.writestr("icon.png", icon_bytes)
        z.writestr("server.js", server_js_bytes)
    return buf.getvalue()


# ─── Plugin-archive (zip) generation ──────────────────────────────────────


def _known_plugin_refs(ctx: ServerContext) -> dict[str, tuple[str, str, Path]]:
    """Thin wrapper over :func:`playbook.enumerate_plugin_refs` bound to ``ctx``.

    The shared service-layer implementation lives in ``playbook/_service.py``
    so the CLI surface (``ammp plugin archive``) and the MCP surface
    (``GetPluginArchive``) call the same code.

    Args:
        ctx: The per-request server context.

    Returns:
        Mapping of ``plugin_name → (plugin_name, marketplace_name, plugin_dir)``.
    """
    return enumerate_plugin_refs(ctx.mentors, ctx.settings.marketplaces_root)


def _build_plugin_archive(plugin_dir: Path, plugin_name: str) -> bytes:
    """Zip a plugin folder into a Claude-Code-installable archive.

    Walks the plugin directory and writes every file under a
    ``<plugin_name>/`` prefix so the extracted tree matches the layout
    Claude Code's plugin loader expects. Skips ``.git`` and
    ``__pycache__`` to keep the archive small + reproducible.

    Args:
        plugin_dir: Source directory holding ``.claude-plugin/plugin.json``
            plus ``skills/<id>/SKILL.md`` and friends.
        plugin_name: The plugin's slug — used as the top-level
            directory inside the zip so Claude Code installs it
            under the right name.

    Returns:
        The zipped plugin as raw bytes, ready for an HTTP response body.
    """
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(plugin_dir.rglob("*")):
            rel = path.relative_to(plugin_dir)
            parts = rel.parts
            if any(p in {".git", "__pycache__", ".DS_Store"} for p in parts):
                continue
            if path.is_dir():
                continue
            arcname = f"{plugin_name}/{rel.as_posix()}"
            z.write(path, arcname=arcname)
    return buf.getvalue()


def _handle_get_plugin_archive(ctx: ServerContext, plugin: str, api_key: str | None) -> dict[str, Any]:
    """Return a download URL for a plugin zip that the mentee can pass to its user.

    The zip itself is served from a separate HTTP route gated by the
    same Bearer token; this tool exists so the LLM can hand the user
    a single URL + install instructions without having to ferry the
    binary through the MCP wire.

    Args:
        ctx: The per-request server context.
        plugin: The plugin name (kebab-case slug). Must match a plugin
            referenced by some playbook on this server.
        api_key: Explicit bearer token from CLI paths, or ``None`` when
            the request rides the HTTP ``Authorization`` header.

    Returns:
        On success: ``{plugin, marketplace, archive_url,
        install_instructions}``. On failure: ``{error, detail?}``.
    """
    try:
        _authenticate(ctx, api_key)
    except ValueError as exc:
        return {"error": "auth_failed", "detail": str(exc)}
    known = _known_plugin_refs(ctx)
    response = build_plugin_archive_response(ctx.settings.public_url, plugin, known)
    if "error" not in response:
        log_event(
            ctx.settings.audit_log_path,
            "GetPluginArchive",
            extra=f"plugin={plugin} marketplace={response['marketplace']}",
        )
    return response


# ─── Human-facing landing page ────────────────────────────────────────────


def _build_mentor_playbook_prompt(
    *,
    public_url: str,
    mentor: Mentor,
    playbook_id: str,
    playbook_name: str,
    playbook_description: str,
    skill_count: int,
    plugin_ref: tuple[str, str] | None = None,
) -> str:
    """Render a copy-paste prompt that starts a mentoring session.

    The user pastes this into their MCP-aware agent (Claude.ai /
    Cowork / Code / OpenClaw / Hermes) after they've configured the
    ammp-mcp server in the agent's connector settings. When the
    playbook has a related plugin on a marketplace, the prompt opens
    with an install step so the agent's runtime durably picks up
    the skills as AgentSkills files; the mentor then stands by on
    the AMMP wire for `AskMentor` / `EscalateToHumanMentor`.

    Args:
        public_url: The server's advertised URL (no trailing slash).
        mentor: The :class:`Mentor` being addressed.
        playbook_id: The playbook's directory-name slug.
        playbook_name: The playbook's human-readable name.
        playbook_description: The one-line description from
            ``playbook.json``.
        skill_count: Number of skills inside this playbook.
        plugin_ref: Optional ``(plugin_name, marketplace_name)``
            reference from the playbook's ``playbook.json``. When
            set, the prompt asks the mentee to install the plugin
            first so the skills are local + durable.

    Returns:
        A multi-line prompt string, ready to drop into a ``<pre>``.
    """
    url = public_url.rstrip("/")
    n = skill_count
    skill_word = "skill" if n == 1 else "skills"
    body = (
        f'You are now operating under the mentorship of {mentor.name}\'s "{playbook_name}" playbook '
        f"on the helmguild AMMP server at {url}.\n\n"
        "To start:\n\n"
    )
    step = 1
    if plugin_ref is not None:
        plugin_name, _marketplace_name = plugin_ref
        body += (
            f"{step}. Install the plugin so the {n} {skill_word} land locally in your runtime as durable AgentSkills:\n"
            f'   - Call `GetPluginArchive` with `plugin: "{plugin_name}"` — you\'ll get back a URL pointing to a zip.\n'
            "   - Hand the URL + the returned install_instructions to your user. They download the zip "
            "(the same Bearer token authenticates the download) and install it into Claude Code / Desktop "
            "via the runtime's plugin installer (e.g. `/plugin install <path>` after extracting).\n"
            "   - The plugin's `.mcp.json` wires this AMMP server, so the live ops below keep working after the install.\n"
        )
        step += 1
    body += f'{step}. Call `ListPlaybooks` with `mentor: "{mentor.slug}"` to confirm you can reach the server.\n'
    step += 1
    body += (
        f'{step}. Call `GetPlaybook` with `id: "{playbook_id}"` and `mentor: "{mentor.slug}"` to load the playbook — '
        f'{n} {skill_word} covering "{playbook_description}".\n'
    )
    step += 1
    body += (
        f"{step}. Internalize the {skill_word} and apply them going forward in this conversation. "
        f"When you hit something the playbook does not cover, call `AskMentor` on `{mentor.slug}` rather than guessing.\n"
    )
    step += 1
    if mentor.human_mentor is not None:
        hm_name = mentor.human_mentor.name
        body += (
            f"{step}. If your confidence stays low after `AskMentor` AND my operator approves the forward, "
            f"you may call `EscalateToHumanMentor` to forward the question to {hm_name}, the human behind "
            f"{mentor.name}. The call returns either A.h's answer (within `wait_seconds`) or a `pending` "
            f"escalation id; in the pending case, call `GetEscalation` later to pick up the reply.\n"
        )
    body += f"\nAcknowledge by quoting the playbook name and the count of {skill_word} you loaded, then proceed."
    return body


_LANDING_COPY: dict[str, dict[str, str]] = {
    "en": {
        "html_lang": "en",
        "meta_description": "Mentor your agent — give your Claude (or other MCP-aware) agent a senior mentor it can ask. Reasons over a curated playbook library, answers grounded and cited, escalates to a human when out of depth.",
        "banner_label": "← back to helmguild.com",
        "lang_aria": "Language",
        "h1": "Mentor your agent.",
        "lede": "Give your Claude (or other MCP-aware) agent a senior mentor it can ask. The mentor reasons over a curated playbook library, answers grounded and cited, and escalates to a human when it's out of its depth. No kept history. Four steps.",
        "step1_h": "Step 1 — Request your access token",
        "step1_p": "Tokens are issued by hand — one form, one reply. Open the form below and we'll send yours back through the secure channel you specify.",
        "step1_cta": "Request access",
        "step2_h": "Step 2 — Configure your agent's MCP connection",
        "step2_p": "Pick your agent below and follow the paste path. Don't see yours? The <em>Generic</em> tab has the raw URL and Bearer header.",
        "tab_desktop_cta": "Download extension",
        "tab_desktop_li1": "Click the downloaded <code>.mcpb</code> file — Claude Cowork opens its extension installer.",
        "tab_desktop_li2": "Paste your Bearer token when prompted.",
        "tab_desktop_li3": "Enable the extension. Done.",
        "tab_code_intro": "One command in your terminal. Replace <code>&lt;your-token&gt;</code> with the value mailed to you.",
        "tab_code_docs": "Anthropic Claude Code MCP docs →",
        "copy": "Copy",
        "copy_url": "Copy URL",
        "tab_copilot_intro": "GitHub Copilot Chat supports MCP servers in VS Code and JetBrains IDEs.",
        "tab_copilot_vscode": "<strong>VS Code:</strong> Command Palette → <em>MCP: Add Server</em> → fill in the URL and the Bearer header.",
        "tab_copilot_jetbrains": "<strong>JetBrains:</strong> Settings → Tools → GitHub Copilot → MCP servers → add a new server.",
        "tab_copilot_docs": "GitHub Copilot MCP docs →",
        "tab_openclaw_intro": "OpenClaw is helmguild's own agent runtime and speaks MCP natively.",
        "tab_openclaw_li": "Add the server through the runtime's connector UI or config file using the same URL + Bearer header.",
        "tab_openclaw_note": "(OpenClaw is currently private to the helmguild network.)",
        "tab_hermes_intro": "Hermes is an alternative agent runtime; it connects to MCP servers via its tool-server registry.",
        "tab_hermes_li": "Point Hermes at the URL with the Bearer header — no Hermes-specific handshake.",
        "tab_hermes_note": "(Hermes docs vary by deployment — see the manual that ships with your install.)",
        "tab_generic_intro": "For any MCP-aware client not listed above. Open its connector settings, add a custom MCP server, and paste these two values.",
        "step3_h": "Step 3 — Check the connection",
        "step3_p": "Quick sanity check: paste this into your agent. If it responds with the mentors hosted here, the URL, token, and tool registration are all good.",
        "step3_prompt": "List mentors on helmguild.",
        "step4_h": "Step 4 — Pick a mentor and start the session",
        "step4_p": "Browse the mentors below, expand the playbook you want to be mentored on, and copy its prompt into your now-connected agent. The prompt walks the agent through the canonical first calls so the mentoring starts right away.",
        "standards_h": "Built on open standards",
        "standards_p": 'AMMP is the protocol on the wire; the on-disk format leans on what Anthropic has already opened. Skills follow the <a href="https://agentskills.io/home">AgentSkills</a> <code>SKILL.md</code> format. Plugins and the marketplace catalogue follow Anthropic\'s <a href="https://code.claude.com/docs/en/plugins">Claude Code plugin</a> and <a href="https://code.claude.com/docs/en/plugin-marketplaces">marketplace</a> conventions, so an extracted plugin installs into Claude Code / Desktop with the same one-shot <code>/plugin install &lt;path&gt;</code> path as any other plugin. The <code>helmguild-plugins</code> marketplace itself is private — AMMP brokers access through <code>GetPluginArchive</code>, which hands the mentee a Bearer-gated zip URL it can pass to its user. <a href="https://www.helmguild.com/rfc/ammp/">The AMMP draft</a> spells out how AMMP sits above these.',
        "privacy_h": "Privacy",
        "privacy_p": 'No retention. Your questions and the mentor\'s answers are never stored — only an opaque hash of each call lands in the audit log. See <a href="https://www.helmguild.com/rfc/ammp/">the AMMP draft</a> for the normative wording.',
        "footer": 'Reference implementation of the <a href="https://www.helmguild.com/rfc/ammp/">Agentic Mentor-Mentee Protocol</a>. Skills follow the <a href="https://agentskills.io/home">AgentSkills</a> standard; plugins + marketplace follow the <a href="https://code.claude.com/docs/en/plugins">Claude Code plugin</a> spec. MIT-licensed. Run by <a href="https://helmut.hoffer-von-ankershoffen.me/">Helmut Hoffer von Ankershoffen</a>. <a href="{base}/.well-known/agent.json">Capability JSON</a> · <a href="https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp">Source</a>',
        # Mentor-card chrome bits
        "mentor_behind": "Behind {mentor_name}: ",
        "mentor_no_playbooks": "No playbooks yet.",
        "mentor_no_skills": "No skills yet.",
        "mentor_skill_count_one": "1 skill",
        "mentor_skill_count_many": "{n} skills",
        "mentor_prompt_summary": "Prompt to start this mentoring",
        "mentor_prompt_copy": "Copy prompt",
        "mentors_empty": "No mentors are currently available.",
        "commercial_label": "Commercial",
        "commercial_title": "Distributed only inside an active helmguild mentoring engagement via AMMP GetPluginArchive — Helmguild Mentoring License v1.0.",
        "free_label": "Free",
        "free_title": "Open + community-licensed (CC-BY-4.0 skill bodies + MIT bundled scripts). Available in the public helmguild-plugins-public marketplace.",
        "validated_label": "Validated",
        "validated_title": "Ships with {n} validation prompt(s). Run `ammp playbook validate <id>` to spawn a fresh mentee against the live AMMP wire and verify the playbook teaches what it claims.",
        "playbook_requires": "Requires:",
        # Mailto draft
        "form_title": "Access request form",
    },
    "de": {
        "html_lang": "de",
        "meta_description": "Mentor your agent — gib deinem Claude (oder einem anderen MCP-fähigen Agenten) einen erfahrenen Mentor, den er befragen kann. Denkt anhand einer kuratierten Playbook-Bibliothek nach, antwortet fundiert und mit Quellenangaben, eskaliert an einen Menschen, wenn es zu komplex wird.",
        "banner_label": "← zurück zu helmguild.com",
        "lang_aria": "Sprache",
        "h1": "Mentor your agent.",
        "lede": "Gib deinem Claude (oder einem anderen MCP-fähigen Agenten) einen erfahrenen Mentor, den er befragen kann. Der Mentor denkt anhand einer kuratierten Playbook-Bibliothek nach, antwortet fundiert und mit Quellenangaben, und eskaliert an einen Menschen, wenn es zu komplex wird. Keine gespeicherten Verläufe. Vier Schritte.",
        "step1_h": "Schritt 1 — Zugangs-Token anfordern",
        "step1_p": "Tokens werden manuell ausgegeben — ein Formular, eine Antwort. Öffne das Formular unten, und wir schicken dir deinen Token über den sicheren Kanal zurück, den du angibst.",
        "step1_cta": "Zugang anfordern",
        "step2_h": "Schritt 2 — MCP-Verbindung deines Agenten einrichten",
        "step2_p": "Wähl unten deinen Agenten und folge der Anleitung. Nicht dabei? Im Tab <em>Generic</em> findest du URL und Bearer-Header pur.",
        "tab_desktop_cta": "Erweiterung herunterladen",
        "tab_desktop_li1": "Klick auf die heruntergeladene <code>.mcpb</code>-Datei — Claude Cowork öffnet den Erweiterungs-Installer.",
        "tab_desktop_li2": "Bearer-Token einfügen, wenn du gefragt wirst.",
        "tab_desktop_li3": "Erweiterung aktivieren. Fertig.",
        "tab_code_intro": "Ein Befehl in deinem Terminal. Ersetze <code>&lt;dein-token&gt;</code> durch den Wert aus der Mail.",
        "tab_code_docs": "Anthropic Claude Code MCP-Doku →",
        "copy": "Kopieren",
        "copy_url": "URL kopieren",
        "tab_copilot_intro": "GitHub Copilot Chat unterstützt MCP-Server in VS Code und JetBrains-IDEs.",
        "tab_copilot_vscode": "<strong>VS Code:</strong> Befehlspalette → <em>MCP: Add Server</em> → URL und Bearer-Header eintragen.",
        "tab_copilot_jetbrains": "<strong>JetBrains:</strong> Einstellungen → Tools → GitHub Copilot → MCP servers → neuen Server hinzufügen.",
        "tab_copilot_docs": "GitHub Copilot MCP-Doku →",
        "tab_openclaw_intro": "OpenClaw ist helmguilds eigene Agenten-Laufzeit und spricht MCP nativ.",
        "tab_openclaw_li": "Den Server über die Connector-Oberfläche der Laufzeit oder die Konfigurationsdatei mit derselben URL und dem Bearer-Header hinzufügen.",
        "tab_openclaw_note": "(OpenClaw ist derzeit nur im helmguild-Netzwerk verfügbar.)",
        "tab_hermes_intro": "Hermes ist eine alternative Agenten-Laufzeit; sie verbindet sich über ihr Tool-Server-Register mit MCP-Servern.",
        "tab_hermes_li": "Richte Hermes auf die URL mit dem Bearer-Header — kein Hermes-spezifischer Handshake nötig.",
        "tab_hermes_note": "(Hermes-Doku variiert je nach Deployment — siehe Handbuch deiner Installation.)",
        "tab_generic_intro": "Für jeden anderen MCP-fähigen Client. Öffne die Connector-Einstellungen, füge einen Custom-MCP-Server hinzu und kopier diese beiden Werte hinein.",
        "step3_h": "Schritt 3 — Verbindung prüfen",
        "step3_p": "Schneller Test: Füg das in deinen Agenten ein. Wenn er mit der Liste der hier gehosteten Mentoren antwortet, stimmen URL, Token und Tool-Registrierung.",
        "step3_prompt": "Liste die Mentoren auf helmguild auf.",
        "step4_h": "Schritt 4 — Mentor auswählen und Session starten",
        "step4_p": "Stöber durch die Mentoren unten, klapp das Playbook auf, zu dem du beraten werden willst, und kopier seinen Prompt in deinen verbundenen Agenten. Der Prompt führt den Agenten durch die ersten kanonischen Aufrufe, sodass das Mentoring direkt beginnt.",
        "standards_h": "Auf offenen Standards aufgebaut",
        "standards_p": 'AMMP ist das Protokoll auf der Wire; das On-Disk-Format setzt auf das, was Anthropic bereits offengelegt hat. Skills folgen dem <a href="https://agentskills.io/home">AgentSkills</a>-<code>SKILL.md</code>-Format. Plugins und der Marketplace-Katalog folgen den <a href="https://code.claude.com/docs/en/plugins">Claude-Code-Plugin</a>- und <a href="https://code.claude.com/docs/en/plugin-marketplaces">Marketplace</a>-Konventionen — ein entpacktes Plugin installiert sich in Claude Code / Desktop mit demselben One-Shot-Befehl <code>/plugin install &lt;pfad&gt;</code> wie jedes andere Plugin. Der <code>helmguild-plugins</code>-Marketplace selbst ist privat — AMMP vermittelt den Zugriff über <code>GetPluginArchive</code>, das dem Mentee eine Bearer-gesicherte Zip-URL liefert, die er seinem Nutzer übergibt. <a href="https://www.helmguild.com/de/rfc/ammp/">Der AMMP-Entwurf</a> beschreibt, wie AMMP darüber sitzt.',
        "privacy_h": "Datenschutz",
        "privacy_p": 'Keine Speicherung. Deine Fragen und die Antworten der Mentoren werden nie gespeichert — nur ein undurchsichtiger Hash jedes Aufrufs landet im Audit-Log. Den verbindlichen Wortlaut findest du im <a href="https://www.helmguild.com/de/rfc/ammp/">AMMP-Entwurf</a>.',
        "footer": 'Referenz-Implementierung des <a href="https://www.helmguild.com/de/rfc/ammp/">Agentic Mentor-Mentee Protocol</a>. Skills folgen dem <a href="https://agentskills.io/home">AgentSkills</a>-Standard; Plugins + Marketplace folgen der <a href="https://code.claude.com/docs/en/plugins">Claude-Code-Plugin</a>-Spezifikation. MIT-lizenziert. Betrieben von <a href="https://helmut.hoffer-von-ankershoffen.me/">Helmut Hoffer von Ankershoffen</a>. <a href="{base}/.well-known/agent.json">Capability-JSON</a> · <a href="https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp">Quellcode</a>',
        "mentor_behind": "Hinter {mentor_name}: ",
        "mentor_no_playbooks": "Noch keine Playbooks.",
        "mentor_no_skills": "Noch keine Skills.",
        "mentor_skill_count_one": "1 Skill",
        "mentor_skill_count_many": "{n} Skills",
        "mentor_prompt_summary": "Prompt, um dieses Mentoring zu starten",
        "mentor_prompt_copy": "Prompt kopieren",
        "mentors_empty": "Aktuell stehen keine Mentoren zur Verfügung.",
        "commercial_label": "Kommerziell",
        "commercial_title": "Wird nur innerhalb einer aktiven helmguild-Mentoring-Beziehung über AMMP GetPluginArchive verteilt — Helmguild Mentoring License v1.0.",
        "free_label": "Frei",
        "free_title": "Offen + community-lizenziert (CC-BY-4.0 Skill-Texte + MIT für gebündelte Skripte). Verfügbar im öffentlichen helmguild-plugins-public Marketplace.",
        "validated_label": "Validiert",
        "validated_title": "Hat {n} Validierungs-Prompt(s). `ammp playbook validate <id>` startet einen frischen Mentee am Live-AMMP-Wire und prüft, ob das Playbook tatsächlich vermittelt, was es behauptet.",
        "playbook_requires": "Voraussetzungen:",
        "form_title": "Zugangsanfrage-Formular",
    },
}


def _render_landing(ctx: ServerContext, lang: str = "en") -> str:
    """Render the mentee-facing landing page served at ``GET /``.

    Self-contained HTML (inline CSS + JS, no external assets). For each
    mentor loaded in ``ctx`` renders avatar, name, description, the
    *human mentor* attribution (so escalation destinations are explicit),
    and the playbook → skill tree. All four sources are
    pulled fresh per request — avatar via :meth:`Mentor.avatar_path`,
    playbooks via :func:`ammp_mcp.playbook.load_playbooks` — so the page
    reflects on-disk changes without a server restart. A single
    "Request access" button opens a pre-filled ``mailto:`` to the
    operator. The Copy button uses ``navigator.clipboard.writeText``.
    Operator-side minting / rotation / revocation lives in
    ``OPERATING.md``, not on this page.

    Args:
        ctx: The boot-time server context — used for the live mentor
            list and the public URL.
        lang: Language code (``"en"`` or ``"de"``). Selects the copy
            block; the page structure / mentor corpus stays the same
            (mentor names + playbook content are content, not chrome).

    Returns:
        A complete HTML document as a string, ready for ``HTMLResponse``.
    """
    c = _LANDING_COPY.get(lang, _LANDING_COPY["en"])
    import re as _re
    from html import escape as _h

    # Inline-markdown renderer — handles `**bold**`, `*italic*`, and
    # `` `code` `` in summary lines so playbook / skill
    # markdown doesn't show raw asterisks on the page. HTML-escape
    # first; the conversion only ever inserts the small set of tags
    # below, never anything attacker-controlled.
    _re_bold = _re.compile(r"\*\*([^*\n]+?)\*\*")
    _re_italic = _re.compile(r"(?<![*\w])\*([^*\n]+?)\*(?!\w)")
    _re_code = _re.compile(r"`([^`\n]+?)`")

    def _md_inline(text: str) -> str:
        """Render a one-line markdown snippet to safe HTML.

        Applies HTML-escape, then a small regex pass for ``**bold**``,
        ``*italic*`` and `` `code` `` — the only inline forms that
        appear in the summary lines we surface on the landing.

        Args:
            text: The raw text from a playbook description or skill
                summary.

        Returns:
            HTML-safe rendered string.
        """
        escaped = _h(text)
        escaped = _re_code.sub(r"<code>\1</code>", escaped)
        escaped = _re_bold.sub(r"<strong>\1</strong>", escaped)
        escaped = _re_italic.sub(r"<em>\1</em>", escaped)
        return escaped

    from urllib.parse import urlparse as _urlparse

    base = ctx.settings.public_url.rstrip("/")
    mcp_url = f"{base}/mcp/"
    # Use urlparse to derive the host — string-replace with a scheme
    # literal trips SonarQube python:S5332 (false-positive: we're
    # stripping a prefix for display, not making an http:// request).
    parsed = _urlparse(base)
    host = (parsed.netloc + parsed.path).rstrip("/") or base

    # Access-request flow uses an embedded Google Form (the operator
    # receives submissions in a Sheet and replies on the secure channel
    # the requester names). Same form on EN + DE.
    access_form_url = "https://docs.google.com/forms/d/e/1FAIpQLSfGJJCw_wd12OTYb8F4ryvtOaOb3doFShUTKSIrwFfYifSoKg/viewform?embedded=true"

    mentor_blocks_parts: list[str] = []
    for slug, m in ctx.mentors.items():
        playbooks = load_playbooks(m.playbook_dir, marketplaces_root=ctx.settings.marketplaces_root)
        # Avatar: real image if a file exists on disk; otherwise render
        # an initial-letter glyph so every mentor still has a visual.
        if m.avatar_path() is not None:
            # Relative src — resolves correctly whether the landing is
            # served at `/` or at a mount prefix like `/ammp/`.
            avatar_html = f"<img class='avatar' src='mentors/{_h(slug)}/avatar' alt='{_h(m.name)}' width='96' height='96' loading='lazy'>"
        else:
            initial = _h(m.name[:1].upper()) if m.name else "?"
            avatar_html = f"<div class='avatar avatar-fallback' aria-hidden='true'>{initial}</div>"
        description_html = f"<p class='mentor-desc'>{_h(m.description)}</p>" if m.description else ""
        # Human-mentor badge — "Behind: <name>" with optional link. Makes
        # the escalation destination explicit per AMMP §3.4.
        if m.human_mentor is not None:
            hm = m.human_mentor
            # Prefer the helmguild profile URL when set, fall back to
            # the personal bio link. Auto-swap to /de/ on the DE landing
            # for helmguild.com URLs, same as mentor.profile_url.
            hm_link = hm.profile_url or hm.url
            if hm_link and lang == "de" and hm_link.startswith("https://www.helmguild.com/") and "/de/" not in hm_link:
                hm_link = hm_link.replace("https://www.helmguild.com/", "https://www.helmguild.com/de/", 1)
            hm_name_html = f"<a href='{_h(hm_link)}'>{_h(hm.name)}</a>" if hm_link else _h(hm.name)
            hm_contact = f" · <span class='hm-contact'>{_h(hm.contact)}</span>" if hm.contact else ""
            behind_prefix = c["mentor_behind"].format(mentor_name=_h(m.name))
            human_html = f"<p class='human-mentor'>{behind_prefix}{hm_name_html}{hm_contact}</p>"
        else:
            human_html = ""
        # Playbook → skill nested rendering. Each playbook is an area
        # of practice; each skill is one craft rule. Skills are
        # collapsed inside a <details> by default — one mentor with
        # three playbooks of 5-12 skills each would otherwise dominate
        # the page.
        if playbooks:
            pb_html_parts: list[str] = []
            for pb in playbooks:
                if pb.skills:
                    skill_items = "".join(
                        f"<li><span class='skill-title'>{_h(sk.title)}</span>"
                        + (f"<span class='skill-desc'>{_md_inline(sk.summary)}</span>" if sk.summary else "")
                        + "</li>"
                        for sk in pb.skills
                    )
                    n = len(pb.skills)
                    label = c["mentor_skill_count_one"] if n == 1 else c["mentor_skill_count_many"].format(n=n)
                    skills_block = (
                        f"<details class='skills-details'>"
                        f"<summary>{label}</summary>"
                        f"<ul class='skills'>{skill_items}</ul>"
                        "</details>"
                    )
                else:
                    skills_block = f"<p class='empty'>{c['mentor_no_skills']}</p>"
                pb_desc = f"<p class='pb-desc'>{_md_inline(pb.description)}</p>" if pb.description else ""
                if pb.requires:
                    req_links = ", ".join(
                        f"<a href='#pb-{_h(slug)}-{_h(r)}' class='pb-req-link'>{_h(r)}</a>" for r in pb.requires
                    )
                    pb_requires_html = f"<p class='pb-requires'>{_h(c['playbook_requires'])} {req_links}</p>"
                else:
                    pb_requires_html = ""
                # Copy-paste prompt for starting a mentoring session on
                # this specific (mentor, playbook). User pastes into
                # their MCP-aware agent. Sits next to the skills
                # disclosure as a sibling, collapsed by default.
                prompt_text = _build_mentor_playbook_prompt(
                    public_url=base,
                    mentor=m,
                    playbook_id=pb.id,
                    playbook_name=pb.name,
                    playbook_description=pb.description,
                    skill_count=len(pb.skills),
                    plugin_ref=pb.plugin_ref,
                )
                prompt_dom_id = f"prompt--{_h(slug)}--{_h(pb.id)}"
                prompt_block = (
                    "<details class='prompt-details'>"
                    f"<summary>{c['mentor_prompt_summary']}</summary>"
                    f"<pre class='prompt' id='{prompt_dom_id}'"
                    f" data-mentor='{_h(slug)}' data-playbook='{_h(pb.id)}'>"
                    f"{_h(prompt_text)}</pre>"
                    f"<button class='btn copy' data-copy-from='#{prompt_dom_id}'>{c['mentor_prompt_copy']}</button>"
                    "</details>"
                )
                if pb.commercial:
                    badge = (
                        f"<span class='pb-badge pb-commercial' title='{_h(c['commercial_title'])}'>"
                        f"{_h(c['commercial_label'])}</span>"
                    )
                else:
                    badge = f"<span class='pb-badge pb-free' title='{_h(c['free_title'])}'>{_h(c['free_label'])}</span>"
                # Validated badge appears next to Free/Commercial when the
                # playbook declares validation prompts (it's been authored
                # with acceptance tests for mentee learning).
                n_prompts = len(pb.validation.get("prompts", []) or [])
                if n_prompts > 0:
                    validated_badge = (
                        f"<span class='pb-badge pb-validated' title='{_h(c['validated_title'].format(n=n_prompts))}'>"
                        f"{_h(c['validated_label'])}</span>"
                    )
                else:
                    validated_badge = ""
                pb_html_parts.append(
                    f"<section class='playbook' id='pb-{_h(slug)}-{_h(pb.id)}'>"
                    f"<h4 class='pb-name'>{_h(pb.name)} <span class='pb-id'>{_h(pb.id)}</span>{badge}{validated_badge}</h4>"
                    f"{pb_desc}"
                    f"{pb_requires_html}"
                    f"{skills_block}"
                    f"{prompt_block}"
                    "</section>"
                )
            playbook_section = "".join(pb_html_parts)
        else:
            playbook_section = f"<p class='empty'>{c['mentor_no_playbooks']}</p>"
        # When the mentor advertises a longer profile page, hyperlink
        # its name. For helmguild.com URLs on the DE landing, swap in
        # the localised `/de/` variant so German visitors land on the
        # matching page.
        name_html = _h(m.name)
        if m.profile_url:
            pu = m.profile_url
            if lang == "de" and pu.startswith("https://www.helmguild.com/") and "/de/" not in pu:
                pu = pu.replace("https://www.helmguild.com/", "https://www.helmguild.com/de/", 1)
            name_html = f"<a class='mentor-profile' href='{_h(pu)}'>{_h(m.name)}</a>"
        mentor_blocks_parts.append(
            "<section class='mentor'>"
            "<div class='mentor-head'>"
            f"{avatar_html}"
            "<div class='mentor-id'>"
            f"<h3>{name_html} <span class='slug'>{_h(slug)}</span></h3>"
            f"{description_html}"
            f"{human_html}"
            "</div>"
            "</div>"
            f"{playbook_section}"
            "</section>"
        )
    mentor_blocks = (
        "".join(mentor_blocks_parts) if mentor_blocks_parts else f"<p class='empty'>{c['mentors_empty']}</p>"
    )

    # Switcher href: /ammp/ ↔ /ammp/de/. Compute relative so it works
    # whether we're on the EN root or under /de/.
    other_lang = "de" if lang == "en" else "en"
    other_href = "./de/" if lang == "en" else "../"
    other_label = "DE" if lang == "en" else "EN"
    return f"""<!doctype html>
<html lang="{c["html_lang"]}">
<head>
<meta charset="utf-8">
<title>ammp · {host}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{c["meta_description"]}">
<link rel="alternate" hreflang="en" href="{base}/">
<link rel="alternate" hreflang="de" href="{base}/de/">
<link rel="alternate" hreflang="x-default" href="{base}/">
<link rel="icon" type="image/svg+xml" href="{base}/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="{base}/favicon-32.png">
<link rel="apple-touch-icon" href="{base}/apple-touch-icon.png">
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
.helmguild-banner{{display:block;width:100%;padding:.55rem 1rem;background:var(--accent);color:var(--bg);font-family:var(--sans);font-size:.85rem;letter-spacing:.04em;text-align:center;text-decoration:none;border:none}}
.helmguild-banner:hover{{background:var(--accent-hover);color:var(--bg);border:none}}
.lang-pill{{position:fixed;top:calc(.55rem + 2.6rem);right:1rem;z-index:90;display:flex;align-items:center;height:38px;padding:0 .7rem;background:rgba(236,230,217,.75);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border:1px solid var(--rule);border-radius:6px;font-size:.85rem;letter-spacing:.05em;font-family:var(--sans);color:var(--ink-soft);box-sizing:border-box;transition:background .18s ease,border-color .18s ease}}
.lang-pill:hover{{background:rgba(236,230,217,.95);border-color:var(--accent)}}
.lang-pill a{{color:var(--ink-soft);text-decoration:none;border:none;padding:0 .2rem;transition:color .18s ease}}
.lang-pill a:hover{{color:var(--accent)}}
.lang-pill .current{{color:var(--ink);font-weight:600;padding:0 .2rem}}
.lang-pill .sep{{color:var(--ink-soft);opacity:.5;margin:0 .15rem}}
@media (prefers-color-scheme: dark) {{ .lang-pill{{background:rgba(20,23,31,.55)}} .lang-pill:hover{{background:rgba(20,23,31,.75)}} }}
main{{max-width:40rem;margin:0 auto;padding:2.5rem 1.75rem 3rem}}
h1{{font-family:var(--serif);font-weight:600;font-size:2.25rem;margin:0 0 .4rem;letter-spacing:-.01em}}
h2{{font-family:var(--serif);font-size:.82rem;font-weight:600;text-transform:uppercase;letter-spacing:.16em;color:var(--ink-soft);margin:2.5rem 0 .9rem}}
h3{{font-family:var(--serif);font-size:1.1rem;margin:0 0 .4rem;font-weight:600;display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap}}
.lede{{color:var(--ink-soft);margin:0 0 .25rem;font-size:1.02rem}}
a{{color:var(--accent);text-decoration:none;border-bottom:1px solid color-mix(in srgb,var(--accent) 30%,transparent);padding-bottom:1px}}
a:hover{{color:var(--accent-hover);border-bottom-color:var(--accent-hover)}}
code{{font-family:var(--mono);font-size:.92em;background:rgba(0,0,0,.045);border:1px solid var(--rule);border-radius:4px;padding:.1rem .4rem}}
@media (prefers-color-scheme: dark) {{ code{{background:rgba(255,255,255,.04)}} }}
.mentor{{border-top:1px solid var(--rule);padding:1.4rem 0}}
.mentor:last-of-type{{border-bottom:1px solid var(--rule)}}
.mentor-head{{display:flex;gap:1rem;align-items:center;margin-bottom:.75rem}}
.mentor-id{{flex:1;min-width:0}}
.mentor .slug{{font-family:var(--mono);font-size:.78rem;color:var(--ink-soft);font-weight:400;letter-spacing:.02em}}
.mentor-desc{{margin:.15rem 0 0;color:var(--ink-soft);font-size:.95rem;line-height:1.5}}
.human-mentor{{margin:.4rem 0 0;font-size:.85rem;color:var(--ink-soft)}}
.human-mentor a{{border-bottom-color:var(--rule)}}
.hm-contact{{font-family:var(--mono);font-size:.85em}}
.avatar{{width:64px;height:64px;border-radius:50%;flex-shrink:0;object-fit:cover;border:1px solid var(--rule);background:var(--bg-lo)}}
.avatar-fallback{{display:flex;align-items:center;justify-content:center;font-family:var(--serif);font-size:1.6rem;font-weight:600;color:var(--ink-soft)}}
.playbook{{margin-top:1rem;padding:.75rem 0 0;border-top:1px dashed var(--rule)}}
.pb-name{{font-family:var(--serif);font-size:1rem;font-weight:600;margin:0 0 .15rem;color:var(--ink);display:flex;align-items:baseline;gap:.5rem;flex-wrap:wrap}}
.pb-id{{font-family:var(--mono);font-size:.72rem;color:var(--ink-soft);font-weight:400}}
.pb-badge{{font-family:var(--sans);font-size:.65rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;border-radius:3px;padding:.05rem .35rem;cursor:help;border:1px solid;margin-left:.35rem}}
.pb-commercial{{color:var(--accent);background:rgba(46, 79, 107, 0.08);border-color:rgba(46, 79, 107, 0.3)}}
.pb-free{{color:#2e7d52;background:rgba(46, 125, 82, 0.08);border-color:rgba(46, 125, 82, 0.35)}}
.pb-validated{{color:#5b3d8a;background:rgba(91, 61, 138, 0.08);border-color:rgba(91, 61, 138, 0.35)}}
.pb-desc{{margin:0 0 .35rem;color:var(--ink-soft);font-size:.9rem;line-height:1.45}}
.pb-requires{{margin:.15rem 0 .35rem;font-size:.78rem;color:var(--ink-soft)}}
.pb-requires .pb-req-link{{color:var(--accent);text-decoration:none;border-bottom:1px dotted rgba(46,79,107,.4);padding-bottom:1px}}
.pb-requires .pb-req-link:hover{{border-bottom-style:solid}}
.skills-details,.prompt-details{{margin:.4rem 0 0}}
.skills-details > summary,.prompt-details > summary{{cursor:pointer;color:var(--accent);font-size:.88rem;font-family:var(--sans);padding:.25rem 0;list-style:none;user-select:none}}
.skills-details > summary::-webkit-details-marker,.prompt-details > summary::-webkit-details-marker{{display:none}}
.skills-details > summary::before,.prompt-details > summary::before{{content:"▸";display:inline-block;width:.9rem;color:var(--ink-soft);transition:transform .15s ease}}
.skills-details[open] > summary::before,.prompt-details[open] > summary::before{{transform:rotate(90deg)}}
.skills-details > summary:hover,.prompt-details > summary:hover{{color:var(--accent-hover)}}
.prompt-details pre.prompt{{margin:.5rem 0 .5rem;padding:.85rem 1rem;background:rgba(0,0,0,.045);border:1px solid var(--rule);border-radius:6px;font-family:var(--mono);font-size:.82rem;line-height:1.5;color:var(--ink);white-space:pre-wrap;overflow-x:auto}}
@media (prefers-color-scheme: dark) {{ .prompt-details pre.prompt{{background:rgba(255,255,255,.04)}} }}
.skills{{margin:.5rem 0 0;padding:0 0 0 1.4rem;color:var(--ink-soft);list-style:disc}}
.skills li{{margin:.3rem 0;color:var(--ink);font-size:.92rem}}
.skills li::marker{{color:var(--ink-soft)}}
.skills .skill-title{{color:var(--ink);font-weight:500}}
.skills .skill-desc{{display:block;color:var(--ink-soft);font-size:.85rem;line-height:1.45;margin-top:.05rem}}
.skills .skill-desc code{{background:rgba(0,0,0,.045);border:1px solid var(--rule);border-radius:3px;padding:.05rem .3rem;font-size:.92em}}
@media (prefers-color-scheme: dark) {{ .skills .skill-desc code{{background:rgba(255,255,255,.04)}} }}
.skills li.empty{{color:var(--ink-soft);font-style:italic;list-style:none;margin-left:-1.1rem}}
.empty{{color:var(--ink-soft);font-style:italic}}
.runtimes{{margin:.4rem 0 0;color:var(--ink-soft);font-size:.95rem}}
.cta{{margin:1rem 0 .5rem}}
.access-form{{margin:1.5rem 0 .5rem}}
.access-form > summary{{list-style:none;cursor:pointer}}
.access-form > summary::-webkit-details-marker{{display:none}}
.access-form > summary::marker{{content:""}}
.access-form > summary:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
.access-form iframe{{display:block;width:100%;max-width:640px;height:1700px;border:1px solid var(--rule);border-radius:6px;background:#fff;margin-top:1rem;transition:height .35s ease}}
@media (max-width: 480px) {{ .access-form iframe{{height:2200px}} }}
.agent-tabs{{margin:1.25rem 0 0}}
.agent-tab-input{{position:absolute;opacity:0;pointer-events:none}}
.agent-tab-row{{display:flex;gap:.1rem;border-bottom:1px solid var(--rule);margin-bottom:.85rem;flex-wrap:wrap}}
.agent-tab-label{{padding:.5rem .9rem;font-size:.88rem;font-family:var(--sans);cursor:pointer;color:var(--ink-soft);border-bottom:2px solid transparent;margin-bottom:-1px;transition:color .18s ease,border-color .18s ease;user-select:none}}
.agent-tab-label:hover{{color:var(--accent)}}
.agent-tab-panel{{display:none}}
.agent-tab-panel .tab-intro{{margin:0 0 .55rem;color:var(--ink-soft);font-size:.92rem}}
.agent-tab-panel .tab-steps{{margin:.25rem 0 .6rem;padding:0 0 0 1.2rem;list-style:disc;color:var(--ink-soft)}}
.agent-tab-panel .tab-steps li{{margin:.35rem 0;color:var(--ink);font-size:.92rem;line-height:1.55}}
.agent-tab-panel .tab-steps li::marker{{color:var(--ink-soft)}}
.agent-tab-panel .tab-steps code{{font-family:var(--mono);font-size:.84em;background:rgba(0,0,0,.045);border:1px solid var(--rule);border-radius:3px;padding:.05rem .35rem;word-break:break-all}}
@media (prefers-color-scheme: dark) {{ .agent-tab-panel .tab-steps code{{background:rgba(255,255,255,.04)}} }}
.agent-tab-panel .tab-manual{{margin:.45rem 0 0;font-size:.85rem;color:var(--ink-soft)}}
#agent-tab-desktop:checked ~ .agent-tab-row label[for="agent-tab-desktop"],#agent-tab-code:checked ~ .agent-tab-row label[for="agent-tab-code"],#agent-tab-copilot:checked ~ .agent-tab-row label[for="agent-tab-copilot"],#agent-tab-openclaw:checked ~ .agent-tab-row label[for="agent-tab-openclaw"],#agent-tab-hermes:checked ~ .agent-tab-row label[for="agent-tab-hermes"],#agent-tab-generic:checked ~ .agent-tab-row label[for="agent-tab-generic"]{{color:var(--ink);border-bottom-color:var(--accent);font-weight:500}}
#agent-tab-desktop:checked ~ .agent-tab-panel-desktop,#agent-tab-code:checked ~ .agent-tab-panel-code,#agent-tab-copilot:checked ~ .agent-tab-panel-copilot,#agent-tab-openclaw:checked ~ .agent-tab-panel-openclaw,#agent-tab-hermes:checked ~ .agent-tab-panel-hermes,#agent-tab-generic:checked ~ .agent-tab-panel-generic{{display:block}}
button{{font:inherit}}
.btn{{display:inline-flex;align-items:center;gap:.4rem;padding:.45rem .9rem;margin:.25rem .4rem .25rem 0;border:1px solid var(--accent);border-radius:5px;background:transparent;color:var(--accent);font-family:var(--sans);font-size:.92rem;cursor:pointer;text-decoration:none}}
.btn:hover{{background:var(--accent);color:var(--bg)}}
.btn.primary{{background:var(--accent);color:var(--bg);border-color:var(--accent)}}
.btn.primary:hover{{background:var(--accent-hover);border-color:var(--accent-hover)}}
.btn.copy{{font-family:var(--mono);font-size:.82rem;padding:.3rem .6rem}}
.btn.copy[data-copied="1"]{{border-color:#3a8a3a;color:#3a8a3a;background:transparent}}
.url-row{{font-family:var(--mono);font-size:.88rem;margin:.5rem 0;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}}
.url-row code{{flex:1;min-width:0;overflow-wrap:anywhere}}
footer{{color:var(--ink-soft);font-size:.82rem;margin-top:3rem;padding-top:1.5rem;border-top:1px solid var(--rule)}}
footer a{{color:var(--ink-soft);border-bottom-color:var(--rule)}}
</style>
</head>
<body>
<a class="helmguild-banner" href="https://www.helmguild.com/{"de/" if lang == "de" else ""}">{c["banner_label"]}</a>
<nav class="lang-pill" aria-label="{c["lang_aria"]}">
  <span class="current" aria-current="true">{lang.upper()}</span>
  <span class="sep">·</span>
  <a href="{other_href}" hreflang="{other_lang}">{other_label}</a>
</nav>
<main>

<h1>{c["h1"]}</h1>
<p class="lede">{c["lede"]}</p>

<h2>{c["step1_h"]}</h2>
<p>{c["step1_p"]}</p>
<details class="access-form">
  <summary class="btn primary">{c["step1_cta"]}</summary>
  <iframe src="{access_form_url}" title="{c["form_title"]}" loading="lazy" width="640" height="1700" frameborder="0" marginheight="0" marginwidth="0">Loading…</iframe>
</details>

<h2>{c["step2_h"]}</h2>
<p>{c["step2_p"]}</p>

<div class="agent-tabs" role="tablist" aria-label="Per-agent connector instructions">
  <input type="radio" name="agent-tab" id="agent-tab-desktop" class="agent-tab-input" checked>
  <input type="radio" name="agent-tab" id="agent-tab-code" class="agent-tab-input">
  <input type="radio" name="agent-tab" id="agent-tab-copilot" class="agent-tab-input">
  <input type="radio" name="agent-tab" id="agent-tab-openclaw" class="agent-tab-input">
  <input type="radio" name="agent-tab" id="agent-tab-hermes" class="agent-tab-input">
  <input type="radio" name="agent-tab" id="agent-tab-generic" class="agent-tab-input">
  <div class="agent-tab-row" role="tablist">
    <label for="agent-tab-desktop" class="agent-tab-label" role="tab">Claude Cowork</label>
    <label for="agent-tab-code" class="agent-tab-label" role="tab">Claude Code</label>
    <label for="agent-tab-copilot" class="agent-tab-label" role="tab">Copilot</label>
    <label for="agent-tab-openclaw" class="agent-tab-label" role="tab">OpenClaw</label>
    <label for="agent-tab-hermes" class="agent-tab-label" role="tab">Hermes</label>
    <label for="agent-tab-generic" class="agent-tab-label" role="tab">Generic</label>
  </div>
  <section class="agent-tab-panel agent-tab-panel-desktop" role="tabpanel" aria-labelledby="agent-tab-desktop">
    <p class="cta"><a class="btn primary" href="{base}/desktop-bundle.mcpb" download>{c["tab_desktop_cta"]}</a></p>
    <ul class="tab-steps">
      <li>{c["tab_desktop_li1"]}</li>
      <li>{c["tab_desktop_li2"]}</li>
      <li>{c["tab_desktop_li3"]}</li>
    </ul>
  </section>
  <section class="agent-tab-panel agent-tab-panel-code" role="tabpanel" aria-labelledby="agent-tab-code">
    <p class="tab-intro">{c["tab_code_intro"]}</p>
    <div class="url-row"><code id="claude-code-cmd">claude mcp add --scope user ammp {mcp_url} --header "Authorization: Bearer ammp-&lt;your-token&gt;"</code> <button class="btn copy" data-copy-from="#claude-code-cmd">{c["copy"]}</button></div>
    <p class="tab-manual"><a href="https://docs.anthropic.com/en/docs/claude-code/mcp">{c["tab_code_docs"]}</a></p>
  </section>
  <section class="agent-tab-panel agent-tab-panel-copilot" role="tabpanel" aria-labelledby="agent-tab-copilot">
    <p class="tab-intro">{c["tab_copilot_intro"]}</p>
    <ul class="tab-steps">
      <li>{c["tab_copilot_vscode"]}</li>
      <li>{c["tab_copilot_jetbrains"]}</li>
    </ul>
    <p class="tab-manual"><a href="https://docs.github.com/en/copilot/customizing-copilot/extending-copilot-chat-with-mcp">{c["tab_copilot_docs"]}</a></p>
  </section>
  <section class="agent-tab-panel agent-tab-panel-openclaw" role="tabpanel" aria-labelledby="agent-tab-openclaw">
    <p class="tab-intro">{c["tab_openclaw_intro"]}</p>
    <ul class="tab-steps">
      <li>{c["tab_openclaw_li"]}</li>
    </ul>
    <p class="tab-manual">{c["tab_openclaw_note"]}</p>
  </section>
  <section class="agent-tab-panel agent-tab-panel-hermes" role="tabpanel" aria-labelledby="agent-tab-hermes">
    <p class="tab-intro">{c["tab_hermes_intro"]}</p>
    <ul class="tab-steps">
      <li>{c["tab_hermes_li"]}</li>
    </ul>
    <p class="tab-manual">{c["tab_hermes_note"]}</p>
  </section>
  <section class="agent-tab-panel agent-tab-panel-generic" role="tabpanel" aria-labelledby="agent-tab-generic">
    <p class="tab-intro">{c["tab_generic_intro"]}</p>
    <div class="url-row"><code>{mcp_url}</code> <button class="btn copy" data-copy="{mcp_url}">{c["copy_url"]}</button></div>
    <div class="url-row"><code>Authorization: Bearer &lt;your-token&gt;</code></div>
  </section>
</div>

<h2>{c["step3_h"]}</h2>
<p>{c["step3_p"]}</p>
<div class="url-row"><code id="check-prompt">{c["step3_prompt"]}</code> <button class="btn copy" data-copy-from="#check-prompt">{c["copy"]}</button></div>

<h2>{c["step4_h"]}</h2>
<p>{c["step4_p"]}</p>
{mentor_blocks}

<h2>{c["standards_h"]}</h2>
<p>{c["standards_p"]}</p>

<h2>{c["privacy_h"]}</h2>
<p>{c["privacy_p"]}</p>

<footer>
<p>{c["footer"].format(base=base)}</p>
</footer>

</main>

<script>
document.querySelectorAll('button.btn.copy').forEach(function(b) {{
  b.addEventListener('click', async function() {{
    var text = b.dataset.copy || '';
    if (!text && b.dataset.copyFrom) {{
      var src = document.querySelector(b.dataset.copyFrom);
      if (src) text = src.textContent;
    }}
    if (!text) return;
    try {{
      await navigator.clipboard.writeText(text);
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

// Google Forms iframe — same-origin policy blocks reading the iframe's
// content height, so we can't truly auto-fit. The submission redirect
// triggers a second `load` event, which is the reliable signal we have
// that the form has been submitted. Shrink the iframe to the size of
// the "Thanks!" response page on the second load. (First load is the
// form render itself; subsequent loads after the initial render are
// the post-submit redirect.) CSS adds a height transition for smoothness.
document.querySelectorAll('.access-form iframe').forEach(function(f) {{
  var loads = 0;
  f.addEventListener('load', function() {{
    loads += 1;
    if (loads >= 2) {{ f.style.height = '320px'; }}
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
    store = EscalationStore(s.escalations_file)
    expire_orphaned_pending(store)
    broker = EscalationBroker()
    adapter = _build_delivery_adapter(s)
    return ServerContext(
        settings=s,
        mentors=mentors,
        mentees=mentees,
        backends=backends,
        escalation_store=store,
        escalation_broker=broker,
        delivery_adapter=adapter,
        started_at=datetime.now(UTC),
    )


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
        "boot: %d mentors loaded (%s), %d mentees in allowlist, backends={%s}, escalation_adapter=%s",
        len(ctx.mentors),
        ",".join(ctx.mentors) or "none",
        len(ctx.mentees),
        ", ".join(f"{slug}:{b.mode_label}({'live' if b.is_live else 'stub'})" for slug, b in ctx.backends.items())
        or "none",
        ctx.delivery_adapter.kind,
    )

    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_server: FastMCP[Any]) -> AsyncIterator[dict[str, Any]]:
        """Start the delivery adapter on server boot; stop it on shutdown.

        The adapter's inbound task (Telegram long-poll, for the
        Telegram adapter) lives for the duration of the server.
        """
        await ctx.delivery_adapter.start(ctx.escalation_broker, ctx.escalation_store)
        try:
            yield {}
        finally:
            await ctx.delivery_adapter.stop()

    mcp: FastMCP[Any] = FastMCP(
        name="ammp-mcp",
        instructions=(
            "AMMP Mentoring track reference implementation. Each tool takes a "
            "`mentor` slug routing to the relevant playbook corpus. Privacy "
            "posture: no-retention; the mentor never accumulates a profile of "
            "the mentee. EscalateToHuman returns guidance text the mentee "
            "hands to its own operator — the mentor never reaches across "
            "compartments. EscalateToHumanMentor (server-side extension) "
            "forwards a B.h-approved question to the human behind the mentor "
            "and is long-running — the call blocks until the human replies."
        ),
        lifespan=_lifespan,
    )

    # MCP tool functions are deliberately PascalCase — the function name
    # becomes the on-the-wire AMMP operation name. They are thin wrappers
    # that delegate to the module-level handlers above so the cognitive
    # complexity of `create_server` stays well under SonarCloud's S3776 limit.
    # Five of the six are AMMP §5 baseline; `ListMentors` is a server-side
    # extension over the draft (mirrors the capability JSON's mentor block).

    # MCP tool wrappers below intentionally do NOT take an `api_key`
    # parameter. Authentication flows through the HTTP transport's
    # `Authorization: Bearer <token>` header — _authenticate() reads
    # it from the request via fastmcp.server.dependencies.get_http_request().
    # Exposing api_key as a tool parameter would prompt the LLM to ask
    # the user for it on every call, defeating transparent auth.

    @mcp.tool
    def ListMentors() -> dict[str, Any]:
        """List the mentors this server hosts.

        Each entry includes slug, display name, playbook count, confidence
        threshold, backend kind, whether the backend is live, and whether
        this mentor is the configured default. Use the returned slugs in
        the other five operations.
        """
        return _handle_list_mentors(ctx, None)

    @mcp.tool
    def ListPlaybooks(mentor: str = "") -> dict[str, Any]:
        """Enumerate the curated playbook corpus for the given mentor."""
        return _handle_list_playbooks(ctx, mentor, None)

    @mcp.tool
    def GetPlaybook(id: str, mentor: str = "") -> dict[str, Any]:
        """Retrieve one playbook with the full body of every skill inside it."""
        return _handle_get_playbook(ctx, id, mentor, None)

    @mcp.tool
    def GetSkill(playbook_id: str, id: str, mentor: str = "") -> dict[str, Any]:
        """Retrieve one specific skill from a playbook.

        Server-side extension over AMMP-01: lets a mentee fetch a
        single skill body when it already knows ``(playbook_id, id)``,
        without round-tripping the whole playbook.
        """
        return _handle_get_skill(ctx, playbook_id, id, mentor, None)

    @mcp.tool
    def SearchPlaybooks(query: str, mentor: str = "", limit: int = 5) -> dict[str, Any]:
        """Substring-search the mentor's corpus at skill granularity. Returns ranked matches."""
        return _handle_search_playbooks(ctx, query, mentor, limit, None)

    @mcp.tool
    async def AskMentor(question: str, mentor: str = "", context: str = "") -> dict[str, Any]:
        """Ask the mentor a free-form question.

        The mentor synthesises an answer from its playbook corpus +
        general operational knowledge, and returns a self-reported
        confidence in ``[0, 1]``. When confidence is below the mentor's
        threshold, the response also recommends ``EscalateToHuman``
        with suggested phrasing — *mentor-triggered* escalation, in
        addition to the mentee being free to call ``EscalateToHuman``
        directly.
        """
        return await _handle_ask_mentor(ctx, question, mentor, context, None)

    @mcp.tool(
        task=TaskConfig(mode="optional", poll_interval=timedelta(seconds=10)),
    )
    async def EscalateToHumanMentor(
        question: str,
        mentor: str = "",
        context: str = "",
        wait_seconds: float = 25.0,
        ctx_mcp: Context | None = None,
    ) -> dict[str, Any]:
        """Forward a B.h-approved question to A.h. Returns answered or pending.

        Sync-or-pending: the call delivers the question, then waits
        up to ``wait_seconds`` (default 25 s — comfortably below MCP
        client per-tool timeouts) for A.h to reply. If A.h replies
        in time, the response carries the answer. Otherwise the
        response is ``{escalation_id, status: "pending", ...}`` —
        call ``GetEscalation(escalation_id)`` later to retrieve the
        answer when it lands.

        Required precondition (enforced by B.a, not by this server):
        B.h has approved forwarding the question to A.h. The
        cross-compartment forward is only legitimate with that consent
        (AMMP §3.4).

        Args:
            question: The B.h-approved question text X.
            mentor: Slug of the agentic mentor A.a whose human A.h
                the question should reach. Empty falls through to the
                configured default mentor.
            context: Optional context to attach for A.h.
            wait_seconds: Max seconds to block waiting for A.h's
                reply. ``0`` returns immediately with
                ``status="pending"``. Default 25 s.
            ctx_mcp: FastMCP-injected context for progress notifications.

        Returns:
            :class:`EscalateToHumanMentorResponse` envelope dict with
            ``status`` either ``answered`` (answer included) or
            ``pending`` (poll ``GetEscalation`` later). On failure,
            an in-band error envelope: ``auth_failed``,
            ``unknown_mentor``, ``no_human_mentor``, ``empty_question``,
            ``delivery_failed``.
        """

        async def _report(progress: float, message: str) -> None:
            """Forward progress to the MCP client when a context is attached."""
            if ctx_mcp is not None:
                try:
                    await ctx_mcp.report_progress(progress=progress, message=message)
                except Exception as e:
                    logger.debug("report_progress failed (non-fatal): %s", e)

        return await _handle_escalate_to_human_mentor(
            ctx, question, mentor, context, None, wait_seconds=wait_seconds, progress=_report
        )

    @mcp.tool
    async def GetEscalation(
        escalation_id: str,
        wait_seconds: float = 0.0,
        ctx_mcp: Context | None = None,
    ) -> dict[str, Any]:
        """Retrieve an escalation's current state (and optionally wait for it).

        Companion to ``EscalateToHumanMentor`` for the sync-or-pending
        flow. When ``EscalateToHumanMentor`` returned
        ``status="pending"``, call this with the returned
        ``escalation_id`` to fetch A.h's answer once it's landed.
        Optionally block for up to ``wait_seconds`` if you expect
        the answer imminently.

        Args:
            escalation_id: The id returned by a prior
                ``EscalateToHumanMentor`` call.
            wait_seconds: Max seconds to block waiting for the
                answer to arrive (only meaningful while the
                escalation is still in ``delivered``/``pending``
                state). Default ``0`` returns the current state
                immediately.
            ctx_mcp: FastMCP-injected context for progress notifications.

        Returns:
            :class:`GetEscalationResponse` envelope dict with the
            current state, or an in-band ``auth_failed`` /
            ``unknown_escalation`` error.
        """

        async def _report(progress: float, message: str) -> None:
            """Forward progress to the MCP client when a context is attached."""
            if ctx_mcp is not None:
                try:
                    await ctx_mcp.report_progress(progress=progress, message=message)
                except Exception as e:
                    logger.debug("report_progress failed (non-fatal): %s", e)

        return await _handle_get_escalation(ctx, escalation_id, None, wait_seconds=wait_seconds, progress=_report)

    @mcp.tool
    def EscalateToHuman(
        situation: str,
        mentor: str = "",
        why_stuck: str = "",
    ) -> dict[str, Any]:
        """Mentee-triggered escalation to the mentee's own operator.

        Returns suggested phrasing the mentee hands to its own
        operator. The mentor does not page or message anyone — that's
        the Human-Gated Escalation Invariant (AMMP §3.4).
        """
        return _handle_escalate_to_human(ctx, situation, mentor, why_stuck, None)

    @mcp.tool
    def GetPluginArchive(plugin: str) -> dict[str, Any]:
        """Return a download URL for a plugin zip the mentee can install locally.

        Server-side extension over AMMP-01: when a mentor's playbook
        references a private marketplace plugin, the mentee can't
        clone the repo (the marketplace lives behind GitHub auth). It
        calls ``GetPluginArchive(plugin)`` and receives a URL pointing
        at this same server's ``/plugins/<plugin>.zip`` route, gated
        by the same Bearer token. The mentee hands the URL + install
        instructions to its user, who installs the plugin into their
        Claude Code / Desktop runtime by extracting the zip and
        running ``/plugin install <path>`` (or via the runtime's
        upload-zip installer).

        Args:
            plugin: The plugin name (kebab-case slug). Must match a
                plugin referenced by some playbook on this server.

        Returns:
            On success: ``{plugin, marketplace, archive_url,
            install_instructions}``. On failure: ``{error}`` with
            ``error`` one of ``auth_failed`` / ``invalid_plugin`` /
            ``not_found``.
        """
        return _handle_get_plugin_archive(ctx, plugin, None)

    @mcp.tool
    def GetSystemInfo() -> dict[str, Any]:
        """Return build / release / runtime metadata for the server.

        Server-side extension over AMMP-01 for end-to-end debugging:
        answers "what server am I connected to and is it healthy?" in
        one tool call. The response carries:

        * ``name`` + ``version`` — software identifier (e.g. `ammp-mcp 0.4.0`)
        * ``ammp_draft`` — IETF draft revision (e.g. `draft-ammp-01`)
        * ``python_version`` + ``platform`` — runtime info
        * ``started_at`` + ``uptime_seconds`` — boot timestamp and current uptime
        * ``mentor_count`` + ``mentee_count`` + ``default_mentor``
        * ``escalation_adapter`` — `log` / `telegram` / etc.
        * ``mount_path`` + ``public_url`` — addressing surface

        Deliberately does NOT surface file paths, env-var values,
        hostnames, PIDs, tokens, or anything that could compromise
        security. Auth-gated like every other AMMP tool.
        """
        return _handle_get_system_info(ctx, None)

    # Routes register under an optional mount prefix so this server can
    # share `mcp.helmguild.com` with other MCP servers later (e.g. an
    # AMMP Review-track server at `/review`). When `mount_path` is empty
    # (local dev default), the prefix is "" and routes live at root.
    prefix = ctx.settings.mount_path.rstrip("/")

    @mcp.custom_route(f"{prefix}/.well-known/agent.json", methods=["GET"])
    async def agent_card(_request: Request) -> JSONResponse:
        """AMMP capability advertisement. AMMP §10."""
        return JSONResponse(_build_capability_payload(ctx))

    @mcp.custom_route(f"{prefix}/mentors/{{slug}}/avatar", methods=["GET"])
    async def mentor_avatar(request: Request) -> Response:
        """Serve a mentor's avatar image, if one is present on disk.

        Slug is validated by the mentor registry — no user-controlled
        path concatenation. Returns 404 when the mentor exists but has
        no `avatar.*` file, and 404 (not 403) when the slug is unknown.
        """
        slug = request.path_params["slug"]
        mentor = ctx.mentors.get(slug)
        if mentor is None:
            return Response(status_code=404)
        path = mentor.avatar_path()
        if path is None:
            return Response(status_code=404)
        return FileResponse(
            path,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @mcp.custom_route(f"{prefix}/plugins/{{plugin}}.zip", methods=["GET"])
    async def plugin_archive(request: Request) -> Response:
        """Serve a plugin zip a mentee can hand its user to install.

        Bearer-token gated. The plugin name is validated against the
        set of ``plugin:`` references in this server's playbooks —
        unknown plugins return 404 (no information about whether the
        plugin exists in the marketplace clone but isn't referenced).
        """
        try:
            from fastmcp.server.dependencies import get_http_request

            req = get_http_request()
            auth_header = req.headers.get("authorization", "")
        except Exception:
            auth_header = request.headers.get("authorization", "")
        api_key: str | None = None
        if auth_header.lower().startswith("bearer "):
            api_key = auth_header[len("bearer ") :].strip() or None
        try:
            _authenticate(ctx, api_key)
        except ValueError as exc:
            return Response(status_code=401, content=str(exc))
        plugin = request.path_params["plugin"]
        # build_plugin_archive_response handles both invalid-name and
        # not-found via in-band errors; map them to 404 on the HTTP wire.
        known = _known_plugin_refs(ctx)
        meta = build_plugin_archive_response(ctx.settings.public_url, plugin, known)
        if meta.get("error"):
            return Response(status_code=404)
        entry = known[plugin]
        _, marketplace, plugin_dir = entry
        body = _build_plugin_archive(plugin_dir, plugin)
        log_event(
            ctx.settings.audit_log_path,
            "PluginArchiveDownload",
            extra=f"plugin={plugin} marketplace={marketplace} bytes={len(body)}",
        )
        return Response(
            content=body,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{plugin}.zip"',
                # Cache short so an in-flight plugin edit becomes visible
                # without an operator restart.
                "Cache-Control": "private, max-age=60",
            },
        )

    @mcp.custom_route(f"{prefix}/desktop-bundle.mcpb", methods=["GET"])
    async def desktop_bundle(_request: Request) -> Response:
        """Serve a Claude Cowork ``.mcpb`` bundle pointing at this server.

        Visitor double-clicks the downloaded file; Claude Cowork reads
        the bundled ``manifest.json``, prompts for the Bearer token via
        the declared ``user_config.bearer_token`` field, and installs
        the connector. No token is baked into the bundle, so it can
        be shared freely.
        """
        s = ctx.settings
        base = s.public_url.rstrip("/")
        # Trailing slash required — the FastMCP transport is mounted at
        # `/mcp/` and Starlette 307s a no-slash request, which mcp-remote
        # does not follow on POST. (Confirmed empirically 2026-05-11
        # when the .mcpb showed "server disconnected" in Claude Cowork.)
        mcp_url = f"{base}/mcp/"
        bundle = _build_desktop_bundle(public_url=base, mcp_url=mcp_url)
        return Response(
            content=bundle,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="helmguild-ammp.mcpb"',
                # The bundle's URLs come from settings.public_url; cache a
                # short window so a public_url change is visible quickly.
                "Cache-Control": "public, max-age=300",
            },
        )

    @mcp.custom_route(f"{prefix}/favicon.svg", methods=["GET"])
    async def favicon_svg(_request: Request) -> Response:
        """Serve the helmguild compass SVG favicon — same as www.helmguild.com."""
        from ._data import favicon_path as _favicon_path

        return FileResponse(
            _favicon_path() / "favicon.svg",
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @mcp.custom_route(f"{prefix}/favicon-32.png", methods=["GET"])
    async def favicon_png(_request: Request) -> Response:
        """Serve the 32×32 PNG favicon for browsers that don't load SVG."""
        from ._data import favicon_path as _favicon_path

        return FileResponse(
            _favicon_path() / "favicon-32.png",
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @mcp.custom_route(f"{prefix}/apple-touch-icon.png", methods=["GET"])
    async def apple_touch_icon(_request: Request) -> Response:
        """Serve the iOS home-screen icon."""
        from ._data import favicon_path as _favicon_path

        return FileResponse(
            _favicon_path() / "apple-touch-icon.png",
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @mcp.custom_route(f"{prefix}/robots.txt", methods=["GET"])
    async def robots_txt(_request: Request) -> Response:
        """Crawler-friendly robots.txt + Sitemap pointer for mcp.helmguild.com."""
        base = ctx.settings.public_url.rstrip("/")
        body = (
            "# mcp.helmguild.com — public AMMP landing.\n"
            "# Crawl freely; the sitemap below is the canonical URL inventory.\n"
            "# Tool transport endpoints under /mcp/ are bot-irrelevant (JSON-RPC,\n"
            "# Bearer-gated) and are disallowed below to save crawler budget.\n"
            "\n"
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /mcp/\n"
            "Disallow: /plugins/\n"
            "\n"
            f"Sitemap: {base}/sitemap.xml\n"
        )
        return Response(
            content=body,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @mcp.custom_route(f"{prefix}/sitemap.xml", methods=["GET"])
    async def sitemap_xml(_request: Request) -> Response:
        """Sitemap XML covering the bilingual landing + the canonical capability JSON."""
        base = ctx.settings.public_url.rstrip("/")
        today = datetime.now(UTC).date().isoformat()
        urls = [
            (base + "/", "weekly", "1.0", base + "/", base + "/de/"),
            (base + "/de/", "weekly", "1.0", base + "/", base + "/de/"),
            (base + "/.well-known/agent.json", "weekly", "0.7", None, None),
        ]
        parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        parts.append(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'
        )
        for loc, changefreq, priority, en, de in urls:
            parts.append("  <url>")
            parts.append(f"    <loc>{loc}</loc>")
            parts.append(f"    <lastmod>{today}</lastmod>")
            parts.append(f"    <changefreq>{changefreq}</changefreq>")
            parts.append(f"    <priority>{priority}</priority>")
            if en and de:
                parts.append(f'    <xhtml:link rel="alternate" hreflang="en" href="{en}"/>')
                parts.append(f'    <xhtml:link rel="alternate" hreflang="de" href="{de}"/>')
                parts.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{en}"/>')
            parts.append("  </url>")
        parts.append("</urlset>\n")
        return Response(
            content="\n".join(parts),
            media_type="application/xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @mcp.custom_route(f"{prefix}/", methods=["GET"])
    async def landing(_request: Request) -> HTMLResponse:
        """English landing page — how to connect a mentee + CLI usage."""
        return HTMLResponse(
            _render_landing(ctx, lang="en"),
            # The mentor list is rendered from live ctx; the integration
            # cards quote the live public URL. Cache should never serve
            # a stale version of either. `no-store` plus the long-form
            # `no-cache, must-revalidate` belt-and-braces tells every
            # browser + intermediate (Cloudflare, Caddy) not to hold it.
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @mcp.custom_route(f"{prefix}/de/", methods=["GET"])
    async def landing_de(_request: Request) -> HTMLResponse:
        """German landing page — mirrors the EN page with translated chrome.

        Mentor names and playbook content stay in English (corpus is
        content, not chrome) — same convention as helmguild.com itself.
        """
        return HTMLResponse(
            _render_landing(ctx, lang="de"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    # When mounted under a prefix, root `/` redirects to the prefixed
    # landing so visitors who hit the bare hostname find the page.
    # Future portal evolution: replace this with a portal page listing
    # every MCP server hosted under this domain.
    if prefix:

        @mcp.custom_route("/", methods=["GET"])
        async def root_redirect(_request: Request) -> Response:
            """Redirect bare hostname to the mounted landing page."""
            return Response(
                status_code=302,
                headers={
                    "Location": f"{prefix}/",
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                },
            )

    return mcp
