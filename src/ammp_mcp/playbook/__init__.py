"""Playbook module — corpus loader, id validation, and search helpers."""

from __future__ import annotations

from ._service import (
    Playbook,
    WorkInstruction,
    flatten_instructions,
    keyword_rank,
    load_playbooks,
    safe_id,
    search,
)

__all__ = [
    "Playbook",
    "WorkInstruction",
    "flatten_instructions",
    "keyword_rank",
    "load_playbooks",
    "safe_id",
    "search",
]
