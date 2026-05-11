"""Mentor backends — pluggable answer engines for AskMentor.

A *mentor backend* is the thing that actually synthesises an answer
when AskMentor is invoked. The abstraction exists because the same
AMMP server can host different kinds of mentor in parallel:

- ``AnthropicBackend`` — stateless call to the Anthropic Messages API
  with the mentor's persona + playbooks pasted into the system prompt.
  Cheapest, no state, good for a "voice-only" persona.

- ``OpenClawBackend`` — POST the question to a configured HTTP
  endpoint hosted by an OpenClaw runtime where the *real* mentor
  lives. The mentor sees the question with their full conversational
  state (memory, vault, prior decisions) and answers as themselves.
  This is what we use for Pepe Arturo on `mcp.helmguild.com/ammp`.

- ``StubBackend`` — deterministic low-confidence answer for offline
  development, tests, and fallback when no other backend is
  configured. Always sets ``confidence=0.2`` so the mentor-triggered
  escalation kicks in.

Mentors choose their backend in ``mentor.json``::

    {
      "name": "Pepe Arturo",
      "persona": "...",
      "backend": {
        "kind": "openclaw",
        "url": "https://openclaw.helmguild.local/ammp/ask",
        "auth_bearer_env": "OPENCLAW_BEARER",
        "timeout_seconds": 60
      }
    }

Without a ``backend`` block the server falls back to the Anthropic
backend wired from global settings (legacy v0.2 behaviour).
"""

from .anthropic import AnthropicBackend, parse_envelope
from .base import LLMAnswer, MentorBackend
from .factory import build_backend
from .openclaw import OpenClawBackend
from .stub import StubBackend

__all__ = [
    "AnthropicBackend",
    "LLMAnswer",
    "MentorBackend",
    "OpenClawBackend",
    "StubBackend",
    "build_backend",
    "parse_envelope",
]
