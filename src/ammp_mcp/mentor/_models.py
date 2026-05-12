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


# ─── Human mentor (who's behind the agentic mentor) ───────────────────────


class HumanMentor(BaseModel):
    """The human standing behind an agentic mentor.

    Surfaced so mentees (and the people running them) know where an
    escalation ultimately lands when the agentic mentor itself can't
    answer with confidence, or when the mentee explicitly wants to
    reach a human. The agentic mentor never pages the human directly
    — escalation flows through the mentee's own operator per AMMP §3.4
    — but published attribution closes the loop on *who* is upstream.

    Attributes:
        name: Display name, e.g. ``"Helmut Hoffer von Ankershoffen"``.
        url: Optional public bio / personal site, e.g.
            ``"https://helmut.hoffer-von-ankershoffen.me/"``.
        profile_url: Optional URL of the human's longer profile page on
            the brand site, e.g. ``"https://www.helmguild.com/helmut-hoffer-von-ankershoffen/"``.
            When set, the landing wraps the human's name in a link to
            this URL (in preference to ``url``); when the URL points at
            ``https://www.helmguild.com/<path>/``, the DE landing
            automatically swaps in the ``/de/`` variant.
        contact: Optional free-form contact channel description,
            e.g. ``"helmuthva@gmail.com"`` or
            ``"Telegram: @helmuthva"``. The mentor never publishes
            keys or credentials here — just how to reach the human.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=400)
    profile_url: str | None = Field(default=None, max_length=400)
    contact: str | None = Field(default=None, max_length=200)


# ─── Mentor ───────────────────────────────────────────────────────────────


class Mentor(BaseModel):
    """A registered mentor. Loaded from `<mentors_root>/<slug>/mentor.json`."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    name: str
    description: str | None = Field(
        default=None,
        max_length=400,
        description="One-line mentee-facing introduction. Surfaced on the landing page and in ListMentors responses. Distinct from `persona`, which is the LLM system prompt.",
    )
    profile_url: str | None = Field(
        default=None,
        max_length=400,
        description="Optional URL of a longer mentee-facing profile (e.g. the mentor's page on the brand site). When set, the landing wraps the mentor name in a link to this URL. When the URL points at `https://www.helmguild.com/<path>/`, the DE landing automatically swaps in the `/de/` variant.",
    )
    human_mentor: HumanMentor | None = Field(
        default=None,
        description="The human behind the agentic mentor. Published so mentees know where escalation ultimately lands.",
    )
    persona: str = Field(
        description="System prompt used when this mentor answers AskMentor calls. Voice and stance only — never operational secrets."
    )
    mentor_dir: Path = Field(
        description="Directory containing this mentor's `mentor.json`, playbook corpus, and optional `avatar.*`."
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

    @field_validator("playbook_dir", "mentor_dir")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        """Expand ``~`` and resolve a directory field to an absolute path.

        Args:
            v: The raw path value as supplied to the model.

        Returns:
            The same path with user-home expansion and symlink resolution
            applied so downstream code can rely on a normalised absolute
            ``Path`` regardless of how the caller wrote it.
        """
        return v.expanduser().resolve()

    def avatar_path(self) -> Path | None:
        """Locate this mentor's avatar image, if one exists.

        Looks for ``avatar.{png,jpg,jpeg,webp,svg,gif}`` directly under
        ``mentor_dir``. Convention over configuration — operators drop a
        file next to ``mentor.json`` and the server picks it up on the
        next request without restart.

        Returns:
            Path to the first matching avatar file, or ``None`` when the
            mentor has not provided one.
        """
        for ext in ("png", "jpg", "jpeg", "webp", "svg", "gif"):
            candidate = self.mentor_dir / f"avatar.{ext}"
            if candidate.is_file():
                return candidate
        return None
