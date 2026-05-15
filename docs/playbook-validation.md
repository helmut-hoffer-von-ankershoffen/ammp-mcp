# Playbook validation — comprehension layer + behavioral layer

AMMP's playbook validation has two layers. The first one shipped in 0.15.0. The second is in flight after Helmut's "did the mentee actually install the plugin / schedule a task?" critique on 2026-05-15.

## Layer 1 — Comprehension (shipped 0.15.0, proven 4/4 on knowledge-management)

A fresh `claude -p` mentee gets the AMMP MCP wire pre-loaded (`.mcp.json` pointing at `mcp.helmguild.com/ammp`). For each `validation.prompts[]` entry:

1. Mentee reads playbook via `GetPlaybook` + relevant `GetSkill`.
2. Mentee answers the prompt in 4-8 sentences.
3. Harness runs `expect` rules against the response text:
   - `must_mention_skill`, `must_contain`, `must_contain_pattern`, `must_invoke_or_name`, `must_mention_type`.

**Proves:** the skill text is LLM-readable + LLM-actionable in principle.

**Does not prove:**
- The plugin actually installs and the bundled scripts actually run.
- Any state persists after the prompt exits. The mentee says "I'd save this as a feedback memory" but doesn't write one. Says "I'd schedule a daily extraction" but doesn't register a task.
- Mentoring left a mark.

## Layer 2 — Behavioral (in flight)

Same per-prompt loop, but the mentee runs in a **persistent workdir** with the plugin **actually installed**:

1. **Setup (once per validation run):**
   - Harness creates a persistent `<tmp>/mentee/` workdir (NOT cleaned up at end of run unless `--cleanup-on-pass`).
   - Harness invokes `claude plugin marketplace add <mp>` + `claude plugin install <plugin>@<mp>` so the bundled scripts land at `<workdir>/.claude/plugins/<plugin>/`.
   - Harness verifies install via `.claude/settings.local.json` (the canonical install marker — see memory:`claude_plugin_install_local_settings`).
2. **Per behavioral prompt:**
   - Mentee runs in the same workdir, can invoke bundled scripts via the `Bash` tool, can write files, can register scheduling artefacts.
   - Expect rules extend with side-effect assertions:
     - `must_create_file: "<glob-relative-to-workdir>"`
     - `must_invoke_command: "<substring>"` (matched against the mentee's tool-call trace via `claude --json` mode)
     - `must_create_dir: "<glob>"`
3. **Tear-down:** archive the workdir for inspection (or rm with `--cleanup-on-pass`).

**Will prove:**
- The plugin install path works end-to-end (already covered by `e2e-claude-code-install.sh`; this folds it into validation).
- The mentee, given a "scaffold a vault" task, actually invokes `vault-scaffold.sh` and produces the directory layout.
- The mentee, given a "save this correction" task, actually writes a memory entry to the workdir's auto-memory location.
- The mentee, given a "schedule a daily learning extraction" task, registers a cron / launchd / ScheduleWakeup entry.

**Will not prove (deliberate scope cut):**
- The mentee's behavior persists across sessions — that's an auto-memory + shared-vault concern, tested separately.
- The mentee can do this against real publishing surfaces (IG / X / Veo). Behavioral mode stays filesystem-local; live-credential e2e is gated separately.

## Prompt-level flag

Each prompt in `validation.prompts[]` gets an optional `mode: "comprehension" | "behavioral"` (default `"comprehension"` for back-compat with shipped 0.15.x). Behavioral prompts must declare side-effect expectations.

## First behavioral prompt (target — knowledge-management)

```json
{
  "id": "vault-scaffold-actually-runs",
  "mode": "behavioral",
  "task": "Scaffold a canonical knowledge vault for a brand called 'Sandra's Cooking' at /tmp/ammp-behavioral-test/sandra-vault using the bundled scaffolder. Operator slug 'sandra', brand slug 'sandras-cooking', agents 'pepe,cowork'. After running, list the files created.",
  "expect": {
    "must_mention_skill": "canonical-knowledge-vault",
    "must_invoke_command": "vault-scaffold.sh",
    "must_create_file": "/tmp/ammp-behavioral-test/sandra-vault/MEMORY.md",
    "must_create_file": "/tmp/ammp-behavioral-test/sandra-vault/agents/pepe/Charter.md"
  }
}
```

## Status

Layer 1: shipped + green.
Layer 2: this doc + the harness extension. Tracked at `feedback_run_make_check_before_push` + the iteration log.
