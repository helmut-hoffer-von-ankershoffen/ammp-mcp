# Changelog

All notable changes to this project will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [semver](https://semver.org/) (`MAJOR.MINOR.PATCH`).

## [0.4.0] — 2026-05-12

### Added

- **`EscalateToHumanMentor`** mentor-mediated escalation tool. Forwards a B.h-approved question to the human behind a mentor (A.h) via a pluggable delivery adapter (`log` default, `telegram` for the deployed instance). Sync-or-pending: blocks up to `wait_seconds` (default 25 s — safely under typical MCP per-tool client timeouts) for A.h's reply; returns `status="answered"` with the answer if A.h replies in time, otherwise `status="pending"` with an `escalation_id` to poll later. Opts into FastMCP's task primitive (`mode=optional`) so clients that understand the MCP background-task protocol can run it as a true task. (#escalation)
- **`GetEscalation(escalation_id, wait_seconds?)`** companion tool to retrieve a pending answer when it lands. Optional short wait arms a fresh broker waiter; without a wait, returns the current snapshot from the persistent jsonl store. (#escalation)
- **`GetSystemInfo()`** diagnostic tool that returns a small, safe slice of build / release / runtime metadata (software name + version, AMMP draft id, Python version, OS platform, boot timestamp + uptime, mentor / mentee counts, default mentor slug, active escalation adapter kind, mount path, public URL) for end-to-end debugging. Never surfaces file paths, env-var values, hostnames, tokens, PIDs, or anything that could compromise security. (#diagnostics)
- **Telegram delivery adapter** for `EscalateToHumanMentor`. Sends the question via Bot API `sendMessage`; long-polls `getUpdates` for the human's reply (matched by `reply_to_message.message_id`). Configured via `AMMP_ESCALATION_ADAPTER=telegram` + `AMMP_ESCALATION_TELEGRAM_BOT_TOKEN` + `AMMP_ESCALATION_TELEGRAM_CHAT_ID`. (#escalation)
- **Progress heartbeats** on the long-running escalation path. Wraps the wait in a loop that emits `notifications/progress` every `escalation_progress_heartbeat_seconds` (default 25 s) so MCP clients that reset per-tool timeouts on progress notifications hold the call open until the human replies. (#escalation)
- **`Mentor.profile_url`** + **`HumanMentor.profile_url`** fields. When set, the landing page wraps the mentor / human-mentor name in a link to the longer profile page. URLs on `https://www.helmguild.com/<path>/` auto-swap to the `/de/` variant on the German landing. (#landing)
- **Bilingual landing.** `/de/` route serves the German mirror (translated chrome — lede, step headings, tab labels, mailto draft); mentor names + playbook content stay in English (corpus is content, not chrome). EN · DE language pill + helmguild.com banner at the top of every landing. (#landing)
- **`.mcpb` desktop bundle** at `GET /desktop-bundle.mcpb` — a Claude Desktop extension built on the fly from a pure Node stdio→Streamable-HTTP MCP proxy (~100 lines, no deps). Replaces `mcp-remote` for the Bearer-token case (Claude Desktop's extension sandbox blocks `mcp-remote`'s localhost OAuth-callback bind). (#desktop)

### Changed

- **Auth flows via HTTP `Authorization: Bearer` header**, not a tool parameter. `_authenticate` reads the token from the request via FastMCP's request-scoped context; the LLM never sees an `api_key` argument and so never asks for one. (#auth)
- **`ListMentors` returns work-instruction summaries**, not full bodies. Previous behaviour inflated the response to ~100 KB which tripped Claude Desktop's MCP-transport timeout on connect. Full bodies fetched on demand via `GetPlaybook` / `GetWorkInstruction`. (#listmentors)
- **AMMP_MOUNT_PATH** lets the server live under a URL prefix (e.g. `/ammp`). The hostname `mcp.helmguild.com` is now a gateway that can host sibling MCP servers under other prefixes. (#deploy)

### Fixed

- **`asyncio.get_event_loop()` → `get_running_loop()`** in the escalation handler (3.10+ deprecation). (#hygiene)
- **Late Telegram replies are logged**, not silently dropped, when an MCP call has already been cancelled / expired. Helps the operator notice that A.h actually did answer. (#escalation)

### Test coverage

- 185 tests pass.
- 82 % overall coverage maintained.

## [0.3.0] — 2026-05-10

### Added

- **Modulith architecture.** `src/ammp_mcp/` partitioned into per-domain folders following the Aignostics/python-sdk pattern: `mentor/`, `mentee/`, `playbook/`, `system/`. Each holds a `_service.py` (business logic) and `_cli.py` (Typer shell). `cli.py` slimmed from 877 lines to ~60. (#refactor)
- **Stdio MCP transport.** New `AMMP_TRANSPORT={http,stdio}` setting and `ammp system serve --stdio` flag. Stdio mode quiets the root logger to stderr so the wire stays clean MCP JSON-RPC. Suits Claude Desktop / Claude Code subprocess-MCP hosts. (#stdio)
- **`CLI_REFERENCE.md`** — generated from the Typer app by `tools/generate_cli_reference.py`. Regenerate after CLI changes. (#docs)
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
