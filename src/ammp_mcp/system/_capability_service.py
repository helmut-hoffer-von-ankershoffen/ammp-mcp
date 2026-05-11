"""Offline capability-payload assembly — backs `ammp system capability`.

The runtime `/.well-known/agent.json` route is built in `server.py`
(`_build_capability_payload`) where it has access to per-mentor backend
state. This helper is the *offline* CLI render — it can't see backend
liveness, so it advertises only the static facts.
"""

from __future__ import annotations

from typing import Any

from ..mentor import load_mentors
from ..playbook import load_corpus
from ..settings import Settings


def build_offline_capability(s: Settings, version: str, ammp_draft: str) -> dict[str, Any]:
    """Assemble the same shape as `/.well-known/agent.json` for offline render."""
    mentors = load_mentors(s.mentors_root)
    summaries = []
    for slug, m in mentors.items():
        corpus = load_corpus(m.playbook_dir)
        summaries.append({"slug": slug, "name": m.name, "playbookCount": len(corpus)})
    return {
        "name": "ammp-mcp",
        "version": version,
        "url": s.public_url,
        "ammp": {
            "draft": ammp_draft,
            "tracks": ["mentoring"],
            "mentors": summaries,
            "privacyPosture": {
                "retention": "no-retention",
                "auditLog": "hash-only",
                "crossCompartmentEscalation": "prohibited",
                "requireAuth": s.require_auth,
            },
            "bindings": ["mcp"],
            "invariants": ["compartmentalisation", "human-gated-escalation"],
        },
        "operations": [
            "ListMentors",
            "ListPlaybooks",
            "GetPlaybook",
            "SearchPlaybooks",
            "AskMentor",
            "EscalateToHuman",
        ],
    }
