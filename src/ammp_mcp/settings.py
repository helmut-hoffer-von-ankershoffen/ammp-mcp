"""Pydantic-settings configuration loaded from env + .env file.

All env vars are prefixed `AMMP_` so they don't collide with other
applications running on the same host. Example: `AMMP_HOST=0.0.0.0`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TransportKind = Literal["http", "stdio"]


class Settings(BaseSettings):
    """Server configuration — loaded once at boot, immutable thereafter."""

    model_config = SettingsConfigDict(
        env_prefix="AMMP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ─── Network ──────────────────────────────────────────────────
    transport: TransportKind = Field(
        default="http",
        description=(
            "Wire transport for the MCP server. `http` (default) — Streamable-HTTP "
            "with `/mcp/` endpoint, suitable for shared deployments behind a tunnel. "
            "`stdio` — speak MCP JSON-RPC over stdin/stdout, for subprocess "
            "integration with Claude Desktop / Claude Code, where the parent "
            "process is the trust boundary. Stdio mode ignores `host` / `port`."
        ),
    )
    host: str = Field(default="127.0.0.1", description="Bind address")
    port: int = Field(default=8765, description="Bind port")
    public_url: str = Field(
        default="http://127.0.0.1:8765",
        description=(
            "URL the server advertises in /.well-known/agent.json. In production set to https://ammp.helmguild.com."
        ),
    )

    # ─── Mentor registry ──────────────────────────────────────────
    mentors_root: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent / "mentors",
        description="Root directory holding one subdirectory per mentor (slug = dirname).",
    )
    default_mentor: str = Field(
        default="pepe", description="Mentor slug used when a mentee omits the `mentor` argument."
    )

    # ─── Mentee allowlist ─────────────────────────────────────────
    mentees_file: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent / "mentees.json",
        description="JSON file containing the allowlist of mentees + API keys.",
    )
    require_auth: bool = Field(
        default=False,
        description=(
            "When True, every MCP request must carry a Bearer API key matching "
            "an entry in mentees.json. False is fine for localhost dev; flip on "
            "for the public deployment at ammp.helmguild.com."
        ),
    )

    # ─── LLM (AskMentor) ──────────────────────────────────────────
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API key for AskMentor.")
    llm_model: str = Field(default="claude-opus-4-7", description="Anthropic model id for AskMentor calls.")
    llm_max_concurrent: int = Field(
        default=10,
        ge=1,
        le=100,
        description=(
            "Bounded concurrency for AskMentor LLM calls. Excess requests "
            "queue at the asyncio level (FIFO, fast drain). Tune up if you "
            "have higher Anthropic rate limits, down if you want a tighter "
            "cost ceiling."
        ),
    )
    llm_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    llm_confidence_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "Below this self-reported confidence, AskMentor adds a "
            "mentor-triggered EscalateToHuman recommendation to the response."
        ),
    )

    # ─── Audit ────────────────────────────────────────────────────
    audit_log_path: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent / "audit.log",
        description="Hash-only audit log location (AMMP §6.2).",
    )


_singleton: Settings | None = None


def get_settings() -> Settings:
    """Return the cached Settings singleton; load on first call."""
    global _singleton
    if _singleton is None:
        _singleton = Settings()
    return _singleton


def reset_settings_for_testing() -> None:
    """Test-only — drop the cache so a fresh Settings is read on next call."""
    global _singleton
    _singleton = None
