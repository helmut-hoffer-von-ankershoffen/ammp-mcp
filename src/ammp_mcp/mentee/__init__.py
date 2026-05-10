"""Mentee module — pydantic model, allowlist registry, and API-key helpers."""

from __future__ import annotations

from ._models import Mentee
from ._service import (
    find_mentee_by_api_key,
    hash_api_key,
    load_mentees,
    save_mentees,
)

__all__ = [
    "Mentee",
    "find_mentee_by_api_key",
    "hash_api_key",
    "load_mentees",
    "save_mentees",
]
