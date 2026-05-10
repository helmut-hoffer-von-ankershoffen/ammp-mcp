"""FastMCP server exposing AMMP's Mentoring track.

Five operations: ListPlaybooks, GetPlaybook, SearchPlaybooks, AskMentor,
EscalateToHuman. Multi-mentor: each call takes a `mentor: str` slug
and the server routes to the right corpus. Multi-mentee: when
`require_auth` is enabled, requests must carry a Bearer API key matching
an entry in the mentee allowlist.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __ammp_draft__, __version__
from .audit import log_event, short_hash
from .llm import LLMAnswer, LLMClient
from .models import (
    AskMentorResponse,
    EscalateToHumanResponse,
    GetPlaybookResponse,
    ListPlaybooksResponse,
    PlaybookSummary,
    SearchMatch,
    SearchPlaybooksResponse,
)
from .playbooks import keyword_rank, load_corpus, safe_id, search
from .registries import find_mentee_by_api_key, get_mentor, load_mentees, load_mentors
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)


# ─── Server factory ──────────────────────────────────────────────────────


def create_server(settings: Settings | None = None) -> FastMCP:
    """Build a FastMCP server bound to the given settings (or the env-derived
    singleton). Returns the server ready to `.run()`."""
    s = settings or get_settings()

    mentors = load_mentors(s.mentors_root)
    mentees = load_mentees(s.mentees_file) if s.mentees_file.exists() else {}
    llm = LLMClient(
        api_key=s.anthropic_api_key,
        model=s.llm_model,
        max_concurrent=s.llm_max_concurrent,
        timeout_seconds=s.llm_timeout_seconds,
    )

    logger.info(
        "boot: %d mentors loaded (%s), %d mentees in allowlist, llm_live=%s",
        len(mentors),
        ",".join(mentors) or "none",
        len(mentees),
        llm.is_live,
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

    # ─── Auth helper (no-op when require_auth=False) ─────────────────────
    def _auth(api_key: str | None) -> str:
        """Return the mentee slug for this request, or `'anonymous'` when
        auth is disabled. Raises ValueError when auth is required and the
        key is missing or unrecognised."""
        if not s.require_auth:
            return "anonymous"
        if not api_key:
            raise ValueError("api_key_required")
        m = find_mentee_by_api_key(mentees, api_key)
        if not m:
            raise ValueError("api_key_invalid")
        return m.slug

    # ─── Tool: ListPlaybooks ─────────────────────────────────────────────
    @mcp.tool
    def ListPlaybooks(mentor: str = "", api_key: str | None = None) -> dict[str, Any]:
        """Enumerate the curated playbook corpus for the given mentor.

        Args:
            mentor: Mentor slug (e.g. 'pepe'). Empty → server default.
            api_key: Bearer token (only required when server runs with auth on).
        """
        try:
            mentee_slug = _auth(api_key)
        except ValueError as e:
            return {"error": "auth_failed", "detail": str(e)}
        m = get_mentor(mentors, mentor, s.default_mentor)
        if not m:
            return {"error": "unknown_mentor", "detail": f"slug={mentor or s.default_mentor!r}"}
        log_event(s.audit_log_path, "ListPlaybooks", mentor=m.slug, mentee=mentee_slug)
        corpus = load_corpus(m.playbook_dir)
        return ListPlaybooksResponse(
            mentor=m.slug,
            count=len(corpus),
            playbooks=[PlaybookSummary(id=pb.id, title=pb.title, summary=pb.summary) for pb in corpus],
        ).model_dump()

    # ─── Tool: GetPlaybook ───────────────────────────────────────────────
    @mcp.tool
    def GetPlaybook(id: str, mentor: str = "", api_key: str | None = None) -> dict[str, Any]:
        """Retrieve a single playbook from the given mentor's corpus."""
        try:
            mentee_slug = _auth(api_key)
        except ValueError as e:
            return {"error": "auth_failed", "detail": str(e)}
        m = get_mentor(mentors, mentor, s.default_mentor)
        if not m:
            return {"error": "unknown_mentor"}
        clean_id = safe_id(id)
        if not clean_id:
            return {"error": "invalid_id"}
        log_event(
            s.audit_log_path,
            "GetPlaybook",
            mentor=m.slug,
            mentee=mentee_slug,
            request_hash=short_hash(clean_id),
        )
        target = m.playbook_dir / f"{clean_id}.md"
        if not target.is_file():
            return {"error": "not_found", "detail": f"id={clean_id}"}
        from .playbooks import _load_one  # lazy import to avoid widening public surface

        pb = _load_one(target)
        return GetPlaybookResponse(mentor=m.slug, id=pb.id, title=pb.title, body=pb.body).model_dump()

    # ─── Tool: SearchPlaybooks ───────────────────────────────────────────
    @mcp.tool
    def SearchPlaybooks(query: str, mentor: str = "", limit: int = 5, api_key: str | None = None) -> dict[str, Any]:
        """Substring-search the mentor's corpus. Returns ranked matches."""
        try:
            mentee_slug = _auth(api_key)
        except ValueError as e:
            return {"error": "auth_failed", "detail": str(e)}
        m = get_mentor(mentors, mentor, s.default_mentor)
        if not m:
            return {"error": "unknown_mentor"}
        if not query or not query.strip():
            return {"error": "empty_query"}
        log_event(
            s.audit_log_path,
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

    # ─── Tool: AskMentor ─────────────────────────────────────────────────
    @mcp.tool
    async def AskMentor(
        question: str,
        mentor: str = "",
        context: str = "",
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Ask the mentor a free-form question. The mentor synthesises an
        answer from its playbook corpus + general operational knowledge,
        and returns a self-reported confidence in [0, 1]. When confidence
        is below the mentor's threshold, the response also recommends
        EscalateToHuman with suggested phrasing — *mentor-triggered*
        escalation, in addition to the mentee being free to call
        EscalateToHuman directly."""
        try:
            mentee_slug = _auth(api_key)
        except ValueError as e:
            return {"error": "auth_failed", "detail": str(e)}
        m = get_mentor(mentors, mentor, s.default_mentor)
        if not m:
            return {"error": "unknown_mentor"}
        q = (question or "").strip()
        if not q:
            return {"error": "empty_question"}

        log_event(
            s.audit_log_path,
            "AskMentor",
            mentor=m.slug,
            mentee=mentee_slug,
            request_hash=short_hash(q),
        )

        corpus = load_corpus(m.playbook_dir)
        ranked = keyword_rank(corpus, q + " " + context, limit=3)
        relevant = [PlaybookSummary(id=pb.id, title=pb.title, summary=pb.summary) for pb, _ in ranked]
        playbook_bodies = [(pb.title, pb.body) for pb, _ in ranked]

        try:
            llm_answer: LLMAnswer = await llm.ask(
                mentor_name=m.name,
                persona=m.persona,
                question=q if not context else f"{q}\n\nContext:\n{context}",
                playbook_bodies=playbook_bodies,
            )
        except Exception as e:
            logger.warning("AskMentor LLM call failed: %s", e)
            return {
                "error": "llm_failed",
                "detail": "the mentor is currently unable to synthesise an answer; try GetPlaybook on a relevant id",
                "relevant_playbooks": [r.model_dump() for r in relevant],
            }

        threshold = m.confidence_threshold
        escalation_recommended = llm_answer.confidence < threshold
        suggested = None
        if escalation_recommended:
            suggested = (
                f"To your operator: \"My mentor returned an answer at confidence "
                f"{llm_answer.confidence:.2f}, below their threshold of "
                f"{threshold:.2f}, on this question — could you take a look "
                f"before I act on it? Question: {q[:300]}\""
            )

        return AskMentorResponse(
            mentor=m.slug,
            question=q[:500],
            answer=llm_answer.answer,
            confidence=llm_answer.confidence,
            relevant_playbooks=relevant,
            escalation_recommended=escalation_recommended,
            suggested_message_to_your_operator=suggested,
        ).model_dump()

    # ─── Tool: EscalateToHuman ───────────────────────────────────────────
    @mcp.tool
    def EscalateToHuman(
        situation: str,
        mentor: str = "",
        why_stuck: str = "",
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Mentee-triggered escalation. Returns suggested phrasing the
        mentee hands to its own operator. The mentor does not page or
        message anyone — that's the Human-Gated Escalation Invariant
        (AMMP §3.4)."""
        try:
            mentee_slug = _auth(api_key)
        except ValueError as e:
            return {"error": "auth_failed", "detail": str(e)}
        m = get_mentor(mentors, mentor, s.default_mentor)
        if not m:
            return {"error": "unknown_mentor"}
        s_situation = (situation or "").strip()
        if not s_situation:
            return {"error": "empty_situation"}
        log_event(
            s.audit_log_path,
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
            "act until you respond.\""
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

    # ─── Capability advertisement ────────────────────────────────────────
    @mcp.custom_route("/.well-known/agent.json", methods=["GET"])
    async def agent_card(_request: Request) -> JSONResponse:
        """AMMP capability advertisement. AMMP §10."""
        mentor_summaries = []
        for slug, m in mentors.items():
            corpus = load_corpus(m.playbook_dir)
            mentor_summaries.append(
                {
                    "slug": slug,
                    "name": m.name,
                    "playbookCount": len(corpus),
                }
            )
        return JSONResponse(
            {
                "name": "ammp-mcp",
                "version": __version__,
                "url": s.public_url,
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
                        "askMentorMode": "llm-synthesised" if llm.is_live else "structured-pointer",
                        "askMentorMaxConcurrent": s.llm_max_concurrent,
                        "playbookLanguages": ["en"],
                    },
                    "privacyPosture": {
                        "retention": "no-retention",
                        "auditLog": "hash-only",
                        "crossCompartmentEscalation": "prohibited",
                        "requireAuth": s.require_auth,
                    },
                    "bindings": ["mcp"],
                    "invariants": ["compartmentalisation", "human-gated-escalation"],
                },
                "operations": [
                    "ListPlaybooks",
                    "GetPlaybook",
                    "SearchPlaybooks",
                    "AskMentor",
                    "EscalateToHuman",
                ],
            }
        )

    return mcp


def run() -> None:
    """Console entrypoint — `ammp-server`. Reads settings from env."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    server = create_server(s)
    server.run(transport="http", host=s.host, port=s.port)


if __name__ == "__main__":
    run()
