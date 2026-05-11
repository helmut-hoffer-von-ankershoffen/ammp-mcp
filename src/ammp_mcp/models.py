"""Response envelopes for the AMMP Mentoring-track operations.

The static config classes (``Mentor``, ``Mentee``, the ``BackendConfig``
discriminated union) live in :mod:`ammp_mcp.mentor` and
:mod:`ammp_mcp.mentee`. This module is intentionally narrow: only the
envelopes each server tool returns.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlaybookSummary(BaseModel):
    """Summary of one playbook for the response envelopes."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str = ""


class ListPlaybooksResponse(BaseModel):
    """Response envelope for the ``ListPlaybooks`` AMMP operation."""

    model_config = ConfigDict(extra="forbid")

    mentor: str
    track: str = "mentoring"
    count: int
    playbooks: list[PlaybookSummary]


class PlaybookEntry(BaseModel):
    """One playbook entry embedded in a ``ListMentors`` mentor summary.

    Includes the full body so a mentee can take a single ``ListMentors``
    call and have everything it needs to ground itself — no follow-up
    ``GetPlaybook`` round-trip required. Mentees that only need a brief
    overview should still prefer ``ListPlaybooks(mentor)``, which omits
    bodies.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str = ""
    body: str


class MentorSummary(BaseModel):
    """One mentor in a ``ListMentors`` result list.

    ``backend_kind`` is the user-facing kind name from ``mentor.json``
    (``"anthropic" | "openclaw" | "stub"``) — what the operator wrote, and
    what the docs reference. ``backend_live`` indicates whether the
    runtime can actually reach the synthesis path (an Anthropic backend
    without an API key still reports kind ``"anthropic"`` but is not
    live). ``playbooks`` embeds each playbook's id, title, summary, and
    full body so a single ``ListMentors`` call gives the mentee a
    complete picture of what every mentor on this server offers.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str | None = None
    avatar_url: str | None = Field(
        default=None,
        description="Public URL of the mentor's avatar image, or null when no `avatar.*` file is present in the mentor directory.",
    )
    playbook_count: int
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
    """Response envelope for the ``GetPlaybook`` AMMP operation."""

    model_config = ConfigDict(extra="forbid")

    mentor: str
    id: str
    title: str
    body: str


class SearchMatch(BaseModel):
    """One match in a ``SearchPlaybooks`` result list."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    rank: int
    snippet: str


class SearchPlaybooksResponse(BaseModel):
    """Response envelope for the ``SearchPlaybooks`` AMMP operation."""

    model_config = ConfigDict(extra="forbid")

    mentor: str
    query: str
    count: int
    matches: list[SearchMatch]


class AskMentorResponse(BaseModel):
    """Response envelope for the ``AskMentor`` AMMP operation.

    When confidence falls below the mentor's threshold,
    ``escalation_recommended`` is set and
    ``suggested_message_to_your_operator`` is populated — the mentor
    proactively offers an escalation path even without an explicit
    ``EscalateToHuman`` call.
    """

    model_config = ConfigDict(extra="forbid")

    mentor: str
    question: str
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    relevant_playbooks: list[PlaybookSummary] = Field(default_factory=list)
    escalation_recommended: bool = False
    suggested_message_to_your_operator: str | None = None


class EscalateToHumanResponse(BaseModel):
    """Response envelope for the ``EscalateToHuman`` AMMP operation."""

    model_config = ConfigDict(extra="forbid")

    mentor: str
    guidance: str
    suggested_message_to_your_operator: str
    invariant: str = "Human-Gated Escalation (AMMP §3.4)"


class ErrorResponse(BaseModel):
    """Graceful in-band error envelope.

    Returned as a tool result (not an HTTP error) for cases like
    invalid mentor slug, missing playbook id, auth failure, etc.
    """

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = None
