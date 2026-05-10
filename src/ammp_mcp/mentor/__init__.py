"""Mentor module — pydantic model, backend-config union, and registry loader."""

from __future__ import annotations

from ._models import (
    AnthropicBackendConfig,
    BackendConfig,
    Mentor,
    OpenClawBackendConfig,
    StubBackendConfig,
)
from ._service import get_mentor, load_mentors

__all__ = [
    "AnthropicBackendConfig",
    "BackendConfig",
    "Mentor",
    "OpenClawBackendConfig",
    "StubBackendConfig",
    "get_mentor",
    "load_mentors",
]
