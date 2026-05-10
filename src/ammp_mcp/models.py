"""Pydantic models for the protocol surface — Mentor, Mentee, Playbook, plus
the structured response envelopes returned by each AMMP operation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Backend configuration (discriminated union) ──────────────────────────


class AnthropicBackendConfig(BaseModel):
    """Backend that calls the Anthropic Messages API directly.

    Stateless. Persona + playbooks are pasted into the system prompt
    on every call. Cheap and predictable.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["anthropic"] = "anthropic"
    model: str | None = Field(default=None, description="Override the global LLM model for this mentor.")
    max_concurrent: int | None = Field(default=None, ge=1, le=100)
    timeout_seconds: float | None = Field(default=None, ge=1.0, le=300.0)


class OpenClawBackendConfig(BaseModel):
    """Backend that POSTs the question to a live agent runtime.

    The configured URL receives the question + persona + playbooks,
    forwards into a live session (e.g. Pepe-on-OpenClaw), and returns
    the answer + confidence. See ``backends/openclaw.py`` for the
    wire contract.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["openclaw"] = "openclaw"
    url: str = Field(min_length=1, description="HTTP endpoint that hosts the mentor.")
    auth_bearer_env: str | None = Field(
        default=None,
        description=(
            "Name of the environment variable holding the Bearer token sent with each "
            "request. Read lazily so a token rotation doesn't require restarting the server."
        ),
    )
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    max_concurrent: int = Field(default=10, ge=1, le=100)


class StubBackendConfig(BaseModel):
    """Deterministic no-network backend. For tests + offline dev."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["stub"] = "stub"


BackendConfig = Annotated[
    AnthropicBackendConfig | OpenClawBackendConfig | StubBackendConfig,
    Field(discriminator="kind"),
]


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
    backend: BackendConfig | None = Field(
        default=None,
        description=(
            "Pluggable answer engine. When omitted, falls back to a global Anthropic backend "
            "configured from settings (legacy v0.2 behaviour)."
        ),
    )

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
