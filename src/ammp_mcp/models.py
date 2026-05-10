"""Pydantic models for the protocol surface — Mentor, Mentee, Playbook, plus
the structured response envelopes returned by each AMMP operation."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Static configuration objects ─────────────────────────────────────────


class Mentor(BaseModel):
    """A registered mentor. Loaded from `<mentors_root>/<slug>/mentor.json`."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    name: str
    persona: str = Field(
        description="System prompt used when this mentor answers AskMentor calls. Voice and stance only — never operational secrets."
    )
    playbook_dir: Path
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    @field_validator("playbook_dir")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        return v.expanduser().resolve()


class Mentee(BaseModel):
    """A registered mentee allowed to call the server. Identifier is the slug;
    api_key_hash is checked when require_auth is True. The api_key (plaintext)
    is never stored — the server hashes incoming keys at request time and
    compares hashes."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    operator: str = Field(description="Who runs this mentee, e.g. 'human:helmut' or 'human:sandra'.")
    runtime: str = Field(description="What the mentee runs on, e.g. 'claude-cowork', 'claude-ai', 'claude-code'.")
    api_key_hash: str = Field(description="Hex sha256 of the API key. Plaintext key is never stored.")
    rate_limit_per_minute: int = Field(default=60, ge=1, le=1000)


# ─── Operation response envelopes ─────────────────────────────────────────


class PlaybookSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str = ""


class ListPlaybooksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentor: str
    track: str = "mentoring"
    count: int
    playbooks: list[PlaybookSummary]


class GetPlaybookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentor: str
    id: str
    title: str
    body: str


class SearchMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    rank: int
    snippet: str


class SearchPlaybooksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentor: str
    query: str
    count: int
    matches: list[SearchMatch]


class AskMentorResponse(BaseModel):
    """The response from AskMentor. When confidence falls below the mentor's
    threshold, `escalation_recommended` is set and `suggested_message_to_your_operator`
    is populated — the mentor proactively offers an escalation path even
    without an explicit EscalateToHuman call."""

    model_config = ConfigDict(extra="forbid")

    mentor: str
    question: str
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    relevant_playbooks: list[PlaybookSummary] = Field(default_factory=list)
    escalation_recommended: bool = False
    suggested_message_to_your_operator: str | None = None


class EscalateToHumanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentor: str
    guidance: str
    suggested_message_to_your_operator: str
    invariant: str = "Human-Gated Escalation (AMMP §3.4)"


class ErrorResponse(BaseModel):
    """Returned (as a tool result, not an HTTP error) for graceful in-band
    error reporting — invalid mentor slug, missing playbook id, etc."""

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = None
