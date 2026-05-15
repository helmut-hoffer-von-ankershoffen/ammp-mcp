#!/usr/bin/env bash
# e2e-playbook-validation-behavioral.sh — Layer 2 validator.
#
# Where Layer 1 (e2e-playbook-validation.sh) proves a mentee can READ
# a playbook over MCP and ANSWER prompts, this Layer 2 harness proves
# a mentee can ACT on the playbook:
#
#   1. Install the plugin from the marketplace (real `claude plugin
#      install` via the live AMMP `GetPluginArchive` flow).
#   2. Spawn the mentee in a persistent workdir with Bash + Write tool
#      access + the AMMP MCP wire.
#   3. Issue behavioral prompts that ask the mentee to actually run
#      bundled scripts / scaffold files / register scheduling artefacts.
#   4. Assert on filesystem side-effects, not just response text.
#
# Required env:
#   HELMGUILD_AMMP_BEARER          — Bearer for AMMP + GetPluginArchive
#   AMMP_VALIDATE_MENTOR           — e.g. "pepe"
#   AMMP_VALIDATE_PLAYBOOK         — e.g. "knowledge-management"
#   AMMP_VALIDATE_PLAYBOOK_NAME    — display name
#   AMMP_VALIDATE_PLUGIN           — plugin id (e.g. pepe-knowledge-management)
#   AMMP_VALIDATE_MARKETPLACE_REPO — GitHub owner/repo of the marketplace
#
# Optional env:
#   AMMP_URL                       — defaults to https://mcp.helmguild.com/ammp
#   CLAUDE_MODEL                   — defaults to sonnet
#   KEEP_TMP=1                     — preserve the workdir for inspection
#
# Stdin (UTF-8 JSON):
#   {"prompts": [{"id", "task", "mode": "behavioral", "expect": {
#       "must_mention_skill": "<id>",
#       "must_contain": [...],
#       "must_create_file": "<path>" or ["<path>", ...]
#   }}, ...]}
#
# Side-effect rules added in this harness:
#   must_create_file: path or list of paths that must exist after the
#                     mentee finishes. Relative to the workdir or
#                     absolute (e.g. /tmp/...).
#   must_create_dir:  same but must be a directory.
#
# Exit codes: same as Layer 1.

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

for bin in claude curl jq python3 unzip; do
  command -v "$bin" >/dev/null 2>&1 || die "missing required CLI: $bin" 2
done
[[ -n "$BEARER" ]]          || die "HELMGUILD_AMMP_BEARER not set" 2
[[ -n "$MENTOR" ]]          || die "AMMP_VALIDATE_MENTOR not set" 2
[[ -n "$PLAYBOOK" ]]        || die "AMMP_VALIDATE_PLAYBOOK not set" 2
[[ -n "$PLUGIN" ]]          || die "AMMP_VALIDATE_PLUGIN not set" 2
[[ -n "$MARKETPLACE_REPO" ]] || die "AMMP_VALIDATE_MARKETPLACE_REPO not set" 2

spec_json=$(cat)
[[ -n "$spec_json" ]] || die "empty spec on stdin" 2

tmp=$(mktemp -d -t ammp-behavioral-XXXXXX)
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

# .mcp.json wires the AMMP MCP for prompt-time use.
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

# ── Step 1: install the plugin via real `claude plugin install` ──────
log "installing plugin: $PLUGIN from $MARKETPLACE_REPO"

claude plugin marketplace add "$MARKETPLACE_REPO" --scope local \
  >"$tmp/marketplace-add.log" 2>&1 \
  || { cat "$tmp/marketplace-add.log" >&2; die "claude plugin marketplace add failed"; }
log "marketplace added"

mp_name=$(basename "$MARKETPLACE_REPO")
claude plugin install "$PLUGIN@$mp_name" --scope local \
  >"$tmp/plugin-install.log" 2>&1 \
  || { cat "$tmp/plugin-install.log" >&2; die "claude plugin install failed"; }
log "plugin installed"

# Verify install via canonical marker.
local_settings="$workdir/.claude/settings.local.json"
[[ -f "$local_settings" ]] \
  || die "settings.local.json missing after install"
if ! jq -e --arg id "$PLUGIN@$mp_name" '.enabledPlugins | has($id)' "$local_settings" >/dev/null; then
  die "$PLUGIN@$mp_name not enabled in settings.local.json"
fi
log "install verified: $PLUGIN@$mp_name"

# ── Step 2: iterate the behavioral prompts ───────────────────────────
prompt_lines=()
while IFS= read -r line; do
  [[ -n "$line" ]] && prompt_lines+=("$line")
done < <(python3 -c "
import json, sys
spec = json.loads(sys.argv[1])
for p in spec.get('prompts', []):
    if p.get('mode') == 'behavioral':
        print(json.dumps({'id': p['id'], 'task': p['task'], 'expect': p.get('expect', {})}, ensure_ascii=False))
" "$spec_json")

if [[ "${#prompt_lines[@]}" -eq 0 ]]; then
  die "no behavioral prompts in spec (looking for entries with mode='behavioral')" 2
fi

pass_count=0
fail_count=0

for line in "${prompt_lines[@]}"; do
  pid=$(printf '%s' "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
  task=$(printf '%s' "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['task'])")
  log "prompt: $pid"

  # Behavioral prompt: load the playbook over MCP, then execute the
  # task by actually invoking bundled scripts / writing files.
  start_prompt="You are a mentee operator under the mentorship of $PLAYBOOK_NAME on the AMMP server at $AMMP_URL.

Step 1: call mcp__helmguild-ammp__GetPlaybook with id=\"$PLAYBOOK\" and mentor=\"$MENTOR\". Quote the playbook name back.
Step 2: for any skill whose summary is clearly relevant to the user's task below, call mcp__helmguild-ammp__GetSkill to fetch the body.
Step 3: ACTUALLY EXECUTE the user's task. The plugin is installed at .claude/plugins/$PLUGIN/ — bundled scripts live under that dir's scripts/ subdir. Use the Bash tool to invoke them. Use the Write tool to author files.
Step 4: in 2-3 sentences, state what you did + the absolute paths of files you created.

USER TASK:
$task"

  cd "$workdir"
  if command -v gtimeout >/dev/null 2>&1; then
    response=$(gtimeout 300 claude -p "$start_prompt" \
      --model "$CLAUDE_MODEL" \
      --permission-mode bypassPermissions \
      --allowed-tools "mcp__helmguild-ammp__GetPlaybook,mcp__helmguild-ammp__GetSkill,mcp__helmguild-ammp__ListMentors,Bash,Write,Read" \
      2>"$tmp/mentee-$pid.err" || echo "")
  else
    response=$(claude -p "$start_prompt" \
      --model "$CLAUDE_MODEL" \
      --permission-mode bypassPermissions \
      --allowed-tools "mcp__helmguild-ammp__GetPlaybook,mcp__helmguild-ammp__GetSkill,mcp__helmguild-ammp__ListMentors,Bash,Write,Read" \
      2>"$tmp/mentee-$pid.err" || echo "")
  fi

  printf '%s\n' "$response" > "$tmp/mentee-$pid.out"

  # Run expect rules in Python (text + side-effect).
  result=$(MENTEE_RESPONSE_DIR="$tmp" MENTEE_WORKDIR="$workdir" python3 - "$line" <<'PYEOF'
import json, re, sys, os
spec = json.loads(sys.argv[1])
expect = spec.get("expect", {})
resp_file = os.path.join(os.environ["MENTEE_RESPONSE_DIR"], f"mentee-{spec['id']}.out")
workdir = os.environ["MENTEE_WORKDIR"]
with open(resp_file) as fh:
    response = fh.read()
response_low = response.lower()
failures = []

# Text rules (same as Layer 1).
mms = expect.get("must_mention_skill")
if isinstance(mms, str) and mms.lower() not in response_low:
    failures.append(f"must_mention_skill: {mms!r} not in response")

mc = expect.get("must_contain")
if isinstance(mc, list):
    for entry in mc:
        if not isinstance(entry, str):
            continue
        alts = [a.strip().lower() for a in entry.split("|") if a.strip()]
        if not any(a in response_low for a in alts):
            failures.append(f"must_contain: none of {alts!r} matched")

mcp_pat = expect.get("must_contain_pattern")
if isinstance(mcp_pat, str):
    if not re.search(mcp_pat, response, re.IGNORECASE):
        failures.append(f"must_contain_pattern: {mcp_pat!r} did not match")

# Side-effect rules (Layer 2 additions).
def _resolve(p):
    return p if os.path.isabs(p) else os.path.join(workdir, p)

mcf = expect.get("must_create_file")
paths = [mcf] if isinstance(mcf, str) else (mcf if isinstance(mcf, list) else [])
for p in paths:
    if isinstance(p, str):
        rp = _resolve(p)
        if not os.path.isfile(rp):
            failures.append(f"must_create_file: {rp!r} does not exist (or not a regular file)")

mcd = expect.get("must_create_dir")
dirs = [mcd] if isinstance(mcd, str) else (mcd if isinstance(mcd, list) else [])
for p in dirs:
    if isinstance(p, str):
        rp = _resolve(p)
        if not os.path.isdir(rp):
            failures.append(f"must_create_dir: {rp!r} does not exist (or not a directory)")

print(json.dumps({"id": spec["id"], "passed": not failures, "failures": failures}))
PYEOF
  )

  passed=$(printf '%s' "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['passed'])")
  failures=$(printf '%s' "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(' | '.join(d['failures']) or '(none)')")

  if [[ "$passed" == "True" ]]; then
    pass_count=$((pass_count+1))
    [[ "$OUTPUT_FORMAT" == "text" ]] && printf '✓ %s\n' "$pid" || true
  else
    fail_count=$((fail_count+1))
    [[ "$OUTPUT_FORMAT" == "text" ]] && printf '✗ %s — %s\n' "$pid" "$failures" || true
  fi
done

total=$((pass_count + fail_count))
[[ "$OUTPUT_FORMAT" == "text" ]] && printf '\n%d/%d behavioral prompts passed\n' "$pass_count" "$total"
[[ "$fail_count" -eq 0 ]]
