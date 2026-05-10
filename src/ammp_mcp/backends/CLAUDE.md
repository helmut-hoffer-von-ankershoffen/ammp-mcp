# `backends` — AMMP

Responsibility: pluggable answer engines for `AskMentor`. Each backend takes a question + persona + relevant playbook bodies and returns an `LLMAnswer` (text + self-reported confidence).

## Files

- `base.py` — `MentorBackend` Protocol + `LLMAnswer` dataclass. The contract every backend implements.
- `anthropic.py` — calls the Anthropic Messages API directly; stateless; pastes persona + playbooks into the system prompt every call. Also defines `parse_envelope` (canonical JSON-in-text answer envelope).
- `openclaw.py` — POSTs the question to a live agent runtime (e.g. Pepe-on-OpenClaw). The mentor sees the question with their full conversational state.
- `stub.py` — deterministic low-confidence answer for offline dev, tests, and the fallback path when no API key is configured. Always returns `confidence=0.2` so mentor-triggered escalation kicks in.
- `factory.py` — `build_backend(config, settings)` — the only place the `BackendConfig` discriminated union (defined in `ammp_mcp.mentor._models`) is unpacked. Adding a fourth backend is a one-file change here.

## Public API (`from ammp_mcp.backends import …`)

- `MentorBackend` — Protocol every backend satisfies.
- `LLMAnswer` — `(answer, confidence)` dataclass.
- `AnthropicBackend`, `OpenClawBackend`, `StubBackend` — concrete implementations.
- `build_backend(config, settings)` — config → live backend.
- `parse_envelope(text)` — extract `{"answer": ..., "confidence": ...}` from an LLM-produced string.

## How callers use it

`server.create_server()` calls `build_backend(mentor.backend, settings)` once per mentor at boot and stores the result in `ServerContext.backends`. `_handle_ask_mentor` then dispatches to the per-mentor backend.

## Test coverage

- `tests/unit/test_backends.py` — each backend in isolation, plus `build_backend` and `parse_envelope`.
- `tests/integration/test_server.py` — multi-mentor routing with mixed backend kinds (`stubmentor` declared `kind=stub`, `pepe`/`strict` fall through to the anthropic-direct fallback).
