# Changelog

All notable changes to this project will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [semver](https://semver.org/) (`MAJOR.MINOR.PATCH`).

## [0.15.5] — 2026-05-15

### Changed

- **Path-collapse ASGI middleware replaces the two specific-route `//` handlers.** The new `CollapseSlashesMiddleware` (in `server.py`) runs before Starlette route matching: any URL whose path contains `2+` consecutive `/` is collapsed to a single `/` and 308-redirected to the canonical URL. Wired in `system/_cli.py:serve` via `mcp.run(middleware=[Middleware(CollapseSlashesMiddleware)])`. The previous `@mcp.custom_route(f"{prefix}//", ...)` + `@mcp.custom_route(f"{prefix}/de//", ...)` handlers are removed — the middleware supersedes them and handles every path, not just the two landing URLs. SEO consolidation: Google sometimes links with `//` (a referrer artefact); the 308 keeps link equity on the canonical URL. 4 new unit tests pin the middleware shape (HTTP redirect, clean-path pass-through, query-string preservation, non-HTTP-scope pass-through). 244 tests pass. (#seo)

## [0.15.4] — 2026-05-15

### Added

- **GitHub-repo pills left of the language switcher** on the landing. Two small pills with the GitHub mark: "Reference impl" → `github.com/.../ammp-mcp`, "Free marketplace" → `github.com/.../helmguild-plugins-public`. Lets a visitor get from the landing to source without poking through helmguild.com. EN + DE i18n + CSS shipped; pills hide on viewports < 720 px to avoid overlap with the lang switcher. (#landing)
- **Trailing-`//` redirect** for the EN + DE landing. Google sometimes links to URLs with a trailing double-slash (a referrer artefact); Starlette returned 404. New `<prefix>//` + `<prefix>/de//` handlers 308 to the canonical single-slash URL. Closes a parallel breakage to Helmut's report on www.helmguild.com — that site got its CSS paths root-relative-ed in the helmguild.com commit `7e87732`. (#landing)

## [0.15.3] — 2026-05-15

### Removed

- **Validated badge dropped from the landing page.** Helmut: "remove the Validated badges from mcp until we have real validation. Plus we must validate per type of agent: ie we will have `[validated: Cowork, Code, Copilot, OpenClaw, Hermes]` when fully done." The previous badge rendered whenever a playbook declared ≥1 validation prompt — which is "has acceptance tests authored", not "passed acceptance tests". Real Validated state is per-runtime + only earned after a Layer 3 goal-level run passes for that runtime. Re-add as `Validated: <runtime-list>` once the per-runtime state file ships. (#validation)

## [0.15.2] — 2026-05-15

### Fixed

- **SonarCloud `new_coverage` 79.5 % → 87 %.** The 0.15.0–0.15.1 validation system shipped without unit tests on the new code paths: `_load_one_playbook`'s validation-block parser (~20 lines) and `ammp playbook validate`'s CLI (~58 lines). Adds 9 new tests (4 loader unit tests covering happy path / malformed shapes / absent key, 5 CLI integration tests covering unknown mentor / invalid id / not-found / no-prompts / delegates-to-harness with monkeypatched subprocess + missing-harness path). 240 unit/integration tests pass. (#coverage)

## [0.15.1] — 2026-05-15

### Added

- **"Validated" badge** on playbook cards that declare validation prompts. Sits next to the Free/Commercial license badge; purple-tint pill; tooltip names the prompt count + how to run the validator. Pepe's `knowledge-management` playbook now shows `Free · Validated` (4 prompts) on https://mcp.helmguild.com/ammp/. EN + DE i18n + CSS shipped. (#landing)
- **Live proof of AMMP-as-pedagogy:** `ammp playbook validate knowledge-management` against the live `mcp.helmguild.com/ammp` server returned **4/4 prompts passed** on a freshly-spawned Claude Code mentee. The mentee correctly mapped 4 distinct user-task prompts to the right skills (canonical-knowledge-vault, per-agent-auto-memory-discipline), named the bundled scaffolder, and reproduced the load-bearing rules (200-line index cap, save-corrections-as-feedback, vault wins over private memory). First end-to-end proof that mentoring transfers competence, not just RPC. (#validation)

## [0.15.0] — 2026-05-15

### Added — playbook validation (AMMP extension)

This is the closing loop the protocol was missing. Until now AMMP could prove "the wire works" (RPC) and "a mentee can read a playbook" (the install/walkthrough e2e). It could not prove "a mentee actually learns the playbook" — the mentoring-as-pedagogy claim.

- **`playbook.json` carries an optional `validation` object** with shape `{"prompts": [{"id", "task", "expect": {...}}]}`. Each prompt names an `id`, a `task` the mentee receives verbatim, and an `expect` block of rules. Supported rules: `must_mention_skill`, `must_contain` (alternates via `|`), `must_contain_pattern` (regex, case-insensitive), `must_invoke_or_name` (named scripts/tools), `must_mention_type`. Pepe's `knowledge-management` playbook ships 4 prompts covering the canonical-vault Setup, auto-memory correction-handling, private-vs-shared decision, and the 200-line index cap.
- **`ammp playbook validate <id>`** — new CLI. Reads the playbook spec, delegates to `scripts/e2e-playbook-validation.sh` which spawns a fresh `claude -p` mentee per prompt with the live AMMP MCP wire pre-loaded (.mcp.json in tmp workdir), captures the mentee's response, runs the expect rules against it. Reports per-prompt pass/fail; exits 0 only when every rule on every prompt passes. `--json` available.
- **Wire envelopes** (`PlaybookSummary`, `GetPlaybookResponse`) gain a `validation_prompt_count: int` field. Bodies of the prompts stay server-side (they'd otherwise leak the answer key); only the count goes over the wire. Mentees can see "this playbook has N validation prompts" without seeing them. (#validation)

## [0.14.3] — 2026-05-14

### Added

- **`ammp playbook list --json`** — CLI parity with the other list-shaped commands (`ammp mentor list --json`, `ammp playbook search --json`). Emits `{mentor, count, playbooks: [{id, name, description, requires, skill_count}]}` — same shape as the MCP `ListPlaybooks` envelope minus the embedded skill bodies. The 0.14.1 CI's docs-walker LLM check (Haiku) flagged this inconsistency: list-shaped CLI commands universally support `--json` except this one. Closing the gap, CLI_REFERENCE.md regenerated, new test pinned. (#cli)

## [0.14.2] — 2026-05-14

### Removed

- **Mermaid playbook-dependency DAG dropped from the landing page.** The block wasn't rendering on mcp.helmguild.com (CDN ESM import or CSP issue) — and the per-card "Requires:" line with anchor links to depended-on playbooks is already a sufficient surfacing. Drops the `<pre class='mermaid'>` block, the on-demand Mermaid loader script, the `.playbook-deps` / `.pb-deps-help` CSS, and the `playbook_deps_heading` / `playbook_deps_help` i18n strings (EN + DE). The `requires` field on `playbook.json` + the wire envelope + the per-card "Requires:" line all stay. (#landing)

## [0.14.1] — 2026-05-14

### Added

- **"Free" badge on community-licensed playbook cards** — symmetric with the existing "Commercial" badge. Every playbook now carries exactly one badge: `Free` (green tint) for community-licensed playbooks served via `helmguild-plugins-public`, `Commercial` (blue tint) for proprietary ones from `helmguild-plugins`. EN + DE i18n strings + a tooltip naming the licensing posture (`free_title`, `commercial_title`). CSS refactored: shared `.pb-badge` base + variant classes `.pb-commercial` / `.pb-free`. (#landing)

## [0.14.0] — 2026-05-14

### Added — playbook dependencies (`requires`)

- **`playbook.json` carries a `requires: [<id>, ...]` array** listing sibling-playbook ids this playbook depends on. Loader reads it; wire envelopes (`PlaybookSummary`, `GetPlaybookResponse`) pass it through unchanged. Empty list when absent or malformed (defensive). Pepe's deployment now declares: `knowledge-management → []`, `multi-channel-content-pipelines → [knowledge-management]`, `operator-craft → [knowledge-management]`, `personal-assistant-for-managers → [knowledge-management, operator-craft]`. (#deps)
- **Landing page surfaces dependencies** in two ways: (a) every playbook card with non-empty `requires` shows a "Requires: <links>" line pointing to the depended-on playbooks (anchor IDs added to each card); (b) when any playbook in a mentor declares a dependency, the page appends a Mermaid `graph LR` DAG below the playbook list, distinguishing commercial (red-tint) from community-licensed (blue-tint) nodes. EN + DE i18n strings + minimal CSS shipped. The Mermaid bundle is loaded on-demand only when the page actually has a `<pre class="mermaid">` block. (#landing)

## [0.13.0] — 2026-05-14

### Changed — wire envelope (breaking)

- **`GetPlaybook` returns skill summaries, not full bodies.** The response's `skills[]` is now a `list[SkillSummary]` (id + title + summary) instead of `list[SkillEntry]` (id + title + summary + body). Mentees that need a body fetch it via `GetSkill(playbook_id, id)` on demand. Surfaced by the new `e2e-skill-walkthrough.sh` test: a real Claude Code mentee tripped the per-tool token budget reading the pepe-multi-channel-content-pipelines playbook (7 skills × ~20 KB after the 2.x rewrite ≈ 150 KB), fell back to ListMentors' embedded skill list to compensate. The slim-down restores the GetPlaybook = enumeration / GetSkill = body discipline from the `mcp_list_ops_summarize` memory note. (#wire)
- Removed unused `SkillEntry` import from `server.py` (the type is now only referenced by tests + the dropped envelope path). Class itself is retained in `models.py` for any out-of-process consumer that depends on the older shape.

### Added — skill-execution e2e

- **`scripts/e2e-skill-walkthrough.sh`** — spawns a real `claude -p` mentee with a fresh AMMP Bearer, points it at the live `mcp.helmguild.com/ammp` via `.mcp.json`, exercises the bundled scaffolders (`brand-identity-scaffold`, `cameo-roster-scaffold`, `state-dir-init`) against a Sandra-as-cooking-brand fixture, runs `setup-doctor.sh` and asserts `brand-identity` + `cameo-protocol` + `strategy` report `ready`, then asks the mentee to enumerate the playbook's skill ids in order via real `ListMentors` + `GetPlaybook` MCP calls. Proves the skills are LLM-followable end-to-end with no external API spend (Veo / Meta / X not exercised). macOS-compatible (falls back to `gtimeout`, then no-timeout). (#e2e)

## [0.12.1] — 2026-05-14

### Changed

- **Rebrand: Claude Desktop → Claude Cowork.** Anthropic renamed the desktop MCP host; this repo follows. Updated across server prose (HTML landing tabs, EN + DE), README, INSTALLATION, AGENTS, OPERATING, RFC (EN + DE), models / settings / system CLI docstrings, integration + e2e test fixtures, the `.mcpb` desktop-bundle template (Node proxy), and historical CHANGELOG entries for consistency. Technical identifiers — the on-disk config path `~/Library/Application Support/Claude/claude_desktop_config.json`, the `_data/desktop_bundle/` Python module path, and HTML CSS ids like `agent-tab-desktop` — kept as-is to avoid breaking host-side compatibility. (#rebrand)

## [0.12.0] — 2026-05-14

### Added — CLI ↔ MCP parity (shared `_service.py` layer)

- **`ammp plugin list`** — local equivalent of enumerating `GetPluginArchive` references: Rich table over every `(plugin, marketplace, clone-path)` triple known to this server's playbook corpus.
- **`ammp plugin archive <name> [--json]`** — local equivalent of the `GetPluginArchive` MCP tool: resolves a plugin name to its `https://<public_url>/plugins/<name>.zip` URL + install instructions. Same in-process service-layer helper (`build_plugin_archive_response`) the MCP handler + the HTTP route call, so wire + shell + raw download stay in lockstep. (#cli)
- **`ammp system info`** — local equivalent of the `GetSystemInfo` MCP tool: prints the canonical `SystemInfo` envelope as JSON (version, AMMP draft, capability URL, public URL, optional Telegram bot username, `started_at`/`uptime_seconds` are `null` for offline CLI). (#cli)

### Changed — service-layer extraction

- **`playbook/_service.py`** gains two pure helpers `enumerate_plugin_refs(mentors, marketplaces_root)` and `build_plugin_archive_response(public_url, plugin, known_refs)`. `_PLUGIN_NAME_RE` moved here from `server.py`. Both `_handle_get_plugin_archive` (MCP) and the `/plugins/<name>.zip` HTTP route now delegate to `build_plugin_archive_response` for validation + resolution + envelope construction. CLI calls the same two helpers. One source of truth, three surfaces (wire, route, shell). (#refactor)
- **`server._known_plugin_refs(ctx)`** is now a thin wrapper around `enumerate_plugin_refs` — kept only as a private convenience for the FastMCP handler signature.
- **CLI_REFERENCE.md** regenerated to capture the new `ammp plugin {list,archive}` and `ammp system info` subcommands.

## [0.11.1] — 2026-05-14

### Fixed

- **`Playbook.commercial` derivation**: read the flag from the **marketplace.json** (per-entry `commercial` with fallback to `metadata.commercial`) instead of the plugin's own `plugin.json`. Claude Code's `claude plugin validate` ships with a strict plugin.json schema that rejects custom top-level keys — including `commercial` — so the flag now lives only on the catalogue side. Plugins downloaded via `GetPluginArchive` extract cleanly through `claude plugin validate` again. The landing-page "Commercial" badge still renders correctly because the loader reads from the marketplace clone. (#commercial)

### Added — live smoke E2E suite (production)

- **`tests/e2e/test_live_ammp_smoke.py`** — 8 tests against `mcp.helmguild.com/ammp`: capability JSON shape + ops set + Pepe + privacy posture; robots.txt advertises sitemap; sitemap lists EN + DE + capability with hreflang; landing renders the open-standards section + commercial badges; per-plugin zip round-trips the Helmguild Mentoring License on plugin.json + every SKILL.md frontmatter; `/plugins/<name>.zip` 401s without a Bearer.
- **`tests/e2e/test_live_helmguild_com_smoke.py`** — 12 tests against `www.helmguild.com`: robots.txt advertises sitemap; sitemap lists every canonical page (including the new mandatory-mentoring post); Atom feed (EN + DE) carries the latest post; each blog post (EN + DE) reachable; RFC pages name AgentSkills + Claude Code plugin docs; Pepe's profile names standards + no longer says "in provisioning".
- All 23 e2e tests pass against production (2 unrelated tests skip when `AMMP_ANTHROPIC_API_KEY` is absent).

## [0.11.0] — 2026-05-14

### Added

- **`GET /robots.txt`** — crawler-friendly file with `User-agent: *`, `Allow: /`, `Disallow: /mcp/`, `Disallow: /plugins/`, and a `Sitemap:` pointer to `/sitemap.xml`. Bots welcome on the landing; auth-gated MCP transport + plugin zips disallowed to save crawler budget. Tested. (#seo)
- **`GET /sitemap.xml`** — XML sitemap listing `/`, `/de/`, and `/.well-known/agent.json`, with `xhtml:link` hreflang alternates on the landing entries (en + de + x-default). Tested. (#seo)
- **README diagrams extended**: the component diagram now shows the two plugin marketplaces (private/commercial `helmguild-plugins` + public/community `helmguild-plugins-public`), the marketplace clones under `~/.ammp/marketplaces/`, the `GetPluginArchive` zip side-channel, and the bundled `.mcp.json` wire-back from the mentee's installed plugin to this server. Two new sequence diagrams added: end-to-end plugin install via `GetPluginArchive`, and the `EscalateToHumanMentor` sync-or-pending Telegram flow. Verified rendering via `mmdc`. (#docs)

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
- **`.mcpb` desktop bundle** at `GET /desktop-bundle.mcpb` — a Claude Cowork extension built on the fly from a pure Node stdio→Streamable-HTTP MCP proxy (~100 lines, no deps). Replaces `mcp-remote` for the Bearer-token case (Claude Cowork's extension sandbox blocks `mcp-remote`'s localhost OAuth-callback bind). (#desktop)

### Changed

- **Auth flows via HTTP `Authorization: Bearer` header**, not a tool parameter. `_authenticate` reads the token from the request via FastMCP's request-scoped context; the LLM never sees an `api_key` argument and so never asks for one. (#auth)
- **`ListMentors` returns work-instruction summaries**, not full bodies. Previous behaviour inflated the response to ~100 KB which tripped Claude Cowork's MCP-transport timeout on connect. Full bodies fetched on demand via `GetPlaybook` / `GetWorkInstruction`. (#listmentors)
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
- **Stdio MCP transport.** New `AMMP_TRANSPORT={http,stdio}` setting and `ammp system serve --stdio` flag. Stdio mode quiets the root logger to stderr so the wire stays clean MCP JSON-RPC. Suits Claude Cowork / Claude Code subprocess-MCP hosts. (#stdio)
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
