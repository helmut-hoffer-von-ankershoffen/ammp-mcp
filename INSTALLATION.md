# Installation

How to get an `ammp-mcp` server running, on your laptop or behind a tunnel, and how to point a mentee agent at it.

The five-minute path is at the top. Deeper paths follow.

---

## Prerequisites

- **Python 3.11+** (3.11 / 3.12 / 3.13 / 3.14 supported and CI-tested).
- **[uv](https://docs.astral.sh/uv/)** — recommended. `pip` works too.
- (Optional) An Anthropic API key if you want real LLM synthesis for `AskMentor`. Without one, the server falls back to a deterministic stub at confidence 0.2 (which always triggers an escalation suggestion).

---

## Five-minute local install

```bash
git clone https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp
cd ammp-mcp

# One command. `ammp serve` auto-bootstraps `~/.ammp/` on first run
# (copies the shipped example mentor, writes a config.env scaffold)
# then starts serving on 127.0.0.1:8765.
uv run ammp serve

# Or run the interactive wizard first — pick a backend, mint a mentee:
uv run ammp setup        # writes ~/.ammp/config.env, mints first mentee
uv run ammp status       # validates the install
uv run ammp serve
```

In another shell, confirm the server advertises its capability:

```bash
curl -s http://127.0.0.1:8765/.well-known/agent.json | python -m json.tool
```

You should see a JSON document naming the loaded mentors, advertising the AMMP draft version, and listing the six MCP operations (`ListMentors` + the five AMMP §5 Mentoring-track operations).

---

## What `ammp setup` did

The wizard is **idempotent** — safe to re-run.

1. Bootstrapped `~/.ammp/` (or wherever `AMMP_DIR` points). Created the directory, copied the shipped example mentor into `~/.ammp/mentors/example/`, and wrote a minimal `~/.ammp/config.env` scaffold. `ammp serve` does this same bootstrap automatically if you skip the wizard.
2. Validated the mentor directory at `<AMMP_DIR>/mentors/<default>/` (default `example`). To run your own mentor, copy the scaffold: `cp -r ~/.ammp/mentors/example ~/.ammp/mentors/yourname`, edit `mentor.json`, and replace the playbooks with your own. Or set `AMMP_MENTORS_ROOT` to point at an existing curated location (e.g. an Obsidian vault) — the example mentor is only seeded into the default location, never into operator-curated paths.
3. Wrote `<mentor>/mentor.json` with `backend.kind = openclaw` (live runtime). Override with `--backend anthropic` (stateless Claude) or `--backend stub` (offline tests).
4. Minted a first mentee `claude-cowork-helmut`, stored only the SHA-256 hash, **printed the plaintext API key once**. Copy it now — there is no way to recover it later.
5. Wrote the full `~/.ammp/config.env` with the required env vars (mentors_root, mentees_file, audit_log_path, plus the secret name your backend needs).

To restart the wizard without prompts, run `uv run ammp setup --yes`.

---

## Connect a mentee — Claude Code (local)

This is the most common deployment: your local Claude Code talking to a local ammp-mcp.

```bash
# Register the local server at user scope so it's available from any cwd.
# Replace ammp-… with the plaintext key from `ammp setup`.
claude mcp add --scope user ammp-pepe http://127.0.0.1:8765/mcp/ \
  --header "Authorization: Bearer ammp-…"

# Sanity-check.
claude mcp list | grep ammp-pepe
```

Then from inside Claude Code, the six MCP tools (`ListMentors`, `ListPlaybooks`, `GetPlaybook`, `SearchPlaybooks`, `AskMentor`, `EscalateToHuman`) are available. Try:

> *Use the AskMentor tool against mentor `example`: "how do you stay grounded under ambiguity?"*

The mentor's answer will cite the relevant playbooks from its corpus. If confidence drops below the mentor's threshold, the response will also recommend `EscalateToHuman` with suggested phrasing.

---

## Connect a mentee — Claude Code (remote / deployed instance)

If `ammp-mcp` is deployed behind a public tunnel (e.g. `https://ammp.helmguild.com`):

```bash
claude mcp add --scope user ammp-pepe https://ammp.helmguild.com/mcp/ \
  --header "Authorization: Bearer ammp-…"
```

Tunnel setup is out of scope for this guide — see `docs/deployment.md` for the Cloudflare Tunnel pattern Helmut uses.

---

## Connect a mentee — Claude Desktop (stdio)

For Claude Desktop and similar hosts that prefer subprocess-MCP over HTTP, run the server in **stdio mode**:

```jsonc
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "ammp-pepe": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/ammp-mcp", "ammp", "system", "serve", "--stdio"],
      "env": {
        "AMMP_REQUIRE_AUTH": "false"
      }
    }
  }
}
```

`AMMP_REQUIRE_AUTH=false` is the conventional choice for subprocess transport because the parent process is already the trust boundary — there's no network to authenticate.

---

## Configuration

All settings are env vars prefixed `AMMP_`. The server auto-loads `<AMMP_DIR>/config.env` first (the canonical location), then `.env` in cwd as a fallback for dev clones.

| Var | Default | Notes |
|---|---|---|
| `AMMP_DIR` | `~/.ammp` | Single directory holding `config.env`, `mentors/`, `mentees.json`, `audit.log`. Override to relocate the whole tree. |
| `AMMP_TRANSPORT` | `http` | `http` or `stdio`. Stdio wins over `--host` / `--port`. |
| `AMMP_HOST` | `127.0.0.1` | HTTP bind host. Use `0.0.0.0` only behind a reverse proxy. |
| `AMMP_PORT` | `8765` | HTTP bind port. |
| `AMMP_PUBLIC_URL` | `http://127.0.0.1:8765` | Public URL advertised in the capability JSON. Set to `https://ammp.helmguild.com` (or your own) when behind a tunnel. |
| `AMMP_MENTORS_ROOT` | `<AMMP_DIR>/mentors` | Directory with one subfolder per mentor. Override to point at e.g. an Obsidian vault. |
| `AMMP_DEFAULT_MENTOR` | `example` | Mentor slug used when a request omits `mentor=`. |
| `AMMP_MENTEES_FILE` | `<AMMP_DIR>/mentees.json` | Allowlist (SHA-256 hashes only). |
| `AMMP_AUDIT_LOG_PATH` | `<AMMP_DIR>/audit.log` | Hash-only audit log. |
| `AMMP_REQUIRE_AUTH` | `false` | Bearer-key auth on incoming MCP calls. Default is off for localhost dev; flip on for production. |
| `AMMP_ANTHROPIC_API_KEY` | (empty) | Needed when any mentor uses `backend.kind = anthropic`. |
| `AMMP_LLM_MODEL` | `claude-opus-4-7` | Override per-mentor via `backend.model`. |
| `AMMP_LLM_MAX_CONCURRENT` | `10` | Server-wide cap on in-flight backend calls. |
| `AMMP_LLM_TIMEOUT_SECONDS` | `30.0` | Per-call HTTP timeout for the Anthropic backend. |
| `AMMP_LLM_CONFIDENCE_THRESHOLD` | `0.6` | Below this self-reported confidence, `AskMentor` adds a mentor-triggered `EscalateToHuman` recommendation. |

`.env` files must be **ASCII-only** — pydantic-settings via python-dotenv crashes on unicode in `.env` under ASCII locales.

---

## Switching backends

Each mentor selects its backend in `mentor.json`:

```jsonc
{
  "slug": "your-mentor",
  "name": "Your Mentor's Display Name",
  "persona": "...",
  "confidence_threshold": 0.6,
  "backend": {
    "kind": "openclaw",                                       // or "anthropic" or "stub"
    "url": "https://openclaw.example/ammp/ask",
    "auth_bearer_env": "OPENCLAW_BEARER",
    "timeout_seconds": 60,
    "max_concurrent": 10
  }
}
```

- **`openclaw`** — POST the question to a live agent runtime. The runtime answers with mentor memory + lived context. Production default.
- **`anthropic`** — Stateless Claude. Cheap, predictable, no continuity between calls. Good for read-only / first-pass.
- **`stub`** — Deterministic offline backend. Always returns confidence 0.2 (triggers escalation). Test fixture.

Re-run `ammp status` after switching to confirm the env vars and registries pick up the change.

---

## Development setup

```bash
git clone https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp
cd ammp-mcp

uv sync --extra dev               # install dev deps
uv run pre-commit install --hook-type pre-push    # block pushes with broken lint/types

uv run ruff check . && uv run mypy src && uv run pytest -q
```

After CLI changes, regenerate the reference:

```bash
uv run python tools/generate_cli_reference.py
```

After dependency bumps, regenerate attributions:

```bash
uv run python tools/generate_attributions.py
```

CI runs the same three commands (`ruff`, `mypy`, `pytest`) on a 4-Python matrix plus an audit / CodeQL / SonarCloud pass. Push to `main` is gated on all of them.

---

## Persistent local deployment (macOS)

For a stay-alive local deployment (e.g. behind a Cloudflare Tunnel), write a **LaunchAgent** instead of relying on `nohup ... &` — backgrounded shells don't survive reboots or login cycles.

Helmut's setup runs two LaunchAgents:

- `com.helmguild.ammp-mcp` — `ammp serve` on `127.0.0.1:8765`, `KeepAlive` on non-zero exit.
- `com.helmguild.cloudflared-ammp` — Cloudflare Tunnel routing `ammp.helmguild.com` → `http://127.0.0.1:8765`.

Templates live in `docs/deployment.md`.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ammp setup` errors with `No mentor directory found` | `mentors/<default>/` doesn't exist. Copy `mentors/example/` as a starting template, or point `AMMP_MENTORS_ROOT` at the directory that contains your mentor. |
| `ammp serve` boots but `AskMentor` returns `confidence=0.2` always | No `AMMP_ANTHROPIC_API_KEY` set; falling back to the deterministic stub. Set the key in `.env` and restart. |
| `claude mcp list` shows `ammp-pepe` but tools error | Bearer key wrong. Re-mint via `ammp mentee rotate-key <slug>` and update the `--header` flag. |
| `ammp status` warns about a missing env var | A mentor's `backend.auth_bearer_env` points at an unset variable. Add it to `.env`. |
| Cache stale after Cloudflare Tunnel restart | Visitor browsers cache the capability JSON for 10 min. Flush with `curl -X PURGE …` or just wait. |
| Server unreachable from outside despite tunnel running | Cloudflare DNS still on grey-cloud. Flip to orange-cloud in the Cloudflare UI; the tunnel's `cfargotunnel.com` target requires the proxy. |

---

## Next steps

- Read `AGENTS.md` for the architecture and conventions.
- Read `docs/CLI_REFERENCE.md` for the full Typer surface.
- Read `SECURITY.md` for the privacy / threat model.
- Read the AMMP draft at `https://www.helmguild.com/rfc/ammp/` for the protocol itself.
