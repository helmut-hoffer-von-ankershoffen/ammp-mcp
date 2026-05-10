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
