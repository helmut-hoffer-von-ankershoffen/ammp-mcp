"""Factory: build a :class:`MentorBackend` from a mentor's config.

Used at server boot. Each mentor's ``backend`` block (or absence
thereof) maps to one concrete backend instance. The factory is the
*only* place the discriminated union is unpacked, so adding a fourth
backend later is a one-file change.
"""

from __future__ import annotations

import logging

from ..models import (
    AnthropicBackendConfig,
    BackendConfig,
    OpenClawBackendConfig,
    StubBackendConfig,
)
from ..settings import Settings
from .anthropic import AnthropicBackend
from .base import MentorBackend
from .openclaw import OpenClawBackend
from .stub import StubBackend

logger = logging.getLogger(__name__)


def build_backend(config: BackendConfig | None, settings: Settings) -> MentorBackend:
    """Resolve a :class:`MentorBackend` from a config block + global settings.

    When ``config`` is ``None`` (legacy mentor.json without a ``backend``
    block), build an :class:`AnthropicBackend` wired from global settings.
    This preserves v0.2 behaviour for existing mentor definitions.
    """
    if config is None:
        return AnthropicBackend(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
            max_concurrent=settings.llm_max_concurrent,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    if isinstance(config, AnthropicBackendConfig):
        return AnthropicBackend(
            api_key=settings.anthropic_api_key,
            model=config.model or settings.llm_model,
            max_concurrent=config.max_concurrent or settings.llm_max_concurrent,
            timeout_seconds=config.timeout_seconds or settings.llm_timeout_seconds,
        )

    if isinstance(config, OpenClawBackendConfig):
        return OpenClawBackend(
            url=config.url,
            auth_bearer_env=config.auth_bearer_env,
            timeout_seconds=config.timeout_seconds,
            max_concurrent=config.max_concurrent,
        )

    if isinstance(config, StubBackendConfig):
        return StubBackend()

    # Defensive — pydantic's discriminated union should make this unreachable.
    raise ValueError(f"Unknown backend kind: {config!r}")
