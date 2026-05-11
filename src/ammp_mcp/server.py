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
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP
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
    GetPlaybookResponse,
    GetWorkInstructionResponse,
    HumanMentorSummary,
    ListMentorsResponse,
    ListPlaybooksResponse,
    MentorSummary,
    PlaybookEntry,
    PlaybookSummary,
    SearchMatch,
    SearchPlaybooksResponse,
    WorkInstructionEntry,
    WorkInstructionSummary,
)
from .playbook import (
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
        corpus = load_playbooks(m.playbook_dir)
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
            HumanMentorSummary(name=m.human_mentor.name, url=m.human_mentor.url, contact=m.human_mentor.contact)
            if m.human_mentor
            else None
        )
        playbook_entries = [
            PlaybookEntry(
                id=pb.id,
                name=pb.name,
                description=pb.description,
                instructions=[
                    WorkInstructionEntry(id=wi.id, title=wi.title, summary=wi.summary, body=wi.body)
                    for wi in pb.instructions
                ],
            )
            for pb in corpus
        ]
        summaries.append(
            MentorSummary(
                slug=slug,
                name=m.name,
                description=m.description,
                avatar_url=avatar_url,
                human_mentor=human_mentor,
                playbook_count=len(corpus),
                instruction_count=sum(len(pb.instructions) for pb in corpus),
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
    carries its name + description + work-instruction summaries (id +
    title + one-line summary, no bodies). Fetch a full playbook's
    instruction bodies with ``GetPlaybook(id)``, or one instruction's
    body with ``GetWorkInstruction(playbook_id, id)``.

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
    corpus = load_playbooks(m.playbook_dir)
    return ListPlaybooksResponse(
        mentor=m.slug,
        count=len(corpus),
        playbooks=[
            PlaybookSummary(
                id=pb.id,
                name=pb.name,
                description=pb.description,
                instruction_count=len(pb.instructions),
                instructions=[
                    WorkInstructionSummary(id=wi.id, title=wi.title, summary=wi.summary) for wi in pb.instructions
                ],
            )
            for pb in corpus
        ],
    ).model_dump()


def _handle_get_playbook(ctx: ServerContext, playbook_id: str, mentor: str, api_key: str | None) -> dict[str, Any]:
    """Handle the ``GetPlaybook`` MCP tool call (AMMP §5.2).

    Returns one playbook with the full body of every work instruction
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
    corpus = load_playbooks(m.playbook_dir)
    pb = next((p for p in corpus if p.id == clean_id), None)
    if pb is None:
        return {"error": "not_found", "detail": f"id={clean_id}"}
    return GetPlaybookResponse(
        mentor=m.slug,
        id=pb.id,
        name=pb.name,
        description=pb.description,
        instructions=[
            WorkInstructionEntry(id=wi.id, title=wi.title, summary=wi.summary, body=wi.body) for wi in pb.instructions
        ],
    ).model_dump()


def _handle_get_work_instruction(
    ctx: ServerContext, playbook_id: str, instruction_id: str, mentor: str, api_key: str | None
) -> dict[str, Any]:
    """Handle the ``GetWorkInstruction`` MCP-extension tool call.

    Server-side extension beyond AMMP-01's five operations: fetch one
    specific work instruction by ``(playbook_id, instruction_id)`` when
    the mentee already knows which one it wants, without round-tripping
    the whole playbook.

    Args:
        ctx: The per-request server context.
        playbook_id: Directory-name slug of the parent playbook.
        instruction_id: Filename-stem of the requested instruction.
        mentor: Mentor slug. Empty string falls through to the
            configured default mentor.
        api_key: Caller-supplied Bearer key, or ``None``. Required when
            ``ctx.settings.require_auth`` is set.

    Returns:
        A JSON-serialisable dict — either a
        :class:`GetWorkInstructionResponse` envelope on success, or an
        in-band error envelope on auth failure, unknown mentor,
        invalid id, or not-found.
    """
    try:
        mentee_slug = _authenticate(ctx, api_key)
    except ValueError as e:
        return {"error": "auth_failed", "detail": str(e)}
    m = _resolve_mentor(ctx, mentor)
    if not m:
        return {"error": "unknown_mentor"}
    clean_pb = safe_id(playbook_id)
    clean_id = safe_id(instruction_id)
    if not clean_pb or not clean_id:
        return {"error": "invalid_id"}
    log_event(
        ctx.settings.audit_log_path,
        "GetWorkInstruction",
        mentor=m.slug,
        mentee=mentee_slug,
        request_hash=short_hash(f"{clean_pb}/{clean_id}"),
    )
    corpus = load_playbooks(m.playbook_dir)
    pb = next((p for p in corpus if p.id == clean_pb), None)
    if pb is None:
        return {"error": "not_found", "detail": f"playbook_id={clean_pb}"}
    wi = next((w for w in pb.instructions if w.id == clean_id), None)
    if wi is None:
        return {"error": "not_found", "detail": f"playbook_id={clean_pb} id={clean_id}"}
    return GetWorkInstructionResponse(
        mentor=m.slug,
        playbook_id=pb.id,
        id=wi.id,
        title=wi.title,
        summary=wi.summary,
        body=wi.body,
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
    corpus = load_playbooks(m.playbook_dir)
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

    corpus = load_playbooks(m.playbook_dir)
    ranked = keyword_rank(corpus, q + " " + context, limit=3)
    relevant = [WorkInstructionSummary(id=wi.id, title=wi.title, summary=wi.summary) for wi, _ in ranked]
    # Backends consume `(title, body)` pairs to assemble the grounding
    # block in the system prompt. With the new hierarchy we cite each
    # work instruction by its full ``<playbook> · <instruction>`` path so
    # the LLM can attribute back accurately when synthesising.
    playbook_bodies = [(f"{wi.playbook_id} · {wi.title}", wi.body) for wi, _ in ranked]

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
            "detail": "the mentor is currently unable to synthesise an answer; try GetWorkInstruction on a relevant id",
            "relevant_instructions": [r.model_dump() for r in relevant],
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
            human_mentor=HumanMentorSummary(name=hm.name, url=hm.url, contact=hm.contact),
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
        relevant_instructions=relevant,
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


async def _handle_escalate_to_human_mentor(
    ctx: ServerContext,
    question: str,
    mentor: str,
    context: str,
    api_key: str | None,
    progress: Callable[[float, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Long-running: forward a B.h-approved question to A.h, return A.h's reply.

    The flow (B.a is the calling mentee, B.h is its operator, A.a is
    this server's mentor, A.h is the human behind A.a):

    1. B.a (after getting B.h's approval) calls this tool with the
       final question text X.
    2. The handler persists an :class:`Escalation`, opens a waiter on
       the in-memory broker, and hands X to the configured delivery
       adapter (Telegram bot, OpenClaw bridge, or log-only).
    3. The handler emits ``notifications/progress`` updates as the
       delivery transitions ``pending → delivered → answered`` and
       awaits the broker's event.
    4. When A.h replies, the adapter calls
       :meth:`EscalationBroker.resolve`; the waiter wakes, and the
       handler returns A.h's reply Z to B.a synchronously.

    Cancellation: if B.a's MCP client sends ``$/cancelRequest``, the
    awaiting task is cancelled; the handler marks the escalation
    ``cancelled`` and propagates the cancellation back up. Adapter
    inbound replies arriving after cancellation are silently
    dropped (no waiter to resolve; the late reply is logged).

    Args:
        ctx: The per-request server context.
        question: The B.h-approved question text X. Empty / whitespace
            returns ``empty_question``.
        mentor: A.a's slug. Empty falls through to the configured
            default mentor.
        context: Optional context attached for A.h.
        api_key: B.a's Bearer key. Required when ``require_auth``.
        progress: Optional callback for MCP progress notifications.
            Called with ``(0..1 progress, "human readable")``.

    Returns:
        A JSON-serialisable dict — either an
        :class:`EscalateToHumanMentorResponse` envelope on success, or
        an in-band error envelope on auth failure, unknown mentor, no
        human mentor configured, delivery failure, timeout, or
        cancellation.

    Raises:
        asyncio.CancelledError: Re-raised when the MCP client sends
            ``$/cancelRequest``. The escalation is marked ``cancelled``
            in the store before propagating so the audit log stays
            consistent.
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

    timeout = ctx.settings.escalation_default_timeout_seconds
    try:
        await asyncio.wait_for(waiter.event.wait(), timeout=timeout)
    except TimeoutError:
        ctx.escalation_store.update(esc.id, status="expired", cancel_reason="timeout")
        ctx.escalation_broker.cancel(esc.id, "timeout")
        return {"error": "timeout", "detail": f"no reply within {timeout:.0f}s", "escalation_id": esc.id}
    except asyncio.CancelledError:
        ctx.escalation_store.update(esc.id, status="cancelled", cancel_reason="mcp_cancelled")
        ctx.escalation_broker.cancel(esc.id, "mcp_cancelled")
        raise

    if waiter.answer is None:
        # Cancelled or expired between deliver and wake — surface as error.
        return {
            "error": "cancelled",
            "detail": waiter.cancel_reason or "no answer received",
            "escalation_id": esc.id,
        }
    final = ctx.escalation_store.get(esc.id)
    answered_at = final.answered_at.isoformat() if final and final.answered_at else ""
    if progress is not None:
        await progress(1.0, "human mentor replied")
    return EscalateToHumanMentorResponse(
        mentor=m.slug,
        escalation_id=esc.id,
        answer=waiter.answer,
        answered_at=answered_at,
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
        corpus = load_playbooks(m.playbook_dir)
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
                "instructionCount": sum(len(pb.instructions) for pb in corpus),
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
            "GetWorkInstruction",
            "SearchPlaybooks",
            "AskMentor",
            "EscalateToHuman",
            "EscalateToHumanMentor",
        ],
    }


# ─── Human-facing landing page ────────────────────────────────────────────


def _build_mentor_playbook_prompt(
    *,
    public_url: str,
    mentor: Mentor,
    playbook_id: str,
    playbook_name: str,
    playbook_description: str,
    instruction_count: int,
) -> str:
    """Render a copy-paste prompt that starts a mentoring session.

    The user pastes this into their MCP-aware agent (Claude.ai /
    Cowork / Code / OpenClaw / Hermes) after they've configured the
    ammp-mcp server in the agent's connector settings. The prompt
    walks the agent through the canonical first three tool calls
    (`ListPlaybooks` → `GetPlaybook` → `AskMentor`), and adds the
    cross-compartment escalation step when the mentor has a
    configured ``human_mentor``.

    Args:
        public_url: The server's advertised URL (no trailing slash).
        mentor: The :class:`Mentor` being addressed.
        playbook_id: The playbook's directory-name slug.
        playbook_name: The playbook's human-readable name.
        playbook_description: The one-line description from
            ``playbook.json``.
        instruction_count: Number of work instructions inside this
            playbook.

    Returns:
        A multi-line prompt string, ready to drop into a ``<pre>``.
    """
    url = public_url.rstrip("/")
    n = instruction_count
    instr_word = "work instruction" if n == 1 else "work instructions"
    body = (
        f'You are now operating under the mentorship of {mentor.name}\'s "{playbook_name}" playbook '
        f"on the helmguild AMMP server at {url}.\n\n"
        "To start:\n\n"
        f'1. Call `ListPlaybooks` with `mentor: "{mentor.slug}"` to confirm you can reach the server.\n'
        f'2. Call `GetPlaybook` with `id: "{playbook_id}"` and `mentor: "{mentor.slug}"` to load the playbook — '
        f'{n} {instr_word} covering "{playbook_description}".\n'
        "3. Internalize the instructions and apply them going forward in this conversation. "
        f"When you hit something the playbook does not cover, call `AskMentor` on `{mentor.slug}` rather than guessing.\n"
    )
    if mentor.human_mentor is not None:
        hm_name = mentor.human_mentor.name
        body += (
            f"4. If your confidence stays low after `AskMentor` AND my operator approves the forward, "
            f"you may call `EscalateToHumanMentor` (long-running — it blocks until {hm_name} replies) "
            f"to forward the question to {hm_name}, the human behind {mentor.name}. "
            "Wait for the reply before acting.\n"
        )
    body += "\nAcknowledge by quoting the playbook name and the count of work instructions you loaded, then proceed."
    return body


def _render_landing(ctx: ServerContext) -> str:
    """Render the mentee-facing landing page served at ``GET /``.

    Self-contained HTML (inline CSS + JS, no external assets). For each
    mentor loaded in ``ctx`` renders avatar, name, description, the
    *human mentor* attribution (so escalation destinations are explicit),
    and the playbook → work-instruction tree. All four sources are
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

    Returns:
        A complete HTML document as a string, ready for ``HTMLResponse``.
    """
    import re as _re
    from html import escape as _h
    from urllib.parse import quote as _q

    # Inline-markdown renderer — handles `**bold**`, `*italic*`, and
    # `` `code` `` in summary lines so playbook / work-instruction
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
            text: The raw text from a playbook description or work
                instruction summary.

        Returns:
            HTML-safe rendered string.
        """
        escaped = _h(text)
        escaped = _re_code.sub(r"<code>\1</code>", escaped)
        escaped = _re_bold.sub(r"<strong>\1</strong>", escaped)
        escaped = _re_italic.sub(r"<em>\1</em>", escaped)
        return escaped

    base = ctx.settings.public_url.rstrip("/")
    mcp_url = f"{base}/mcp/"
    host = base.replace("https://", "").replace("http://", "")

    mailto_subject = "ammp-mcp — please connect me"
    mailto_body = (
        "Hi Helmut,\n\n"
        f"I'd like to connect to {base} as a mentee.\n\n"
        "  Where I'll connect from : <Claude.ai / Claude Cowork / Claude Code / OpenClaw / Hermes>\n"
        "  Best secure channel     : <Signal / iMessage / Telegram + number>\n\n"
        "Thanks!\n"
    )
    mailto = f"mailto:helmuthva@gmail.com?subject={_q(mailto_subject)}&body={_q(mailto_body)}"

    mentor_blocks_parts: list[str] = []
    for slug, m in ctx.mentors.items():
        playbooks = load_playbooks(m.playbook_dir)
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
            hm_name_html = f"<a href='{_h(hm.url)}'>{_h(hm.name)}</a>" if hm.url else _h(hm.name)
            hm_contact = f" · <span class='hm-contact'>{_h(hm.contact)}</span>" if hm.contact else ""
            human_html = f"<p class='human-mentor'>Behind {_h(m.name)}: {hm_name_html}{hm_contact}</p>"
        else:
            human_html = ""
        # Playbook → work-instruction nested rendering. Each playbook is
        # an area of practice; each instruction is one craft rule.
        # Instructions are collapsed inside a <details> by default —
        # one mentor with three playbooks of 5-12 instructions each
        # would otherwise dominate the page.
        if playbooks:
            pb_html_parts: list[str] = []
            for pb in playbooks:
                if pb.instructions:
                    instr_items = "".join(
                        f"<li><span class='wi-title'>{_h(wi.title)}</span>"
                        + (f"<span class='wi-desc'>{_md_inline(wi.summary)}</span>" if wi.summary else "")
                        + "</li>"
                        for wi in pb.instructions
                    )
                    n = len(pb.instructions)
                    label = f"{n} work instruction{'s' if n != 1 else ''}"
                    instructions_block = (
                        f"<details class='instructions-details'>"
                        f"<summary>{label}</summary>"
                        f"<ul class='instructions'>{instr_items}</ul>"
                        "</details>"
                    )
                else:
                    instructions_block = "<p class='empty'>No work instructions yet.</p>"
                pb_desc = f"<p class='pb-desc'>{_md_inline(pb.description)}</p>" if pb.description else ""
                # Copy-paste prompt for starting a mentoring session on
                # this specific (mentor, playbook). User pastes into
                # their MCP-aware agent. Sits next to the instructions
                # disclosure as a sibling, collapsed by default.
                prompt_text = _build_mentor_playbook_prompt(
                    public_url=base,
                    mentor=m,
                    playbook_id=pb.id,
                    playbook_name=pb.name,
                    playbook_description=pb.description,
                    instruction_count=len(pb.instructions),
                )
                prompt_dom_id = f"prompt--{_h(slug)}--{_h(pb.id)}"
                prompt_block = (
                    "<details class='prompt-details'>"
                    "<summary>Prompt to start this mentoring</summary>"
                    f"<pre class='prompt' id='{prompt_dom_id}'"
                    f" data-mentor='{_h(slug)}' data-playbook='{_h(pb.id)}'>"
                    f"{_h(prompt_text)}</pre>"
                    f"<button class='btn copy' data-copy-from='#{prompt_dom_id}'>Copy prompt</button>"
                    "</details>"
                )
                pb_html_parts.append(
                    "<section class='playbook'>"
                    f"<h4 class='pb-name'>{_h(pb.name)} <span class='pb-id'>{_h(pb.id)}</span></h4>"
                    f"{pb_desc}"
                    f"{instructions_block}"
                    f"{prompt_block}"
                    "</section>"
                )
            playbook_section = "".join(pb_html_parts)
        else:
            playbook_section = "<p class='empty'>No playbooks yet.</p>"
        mentor_blocks_parts.append(
            "<section class='mentor'>"
            "<div class='mentor-head'>"
            f"{avatar_html}"
            "<div class='mentor-id'>"
            f"<h3>{_h(m.name)} <span class='slug'>{_h(slug)}</span></h3>"
            f"{description_html}"
            f"{human_html}"
            "</div>"
            "</div>"
            f"{playbook_section}"
            "</section>"
        )
    mentor_blocks = (
        "".join(mentor_blocks_parts)
        if mentor_blocks_parts
        else "<p class='empty'>No mentors are currently available.</p>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ammp · {host}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Mentor your agent — connect your Claude (or other MCP-aware) agent to a curated playbook library so it returns grounded, cited, calmer.">
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
main{{max-width:40rem;margin:0 auto;padding:3.5rem 1.75rem 3rem}}
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
.pb-desc{{margin:0 0 .35rem;color:var(--ink-soft);font-size:.9rem;line-height:1.45}}
.instructions-details,.prompt-details{{margin:.4rem 0 0}}
.instructions-details > summary,.prompt-details > summary{{cursor:pointer;color:var(--accent);font-size:.88rem;font-family:var(--sans);padding:.25rem 0;list-style:none;user-select:none}}
.instructions-details > summary::-webkit-details-marker,.prompt-details > summary::-webkit-details-marker{{display:none}}
.instructions-details > summary::before,.prompt-details > summary::before{{content:"▸";display:inline-block;width:.9rem;color:var(--ink-soft);transition:transform .15s ease}}
.instructions-details[open] > summary::before,.prompt-details[open] > summary::before{{transform:rotate(90deg)}}
.instructions-details > summary:hover,.prompt-details > summary:hover{{color:var(--accent-hover)}}
.prompt-details pre.prompt{{margin:.5rem 0 .5rem;padding:.85rem 1rem;background:rgba(0,0,0,.045);border:1px solid var(--rule);border-radius:6px;font-family:var(--mono);font-size:.82rem;line-height:1.5;color:var(--ink);white-space:pre-wrap;overflow-x:auto}}
@media (prefers-color-scheme: dark) {{ .prompt-details pre.prompt{{background:rgba(255,255,255,.04)}} }}
.instructions{{margin:.5rem 0 0;padding:0 0 0 1.4rem;color:var(--ink-soft);list-style:disc}}
.instructions li{{margin:.3rem 0;color:var(--ink);font-size:.92rem}}
.instructions li::marker{{color:var(--ink-soft)}}
.instructions .wi-title{{color:var(--ink);font-weight:500}}
.instructions .wi-desc{{display:block;color:var(--ink-soft);font-size:.85rem;line-height:1.45;margin-top:.05rem}}
.instructions .wi-desc code{{background:rgba(0,0,0,.045);border:1px solid var(--rule);border-radius:3px;padding:.05rem .3rem;font-size:.92em}}
@media (prefers-color-scheme: dark) {{ .instructions .wi-desc code{{background:rgba(255,255,255,.04)}} }}
.instructions li.empty{{color:var(--ink-soft);font-style:italic;list-style:none;margin-left:-1.1rem}}
.empty{{color:var(--ink-soft);font-style:italic}}
.runtimes{{margin:.4rem 0 0;color:var(--ink-soft);font-size:.95rem}}
.cta{{margin:1rem 0 .5rem}}
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
<main>

<h1>Mentor your agent.</h1>
<p class="lede">Connect your Claude (or other MCP-aware) agent to a curated playbook library and it comes back grounded, cited, calmer. No kept history. Three steps.</p>

<h2>Step 1 — Request your access token</h2>
<p>Tokens are issued by hand — one mail, one reply. Tap below and we'll send yours back through the secure channel you specify.</p>
<p class="cta"><a class="btn primary" href="{mailto}">Request access</a></p>

<h2>Step 2 — Configure your agent's MCP connection</h2>
<p>Once you have your token, open your agent's connector settings (works with <strong>Claude.ai</strong>, <strong>Claude Cowork</strong>, <strong>Claude Code</strong>, <strong>OpenClaw</strong>, and <strong>Hermes</strong>) and add a custom MCP server with the URL below plus an <code>Authorization: Bearer &lt;your-token&gt;</code> header.</p>
<div class="url-row"><code>{mcp_url}</code> <button class="btn copy" data-copy="{mcp_url}">Copy URL</button></div>

<h2>Step 3 — Pick a mentor and start the session</h2>
<p>Browse the mentors below, expand the playbook you want to be mentored on, and copy its prompt into your now-connected agent. The prompt walks the agent through the canonical first calls so the mentoring starts right away.</p>
{mentor_blocks}

<h2>Privacy</h2>
<p>No retention. Your questions and the mentor's answers are never stored — only an opaque hash of each call lands in the audit log. See <a href="https://www.helmguild.com/rfc/ammp/">the AMMP draft</a> for the normative wording.</p>

<footer>
<p>Reference implementation of the <a href="https://www.helmguild.com/rfc/ammp/">Agentic Mentor-Mentee Protocol</a>. MIT-licensed. Run by <a href="https://helmut.hoffer-von-ankershoffen.me/">Helmut Hoffer von Ankershoffen</a>. <a href="{base}/.well-known/agent.json">Capability JSON</a> · <a href="https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp">Source</a></p>
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

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_server: FastMCP[Any]) -> Any:
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
        """Retrieve one playbook with the full body of every work instruction inside it."""
        return _handle_get_playbook(ctx, id, mentor, api_key)

    @mcp.tool
    def GetWorkInstruction(playbook_id: str, id: str, mentor: str = "", api_key: str | None = None) -> dict[str, Any]:
        """Retrieve one specific work instruction from a playbook.

        Server-side extension over AMMP-01: lets a mentee fetch a single
        instruction body when it already knows ``(playbook_id, id)``,
        without round-tripping the whole playbook.
        """
        return _handle_get_work_instruction(ctx, playbook_id, id, mentor, api_key)

    @mcp.tool
    def SearchPlaybooks(query: str, mentor: str = "", limit: int = 5, api_key: str | None = None) -> dict[str, Any]:
        """Substring-search the mentor's corpus at work-instruction granularity. Returns ranked matches."""
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
    async def EscalateToHumanMentor(
        question: str,
        mentor: str = "",
        context: str = "",
        api_key: str | None = None,
        ctx_mcp: Context | None = None,
    ) -> dict[str, Any]:
        """Forward a B.h-approved question to A.h (the human behind A.a).

        Long-running MCP tool. The call blocks until A.h replies (or
        the configured timeout fires, or B.a sends $/cancelRequest).
        While waiting, the server emits ``notifications/progress``
        updates: queued → delivered → answered.

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
            api_key: B.a's Bearer key. Required when ``require_auth``.
            ctx_mcp: FastMCP-injected context for progress notifications.

        Returns:
            On success: ``{mentor, escalation_id, answer, answered_at}``.
            On failure: an in-band error envelope. Errors:
            ``auth_failed``, ``unknown_mentor``, ``no_human_mentor``,
            ``empty_question``, ``delivery_failed``, ``timeout``,
            ``cancelled``.
        """

        async def _report(progress: float, message: str) -> None:
            """Forward progress to the MCP client when a context is attached."""
            if ctx_mcp is not None:
                try:
                    await ctx_mcp.report_progress(progress=progress, message=message)
                except Exception as e:
                    logger.debug("report_progress failed (non-fatal): %s", e)

        return await _handle_escalate_to_human_mentor(ctx, question, mentor, context, api_key, progress=_report)

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

    @mcp.custom_route(f"{prefix}/", methods=["GET"])
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
