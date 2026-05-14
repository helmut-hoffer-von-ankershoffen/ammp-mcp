# Changelog

All notable changes to this project will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [semver](https://semver.org/) (`MAJOR.MINOR.PATCH`).

## [0.10.0] — 2026-05-14

### Added

- **`Playbook.commercial: bool`** dataclass field, populated by reading the backing plugin's `plugin.json` `"commercial"` flag. Mirrors the two-marketplace model: the private `helmguild-plugins` marketplace serves commercial plugins (`commercial: true`); the public sibling [`helmguild-plugins-public`](https://github.com/helmut-hoffer-von-ankershoffen/helmguild-plugins-public) serves community-licensed plugins (`commercial: false`). (#marketplace)
- **Commercial badge** rendered on each playbook card on the landing (EN + DE). Visible mini-pill in the `.pb-name` header; tooltip explains the distribution policy (Helmguild Mentoring License v1.0 / AMMP `GetPluginArchive`-only). Empty when the playbook isn't backed by a commercial plugin. (#landing)

## [0.9.0] — 2026-05-14

### Added — CI / automation

- **Pyright** added as a second static type-checker alongside mypy. Runs in `make lint` and on every CI push/PR. Config in `pyproject.toml` (`[tool.pyright]`: `typeCheckingMode = "standard"`, includes `src/`, Python 3.11+). Caught + fixed: `asynccontextmanager` `_lifespan` lacked an `AsyncIterator[…]` return annotation; `_data/__init__.py` passed `__package__` (typed `str | None`) to `importlib.resources.files` without a fallback; a stale composite `# type: ignore[…]` comment on the YAML import in `playbook/_service.py`. (#typing)
- **`.github/workflows/dependabot-auto-merge.yml`** — auto-approves + enables auto-merge on patch + minor Dependabot bumps; labels major bumps with `needs-human-review` and leaves them. Uses `dependabot/fetch-metadata` v2.4 (SHA-pinned). Auto-merge respects every branch-protection-required check — no bypass. (#ci)
- **`.github/workflows/claude-review.yml`** — runs Claude Code on every PR diff via the `anthropics/claude-code-action`, posts a structured review against the project's CLAUDE.md / AGENTS.md, and auto-enables merge when the verdict is `approve`. Fork PRs short-circuit gracefully (secrets aren't visible there). Required secret: `ANTHROPIC_API_KEY`. (#ci)
- **`.github/workflows/e2e-install.yml`** — weekly + on-demand workflow that runs `scripts/e2e-claude-code-install.sh` against the live `mcp.helmguild.com` deployment using a `HELMGUILD_AMMP_BEARER` secret. Catches regressions in the end-to-end mentee install path. Gated behind a `vars.AMMP_E2E_ENABLED` repo variable so it stays dormant until Helmut wires the secret. (#ci)

### Pre-existing

- **Trivy** vulnerability + SBOM scan is already integrated in `audit.yml` (HIGH/CRITICAL findings fail the gate; SARIF uploaded to Code Scanning). Helmut's checklist item #2 was already satisfied.

## [0.8.2] — 2026-05-14

### Added

- **License-metadata round-trip assertions** on `test_plugin_archive_zip_round_trips_scripts_and_mcp_payload`. The test now puts a `license` field on the synthetic plugin's `plugin.json` + SKILL.md frontmatter + a `LICENSE.md` body, and asserts all three survive the zip round-trip with the value intact. Pinned so a regression that strips or rewrites license metadata at zip time is caught before any plugin reaches a mentee. The `helmguild-plugins` marketplace this server's reference deployment serves uses `LicenseRef-helmguild-mentoring-1.0` (Helmguild Mentoring License v1.0). (#license)

### Changed (documentation)

- **README** now spells out the two-layer licensing model: the server itself (this repo) stays MIT, while plugin payloads served via `GetPluginArchive` follow whatever license the marketplace chose. The reference helmguild deployment serves a proprietary `LicenseRef-helmguild-mentoring-1.0`; other operators choose their own. (#docs)

## [0.8.1] — 2026-05-14

### Added

- **`scripts/e2e-claude-code-install.sh`** — round-trip exerciser for the install path a real mentee + user walks after `AskMentor` returns a `GetPluginArchive` URL. Downloads the zip from the live AMMP server with a Bearer token, runs `claude plugin validate` on the extract, wraps the plugin in a throwaway local marketplace, runs `claude plugin marketplace add` + `claude plugin install --scope local`, and asserts the install landed in `.claude/settings.local.json`. Project-local scope keeps state isolated; trap-cleanup removes the marketplace + plugin + tmp dir on success or failure. (#e2e)
- **`tests/e2e/test_claude_code_install.py`** — pytest wrapper parametrised over Pepe's three plugins (`pepe-operator-craft`, `pepe-multi-channel-content-pipelines`, `pepe-personal-assistant-for-managers`). Skips automatically when `claude` / `curl` / `jq` / `unzip` is missing from PATH or when `HELMGUILD_AMMP_BEARER` is unset. Locally with the env var, all three plugins install in <8 s. (#e2e)

## [0.8.0] — 2026-05-14

### Added

- **Built-on-open-standards section** on the mcp.helmguild.com landing (EN + DE) — explicitly names the [AgentSkills](https://agentskills.io/home) `SKILL.md` format, the [Claude Code plugin](https://code.claude.com/docs/en/plugins) spec, and the [Claude Code marketplace](https://code.claude.com/docs/en/plugin-marketplaces) catalogue conventions as the open standards AMMP's on-disk format leans on. Footer breadcrumb mentions both standards so the claim is visible regardless of scroll position. (#landing)
- **E2E integration test** (`test_plugin_archive_zip_round_trips_scripts_and_mcp_payload`) covering the full plugin-payload contract — a plugin shipping `scripts/`, `mcp-server/`, and a multi-server `.mcp.json` round-trips through `GetPluginArchive` and `/plugins/<name>.zip` with executable bits preserved + .mcp.json's multi-server config intact. Pins the contract `pepe-multi-channel-content-pipelines` 0.2.0 relies on (bundled stdio MCP + bash helper). (#tests)

### Plugin payload contract — informational

The `pepe-multi-channel-content-pipelines` plugin on the `helmguild-plugins` marketplace bumped to 0.2.0 with:

- `scripts/inspect-content-state.sh` — bundled bash helper.
- `mcp-server/pipeline-status.mjs` — bundled stdio MCP (pure stdlib Node, no deps).
- `.mcp.json` now registers two servers: the HTTP AMMP wire + the bundled stdio MCP via `${CLAUDE_PLUGIN_ROOT}`.

This is the first plugin in the marketplace exercising the multi-MCP install path and the plugin-ships-scripts pattern. Validated end-to-end with `claude plugin validate`.

## [0.7.0] — 2026-05-14

### Removed (BREAKING)

- **`GetWorkInstruction` MCP tool** — removed entirely. Use `GetSkill` instead. 0.5.x mentees that still hardcode the old name will see `tool_not_found` and must update.
- **`WorkInstruction*` Python aliases** — `WorkInstruction`, `WorkInstructionSummary`, `WorkInstructionEntry`, `GetWorkInstructionResponse`, `flatten_instructions` are gone. Downstream code must import `Skill`, `SkillSummary`, `SkillEntry`, `GetSkillResponse`, `flatten_skills`.
- **`ammp instruction` CLI command** — renamed to `ammp skill list / show`. Old subcommand name no longer accepted.

### Changed (BREAKING — JSON wire)

- **`PlaybookSummary` / `PlaybookEntry`** `instructions` field → `skills`.
- **`PlaybookSummary` / `MentorSummary`** `instruction_count` field → `skill_count`.
- **`GetPlaybookResponse`** `instructions` field → `skills`.
- **`AskMentorResponse`** `relevant_instructions` field → `relevant_skills`.
- **`/.well-known/agent.json`** per-mentor `instructionCount` field → `skillCount`. The `operations` array drops `GetWorkInstruction`.
- **`Playbook` dataclass** attribute `instructions` → `skills` (Python-internal).

### Migration

Update mentee code:

```diff
-result = await client.call_tool("GetWorkInstruction", ...)
+result = await client.call_tool("GetSkill", ...)

-for wi in playbook["instructions"]:
+for sk in playbook["skills"]:

-summary.instruction_count
+summary.skill_count
```

This release completes the AgentSkills alignment started in 0.6.0. Every wire-level remnant of the old "work instruction" vocabulary is now removed.

## [0.6.0] — 2026-05-14

### Added

- **AgentSkills alignment: `GetSkill(playbook_id, id)`** server-side extension. Renamed from `GetWorkInstruction` to align the wire name with the open [AgentSkills](https://agentskills.io/home) standard a playbook plugin's on-disk format follows. The old `GetWorkInstruction` tool is preserved as a deprecated alias through the 0.x line — existing mentees keep working without code changes. (#agentskills)
- **`GetPluginArchive(plugin)`** server-side extension. When a playbook is backed by a private marketplace plugin (e.g. `helmut-hoffer-von-ankershoffen/helmguild-plugins`, which the mentee cannot clone from GitHub), this tool returns a Bearer-token-gated download URL pointing to `/plugins/<plugin>.zip` on the same server. The mentee hands the URL + install instructions to its user, who installs the plugin into Claude Code / Desktop by extracting the zip and running `/plugin install <path>`. The plugin's `.mcp.json` wires this same AMMP server, so the live ops keep working after install. (#plugins)
- **Plugin-aware playbook loader.** `playbook.json` now accepts an optional `"plugin": "<name>@<marketplace>"` field. When present, the playbook's skill bodies load from `<marketplaces_root>/<marketplace>/plugins/<plugin>/skills/<id>/SKILL.md` (AgentSkills format with YAML frontmatter). When absent, the legacy `NN-*.md` format under the playbook directory continues to work. (#loader)
- **`AMMP_MARKETPLACES_ROOT`** setting (default `~/.ammp/marketplaces`) — where the server looks for marketplace clones. (#config)
- **Plugin-aware start prompt.** The per-playbook prompt rendered on the landing now opens with a `GetPluginArchive` install step when the playbook is plugin-backed, so the mentee gets a single URL it can hand its user to install the plugin into their runtime.

### Changed

- **`/.well-known/agent.json` `operations`** array now lists `GetSkill` and `GetPluginArchive` in addition to the previous ops. `GetWorkInstruction` remains advertised as a deprecated alias of `GetSkill` through 0.x. (#capability)
- **Landing page copy** (EN + DE) — "work instructions" → "skills" in mentor-card chrome and per-playbook prompts. Functional behaviour unchanged. (#landing)
- **Python rename:** `WorkInstruction` → `Skill`, `WorkInstructionSummary` → `SkillSummary`, `WorkInstructionEntry` → `SkillEntry`, `GetWorkInstructionResponse` → `GetSkillResponse`. Old names re-exported as aliases for backwards compatibility through 0.x.

## [0.5.0] — 2026-05-12

### Changed

- **PyPI distribution renamed: `ammp-mcp` → `ammp`.** The package is now installable as `uvx ammp` / `pip install ammp` once published. The reference implementation is *one* concrete realisation of AMMP, and the bare protocol name on PyPI reads cleaner than the binding-suffixed name. The GitHub repo (`helmut-hoffer-von-ankershoffen/ammp-mcp`), import module (`ammp_mcp`), CLI entrypoint (`ammp`), and on-the-wire server name (`ammp-mcp`) are unchanged — only the PyPI distribution metadata flips. (#packaging)

### Operator note

Before the next tag actually lands on PyPI, the **`ammp` package needs a Trusted Publisher entry** on `pypi.org/manage/account/publishing/` for this GitHub repo + `release.yml` workflow. The previous trusted-publisher config (if any) was scoped to the old `ammp-mcp` name and won't authenticate the upload. Until that's configured, the release workflow's `publish-pypi` step fails-soft (continue-on-error) and the GitHub release still ships dist artefacts.

## [0.4.1] — 2026-05-12

### Fixed

- **License audit:** `tools/audit_licenses.py` now handles SPDX `AND` expressions (e.g. `Apache-2.0 AND BSD-2-Clause`). Previous splitter only handled `OR` / `;`, which let `prometheus_client` (pulled in transitively by `fastmcp[tasks]` → docket → redis) trip the Audit gate on 0.4.0. (#audit)
- **SonarQube hotspot S5332:** `_render_landing` derived the display host via `base.replace("https://", "").replace("http://", "")` — Sonar flagged the `"http://"` literal as an insecure protocol. Rewritten to use `urllib.parse.urlparse`. False-positive removed at source. (#sonar)

### Added

- **Telegram adapter unit tests** — `tests/unit/test_telegram_adapter.py` covers `deliver()`, `_format_outbound()`, `_handle_update()` (delivered match, no `reply_to`, empty text, cancelled late-reply branch), and `_poll_forever()` via `httpx.MockTransport`. Coverage on `_telegram.py` jumps 21 % → 79 %, lifting Sonar's new-code coverage above the 80 % threshold. (#tests)
- **Release-time quality-gate verification.** `release.yml` grows a `gates` job that polls the GitHub Actions runs API for the tagged SHA and refuses to proceed to `build` / `publish-pypi` / `github-release` unless `Audit`, `SonarCloud`, `CodeQL`, and `CI` all report `success`. The 0.4.0 release shipped with `Audit` + `SonarCloud` red; this prevents a repeat. (#ci)

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
