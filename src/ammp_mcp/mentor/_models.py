"""Mentor pydantic model + backend-config discriminated union.

``BackendConfig`` lives next to ``Mentor`` because it's a property of
a mentor (each mentor selects its answer engine via ``mentor.json``).
The backend *implementations* live under :mod:`ammp_mcp.backends`; this
module only defines the static configuration shape.
"""

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


# ─── Mentor ───────────────────────────────────────────────────────────────


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
