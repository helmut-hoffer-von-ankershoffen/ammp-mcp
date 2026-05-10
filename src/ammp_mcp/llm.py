"""Legacy import shim.

The v0.2 ``LLMClient`` was renamed and split out into the
:mod:`ammp_mcp.backends` package. This module re-exports the old names
so existing call sites and tests keep resolving without churn while we
settle the new abstraction.

New code SHOULD import from ``ammp_mcp.backends`` directly.
"""

from __future__ import annotations

from .backends import AnthropicBackend, LLMAnswer, parse_envelope

#: v0.2 alias preserved for backwards compatibility.
LLMClient = AnthropicBackend

#: Internal helper preserved for tests that imported it directly.
_parse_envelope = parse_envelope

__all__ = ["LLMAnswer", "LLMClient", "_parse_envelope"]
