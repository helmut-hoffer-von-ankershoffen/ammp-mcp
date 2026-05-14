# Operating `ammp-mcp` — day-to-day playbook

Reference for the operator running an `ammp-mcp` deployment. Pairs with [`INSTALLATION.md`](INSTALLATION.md) (how to install) and [`README.md`](README.md) (what the project is). Everything below assumes you've already done the install and have the `ammp` CLI on `$PATH`.

The cheat sheet at the top of every section is `make` targets where available, raw `ammp` invocations everywhere else.

## What needs a restart, what doesn't

| Change | Restart needed? | Why |
|---|---|---|
| `ammp mentee add` / `rotate-key` / `remove` | **No** | `mentees.json` is mtime-cached and hot-reloaded by `_authenticate` on each request. |
| Edit a playbook (`<mentor>/playbooks/*.md`) | **No** | `load_corpus` is called per request. Add a file, edit one, rename one — visible on the next call. |
| Edit `mentor.json` (persona, threshold, backend block) | **Yes** | Mentor + backend instances are cached at boot. Backends hold sockets / semaphores; rebuilding mid-process is not yet supported. |
| Add a new mentor directory | **Yes** | Same reason. |
| Change `config.env` / `AMMP_*` env vars | **Yes** | Settings are read once at boot. |
| Update the package (new release) | **Yes** | Reinstall the wheel and restart so the new code paths run. |

### Restart commands

| Deployment style | Command |
|---|---|
| macOS LaunchAgent | `launchctl kickstart -k gui/$(id -u)/<your-launchagent-label>` |
| Docker | `docker restart <container>` |
| systemd | `systemctl restart <unit>` |
| Local dev (foreground) | `Ctrl-C` and re-run `make serve` / `uv run ammp serve` |

---

## Mint a token for a new mentee

Triggered by a token request landing in your inbox (the landing page's **Request access** CTA pre-fills a mailto with five fields). Map the email's fields to CLI args as follows:

| Email field | CLI arg | Notes |
|---|---|---|
| Your name | derive slug + operator | `name=Sandra` + `runtime=claude-cowork` → `slug=claude-cowork-sandra`, `--operator human:sandra` |
| Where I'll connect from | `--runtime` | Pick the canonical kebab — `claude-ai`, `claude-cowork`, `claude-code`, `openclaw`, `hermes` |
| Secure delivery channel | (out-of-band) | Use this to deliver the token. **Never** plain email / Slack / SMS |
| Mentor of interest | n/a | Mentee gets access to every mentor on the server; field is just context |
| Anything else? | n/a | Operator calibration only — no field flows into the CLI |

Then:

```bash
uv run ammp mentee add <slug> \
  --operator human:<owner> \
  --runtime <claude-ai|claude-cowork|claude-code|openclaw|hermes>
```

The CLI mints a fresh `ammp-<32 url-safe bytes>` token, stores **only its SHA-256** in `mentees.json`, and **prints the plaintext token ONCE** — re-derivation is not possible. Copy that line straight into the secure channel the requester specified (Signal, iMessage, 1Password share). Never plain email / Slack / SMS.

**No restart required** — the next authenticated request the server handles will hot-reload `mentees.json` (mtime-cached) and recognise the new mentee.

```bash
uv run ammp mentee list           # see the current allowlist (hashes only)
```

## Rotate a leaked token

```bash
uv run ammp mentee rotate-key <slug>
```

Same shape as `add`: prints a fresh plaintext token once, swaps the SHA-256 on disk. The old token immediately stops working on the next request (mtime-cache invalidates).

## Revoke a mentee

```bash
uv run ammp mentee remove <slug>
```

Removes the entry from `mentees.json`. Their token stops working on the next request.

## Resolve a plaintext token back to a mentee

Sometimes a mentee says "auth_failed" and you don't know which token they're holding. Paste the plaintext key in:

```bash
uv run ammp mentee check-key <ammp-...>
```

The CLI hashes the input and tells you which mentee slug (if any) it maps to. Nothing is written to disk.

---

## Add / edit / replace a mentor

A mentor is one directory under `<AMMP_DIR>/mentors/<slug>/` with `mentor.json` and `playbooks/*.md`. See [`examples/mentor.example.json`](examples/mentor.example.json) for the schema; [`examples/playbook.example.md`](examples/playbook.example.md) for the playbook markdown shape.

```bash
# Start from the shipped scaffold:
cp -r <AMMP_DIR>/mentors/example <AMMP_DIR>/mentors/<your-slug>
$EDITOR <AMMP_DIR>/mentors/<your-slug>/mentor.json
$EDITOR <AMMP_DIR>/mentors/<your-slug>/playbooks/*.md
```

If `mentor.json` itself changed (persona, threshold, backend block) **restart the server**. Playbook edits land without restart.

The interactive wizard can also flip an existing mentor between backend kinds — handy when promoting from `stub` to `anthropic` or `openclaw`:

```bash
uv run ammp setup --backend openclaw \
  --openclaw-url https://<your-openclaw-host>/ammp/ask \
  --auth-bearer-env OPENCLAW_BEARER
```

The wizard is idempotent; re-run any time.

## Edit Pepe's playbooks (this deployment specifically)

Pepe's mentor lives at `~/Obsidian/vaults/AI Agents Memory/Pepe Arturo/Mentorship/ammp-corpus/pepe/` — `AMMP_MENTORS_ROOT` in the LaunchAgent points there. The corpus is a two-level hierarchy:

```
pepe/
├── mentor.json                 # name, description, human_mentor, persona, backend
├── avatar.jpg                  # 256-512px square (served at /mentors/pepe/avatar)
└── playbooks/
    ├── <playbook-slug>/
    │   └── playbook.json       # name, description, optional plugin: "<name>@<marketplace>"
    └── ...
```

Each `playbook.json` carries an optional `plugin: "<plugin-name>@<marketplace-name>"` reference. When set, skill bodies load from `<AMMP_MARKETPLACES_ROOT>/<marketplace>/plugins/<plugin>/skills/<id>/SKILL.md` (AgentSkills format); when absent, the loader falls back to legacy `NN-*.md` files alongside the `playbook.json`.

For Pepe's deployment, all three playbooks (`multi-channel-content-pipelines`, `operator-craft`, `personal-assistant-for-managers`) are plugin-backed and live in the private `helmut-hoffer-von-ankershoffen/helmguild-plugins` marketplace cloned at `~/.ammp/marketplaces/helmguild-plugins/`.

Editing a `SKILL.md` body in the marketplace clone is visible on the next `GetPlaybook` / `ListPlaybooks` / `AskMentor` call — no restart. Adding/removing a whole playbook subdirectory likewise needs no restart. Editing `pepe/mentor.json` or any `playbook.json` (name/description/plugin-ref metadata) needs a restart.

---

## Mentor-mediated escalation (B.a → A.h via A.a)

When `AskMentor` returns low-confidence and the mentor has a `human_mentor` configured (Helmut, in Pepe's case), the response carries an `escalation_to_human_mentor_draft`. The mentee (B.a) is expected to:

1. Show the draft to its own operator (B.h) and get approval (possibly with edits to X).
2. Call the new `EscalateToHumanMentor(question=X, mentor=…)` tool. This is **long-running** — the call blocks until A.h replies (24h default timeout), emitting MCP `notifications/progress` along the way.

Server side: the question is persisted to `<AMMP_DIR>/escalations.jsonl`, handed to the configured delivery adapter, then the handler awaits A.h's reply on an in-memory `asyncio.Event`. When A.h replies, the event fires and the tool returns the answer to B.a.

Delivery adapters (`AMMP_ESCALATION_ADAPTER`):

* `log` (default) — outbound writes to the server log; **no inbound**. Use this until Telegram is wired. Resolve manually with `ammp escalation answer <id> "..."`.
* `telegram` — outbound via Telegram Bot API `sendMessage` to a configured chat id; inbound via long-polled `getUpdates`. A.h replies in Telegram (reply-to-message), the bot routes the reply back to the waiting tool call. Requires `AMMP_ESCALATION_TELEGRAM_BOT_TOKEN` + `AMMP_ESCALATION_TELEGRAM_CHAT_ID`.

Operator surface:

```bash
ammp escalation list                    # all escalations + status
ammp escalation list --status pending   # filter
ammp escalation show <id-prefix>        # full record (question + answer)
ammp escalation answer <id> "..."       # manually inject A.h's reply (log adapter)
ammp escalation cancel <id>             # mark cancelled
```

`answer` / `cancel` update the persistent store but **do not wake** an in-flight broker waiter in the running server — those resolve only via the live delivery adapter's inbound path. For manual injection while a B.a call is blocking, restart the server (the boot path marks orphan pending escalations as `expired`, so B.a will see the timeout) or wait for the configured timeout.

Per AMMP §3.4, the cross-compartment forward is only legitimate when B.h has approved it. ammp-mcp does not enforce that gate — B.a is responsible for getting consent before invoking the tool.

---

## Health, status, and usage

```bash
make status        # static install validation (paths, registries, env vars)
make capability    # offline render of /.well-known/agent.json
ammp system health # active probe — GET /.well-known/agent.json + per-backend HEAD
```

External:

```bash
curl -sf https://<public-host>/.well-known/agent.json | jq .
curl -sf https://<public-host>/                       # human landing page
```

Audit-log breakdown (hash-only — no payloads):

```bash
ammp usage --days 7                  # last week, by op + by mentor + by mentee
ammp usage --days 0                  # all time
```

## Reset / wipe state

The runtime tree lives under `<AMMP_DIR>` (default `~/.ammp/`). To start fresh:

```bash
rm -rf ~/.ammp
ammp serve     # auto-bootstraps a new ~/.ammp/ with the example mentor
```

You'll need to re-mint any mentees (the SHA-256 hashes go away with the file).

---

## Production deployment (this repo's instance)

The deployed instance at `https://mcp.helmguild.com/ammp` runs as two macOS LaunchAgents on Helmut's Mac:

- `com.helmguild.ammp-mcp` — `~/.openclaw/workspace/repos/ammp-mcp/.venv/bin/ammp serve` on `127.0.0.1:8765`. Env vars in the plist pin `AMMP_REQUIRE_AUTH=true`, `AMMP_PUBLIC_URL=https://mcp.helmguild.com/ammp`, `AMMP_MOUNT_PATH=/ammp`, `AMMP_DEFAULT_MENTOR=pepe`, `AMMP_MENTORS_ROOT=…/Pepe Arturo/Mentorship/ammp-corpus`, and `AMMP_MENTEES_FILE=…/repos/ammp-mcp/mentees.json`. The mount prefix lets `mcp.helmguild.com` host sibling MCP servers later under `/<other>/`.
- `com.helmguild.cloudflared-ammp` — Cloudflare Tunnel routing `mcp.helmguild.com` → `http://127.0.0.1:8765`.

Logs at `~/Library/Logs/ammp-mcp.{out,err}.log`. Reload either with `launchctl unload <plist> && launchctl load <plist>`, or `launchctl kickstart -k gui/$(id -u)/com.helmguild.ammp-mcp` for an in-place restart.

For Docker / cross-platform deployments, see the [Docker section in `README.md`](README.md#docker).
