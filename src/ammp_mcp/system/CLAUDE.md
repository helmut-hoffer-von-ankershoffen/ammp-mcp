# `system` — AMMP

Responsibility: install-wide CLI operations — first-run setup, static install validation, runtime health probing, audit-log usage aggregation, offline capability render, and the `serve` shortcut.

## Files

- `_setup_service.py` — `_setup_*` helpers backing the `setup` wizard (idempotent backend-block write, .env scaffolding, first-mentee minting).
- `_status_service.py` — `_StatusReporter` plus `_status_check_*` helpers that walk the install statically (paths, registry loads, env vars).
- `_health_service.py` — `_health_probe_*` helpers — GET `/.well-known/agent.json`, HEAD each `openclaw` mentor backend.
- `_usage_service.py` — `_AuditAggregate` + `_AUDIT_LINE_RE` + parsers; aggregates the hash-only audit log into op / mentor / mentee breakdowns.
- `_capability_service.py` — `build_offline_capability(s, version, draft)` — same shape as the live `/.well-known/agent.json` payload, without live backend status.
- `_cli.py` — `system_app` Typer subgroup wiring all of the above to the `ammp system <verb>` surface.

## Public API

Internal to `ammp_mcp.cli`. The top-level CLI re-exports the command callables (`serve`, `setup`, `status`, `health`, `usage`, `capability`) from `system_app` to register top-level aliases (`ammp serve`, …).

## How callers use it

```python
# from ammp_mcp.cli
from ammp_mcp.system._cli import system_app, serve, setup, status, health, usage, capability
```

## Test coverage

- `tests/integration/test_cli.py` — every command via Typer's `CliRunner`, including the `ammp system <verb>` and top-level-alias paths.
