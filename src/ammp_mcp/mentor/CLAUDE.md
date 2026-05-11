# `mentor` — AMMP

Responsibility: describe what a mentor is (pydantic model + backend-config union) and discover mentors on disk.

## Files

- `_models.py` — `Mentor` pydantic class and the `BackendConfig` discriminated union (`AnthropicBackendConfig`, `OpenClawBackendConfig`, `StubBackendConfig`). The backend-config types live here because each mentor selects its answer engine via `mentor.json`; backend *implementations* live under `ammp_mcp.backends`.
- `_service.py` — `load_mentors(root)` walks `<root>/<slug>/mentor.json` and returns a registry; `get_mentor(mentors, slug, default_slug)` resolves a caller-supplied slug (empty → default).
- `_cli.py` — `mentor_app` Typer subgroup. `ammp mentor list` inspects the registry; `ammp mentor ask` / `ammp mentor escalate` give the shell CLI parity with the AMMP `AskMentor` / `EscalateToHuman` ops by invoking the same in-process handlers `server.py` exposes over MCP. Local CLI bypasses Bearer auth (operator already has filesystem access).

## Public API (`from ammp_mcp.mentor import …`)

- `Mentor` — the pydantic class loaded from `mentor.json`.
- `BackendConfig`, `AnthropicBackendConfig`, `OpenClawBackendConfig`, `StubBackendConfig` — the per-mentor backend selector.
- `load_mentors(root)` — disk → registry.
- `get_mentor(mentors, slug, default_slug)` — slug → `Mentor | None`.

## How callers use it

```python
from ammp_mcp.mentor import load_mentors, get_mentor

mentors = load_mentors(settings.mentors_root)
m = get_mentor(mentors, requested_slug, settings.default_mentor)
```

## Test coverage

- `tests/unit/test_registries.py` — `load_mentors`, `get_mentor`.
- `tests/unit/test_models.py` — `Mentor` validation.
- `tests/unit/test_backends.py` — `BackendConfig` shapes via the factory.
