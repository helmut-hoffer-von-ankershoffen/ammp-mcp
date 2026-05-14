# AGENTS.md — `ammp-mcp`

Operator guide for AI agents (and humans) editing this repo. Match the project's tone: terse, factual, no decoration.

## What this is

Reference implementation of **AMMP** (the Agentic Mentor-Mentee Protocol — Mentoring track), an open IETF Internet-Draft for agentic mentoring + on-demand engineering review. Implemented as a FastMCP server exposing eleven MCP tools:

* Five AMMP §5 baseline operations — `ListPlaybooks`, `GetPlaybook`, `SearchPlaybooks`, `AskMentor`, `EscalateToHuman`.
* Server-side extensions — `ListMentors` (mentee enumerates mentors over the same wire), `GetSkill` (fetches one skill body by `(playbook_id, id)` without a whole-playbook round-trip — aligned with the open [AgentSkills](https://agentskills.io/home) standard), `GetPluginArchive` (Bearer-token-gated zip download URL for plugin-backed playbooks the mentee can't clone from GitHub), `EscalateToHumanMentor` (long-running tool that forwards a B.h-approved question to A.h via a delivery adapter and returns A.h's reply synchronously), `GetEscalation` (poll a pending escalation), and `GetSystemInfo` (diagnostic metadata).

The corpus is a two-level hierarchy: each mentor has zero or more **playbooks** (areas of practice; each is one subdirectory with `playbook.json`), and each playbook contains zero or more **skills**. Skills load either from an AgentSkills plugin (when `playbook.json` carries an optional `plugin: "<name>@<marketplace>"` field — bodies live in `<marketplaces_root>/<marketplace>/plugins/<plugin>/skills/<id>/SKILL.md`) or from local `NN-*.md` files alongside the `playbook.json`.

- IETF draft: `https://www.helmguild.com/rfc/ammp/`
- Deployed instance: `https://mcp.helmguild.com/ammp` (one specific deployment under the gateway hostname; the implementation is protocol-named, not persona-named).
- License: MIT.

## Architecture (modulith)

```
src/ammp_mcp/
├── __init__.py            # package metadata (__version__, __ammp_draft__)
├── __main__.py            # `python -m ammp_mcp` entry
├── cli.py                 # Root Typer app — wires sub-apps + top-level aliases (thin shell)
├── server.py              # FastMCP factory + tool handlers
├── settings.py            # pydantic-settings root config (AMMP_DIR + leaves)
├── audit.py               # Hash-only audit log (cross-cutting)
├── llm.py                 # Legacy v0.2 shim (kept for backwards compat)
├── models.py              # Response envelopes only (PlaybookSummary, AskMentorResponse, …)
│
├── _data/                 # Packaged data shipped with the wheel
│   ├── __init__.py        # `example_mentor_path()` — resolves to the dir below
│   └── example_mentor/    # Reference corpus copied into ~/.ammp/mentors/example/ on bootstrap
│
├── mentor/                # Mentor domain
│   ├── _models.py         # Mentor + BackendConfig discriminated union
│   ├── _service.py        # load_mentors, get_mentor
│   ├── _cli.py            # `ammp mentor list`
│   └── CLAUDE.md
│
├── mentee/                # Mentee domain
│   ├── _models.py         # Mentee
│   ├── _service.py        # load_mentees, save_mentees, hash_api_key, find_mentee_by_api_key
│   ├── _cli.py            # `ammp mentee list/add/remove/rotate-key/check-key`
│   └── CLAUDE.md
│
├── playbook/              # Playbook + Skill domain (AgentSkills-aligned)
│   ├── _service.py        # Playbook + Skill dataclasses; load_playbooks (resolves plugin: refs), search, keyword_rank, safe_id
│   ├── _cli.py            # `ammp playbook list/show/search`, `ammp instruction list/show`
│   └── CLAUDE.md
│
├── escalation/            # Mentor-mediated escalation (B.a → A.h via A.a)
│   ├── _models.py         # Escalation pydantic model + status enum
│   ├── _service.py        # EscalationStore (jsonl) + EscalationBroker (asyncio.Event registry)
│   ├── _cli.py            # `ammp escalation list/show/answer/cancel`
│   └── _adapters/
│       ├── _base.py       # DeliveryAdapter ABC + DeliveryError
│       ├── _log.py        # No-op default (outbound → logs, no inbound)
│       └── _telegram.py   # Bot API sendMessage + getUpdates long-poll
│
├── system/                # Install-level operations
│   ├── _setup_service.py  # bootstrap_ammp_dir + wizard helpers
│   ├── _status_service.py
│   ├── _health_service.py
│   ├── _usage_service.py
│   ├── _capability_service.py
│   ├── _cli.py            # `ammp system setup/status/health/usage/capability/serve`
│   └── CLAUDE.md
│
└── backends/              # Mentor backend plugins
    ├── base.py            # MentorBackend ABC + LLMAnswer
    ├── anthropic.py       # Stateless Claude
    ├── openclaw.py        # POST to live agent runtime
    ├── stub.py            # Deterministic test backend
    ├── factory.py         # build_backend() dispatcher
    └── CLAUDE.md
```

**Pattern (Aignostics modulith):**
- Each domain folder has `_service.py` (business logic) and `_cli.py` (Typer subgroup, thin shell).
- Private modules are underscore-prefixed; public API lives in the package's `__init__.py`.
- Cross-cutting concerns (audit, settings, response models, server, the root cli) stay flat.

## Runtime directory (`~/.ammp/`)

Everything the server reads or writes lives under a single directory — `~/.ammp/` by default, overridable with `AMMP_DIR`. The repo ships zero runtime state; the example mentor lives as package data at `src/ammp_mcp/_data/example_mentor/` and is copied into `~/.ammp/mentors/example/` on first run.

```
~/.ammp/
├── config.env           # auto-loaded by pydantic-settings (then `.env` in cwd as fallback)
├── mentors/             # one subdir per mentor (slug = dirname)
├── mentees.json         # Bearer-key allowlist (SHA-256 hashes only)
├── audit.log            # hash-only audit log
└── escalations.jsonl    # mentor-mediated escalations (one record per line, status state machine)
```

`ammp serve` calls `system._setup_service.bootstrap_ammp_dir()` on every boot — idempotent, exits early when the tree is already set up. The bootstrap only seeds the example mentor when `AMMP_MENTORS_ROOT` is the default (`<AMMP_DIR>/mentors`); operators who have pointed it at an Obsidian vault or other curated location see no example-mentor write into their tree.

Each leaf can still be overridden individually (`AMMP_MENTORS_ROOT`, `AMMP_MENTEES_FILE`, `AMMP_AUDIT_LOG_PATH`) for installs that want one piece in a different place. The production LaunchAgent does this — points `AMMP_MENTORS_ROOT` at an Obsidian vault while keeping `mentees.json` and `audit.log` under `~/.ammp/`.

## Conventions (load-bearing)

- **Subject-then-action CLI**: `ammp mentor list` not `ammp list mentors`. Top-level aliases exist for the most-used system verbs (`ammp serve`, `ammp setup`, etc.) — canonical home stays on `ammp system <verb>`.
- **Auto-bootstrap on `ammp serve`**: a fresh install with no `~/.ammp/` boots cleanly anyway; the server creates the tree, copies the example mentor, writes `config.env`. The wizard (`ammp setup`) adds backend choice + first mentee on top of that.
- **Pluggable backends**: each mentor's `mentor.json` selects its answer engine via `backend.kind ∈ {anthropic, openclaw, stub}`. The factory in `backends/factory.py` is the only place that constructs concrete backends.
- **Mentee allowlist**: SHA-256-hashed Bearer keys in `mentees.json`. Plaintext keys are shown once at mint time. `hash_api_key` uses plain SHA-256 — not a slow KDF — because the inputs are 256-bit OS-CSPRNG random tokens, not human-chosen passwords. CodeQL flags this as a false positive; the rationale lives in `mentee/_service.py:hash_api_key.__doc__` and the dismissal lives on the alert in GitHub.
- **Hash-only audit log**: `audit.log` records `<ts> op=<op> mentor=<slug> mentee=<slug> hash=<8 hex>` and never any payload. `ammp system usage` aggregates it without leaking content.
- **No-retention**: the server never persists mentee questions or full context past the audit-log hash. Privacy posture is advertised in `/.well-known/agent.json`.
- **`ListMentors` is a server-side extension** over AMMP-01's five §5 ops. Same data the capability JSON publishes, exposed over the MCP wire so mentees don't need an out-of-band HTTP fetch. The envelope embeds each mentor's full playbook list (every playbook with all its skill bodies) so a single call can cold-start a mentee.
- **`GetSkill` is a server-side extension** that lets a mentee fetch one specific skill body by `(playbook_id, id)` — useful once `ListMentors` or `ListPlaybooks` has given the mentee the index. Aligned with the open [AgentSkills](https://agentskills.io/home) standard the on-disk plugin format follows.
- **`GetPluginArchive` is a server-side extension** that returns a Bearer-token-gated zip download URL (`<public_url>/plugins/<plugin>.zip`) for any plugin referenced by a playbook on this server. Required when the marketplace lives in a private GitHub repo and mentees can't `git clone` the marketplace themselves. The mentee hands the URL + install instructions to its user, who installs the plugin into Claude Code / Desktop by extracting the zip and running `/plugin install <path>`.
- **`EscalateToHumanMentor` is a long-running server-side extension** that forwards a B.h-approved question to A.h. Implemented as an async MCP tool that awaits A.h's reply on an in-memory `asyncio.Event` (registered in `EscalationBroker`); the configured delivery adapter is responsible for both outbound (push X to A.h's channel) and inbound (route A.h's reply back, resolving the broker). Default adapter is `log` (no-op inbound); `telegram` adapter activates when bot token + chat id are set in `config.env`. Cross-compartment consent (B.h must approve forwarding) stays on the mentee side — `AskMentor` low-confidence responses carry a `escalation_to_human_mentor_draft` to surface to B.h, but A.a does not enforce the gate.
- **JSON files written by the CLI use `ensure_ascii=False`** so em-dashes and smart quotes survive round-trips (`mentor.json`, `mentees.json`).
- **`.env` / `config.env` files stay ASCII-only** — pydantic-settings crashes on unicode under ASCII locales.

## Per-module guidance

Each domain folder has its own `CLAUDE.md` with module-specific public API and test coverage. Read those before touching code in that domain. `mentor/CLAUDE.md` is the canonical template.

## Operating playbook

[`OPERATING.md`](OPERATING.md) is the day-to-day operator manual: minting / rotating / revoking mentees, adding / editing mentors and playbooks, which changes need a server restart (mentor.json, env vars) and which don't (mentees, playbooks — both hot-reloaded), health probes, audit-log breakdown, and the deployed-instance shape at `mcp.helmguild.com/ammp`. When in doubt about how to do a runtime task, look there before re-deriving from source.

## CLI reference

Live, generated copy in `CLI_REFERENCE.md`. Regenerate after CLI changes:

```sh
uv run python tools/generate_cli_reference.py
```

(The script wraps `typer ammp_mcp.cli utils docs`.)

## Quality bars (enforced in CI)

- **CI** — `ruff check` + `ruff format --check` + `mypy --strict` + `pytest` on Python 3.11 / 3.12 / 3.13 / 3.14.
- **Audit** — `pip-audit` (CVE scan) + `pip-licenses` (allow-list) + `cyclonedx-py` (CycloneDX SBOM) + Trivy (SARIF).
- **CodeQL** — semantic analysis (currently 0 open alerts; 1 dismissed false-positive on `py/weak-sensitive-data-hashing` — see `mentee/_service.py:hash_api_key`).
- **SonarCloud** — quality gate OK, A/A/A, ≥86 % coverage.

**Releases are blocked on red gates.** Never tag a release on a commit whose required workflows or external gates (Audit / CodeQL / SonarCloud) are not green. Fix the root cause, push, wait for green, *then* tag. The release workflow itself also fails on any sub-step failure — see "Release pipeline" below.

## Release pipeline

PyPI package name is **`ammp`**, *not* `ammp-mcp` (the repo is named after the protocol implementation; the PyPI distribution is named after the protocol). Wheel name resolves to `ammp-<version>-py3-none-any.whl`. Console-script entry point stays `ammp` (Typer root app).

Release order is fixed:

1. **GitHub release** is created first — tag pushed, release notes generated, artefacts uploaded.
2. **PyPI release** runs *only after* the GitHub release has succeeded. The workflow gates the PyPI publish step on the GitHub release step's success.

The release workflow fails as a whole if either the GitHub release or the PyPI publish fails — you cannot end up with a tag on GitHub but no PyPI artefact, or with a PyPI artefact and a broken GitHub release. PyPI badge in the README points at `https://pypi.org/project/ammp/`.

## Protocol additions — sweep all surfaces

Adding a new MCP tool / operation (e.g. `EscalateToHumanMentor`, `GetEscalation`, `GetSystemInfo`) is *not done* until all of these are updated in the same change set:

1. The code + tests.
2. The IETF draft (`draft-ammp-NN`) in `helmut-hoffer-von-ankershoffen/helmguild.com` — list the new op alongside existing ones with the same level of detail. The draft URL is referenced from this repo but the file lives in the helmguild.com repo; cross-repo edits are part of the change.
3. The repo `README.md` — enumerate the new op in the tools / operations section.
4. The CLI surface — `ammp <subject> <action>` parity with sibling ops (e.g. `ammp escalation list/show/answer/cancel`).
5. This `AGENTS.md` (the tool inventory in "What this is").
6. `CLI_REFERENCE.md` — regenerated via `uv run python tools/generate_cli_reference.py`.

Server-side extensions over the AMMP-01 baseline must be labelled as such in both the README and the RFC's "Extensions" section so operators reading the spec can tell baseline ops from vendor extensions.

## Tests

```
tests/
├── conftest.py            # Shared fixtures (isolated_tree, fake mentors/mentees, audit_log_path)
├── unit/                  # ≤1 ms each, pure stdlib
├── integration/           # In-process CliRunner + FastMCP server
└── e2e/                   # Real network (skipped without API keys / a live AMMP endpoint)
```

102 tests + 1 skipped at last refactor. Coverage ≈ 87 %. Run with `uv run pytest -q`.

## Stdio transport

`ammp system serve --stdio` boots the server speaking MCP JSON-RPC over stdin/stdout instead of HTTP. Useful for Claude Code / Claude Desktop integrations that prefer subprocess MCPs. HTTP mode (default) is for shared deployments behind a tunnel like Cloudflare.

## Commit identity

Commit as Helmut, not the OpenClaw VM identity. Use `-c` per invocation rather than editing global config:

```sh
git -c user.email=helmuthva@gmail.com -c user.name='Helmut Hoffer von Ankershoffen' commit -m "..."
```

## Pre-push hook auto-fixes

`pre-commit install --hook-type pre-push` runs ruff + EOF-fixer + trailing-whitespace on push. When it auto-modifies a file, push fails and the tree is dirty. **Always create a new commit** (`git add <fixed> && git commit -m "...: pre-commit hygiene pass"`); never `--amend` (it rewrites prior history). The attribution generator right-strips each line and collapses trailing blanks (`tools/generate_attributions.py:render`) so regens stay hook-clean.

## What lives elsewhere

- The AMMP RFC (`draft-ammp-01`) and the helmguild manifesto live in `helmut-hoffer-von-ankershoffen/helmguild.com`. This repo references the draft URL but doesn't copy it. Keep the spec in sync when extending the protocol surface — the `ListMentors` extension is currently a server-side addition; if it migrates into the draft, update both at the same time.
- The deployed instance (`mcp.helmguild.com/ammp`) is managed by two LaunchAgents on Helmut's Mac — `~/Library/LaunchAgents/com.helmguild.ammp-mcp.plist` and `com.helmguild.cloudflared-ammp.plist`. The Cloudflare Tunnel routes the hostname `mcp.helmguild.com` to the local server; the `/ammp` prefix is enforced at the FastMCP app level via `AMMP_MOUNT_PATH`. Not represented in this repo.
- Pepe Arturo (one example mentor running on this server) is a separate persona; his corpus lives in Helmut's Obsidian vault (`~/Obsidian/vaults/AI Agents Memory/Pepe Arturo/Mentorship/ammp-corpus/pepe/`), pulled in via `AMMP_MENTORS_ROOT`. The implementation itself stays protocol-named, never persona-named.
