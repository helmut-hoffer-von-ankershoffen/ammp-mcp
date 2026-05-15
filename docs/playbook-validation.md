# Playbook validation — three layers

AMMP's playbook validation has three layers, each strictly stronger than the previous.

## Layer 1 — Comprehension (shipped 0.15.0; 4/4 on knowledge-management)

A fresh `claude -p` mentee gets the AMMP MCP wire. For each `validation.prompts[]` entry (default `mode: "comprehension"`):

1. Mentee reads playbook via `GetPlaybook` + `GetSkill`.
2. Mentee answers the prompt in 4-8 sentences.
3. Harness asserts on response text via text rules: `must_mention_skill`, `must_contain`, `must_contain_pattern`, `must_invoke_or_name`, `must_mention_type`.

**Proves:** the skill text is LLM-readable + LLM-actionable.

**Doesn't prove:** plugin installs, bundled scripts run, state persists.

Script: `scripts/e2e-playbook-validation.sh`. CLI: `ammp playbook validate <id>`.

## Layer 2 — Behavioral (in flight, install path verified)

Per behavioral prompt (`mode: "behavioral"`):

1. **Setup once per run:** harness invokes real `claude plugin marketplace add` + `claude plugin install --scope local`. Verifies via `.claude/settings.local.json`.
2. **Per prompt:** mentee runs in persistent workdir with `Bash`, `Write`, `Read` tool access + AMMP MCP wire. Plugin's bundled scripts are at `<workdir>/.claude/plugins/<plugin>/scripts/`.
3. **Side-effect assertions:** `must_create_file`, `must_create_dir` in addition to text rules.

**Proves:** plugin install works end-to-end; mentee actually invokes bundled tooling; specific files/dirs land on disk.

**Doesn't prove:** the playbook's stated **goal** was achieved — that the whole playbook delivers its promised outcome, not just that each skill triggered in isolation.

Script: `scripts/e2e-playbook-validation-behavioral.sh`. CLI: `ammp playbook validate <id> --behavioral --plugin <id> --marketplace-repo <repo>`.

## Layer 3 — Goal-level (designed; building)

**Helmut's bar:** "the validation is not only that some skill was triggered — but that the skills together achieve the goal of the playbook."

Per-playbook ONE `validation.goal` block:

```json
"validation": {
  "goal": {
    "id": "operator-stack-fully-wired",
    "task": "End-to-end multi-step task that exercises the playbook holistically...",
    "expect": {
      "must_create_file": [...],
      "must_create_dir": [...],
      "must_contain_in_file": [
        {"path": "<rel>", "patterns": ["...", "..."]},
        {"path": "<rel>", "patterns_min_length": 200}
      ],
      "consumer_check": {
        "task": "Read this workdir cold (no AMMP wire) and answer: <question>",
        "must_contain": ["..."]
      }
    }
  }
}
```

### The consumer-mentee — load-bearing

After the producer mentee finishes, a **second fresh mentee** spawns:

- **No AMMP MCP wire** — it sees only the workdir + the read-only filesystem.
- Receives the `consumer_check.task` (e.g. "what's Sandra's privacy posture?").
- If it can answer correctly from the workdir alone, the playbook **actually delivered**: the artefacts are consumable, not just present.

This is the difference between "the scaffolder ran" and "the operator's stack now has functioning knowledge management".

### New expect rules in Layer 3

- `must_contain_in_file: [{path, patterns}]` — file exists AND its content contains every pattern.
- `must_contain_in_file: [{path, patterns_min_length}]` — file's content is at least N chars long (defends against stub-only output that satisfies `must_create_file` but is empty templates).
- `consumer_check: {task, must_contain}` — the second-mentee acceptance test.

### Knowledge-management goal scenario (first target)

Task: "Sandra is a new operator with 3 agents (pepe, cowork, hermes). Bootstrap her complete knowledge-management infrastructure: scaffold the vault, populate the operator profile + brand charter + privacy boundaries with real content (not stub text), wire MEMORY.md to index every doc, append a scaffold event to _shared/Changelog.md. After running, confirm in 3-4 sentences."

Expect:
- File tree matches the full Layer 1 vault schema.
- `MEMORY.md` patterns include all 3 agent slugs + brand slug + operator slug.
- `sandra/Privacy-Boundaries.md` content > 200 chars (not just template stub).
- `_shared/Changelog.md` has the scaffold entry.
- Consumer-mentee can answer "what's the brand emoji signature" + "which agents are mounted" from the vault alone.

Script: `scripts/e2e-playbook-validation-goal.sh` (in flight).

## Status

| Layer | State | Proven |
|---|---|---|
| 1. Comprehension | Shipped 0.15.0 | knowledge-management 4/4; multi-channel-content-pipelines 4/5 (1 too-strict rule) |
| 2. Behavioral | Install verified, first prompt in flight (run `bk47vchee`) | Plugin install end-to-end via `claude plugin install --scope local` succeeds |
| 3. Goal-level | Design landed in this doc; harness in flight | — |
