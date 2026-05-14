# `ammp`

Housekeeping CLI for the AMMP Mentoring-track reference server.

**Usage**:

```console
$ ammp [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `serve`: Alias for `ammp system serve`.
* `setup`: Alias for `ammp system setup`.
* `status`: Alias for `ammp system status`.
* `health`: Alias for `ammp system health`.
* `usage`: Alias for `ammp system usage`.
* `capability`: Alias for `ammp system capability`.
* `mentor`: Inspect registered mentors; ask one a...
* `mentee`: Manage the mentee allowlist (add / remove...
* `playbook`: Inspect and read a mentor&#x27;s playbooks...
* `skill`: Inspect and read individual skills inside...
* `escalation`: Inspect and manage mentor-mediated...
* `system`: Operate the install as a whole (setup,...

## `ammp serve`

Alias for `ammp system serve`.

**Usage**:

```console
$ ammp serve [OPTIONS]
```

**Options**:

* `--stdio`: Speak MCP JSON-RPC over stdin/stdout (subprocess transport). Wins over --host/--port and AMMP_TRANSPORT=http when set. Use this for Claude Desktop / Claude Code subprocess MCP integration.
* `--host TEXT`: Override bind host (HTTP transport only).
* `--port INTEGER`: Override bind port (HTTP transport only).  [default: 0]
* `--help`: Show this message and exit.

## `ammp setup`

Alias for `ammp system setup`.

**Usage**:

```console
$ ammp setup [OPTIONS]
```

**Options**:

* `-y, --yes`: Accept all defaults; no prompts.
* `--mentor-slug TEXT`: Slug of the default mentor to configure (matches a directory under mentors/).  [default: example]
* `--backend TEXT`: Backend kind for the mentor: &#x27;openclaw&#x27; (live agent runtime), &#x27;anthropic&#x27; (stateless), or &#x27;stub&#x27;.  [default: openclaw]
* `--openclaw-url TEXT`: OpenClaw webhook URL (when backend=openclaw).  [default: https://openclaw.helmguild.local/ammp/ask]
* `--auth-bearer-env TEXT`: Env var name holding the Bearer token sent to the OpenClaw webhook.  [default: OPENCLAW_BEARER]
* `--require-auth / --no-require-auth`: Enforce Bearer-key auth on incoming MCP calls.  [default: require-auth]
* `--mint-first-mentee / --no-mint-first-mentee`: Mint a first mentee (claude-cowork-helmut) so you can hit the server immediately.  [default: mint-first-mentee]
* `--help`: Show this message and exit.

## `ammp status`

Alias for `ammp system status`.

**Usage**:

```console
$ ammp status [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `ammp health`

Alias for `ammp system health`.

**Usage**:

```console
$ ammp health [OPTIONS]
```

**Options**:

* `--url TEXT`: Override the URL to probe. Default: configured public_url, fallback localhost.
* `--timeout FLOAT`: Per-probe timeout in seconds.  [default: 5.0]
* `--probe-backends / --no-probe-backends`: Probe each mentor&#x27;s openclaw webhook URL too.  [default: probe-backends]
* `--help`: Show this message and exit.

## `ammp usage`

Alias for `ammp system usage`.

**Usage**:

```console
$ ammp usage [OPTIONS]
```

**Options**:

* `--days INTEGER`: Aggregation window — last N days. 0 = all-time.  [default: 7]
* `--by-mentee / --no-by-mentee`: Show per-mentee breakdown.  [default: by-mentee]
* `--by-mentor / --no-by-mentor`: Show per-mentor breakdown.  [default: by-mentor]
* `--help`: Show this message and exit.

## `ammp capability`

Alias for `ammp system capability`.

**Usage**:

```console
$ ammp capability [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `ammp mentor`

Inspect registered mentors; ask one a question; escalate to your operator.

**Usage**:

```console
$ ammp mentor [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List registered mentors and their corpus...
* `ask`: Ask a mentor a free-form question (CLI...
* `escalate`: Request escalation phrasing to hand to...

### `ammp mentor list`

List registered mentors and their corpus sizes.

CLI parity with the MCP ``ListMentors`` extension. ``--json`` returns
the wire envelope; default rendering is a Rich table with the
additional ``playbook_dir`` column (operator-facing, not part of the
wire shape).

**Usage**:

```console
$ ammp mentor list [OPTIONS]
```

**Options**:

* `--json`: Emit the same JSON envelope an MCP `ListMentors` call returns. Useful for scripting or for agents that prefer Bash+CLI to MCP.
* `--help`: Show this message and exit.

### `ammp mentor ask`

Ask a mentor a free-form question (CLI parity with AMMP ``AskMentor``).

Calls the same handler the MCP server uses. The mentor&#x27;s backend
synthesises an answer + confidence; when confidence falls below the
mentor&#x27;s threshold, the response also recommends ``EscalateToHuman``
with suggested phrasing.

**Usage**:

```console
$ ammp mentor ask [OPTIONS] QUESTION
```

**Arguments**:

* `QUESTION`: The free-form question to put to the mentor.  [required]

**Options**:

* `-m, --mentor TEXT`: Mentor slug. Empty → server default.
* `-c, --context TEXT`: Additional context to attach to the question.
* `--json`: Emit raw JSON (machine-readable) instead of a Rich panel.
* `--help`: Show this message and exit.

### `ammp mentor escalate`

Request escalation phrasing to hand to your operator (AMMP ``EscalateToHuman``).

Returns suggested phrasing the mentee hands to its own operator. The
mentor does not page or message anyone — that&#x27;s the Human-Gated
Escalation Invariant (AMMP §3.4).

**Usage**:

```console
$ ammp mentor escalate [OPTIONS] SITUATION
```

**Arguments**:

* `SITUATION`: One-paragraph description of the situation that needs the operator.  [required]

**Options**:

* `-m, --mentor TEXT`: Mentor slug. Empty → server default.
* `--why-stuck TEXT`: Optional note on what&#x27;s making you uncertain.
* `--json`: Emit raw JSON (machine-readable) instead of a Rich panel.
* `--help`: Show this message and exit.

## `ammp mentee`

Manage the mentee allowlist (add / remove / rotate-key / list / check-key).

**Usage**:

```console
$ ammp mentee [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: Show the mentee allowlist.
* `add`: Add a mentee.
* `remove`: Remove a mentee from the allowlist.
* `rotate-key`: Issue a fresh API key, replace the stored...
* `check-key`: Resolve a plaintext API key to a mentee —...

### `ammp mentee list`

Show the mentee allowlist. Plaintext keys are NEVER shown — only hashes.

**Usage**:

```console
$ ammp mentee list [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `ammp mentee add`

Add a mentee. Mints a fresh API key, prints it ONCE, stores only the hash.

**Usage**:

```console
$ ammp mentee add [OPTIONS] SLUG
```

**Arguments**:

* `SLUG`: Mentee slug, e.g. &#x27;claude-cowork-sandra&#x27;.  [required]

**Options**:

* `--operator TEXT`: Operator, e.g. &#x27;human:sandra&#x27;.  [required]
* `--runtime TEXT`: Runtime, e.g. &#x27;claude-cowork&#x27;, &#x27;claude-ai&#x27;, &#x27;claude-code&#x27;.  [required]
* `--rate-limit INTEGER`: Per-minute request budget.  [default: 60]
* `--help`: Show this message and exit.

### `ammp mentee remove`

Remove a mentee from the allowlist.

**Usage**:

```console
$ ammp mentee remove [OPTIONS] SLUG
```

**Arguments**:

* `SLUG`: Mentee slug to remove.  [required]

**Options**:

* `--help`: Show this message and exit.

### `ammp mentee rotate-key`

Issue a fresh API key, replace the stored hash. Prints the new key once.

**Usage**:

```console
$ ammp mentee rotate-key [OPTIONS] SLUG
```

**Arguments**:

* `SLUG`: Mentee slug.  [required]

**Options**:

* `--help`: Show this message and exit.

### `ammp mentee check-key`

Resolve a plaintext API key to a mentee — useful for debugging auth.

**Usage**:

```console
$ ammp mentee check-key [OPTIONS] API_KEY
```

**Arguments**:

* `API_KEY`: Plaintext key to test.  [required]

**Options**:

* `--help`: Show this message and exit.

## `ammp playbook`

Inspect and read a mentor&#x27;s playbooks (areas of practice).

**Usage**:

```console
$ ammp playbook [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List the playbooks (areas of practice) for...
* `show`: Print one playbook&#x27;s metadata + its skill...
* `search`: Substring-search a mentor&#x27;s corpus (CLI...

### `ammp playbook list`

List the playbooks (areas of practice) for a mentor.

**Usage**:

```console
$ ammp playbook list [OPTIONS]
```

**Options**:

* `--mentor TEXT`: Mentor slug. Empty → server default.
* `--help`: Show this message and exit.

### `ammp playbook show`

Print one playbook&#x27;s metadata + its skill list.

**Usage**:

```console
$ ammp playbook show [OPTIONS] PLAYBOOK_ID
```

**Arguments**:

* `PLAYBOOK_ID`: Playbook id (directory name).  [required]

**Options**:

* `--mentor TEXT`: Mentor slug.
* `--help`: Show this message and exit.

### `ammp playbook search`

Substring-search a mentor&#x27;s corpus (CLI parity with AMMP ``SearchPlaybooks``).

Calls the same handler the MCP server uses. Search runs at skill
granularity; each match names its parent playbook. The hash-only
audit log records the call.

**Usage**:

```console
$ ammp playbook search [OPTIONS] QUERY
```

**Arguments**:

* `QUERY`: Substring to search for across the mentor&#x27;s skill corpus.  [required]

**Options**:

* `-m, --mentor TEXT`: Mentor slug. Empty → server default.
* `-n, --limit INTEGER RANGE`: Maximum number of matches to return.  [default: 5; 1&lt;=x&lt;=50]
* `--json`: Emit raw JSON (machine-readable) instead of a Rich table.
* `--help`: Show this message and exit.

## `ammp skill`

Inspect and read individual skills inside a mentor&#x27;s playbooks.

**Usage**:

```console
$ ammp skill [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List the skills inside a playbook.
* `show`: Print one skill body to stdout (CLI parity...

### `ammp skill list`

List the skills inside a playbook.

**Usage**:

```console
$ ammp skill list [OPTIONS]
```

**Options**:

* `-p, --playbook TEXT`: Playbook id (directory name).  [required]
* `--mentor TEXT`: Mentor slug. Empty → server default.
* `--help`: Show this message and exit.

### `ammp skill show`

Print one skill body to stdout (CLI parity with ``GetSkill``).

**Usage**:

```console
$ ammp skill show [OPTIONS] SKILL_ID
```

**Arguments**:

* `SKILL_ID`: Skill id (folder name or filename stem).  [required]

**Options**:

* `-p, --playbook TEXT`: Playbook id (directory name).  [required]
* `--mentor TEXT`: Mentor slug.
* `--help`: Show this message and exit.

## `ammp escalation`

Inspect and manage mentor-mediated escalations.

**Usage**:

```console
$ ammp escalation [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: List escalations recorded by this server.
* `show`: Print one escalation in detail.
* `answer`: Manually inject A.h&#x27;s answer Z onto a...
* `cancel`: Mark an escalation cancelled (e.g.

### `ammp escalation list`

List escalations recorded by this server.

**Usage**:

```console
$ ammp escalation list [OPTIONS]
```

**Options**:

* `--status TEXT`: Filter by status: pending | delivered | answered | cancelled | expired. Empty → all.
* `--json`: Emit raw JSON instead of a Rich table.
* `--help`: Show this message and exit.

### `ammp escalation show`

Print one escalation in detail.

**Usage**:

```console
$ ammp escalation show [OPTIONS] ESCALATION_ID
```

**Arguments**:

* `ESCALATION_ID`: Full or short (8-hex) escalation id.  [required]

**Options**:

* `--help`: Show this message and exit.

### `ammp escalation answer`

Manually inject A.h&#x27;s answer Z onto a pending escalation.

Updates the persistent store; does NOT resolve any in-flight
broker waiter in the running server. Use this when the delivery
adapter is log-only, or for post-hoc records.

**Usage**:

```console
$ ammp escalation answer [OPTIONS] ESCALATION_ID ANSWER
```

**Arguments**:

* `ESCALATION_ID`: Escalation id (full or short).  [required]
* `ANSWER`: Answer text Z from A.h, to record on the escalation.  [required]

**Options**:

* `--help`: Show this message and exit.

### `ammp escalation cancel`

Mark an escalation cancelled (e.g. B.a gave up, A.h unreachable).

**Usage**:

```console
$ ammp escalation cancel [OPTIONS] ESCALATION_ID
```

**Arguments**:

* `ESCALATION_ID`: Escalation id (full or short).  [required]

**Options**:

* `--reason TEXT`: Free-form note recorded on the escalation.  [default: operator_cancelled]
* `--help`: Show this message and exit.

## `ammp system`

Operate the install as a whole (setup, status, health, usage, capability, serve).

**Usage**:

```console
$ ammp system [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `capability`: Print the AMMP capability advertisement...
* `serve`: Start the MCP server.
* `setup`: First-run installation wizard.
* `status`: Validate the current installation.
* `usage`: Aggregate usage statistics from the...
* `health`: Active runtime probe.

### `ammp system capability`

Print the AMMP capability advertisement (offline render).

**Usage**:

```console
$ ammp system capability [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `ammp system serve`

Start the MCP server.

Auto-bootstraps `~/.ammp/` (creates the dir, copies the shipped
example mentor, writes a `config.env` scaffold) on first run so a
fresh install can `ammp serve` immediately. Re-runs are no-ops.

Default transport is HTTP (Streamable-HTTP at `/mcp/`). Use ``--stdio`` for
subprocess transport (Claude Desktop, Claude Code stdio integrations). The
transport can also be set via ``AMMP_TRANSPORT={http,stdio}``; the
``--stdio`` flag wins when both are present.

**Usage**:

```console
$ ammp system serve [OPTIONS]
```

**Options**:

* `--stdio`: Speak MCP JSON-RPC over stdin/stdout (subprocess transport). Wins over --host/--port and AMMP_TRANSPORT=http when set. Use this for Claude Desktop / Claude Code subprocess MCP integration.
* `--host TEXT`: Override bind host (HTTP transport only).
* `--port INTEGER`: Override bind port (HTTP transport only).  [default: 0]
* `--help`: Show this message and exit.

### `ammp system setup`

First-run installation wizard.

Configures the chosen mentor&#x27;s backend block, optionally mints a first
mentee, writes a .env scaffold with the required env vars, and prints
the next-step commands. Idempotent.

**Usage**:

```console
$ ammp system setup [OPTIONS]
```

**Options**:

* `-y, --yes`: Accept all defaults; no prompts.
* `--mentor-slug TEXT`: Slug of the default mentor to configure (matches a directory under mentors/).  [default: example]
* `--backend TEXT`: Backend kind for the mentor: &#x27;openclaw&#x27; (live agent runtime), &#x27;anthropic&#x27; (stateless), or &#x27;stub&#x27;.  [default: openclaw]
* `--openclaw-url TEXT`: OpenClaw webhook URL (when backend=openclaw).  [default: https://openclaw.helmguild.local/ammp/ask]
* `--auth-bearer-env TEXT`: Env var name holding the Bearer token sent to the OpenClaw webhook.  [default: OPENCLAW_BEARER]
* `--require-auth / --no-require-auth`: Enforce Bearer-key auth on incoming MCP calls.  [default: require-auth]
* `--mint-first-mentee / --no-mint-first-mentee`: Mint a first mentee (claude-cowork-helmut) so you can hit the server immediately.  [default: mint-first-mentee]
* `--help`: Show this message and exit.

### `ammp system status`

Validate the current installation. Exits non-zero if anything is broken.

**Usage**:

```console
$ ammp system status [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

### `ammp system usage`

Aggregate usage statistics from the hash-only audit log.

The audit log records *only* operation + mentor + mentee + opaque
request hash — never any payload — so this view tells you who used
what without leaking anything.

**Usage**:

```console
$ ammp system usage [OPTIONS]
```

**Options**:

* `--days INTEGER`: Aggregation window — last N days. 0 = all-time.  [default: 7]
* `--by-mentee / --no-by-mentee`: Show per-mentee breakdown.  [default: by-mentee]
* `--by-mentor / --no-by-mentor`: Show per-mentor breakdown.  [default: by-mentor]
* `--help`: Show this message and exit.

### `ammp system health`

Active runtime probe.

`status` validates the install statically (paths, files, env vars).
`health` validates that the running thing actually answers — it
GETs `/.well-known/agent.json` from the server and (optionally)
HEADs each openclaw mentor backend&#x27;s webhook URL.

**Usage**:

```console
$ ammp system health [OPTIONS]
```

**Options**:

* `--url TEXT`: Override the URL to probe. Default: configured public_url, fallback localhost.
* `--timeout FLOAT`: Per-probe timeout in seconds.  [default: 5.0]
* `--probe-backends / --no-probe-backends`: Probe each mentor&#x27;s openclaw webhook URL too.  [default: probe-backends]
* `--help`: Show this message and exit.
