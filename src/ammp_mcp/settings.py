"""Pydantic-settings configuration loaded from env + .env file.

All env vars are prefixed `AMMP_` so they don't collide with other
applications running on the same host. Example: `AMMP_HOST=0.0.0.0`.

The runtime tree lives under a single directory — `~/.ammp/` by default,
overridable with `AMMP_DIR` — and contains:

    <AMMP_DIR>/config.env      — `.env`-style settings file (auto-loaded)
    <AMMP_DIR>/mentors/        — one subdirectory per mentor
    <AMMP_DIR>/mentees.json    — Bearer-key allowlist (SHA-256 hashes only)
    <AMMP_DIR>/audit.log       — hash-only audit log (AMMP §6.2)

Each of those locations is individually overridable (`AMMP_MENTORS_ROOT`,
`AMMP_MENTEES_FILE`, `AMMP_AUDIT_LOG_PATH`) for installs that want to
point a single piece at, say, an Obsidian vault.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TransportKind = Literal["http", "stdio"]


def _resolve_ammp_dir() -> Path:
    """The single directory holding config, mentor data, and the audit log.

    Reads ``AMMP_DIR`` directly from the process environment so the
    derived defaults below can compose. Falls back to ``~/.ammp``. Each
    leaf path can still be overridden by its own ``AMMP_*`` env var.
    """
    override = os.environ.get("AMMP_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".ammp"


def _default_config_env_path() -> Path:
    return _resolve_ammp_dir() / "config.env"


def _default_mentors_root() -> Path:
    return _resolve_ammp_dir() / "mentors"


def _default_mentees_file() -> Path:
    return _resolve_ammp_dir() / "mentees.json"


def _default_audit_log_path() -> Path:
    return _resolve_ammp_dir() / "audit.log"


class Settings(BaseSettings):
    """Server configuration — loaded once at boot, immutable thereafter."""

    model_config = SettingsConfigDict(
        env_prefix="AMMP_",
        # Loaded in order. The first file that exists wins for each key;
        # later sources can still add keys the earlier ones didn't set.
        # `config.env` under `~/.ammp/` is the canonical location; `.env`
        # in cwd is the dev-clone fallback.
        env_file=(str(_default_config_env_path()), ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ─── AMMP runtime directory ──────────────────────────────────
    ammp_dir: Path = Field(
        default_factory=_resolve_ammp_dir,
        description=(
            "Single directory holding config (`config.env`), mentor data "
            "(`mentors/`), the mentee allowlist (`mentees.json`), and the "
            "audit log (`audit.log`). Defaults to `~/.ammp/`. Each leaf "
            "location is individually overridable."
        ),
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
        default_factory=_default_mentors_root,
        description="Root directory holding one subdirectory per mentor (slug = dirname). Defaults to `<AMMP_DIR>/mentors`.",
    )
    default_mentor: str = Field(
        default="example", description="Mentor slug used when a mentee omits the `mentor` argument."
    )

    # ─── Mentee allowlist ─────────────────────────────────────────
    mentees_file: Path = Field(
        default_factory=_default_mentees_file,
        description="JSON file containing the allowlist of mentees + API keys. Defaults to `<AMMP_DIR>/mentees.json`.",
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
        default_factory=_default_audit_log_path,
        description="Hash-only audit log location (AMMP §6.2). Defaults to `<AMMP_DIR>/audit.log`.",
    )

    @model_validator(mode="after")
    def _rebase_paths_when_ammp_dir_overridden(self) -> Settings:
        """If the caller overrode `ammp_dir` directly, propagate to the leaves.

        The default_factory functions read ``AMMP_DIR`` from the environment
        at module-import time, so they DO compose with ``AMMP_DIR=...`` env
        overrides. But callers constructing ``Settings(ammp_dir=...)`` in
        tests would otherwise see the leaf defaults pinned to the global
        ``~/.ammp/`` — this validator catches that case and rebases.
        """
        explicit_ammp_dir = self.ammp_dir.resolve()
        env_default = _resolve_ammp_dir()
        if explicit_ammp_dir == env_default:
            return self  # nothing to rebase
        # If a leaf path was explicitly set, leave it; only rebase the
        # ones still pointing at the env-default tree.
        if str(self.mentors_root).startswith(str(env_default)):
            object.__setattr__(self, "mentors_root", explicit_ammp_dir / "mentors")
        if str(self.mentees_file).startswith(str(env_default)):
            object.__setattr__(self, "mentees_file", explicit_ammp_dir / "mentees.json")
        if str(self.audit_log_path).startswith(str(env_default)):
            object.__setattr__(self, "audit_log_path", explicit_ammp_dir / "audit.log")
        return self


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
