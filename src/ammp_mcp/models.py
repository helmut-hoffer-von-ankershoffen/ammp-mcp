"""Response envelopes for the AMMP Mentoring-track operations.

The static config classes (``Mentor``, ``Mentee``, the ``BackendConfig``
discriminated union) live in :mod:`ammp_mcp.mentor` and
:mod:`ammp_mcp.mentee`. This module is intentionally narrow: only the
envelopes each server tool returns.

The corpus is a two-level hierarchy: a mentor has zero or more
**playbooks** (areas of practice); each playbook contains zero or more
**work instructions** (individual craft rules, one markdown file each).
The envelopes here mirror that shape.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HumanMentorSummary(BaseModel):
    """The human standing behind an agentic mentor, as published on the wire.

    Mirrors :class:`ammp_mcp.mentor.HumanMentor` but lives in the
    envelope layer so the wire surface stays decoupled from the storage
    model. Published so mentees know where escalation ultimately
    lands — the agentic mentor never pages the human directly (AMMP
    §3.4), but downstream operators can use this to make manual
    escalation paths concrete.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str | None = None
    profile_url: str | None = None
    contact: str | None = None


class WorkInstructionSummary(BaseModel):
    """Compact identity of one work instruction.

    No body — used by ``ListPlaybooks`` (folded into each playbook) and
    by ``SearchPlaybooks`` (snippet replaces body). Mentees fetch the
    full body with ``GetWorkInstruction``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str = ""


class WorkInstructionEntry(BaseModel):
    """One work instruction with its full body — for ``GetPlaybook`` etc."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str = ""
    body: str


class PlaybookSummary(BaseModel):
    """Summary of one playbook (area of practice). No instruction bodies."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    instruction_count: int = 0
    instructions: list[WorkInstructionSummary] = Field(default_factory=list)


class ListPlaybooksResponse(BaseModel):
    """Response envelope for the ``ListPlaybooks`` AMMP operation.

    Returns the mentor's playbooks (areas of practice) — each carries
    its name + description + instruction summaries (title only, no
    bodies). Fetch a full instruction body with ``GetWorkInstruction``;
    fetch a whole playbook (descriptions + every instruction body)
    with ``GetPlaybook``.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    track: str = "mentoring"
    count: int
    playbooks: list[PlaybookSummary]


class PlaybookEntry(BaseModel):
    """One playbook entry embedded in a ``ListMentors`` mentor summary.

    Carries the playbook's identity + work-instruction *summaries*
    (id/title/summary). Full instruction bodies are intentionally
    omitted to keep the envelope small enough for transports with
    response-size limits — fetch the body for a specific instruction
    via ``GetWorkInstruction``, or the whole playbook via
    ``GetPlaybook``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    instructions: list[WorkInstructionSummary] = Field(default_factory=list)


class MentorSummary(BaseModel):
    """One mentor in a ``ListMentors`` result list.

    ``backend_kind`` is the user-facing kind name from ``mentor.json``
    (``"anthropic" | "openclaw" | "stub"``) — what the operator wrote, and
    what the docs reference. ``backend_live`` indicates whether the
    runtime can actually reach the synthesis path (an Anthropic backend
    without an API key still reports kind ``"anthropic"`` but is not
    live). ``playbooks`` embeds each playbook and its work-instruction
    summaries (id + title + one-line summary). Full bodies are fetched
    on demand via ``GetPlaybook`` / ``GetWorkInstruction`` so this
    envelope stays small (under most MCP-transport response-size limits).

    ``human_mentor`` names the human who stands behind the agentic
    mentor — surfaced so escalation paths are explicit (AMMP §3.4).
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str | None = None
    profile_url: str | None = Field(
        default=None,
        description="Optional URL of a longer mentee-facing profile page. Mirrors `Mentor.profile_url` so MCP clients can render a 'View full profile' link without reading the capability JSON.",
    )
    avatar_url: str | None = Field(
        default=None,
        description="Public URL of the mentor's avatar image, or null when no `avatar.*` file is present in the mentor directory.",
    )
    human_mentor: HumanMentorSummary | None = None
    playbook_count: int
    instruction_count: int = 0
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    backend_kind: str
    backend_live: bool
    is_default: bool = False
    playbooks: list[PlaybookEntry] = Field(default_factory=list)


class ListMentorsResponse(BaseModel):
    """Response envelope for the ``ListMentors`` AMMP-extension operation.

    Server-side extension beyond the AMMP-01 draft's five Mentoring-track
    operations: lets a mentee enumerate the mentors this server hosts so
    it can pick a slug for the other five ops without first reading the
    capability JSON over an out-of-band HTTP GET.
    """

    model_config = ConfigDict(extra="forbid")

    track: str = "mentoring"
    count: int
    default_mentor: str
    mentors: list[MentorSummary]


class GetPlaybookResponse(BaseModel):
    """Response envelope for the ``GetPlaybook`` AMMP operation.

    Returns a playbook's identity plus the full body of every work
    instruction in it — one round-trip to load everything the mentee
    needs about an area of practice.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    id: str
    name: str
    description: str = ""
    instructions: list[WorkInstructionEntry]


class GetWorkInstructionResponse(BaseModel):
    """Response envelope for the ``GetWorkInstruction`` AMMP-extension operation.

    Server-side extension over AMMP-01's five operations — lets a
    mentee fetch one specific work instruction by ``(playbook_id, id)``
    when it already knows which one it wants, without round-tripping
    the entire playbook.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    playbook_id: str
    id: str
    title: str
    summary: str = ""
    body: str


class SearchMatch(BaseModel):
    """One match in a ``SearchPlaybooks`` result list.

    Searches always run at work-instruction granularity, so each match
    carries both its ``playbook_id`` (area of practice) and its own
    ``id`` (the instruction's filename stem).
    """

    model_config = ConfigDict(extra="forbid")

    playbook_id: str
    id: str
    title: str
    rank: int
    snippet: str


class SearchPlaybooksResponse(BaseModel):
    """Response envelope for the ``SearchPlaybooks`` AMMP operation.

    Despite the name (kept stable with AMMP §5.3), the search runs at
    work-instruction granularity — matches name the instruction that
    contained the hit plus its parent playbook id.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    query: str
    count: int
    matches: list[SearchMatch]


class EscalationToHumanMentorDraft(BaseModel):
    """Draft of an escalation to A.h that B.a can show its operator B.h.

    Surfaced on ``AskMentor`` responses when the mentor's confidence
    falls below threshold. B.a is expected to present this draft to
    its operator B.h for review/edit/approval before invoking
    ``EscalateToHumanMentor`` on A.a. The draft summarises *only the
    professional question*; private context the mentee may have
    received from B.h does not appear here.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        description="The professional question X drafted by A.a — fit to forward to A.h once B.h has approved (or edited)."
    )
    human_mentor: HumanMentorSummary = Field(
        description="The human standing behind A.a, so B.h knows who the question will reach."
    )
    suggested_message_to_your_operator: str = Field(
        description="First-person phrasing B.a can quote verbatim to B.h to request approval for the forward."
    )


class AskMentorResponse(BaseModel):
    """Response envelope for the ``AskMentor`` AMMP operation.

    When confidence falls below the mentor's threshold,
    ``escalation_recommended`` is set and the response carries:

    * ``suggested_message_to_your_operator`` — first-person phrasing
      to surface to B.h.
    * ``escalation_to_human_mentor_draft`` — when A.a is configured
      with a ``human_mentor``, the draft B.a can ask B.h to approve
      for forwarding to A.h via ``EscalateToHumanMentor``.

    ``relevant_instructions`` cites the work instructions the mentor's
    keyword ranker considered most relevant to the question — both for
    auditability and so the mentee can fetch the full bodies if needed.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    question: str
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    relevant_instructions: list[WorkInstructionSummary] = Field(default_factory=list)
    escalation_recommended: bool = False
    suggested_message_to_your_operator: str | None = None
    escalation_to_human_mentor_draft: EscalationToHumanMentorDraft | None = None


class EscalateToHumanResponse(BaseModel):
    """Response envelope for the ``EscalateToHuman`` AMMP operation."""

    model_config = ConfigDict(extra="forbid")

    mentor: str
    guidance: str
    suggested_message_to_your_operator: str
    invariant: str = "Human-Gated Escalation (AMMP §3.4)"


class EscalateToHumanMentorResponse(BaseModel):
    """Response envelope for the ``EscalateToHumanMentor`` AMMP-extension.

    Server-side extension over AMMP-01: relays a B.h-approved question
    from B.a to A.h (the human behind A.a). The handler delivers the
    question, then waits up to ``wait_seconds`` for A.h's reply Z.

    Two shapes:

    * **answered:** ``status == "answered"``, ``answer`` populated,
      ``answered_at`` set. The mentee can use the answer directly.
    * **pending:** ``status == "pending"``, ``answer`` is null. A.h
      hasn't replied within ``wait_seconds``. The mentee should call
      ``GetEscalation(escalation_id)`` later (e.g. when the user
      pings, or after a polite delay) to retrieve the answer.

    The pending path is the production default — MCP clients vary in
    how long they hold a single tool call open (Claude Desktop's
    per-tool timeout is ~60 s and is not always reset by progress
    notifications), so we don't bet on a long sync wait.

    Per AMMP §3.4, this cross-compartment forward is only legitimate
    when B.h has explicitly approved it. The server records the
    escalation id in the hash-only audit log and stores full
    pending-state under ``<AMMP_DIR>/escalations.jsonl``.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    escalation_id: str
    status: str = Field(
        description="`answered` when A.h has replied within wait_seconds; `pending` when the call returned before the reply landed (mentee should call GetEscalation later).",
    )
    answer: str | None = None
    answered_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp at which A.h's reply landed back on the server (null while status=pending).",
    )
    suggested_message_to_your_operator: str | None = Field(
        default=None,
        description="When status=pending, first-person phrasing the mentee can use to tell B.h that the question is in flight and that GetEscalation will be polled.",
    )
    invariant: str = "Mentor-Mediated Escalation (B.h approved)"


class GetEscalationResponse(BaseModel):
    """Response envelope for ``GetEscalation`` — retrieve a pending answer.

    Mirrors :class:`EscalateToHumanMentorResponse` so a mentee can use
    one of the two shapes interchangeably depending on whether the
    answer was synchronous or polled.

    ``status`` distinguishes the lifecycle states:

    * ``pending``: delivered but A.h hasn't replied; poll again later.
    * ``answered``: A.h replied; ``answer`` is set.
    * ``cancelled``: B.a (or its operator) abandoned the call before A.h replied.
    * ``expired``: ``escalation_default_timeout_seconds`` elapsed (default 24 h) without a reply.
    * ``unknown``: no escalation with that id (typo, or it was never created).
    """

    model_config = ConfigDict(extra="forbid")

    escalation_id: str
    status: str
    mentor: str | None = None
    question: str | None = None
    answer: str | None = None
    answered_at: str | None = None
    cancel_reason: str | None = None


class ErrorResponse(BaseModel):
    """Graceful in-band error envelope.

    Returned as a tool result (not an HTTP error) for cases like
    invalid mentor slug, missing playbook id, auth failure, etc.
    """

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = None
