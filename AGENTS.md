# AGENTS.md — `ammp-mcp`

Operator guide for AI agents (and humans) editing this repo. Match the project's tone: terse, factual, no decoration.

## What this is

Reference implementation of **AMMP** (the Agentic Mentor-Mentee Protocol — Mentoring track), an open IETF Internet-Draft for agentic mentoring + on-demand engineering review. Implemented as a FastMCP server exposing five operations (`ListPlaybooks`, `GetPlaybook`, `SearchPlaybooks`, `AskMentor`, `EscalateToHuman`).

- IETF draft: `https://www.helmguild.com/rfc/ammp/`
- Deployed instance: `https://ammp.helmguild.com` (one specific deployment; the implementation is protocol-named, not persona-named).
- License: MIT.

## Architecture (modulith)

```
src/ammp_mcp/
├── __init__.py            # package metadata (__version__, __ammp_draft__)
├── __main__.py            # `python -m ammp_mcp` entry
├── cli.py                 # Root Typer app — wires sub-apps + top-level aliases (thin shell)
├── server.py              # FastMCP factory + tool handlers
├── settings.py            # pydantic-settings root config
├── audit.py               # Hash-only audit log (cross-cutting)
├── llm.py                 # Legacy v0.2 shim (kept for backwards compat)
├── models.py              # Response envelopes only (PlaybookSummary, AskMentorResponse, …)
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
├── playbook/              # Playbook domain
│   ├── _service.py        # Playbook dataclass + load_corpus, search, keyword_rank, safe_id
│   ├── _cli.py            # `ammp playbook list/show`
│   └── CLAUDE.md
│
├── system/                # Install-level operations
│   ├── _setup_service.py
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

## Conventions (load-bearing)

- **Subject-then-action CLI**: `ammp mentor list` not `ammp list mentors`. Top-level aliases exist for the most-used system verbs (`ammp serve`, `ammp setup`, etc.) — canonical home stays on `ammp system <verb>`.
- **Pluggable backends**: each mentor's `mentor.json` selects its answer engine via `backend.kind ∈ {anthropic, openclaw, stub}`. The factory in `backends/factory.py` is the only place that constructs concrete backends.
- **Mentee allowlist**: SHA-256-hashed Bearer keys in `mentees.json`. Plaintext keys are shown once at mint time. `hash_api_key` uses plain SHA-256 — not a slow KDF — because the inputs are 256-bit OS-CSPRNG random tokens, not human-chosen passwords. CodeQL flags this as a false positive; the rationale lives in `mentee/_service.py:hash_api_key.__doc__` and the dismissal lives on the alert in GitHub.
- **Hash-only audit log**: `audit.log` records `<ts> op=<op> mentor=<slug> mentee=<slug> hash=<8 hex>` and never any payload. `ammp system usage` aggregates it without leaking content.
- **No-retention**: the server never persists mentee questions or full context past the audit-log hash. Privacy posture is advertised in `/.well-known/agent.json`.
- **JSON files written by the CLI use `ensure_ascii=False`** so em-dashes and smart quotes survive round-trips (`mentor.json`, `mentees.json`).
- **`.env` files stay ASCII-only** — pydantic-settings crashes on unicode in `.env` under ASCII locales.

## Per-module guidance

Each domain folder has its own `CLAUDE.md` with module-specific public API and test coverage. Read those before touching code in that domain. `mentor/CLAUDE.md` is the canonical template.

## CLI reference

Live, generated copy in `docs/CLI_REFERENCE.md`. Regenerate after CLI changes:

```sh
uv run python scripts/generate_cli_reference.py
```

(The script wraps `typer ammp_mcp.cli utils docs`.)

## Quality bars (enforced in CI)

- **CI** — `ruff check` + `ruff format --check` + `mypy --strict` + `pytest` on Python 3.11 / 3.12 / 3.13 / 3.14.
- **Audit** — `pip-audit` (CVE scan) + `pip-licenses` (allow-list) + `cyclonedx-py` (CycloneDX SBOM) + Trivy (SARIF).
- **CodeQL** — semantic analysis (currently 0 open alerts; 1 dismissed false-positive on `py/weak-sensitive-data-hashing` — see `mentee/_service.py:hash_api_key`).
- **SonarCloud** — quality gate OK, A/A/A, ≥86 % coverage.

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

`pre-commit install --hook-type pre-push` runs ruff + EOF-fixer + trailing-whitespace on push. When it auto-modifies a file, push fails and the tree is dirty. **Always create a new commit** (`git add <fixed> && git commit -m "...: pre-commit hygiene pass"`); never `--amend` (it rewrites prior history). The attribution generator right-strips each line and collapses trailing blanks (`scripts/generate_attributions.py:render`) so regens stay hook-clean.

## What lives elsewhere

- The AMMP RFC (`draft-ammp-01`) and the helmguild manifesto live in `helmut-hoffer-von-ankershoffen/helmguild.com`. This repo references the draft URL but doesn't copy it.
- The deployed instance (`ammp.helmguild.com`) is managed by two LaunchAgents on Helmut's Mac — `~/Library/LaunchAgents/com.helmguild.ammp-mcp.plist` and `com.helmguild.cloudflared-ammp.plist`. Not represented in this repo.
- Pepe Arturo (one example mentor running on this server) is a separate persona; this repo only ships his `mentor.json` + playbooks under `mentors/pepe/`. The implementation itself stays protocol-named, never persona-named.
