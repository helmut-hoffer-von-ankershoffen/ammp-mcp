"""Playbook module — corpus loader, id validation, and search helpers."""

from __future__ import annotations

from ._service import (
    Playbook,
    Skill,
    flatten_instructions,
    flatten_skills,
    keyword_rank,
    load_playbooks,
    safe_id,
    search,
)

# Backward-compat alias: the dataclass was renamed `WorkInstruction → Skill`
# in 0.6.0 to align with the AgentSkills standard, but downstream tooling
# (e.g. ammp-evals) may still import the old name through 0.x.
WorkInstruction = Skill

__all__ = [
    "Playbook",
    "Skill",
    "WorkInstruction",
    "flatten_instructions",
    "flatten_skills",
    "keyword_rank",
    "load_playbooks",
    "safe_id",
    "search",
]
