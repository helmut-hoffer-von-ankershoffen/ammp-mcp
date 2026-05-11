# Changelog

All notable changes to this project will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [semver](https://semver.org/) (`MAJOR.MINOR.PATCH`).

## [0.3.0] — 2026-05-10

### Added

- **Modulith architecture.** `src/ammp_mcp/` partitioned into per-domain folders following the Aignostics/python-sdk pattern: `mentor/`, `mentee/`, `playbook/`, `system/`. Each holds a `_service.py` (business logic) and `_cli.py` (Typer shell). `cli.py` slimmed from 877 lines to ~60. (#refactor)
- **Stdio MCP transport.** New `AMMP_TRANSPORT={http,stdio}` setting and `ammp system serve --stdio` flag. Stdio mode quiets the root logger to stderr so the wire stays clean MCP JSON-RPC. Suits Claude Desktop / Claude Code subprocess-MCP hosts. (#stdio)
- **`docs/CLI_REFERENCE.md`** — generated from the Typer app by `tools/generate_cli_reference.py`. Regenerate after CLI changes. (#docs)
- **`AGENTS.md`** + **`CLAUDE.md`** at the repo root, plus per-module `CLAUDE.md` files under each domain (`mentor/`, `mentee/`, `playbook/`, `system/`, `backends/`). (#docs)
- **`INSTALLATION.md`** — five-minute path, Claude-Code wiring (HTTP + stdio), config matrix, troubleshooting table. (#docs)
- **E2E tests.** New `tests/e2e/test_stdio_transport.py` (stdio round-trip) and `tests/e2e/test_scenarios.py` (cross-runtime scenarios #7-#9: OpenClaw-routed mentor + Claude-Code-labelled mentee, roles switched, multi-mentor/multi-mentee concurrent). Backed by `tests/e2e/harness.py` (shared scaffolding including a `mock_openclaw_webhook` Starlette/uvicorn fixture). (#tests)

### Fixed

- **`ATTRIBUTIONS.md`** — `tools/generate_attributions.py` now `html.unescape()`s license + notice bodies. The aiofile Apache NOTICE used to render `&lt;…&gt;` literally inside the code fence. (#hygiene)

### Changed

- `src/ammp_mcp/models.py` slimmed to response envelopes only. `Mentor`, `Mentee`, and `BackendConfig` moved to their respective domain modules (`mentor/_models.py`, `mentee/_models.py`).
- `registries.py` and `playbooks.py` removed — their contents moved into `mentor/_service.py`, `mentee/_service.py`, and `playbook/_service.py` respectively.

### Test coverage

- 107 tests pass + 1 skipped (was 102 + 1).
- 86 % overall coverage maintained.

## [0.2.0] — 2026-05-09

### Added

- Pluggable mentor backends (`anthropic`, `openclaw`, `stub`) selected per-mentor via `mentor.json`.
- Multi-mentor routing — every operation takes a `mentor` slug.
- Multi-mentee allowlist (SHA-256-hashed Bearer keys in `mentees.json`).
- Hash-only audit log per AMMP §6.2.
- `ammp` Typer CLI for housekeeping (mentor/mentee/playbook/system subjects).

## [0.1.0] — 2026-05-08

- Initial reference implementation under the working name `pepe-mentor-mcp`. Renamed to `ammp-mcp` at v0.2 — see the corresponding memory entry.
