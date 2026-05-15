#!/usr/bin/env bash
# e2e-playbook-validation-goal.sh — Layer 3 validator.
#
# Where Layer 2 proves "the bundled tools ran and produced files",
# Layer 3 proves "the PLAYBOOK delivers its promised outcome" via:
#
#   1. Plugin install (same as Layer 2).
#   2. ONE producer mentee runs the goal task end-to-end — multi-step,
#      multi-skill — with Bash + Write tool access + AMMP MCP wire.
#   3. Composite side-effect rules (must_create_file + content
#      patterns + min-length to defend against stub-only output).
#   4. THE ACID TEST: a SECOND fresh mentee runs against the workdir
#      ALONE — no AMMP wire, no MCP — and must answer questions about
#      the deliverable purely from the filesystem. If it can, the
#      playbook actually delivered: the artefacts are consumable, not
#      just present.
#
# Required env: same as Layer 2 plus the stdin shape carries a
# `goal: {id, task, expect}` object (NOT the `prompts[]` array).
#
# Exit codes: 0 if every assertion (producer + consumer) passes; 1 otherwise.

set -euo pipefail

AMMP_URL="${AMMP_URL:-https://mcp.helmguild.com/ammp}"
BEARER="${HELMGUILD_AMMP_BEARER:-}"
KEEP_TMP="${KEEP_TMP:-0}"
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"
MENTOR="${AMMP_VALIDATE_MENTOR:-}"
PLAYBOOK="${AMMP_VALIDATE_PLAYBOOK:-}"
PLAYBOOK_NAME="${AMMP_VALIDATE_PLAYBOOK_NAME:-}"
PLUGIN="${AMMP_VALIDATE_PLUGIN:-}"
MARKETPLACE_REPO="${AMMP_VALIDATE_MARKETPLACE_REPO:-}"
OUTPUT_FORMAT="${AMMP_VALIDATE_OUTPUT_FORMAT:-text}"

die() { echo "✗ $*" >&2; exit "${2:-1}"; }
log() { [[ "$OUTPUT_FORMAT" == "text" ]] && printf '· %s\n' "$*" >&2 || true; }

for bin in claude curl jq python3; do
  command -v "$bin" >/dev/null 2>&1 || die "missing required CLI: $bin" 2
done
[[ -n "$BEARER" ]]          || die "HELMGUILD_AMMP_BEARER not set" 2
[[ -n "$MENTOR" ]]          || die "AMMP_VALIDATE_MENTOR not set" 2
[[ -n "$PLAYBOOK" ]]        || die "AMMP_VALIDATE_PLAYBOOK not set" 2
[[ -n "$PLUGIN" ]]          || die "AMMP_VALIDATE_PLUGIN not set" 2
[[ -n "$MARKETPLACE_REPO" ]] || die "AMMP_VALIDATE_MARKETPLACE_REPO not set" 2

spec_json=$(cat)
goal_json=$(printf '%s' "$spec_json" | python3 -c "
import json, sys
spec = json.loads(sys.stdin.read())
goal = spec.get('goal')
if not isinstance(goal, dict):
    sys.stderr.write('no goal block in spec (expected {goal: {task, expect}})\n')
    sys.exit(2)
print(json.dumps(goal, ensure_ascii=False))
")
[[ -n "$goal_json" ]] || die "no goal block in spec" 2

tmp=$(mktemp -d -t ammp-goal-XXXXXX)
log "workdir: $tmp"
cleanup() {
  rc=$?
  cd / >/dev/null 2>&1 || true
  if [[ "$KEEP_TMP" == "1" ]] || [[ "$rc" -ne 0 ]]; then
    log "workdir preserved at: $tmp"
  else
    rm -rf "$tmp"
  fi
  exit $rc
}
trap cleanup EXIT

workdir="$tmp/mentee"
mkdir -p "$workdir"
cd "$workdir"

cat > .mcp.json <<EOF
{
  "mcpServers": {
    "helmguild-ammp": {
      "type": "http",
      "url": "${AMMP_URL%/}/mcp/",
      "headers": {"Authorization": "Bearer ${BEARER}"}
    }
  }
}
EOF

# ── Plugin install (Layer 2 shape) ────────────────────────────────────
log "installing plugin: $PLUGIN from $MARKETPLACE_REPO"
claude plugin marketplace add "$MARKETPLACE_REPO" --scope local \
  >"$tmp/marketplace-add.log" 2>&1 \
  || { cat "$tmp/marketplace-add.log" >&2; die "claude plugin marketplace add failed"; }
mp_name=$(basename "$MARKETPLACE_REPO")
claude plugin install "$PLUGIN@$mp_name" --scope local \
  >"$tmp/plugin-install.log" 2>&1 \
  || { cat "$tmp/plugin-install.log" >&2; die "claude plugin install failed"; }
log "plugin installed"
local_settings="$workdir/.claude/settings.local.json"
jq -e --arg id "$PLUGIN@$mp_name" '.enabledPlugins | has($id)' "$local_settings" >/dev/null \
  || die "$PLUGIN@$mp_name not enabled in settings.local.json"

# ── Producer mentee — runs the goal task end-to-end ───────────────────
task=$(printf '%s' "$goal_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['task'])")
gid=$(printf '%s' "$goal_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id','goal'))")

log "producer mentee: $gid"
producer_prompt="You are a mentee operator under the mentorship of $PLAYBOOK_NAME on the AMMP server at $AMMP_URL.

Step 1: call mcp__helmguild-ammp__GetPlaybook with id=\"$PLAYBOOK\" and mentor=\"$MENTOR\".
Step 2: for every skill in the playbook, call mcp__helmguild-ammp__GetSkill to fetch the body. You will need ALL the skills in concert to achieve the goal below.
Step 3: ACTUALLY EXECUTE the goal. The plugin is installed at .claude/plugins/$PLUGIN/. Bundled scripts live in its scripts/ subdir; you have Bash + Write tool access. Populate files with REAL content drawn from the playbook's skill bodies, not stub text.
Step 4: in 3-5 sentences, summarise what you delivered.

GOAL:
$task"

cd "$workdir"
if command -v gtimeout >/dev/null 2>&1; then
  producer_response=$(gtimeout 480 claude -p "$producer_prompt" \
    --model "$CLAUDE_MODEL" \
    --permission-mode bypassPermissions \
    --allowed-tools "mcp__helmguild-ammp__GetPlaybook,mcp__helmguild-ammp__GetSkill,mcp__helmguild-ammp__ListMentors,Bash,Write,Read,Edit" \
    2>"$tmp/producer.err" || echo "")
else
  producer_response=$(claude -p "$producer_prompt" \
    --model "$CLAUDE_MODEL" \
    --permission-mode bypassPermissions \
    --allowed-tools "mcp__helmguild-ammp__GetPlaybook,mcp__helmguild-ammp__GetSkill,mcp__helmguild-ammp__ListMentors,Bash,Write,Read,Edit" \
    2>"$tmp/producer.err" || echo "")
fi
printf '%s\n' "$producer_response" > "$tmp/producer.out"
log "producer finished"

# ── Side-effect assertions on the workdir ────────────────────────────
producer_result=$(MENTEE_WORKDIR="$workdir" python3 - "$goal_json" <<'PYEOF'
import json, sys, os, re
goal = json.loads(sys.argv[1])
expect = goal.get("expect", {})
workdir = os.environ["MENTEE_WORKDIR"]
failures = []

def _resolve(p):
    return p if os.path.isabs(p) else os.path.join(workdir, p)

# must_create_file
for entry in expect.get("must_create_file", []) or []:
    if isinstance(entry, str):
        if not os.path.isfile(_resolve(entry)):
            failures.append(f"must_create_file: {entry!r} missing")

# must_create_dir
for entry in expect.get("must_create_dir", []) or []:
    if isinstance(entry, str):
        if not os.path.isdir(_resolve(entry)):
            failures.append(f"must_create_dir: {entry!r} missing")

# must_contain_in_file
for spec in expect.get("must_contain_in_file", []) or []:
    if not isinstance(spec, dict):
        continue
    path = _resolve(spec.get("path", ""))
    if not os.path.isfile(path):
        failures.append(f"must_contain_in_file: {path!r} missing")
        continue
    with open(path, errors="replace") as fh:
        text = fh.read()
    low = text.lower()
    for pat in spec.get("patterns", []) or []:
        if isinstance(pat, str):
            if pat.lower() not in low:
                failures.append(f"must_contain_in_file: pattern {pat!r} not in {path!r}")
    min_len = spec.get("patterns_min_length")
    if isinstance(min_len, int) and len(text.strip()) < min_len:
        failures.append(f"must_contain_in_file: {path!r} is {len(text.strip())} chars, expected ≥ {min_len}")

print(json.dumps({"layer": "producer", "passed": not failures, "failures": failures}))
PYEOF
)
producer_passed=$(printf '%s' "$producer_result" | python3 -c "import json,sys; print(json.load(sys.stdin)['passed'])")
producer_failures=$(printf '%s' "$producer_result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(' | '.join(d['failures']) or '(none)')")

if [[ "$producer_passed" == "True" ]]; then
  [[ "$OUTPUT_FORMAT" == "text" ]] && printf '✓ producer (composite filesystem assertions)\n' || true
else
  [[ "$OUTPUT_FORMAT" == "text" ]] && printf '✗ producer — %s\n' "$producer_failures" || true
fi

# ── Consumer mentee — THE ACID TEST: read workdir cold ────────────────
consumer_check=$(printf '%s' "$goal_json" | python3 -c "
import json,sys
g = json.loads(sys.stdin.read())
cc = g.get('expect', {}).get('consumer_check')
print(json.dumps(cc) if isinstance(cc, dict) else '')
")
consumer_passed="True"
consumer_failures="(none)"

if [[ -n "$consumer_check" ]]; then
  consumer_task=$(printf '%s' "$consumer_check" | python3 -c "import json,sys; print(json.load(sys.stdin).get('task',''))")
  consumer_workdir="$tmp/consumer"
  mkdir -p "$consumer_workdir"
  cp -R "$workdir/." "$consumer_workdir/"
  # Strip the AMMP MCP wire so the consumer ONLY sees the workdir.
  rm -f "$consumer_workdir/.mcp.json"

  log "consumer mentee (no AMMP wire — reads workdir alone)"
  cd "$consumer_workdir"
  consumer_prompt="You are a fresh mentee operator. You have READ-ONLY filesystem access to the directory you're running in — that's all. No MCP servers, no AMMP wire, no internet. Answer the question below by reading the files in this directory.

QUESTION:
$consumer_task"

  if command -v gtimeout >/dev/null 2>&1; then
    consumer_response=$(gtimeout 180 claude -p "$consumer_prompt" \
      --model "$CLAUDE_MODEL" \
      --permission-mode bypassPermissions \
      --allowed-tools "Bash,Read" \
      2>"$tmp/consumer.err" || echo "")
  else
    consumer_response=$(claude -p "$consumer_prompt" \
      --model "$CLAUDE_MODEL" \
      --permission-mode bypassPermissions \
      --allowed-tools "Bash,Read" \
      2>"$tmp/consumer.err" || echo "")
  fi
  printf '%s\n' "$consumer_response" > "$tmp/consumer.out"

  consumer_result=$(printf '%s' "$consumer_check" | CONSUMER_RESPONSE="$consumer_response" python3 -c "
import json, os, sys
spec = json.load(sys.stdin)
resp = os.environ['CONSUMER_RESPONSE']
resp_low = resp.lower()
failures = []
for entry in spec.get('must_contain', []) or []:
    if isinstance(entry, str):
        alts = [a.strip().lower() for a in entry.split('|') if a.strip()]
        if not any(a in resp_low for a in alts):
            failures.append(f'consumer must_contain: none of {alts!r} matched')
print(json.dumps({'layer': 'consumer', 'passed': not failures, 'failures': failures}))
")
  consumer_passed=$(printf '%s' "$consumer_result" | python3 -c "import json,sys; print(json.load(sys.stdin)['passed'])")
  consumer_failures=$(printf '%s' "$consumer_result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(' | '.join(d['failures']) or '(none)')")
  if [[ "$consumer_passed" == "True" ]]; then
    [[ "$OUTPUT_FORMAT" == "text" ]] && printf '✓ consumer (workdir is actually consumable cold)\n' || true
  else
    [[ "$OUTPUT_FORMAT" == "text" ]] && printf '✗ consumer — %s\n' "$consumer_failures" || true
  fi
fi

[[ "$OUTPUT_FORMAT" == "text" ]] && printf '\n'
if [[ "$producer_passed" == "True" && "$consumer_passed" == "True" ]]; then
  [[ "$OUTPUT_FORMAT" == "text" ]] && printf '🍝 GOAL ACHIEVED: playbook %s delivers its promised outcome\n' "$PLAYBOOK"
  exit 0
fi
[[ "$OUTPUT_FORMAT" == "text" ]] && printf '✗ goal-level validation FAILED\n'
exit 1
