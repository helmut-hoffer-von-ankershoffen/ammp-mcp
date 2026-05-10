"""Playbook module — corpus loader, id validation, and search helpers."""

from __future__ import annotations

from ._service import (
    Playbook,
    keyword_rank,
    load_corpus,
    safe_id,
    search,
)

__all__ = [
    "Playbook",
    "keyword_rank",
    "load_corpus",
    "safe_id",
    "search",
]
