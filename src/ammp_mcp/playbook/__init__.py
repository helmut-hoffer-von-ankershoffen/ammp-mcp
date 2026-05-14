"""Playbook module — corpus loader, id validation, and search helpers."""

from __future__ import annotations

from ._service import (
    Playbook,
    Skill,
    build_plugin_archive_response,
    enumerate_plugin_refs,
    flatten_skills,
    keyword_rank,
    load_playbooks,
    safe_id,
    search,
)

__all__ = [
    "Playbook",
    "Skill",
    "build_plugin_archive_response",
    "enumerate_plugin_refs",
    "flatten_skills",
    "keyword_rank",
    "load_playbooks",
    "safe_id",
    "search",
]
