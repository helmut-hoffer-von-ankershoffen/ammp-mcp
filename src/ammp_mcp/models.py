"""Response envelopes for the AMMP Mentoring-track operations.

The static config classes (``Mentor``, ``Mentee``, the ``BackendConfig``
discriminated union) live in :mod:`ammp_mcp.mentor` and
:mod:`ammp_mcp.mentee`. This module is intentionally narrow: only the
envelopes each server tool returns.

The corpus is a two-level hierarchy: a mentor has zero or more
**playbooks** (areas of practice); each playbook contains zero or more
**skills** (individual craft rules, one [AgentSkills](https://agentskills.io)
SKILL.md file each). The envelopes here mirror that shape.
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


class SkillSummary(BaseModel):
    """Compact identity of one skill.

    No body — used by ``ListPlaybooks`` (folded into each playbook) and
    by ``SearchPlaybooks`` (snippet replaces body). Mentees fetch the
    full body with ``GetSkill``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str = ""


class SkillEntry(BaseModel):
    """One skill with its full body — for ``GetPlaybook`` etc."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str = ""
    body: str


class PlaybookSummary(BaseModel):
    """Summary of one playbook (area of practice). No skill bodies."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    skill_count: int = 0
    skills: list[SkillSummary] = Field(default_factory=list)


class ListPlaybooksResponse(BaseModel):
    """Response envelope for the ``ListPlaybooks`` AMMP operation.

    Returns the mentor's playbooks (areas of practice) — each carries
    its name + description + skill summaries (title only, no bodies).
    Fetch a full skill body with ``GetSkill``; fetch a whole playbook
    (descriptions + every skill body) with ``GetPlaybook``.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    track: str = "mentoring"
    count: int
    playbooks: list[PlaybookSummary]


class PlaybookEntry(BaseModel):
    """One playbook entry embedded in a ``ListMentors`` mentor summary.

    Carries the playbook's identity + skill *summaries*
    (id/title/summary). Full skill bodies are intentionally omitted
    to keep the envelope small enough for transports with
    response-size limits — fetch the body for a specific skill via
    ``GetSkill``, or the whole playbook via ``GetPlaybook``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    skills: list[SkillSummary] = Field(default_factory=list)


class MentorSummary(BaseModel):
    """One mentor in a ``ListMentors`` result list.

    ``backend_kind`` is the user-facing kind name from ``mentor.json``
    (``"anthropic" | "openclaw" | "stub"``) — what the operator wrote, and
    what the docs reference. ``backend_live`` indicates whether the
    runtime can actually reach the synthesis path (an Anthropic backend
    without an API key still reports kind ``"anthropic"`` but is not
    live). ``playbooks`` embeds each playbook and its skill summaries
    (id + title + one-line summary). Full bodies are fetched on demand
    via ``GetPlaybook`` / ``GetSkill`` so this envelope stays small
    (under most MCP-transport response-size limits).

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
    skill_count: int = 0
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

    Returns a playbook's identity plus the full body of every skill
    in it — one round-trip to load everything the mentee needs about
    an area of practice.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    id: str
    name: str
    description: str = ""
    skills: list[SkillEntry]


class GetSkillResponse(BaseModel):
    """Response envelope for the ``GetSkill`` AMMP-extension operation.

    Server-side extension over AMMP-01's five operations — lets a
    mentee fetch one specific skill by ``(playbook_id, id)`` when it
    already knows which one it wants, without round-tripping the
    entire playbook.
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

    Searches always run at skill granularity, so each match carries
    both its ``playbook_id`` (area of practice) and its own ``id``
    (the skill's folder name or filename stem).
    """

    model_config = ConfigDict(extra="forbid")

    playbook_id: str
    id: str
    title: str
    rank: int
    snippet: str


class SearchPlaybooksResponse(BaseModel):
    """Response envelope for the ``SearchPlaybooks`` AMMP operation.

    The search runs at skill granularity — each match names the skill
    that contained the hit plus its parent playbook id.
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

    ``relevant_skills`` cites the skills the mentor's keyword ranker
    considered most relevant to the question — both for auditability
    and so the mentee can fetch the full bodies if needed.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    question: str
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    relevant_skills: list[SkillSummary] = Field(default_factory=list)
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


class GetSystemInfoResponse(BaseModel):
    """Response envelope for the ``GetSystemInfo`` AMMP-extension operation.

    Server-side extension over AMMP-01 — surfaces a small, safe slice
    of build / release / runtime metadata so an MCP client can confirm
    *which* server it just talked to during end-to-end debugging.

    Everything here is already publicly observable (the version is in
    the repo, the AMMP draft id is in the capability JSON, the
    mentor / mentee counts are in ``ListMentors``). The envelope just
    bundles them so a single tool call answers "what am I connected
    to?" without an out-of-band fetch.

    Deliberately omitted (privacy / security):

    * any file path (``AMMP_DIR``, ``mentors_root``, audit log path)
    * environment-variable values, Bearer tokens, bot tokens
    * hostnames, IP addresses, or internal network details
    * the OS user, hostname, or process PID
    * stack traces or recent error counts

    The fields here MUST stay safe to publish over an unauthenticated
    surface — though in practice ``GetSystemInfo`` is auth-gated like
    every other AMMP tool when ``require_auth`` is enabled.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Software identifier, e.g. `ammp-mcp`.")
    version: str = Field(description="Software version, e.g. `0.7.0` — matches the GitHub release tag.")
    ammp_draft: str = Field(
        description="AMMP IETF Internet-Draft revision this server claims compliance with, e.g. `draft-ammp-01`."
    )
    python_version: str = Field(
        description="Runtime Python version, e.g. `3.13.13` — useful when debugging client / server compat."
    )
    platform: str = Field(description="OS platform identifier (`sys.platform`), e.g. `darwin` or `linux`.")
    started_at: str = Field(description="ISO 8601 UTC timestamp at which the server context was built (boot time).")
    uptime_seconds: float = Field(
        ge=0.0, description="Whole-second uptime since `started_at`. Recomputed on each call."
    )
    mentor_count: int = Field(ge=0, description="Number of mentors loaded into this server.")
    mentee_count: int = Field(ge=0, description="Number of mentees in the allowlist.")
    default_mentor: str = Field(
        description="Slug of the configured default mentor — the one calls fall through to when `mentor` is empty."
    )
    escalation_adapter: str = Field(
        description="Active escalation delivery adapter kind: `log`, `telegram`, or future adapter names."
    )
    mount_path: str = Field(
        description="HTTP mount prefix this server lives under, e.g. `/ammp`. Empty string when mounted at root."
    )
    public_url: str = Field(description="Public URL the server advertises itself at (from `AMMP_PUBLIC_URL`).")


class ErrorResponse(BaseModel):
    """Graceful in-band error envelope.

    Returned as a tool result (not an HTTP error) for cases like
    invalid mentor slug, missing playbook id, auth failure, etc.
    """

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = None
