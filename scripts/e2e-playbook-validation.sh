#!/usr/bin/env bash
# e2e-playbook-validation.sh — run a playbook's validation prompts
# against a freshly-spawned Claude Code mentee and assert outcomes.
#
# This is THE end-to-end test of AMMP-as-mentoring, not just AMMP-as-
# RPC. It answers: "does loading this playbook actually teach a mentee
# to do the things the playbook claims to teach?"
#
# How it works:
#   1. Read the playbook spec from stdin (JSON) — the calling `ammp
#      playbook validate <id>` CLI hands us `{"prompts": [...]}`.
#   2. For each prompt:
#      a. Spawn a fresh `claude -p` mentee with the AMMP MCP wire
#         pre-loaded (.mcp.json in tmp workdir).
#      b. The mentee gets a single combined prompt: "Load this
#         playbook via AMMP, then answer this user task: <task>".
#      c. Capture the mentee's response.
#      d. Run the `expect` rules in the spec against the response.
#   3. Report per-prompt pass/fail; exit 0 if all pass, 1 otherwise.
#
# Required env (set by the `ammp playbook validate` CLI):
#   AMMP_VALIDATE_MENTOR         — mentor slug (e.g. "pepe")
#   AMMP_VALIDATE_PLAYBOOK       — playbook id (e.g. "knowledge-management")
#   AMMP_VALIDATE_PLAYBOOK_NAME  — display name
#   AMMP_VALIDATE_OUTPUT_FORMAT  — "json" or "text"
#   HELMGUILD_AMMP_BEARER        — mentee Bearer for the AMMP wire
#
# Optional env:
#   AMMP_URL    — defaults to https://mcp.helmguild.com/ammp
#   CLAUDE_MODEL — defaults to sonnet
#   KEEP_TMP=1  — preserve the tmp workdir for inspection
#
# Stdin (UTF-8 JSON):
#   {"prompts": [{"id", "task", "expect": {...}}, ...]}
#
# Exit codes:
#   0 — every prompt passed every expect rule
#   1 — at least one prompt failed at least one rule
#   2 — usage / setup error

set -euo pipefail

AMMP_URL="${AMMP_URL:-https://mcp.helmguild.com/ammp}"
BEARER="${HELMGUILD_AMMP_BEARER:-}"
KEEP_TMP="${KEEP_TMP:-0}"
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"
MENTOR="${AMMP_VALIDATE_MENTOR:-}"
PLAYBOOK="${AMMP_VALIDATE_PLAYBOOK:-}"
PLAYBOOK_NAME="${AMMP_VALIDATE_PLAYBOOK_NAME:-}"
OUTPUT_FORMAT="${AMMP_VALIDATE_OUTPUT_FORMAT:-text}"

die() { echo "✗ $*" >&2; exit "${2:-1}"; }
log() { [[ "$OUTPUT_FORMAT" == "text" ]] && printf '· %s\n' "$*" >&2 || true; }

for bin in claude curl jq python3; do
  command -v "$bin" >/dev/null 2>&1 || die "missing required CLI: $bin" 2
done
[[ -n "$BEARER" ]]   || die "HELMGUILD_AMMP_BEARER not set" 2
[[ -n "$MENTOR" ]]   || die "AMMP_VALIDATE_MENTOR not set" 2
[[ -n "$PLAYBOOK" ]] || die "AMMP_VALIDATE_PLAYBOOK not set" 2

spec_json=$(cat)
[[ -n "$spec_json" ]] || die "empty spec on stdin (expected {\"prompts\": [...]} JSON)" 2

tmp=$(mktemp -d -t ammp-validate-XXXXXX)
log "tmp: $tmp"
cleanup() {
  rc=$?
  cd / >/dev/null 2>&1 || true
  [[ "$KEEP_TMP" != "1" ]] && rm -rf "$tmp"
  exit $rc
}
trap cleanup EXIT

# .mcp.json wires the mentee to the live AMMP server.
mentee_workdir="$tmp/mentee"
mkdir -p "$mentee_workdir"
cd "$mentee_workdir"
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

# Iterate the prompts via a Python helper (jq-level introspection of
# nested objects is painful; Python is shipped on macOS + ubuntu by
# default).
# Bash 3.2 (macOS default) doesn't have `mapfile`; use a while-read
# loop into an array instead.
prompt_lines=()
while IFS= read -r line; do
  [[ -n "$line" ]] && prompt_lines+=("$line")
done < <(python3 -c "
import json, sys
spec = json.loads(sys.argv[1])
for p in spec.get('prompts', []):
    print(json.dumps({'id': p['id'], 'task': p['task'], 'expect': p.get('expect', {})}, ensure_ascii=False))
" "$spec_json")

[[ "${#prompt_lines[@]}" -gt 0 ]] || die "no prompts in spec"

pass_count=0
fail_count=0
results_json="["
first=1

for line in "${prompt_lines[@]}"; do
  pid=$(printf '%s' "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
  task=$(printf '%s' "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['task'])")
  log "prompt: $pid"

  # Combined mentee prompt: load the playbook, then answer the task.
  start_prompt="You are now operating under the mentorship of $PLAYBOOK_NAME on the AMMP server at $AMMP_URL.

Step 1: call mcp__helmguild-ammp__GetPlaybook with id=\"$PLAYBOOK\" and mentor=\"$MENTOR\". Quote the playbook name back.

Step 2: read every skill summary returned. For any skill whose summary suggests it's relevant to the user's task below, call mcp__helmguild-ammp__GetSkill to fetch the full body.

Step 3: answer the user's task below in 4-8 sentences. Cite specific skill ids and any bundled scripts you'd run. Be terse, no preamble.

USER TASK:
$task"

  if command -v gtimeout >/dev/null 2>&1; then
    response=$(gtimeout 180 claude -p "$start_prompt" \
      --model "$CLAUDE_MODEL" \
      --permission-mode bypassPermissions \
      --allowed-tools "mcp__helmguild-ammp__GetPlaybook,mcp__helmguild-ammp__GetSkill,mcp__helmguild-ammp__ListMentors" \
      2>"$tmp/mentee-$pid.err" || echo "")
  else
    response=$(claude -p "$start_prompt" \
      --model "$CLAUDE_MODEL" \
      --permission-mode bypassPermissions \
      --allowed-tools "mcp__helmguild-ammp__GetPlaybook,mcp__helmguild-ammp__GetSkill,mcp__helmguild-ammp__ListMentors" \
      2>"$tmp/mentee-$pid.err" || echo "")
  fi

  printf '%s\n' "$response" > "$tmp/mentee-$pid.out"

  # Run the expect rules in Python — easier than shelling them.
  result=$(MENTEE_RESPONSE_DIR="$tmp" python3 - "$line" <<'PYEOF'
import json, re, sys, os
spec = json.loads(sys.argv[1])
expect = spec.get("expect", {})
resp_file = os.path.join(os.environ["MENTEE_RESPONSE_DIR"], f"mentee-{spec['id']}.out")
with open(resp_file) as fh:
    response = fh.read()
response_low = response.lower()
failures = []
mms = expect.get("must_mention_skill")
if isinstance(mms, str) and mms.lower() not in response_low:
    failures.append(f"must_mention_skill: {mms!r} not in response")
elif isinstance(mms, list):
    for s in mms:
        if isinstance(s, str) and s.lower() not in response_low:
            failures.append(f"must_mention_skill: {s!r} not in response")
mc = expect.get("must_contain")
if isinstance(mc, list):
    for entry in mc:
        if not isinstance(entry, str):
            continue
        alternatives = [a.strip().lower() for a in entry.split("|") if a.strip()]
        if not any(a in response_low for a in alternatives):
            failures.append(f"must_contain: none of {alternatives!r} matched")
mcp_pat = expect.get("must_contain_pattern")
if isinstance(mcp_pat, str):
    if not re.search(mcp_pat, response, re.IGNORECASE):
        failures.append(f"must_contain_pattern: {mcp_pat!r} did not match")
mio = expect.get("must_invoke_or_name")
if isinstance(mio, list):
    for entry in mio:
        if not isinstance(entry, str):
            continue
        if entry.lower() not in response_low:
            failures.append(f"must_invoke_or_name: {entry!r} not named")
mmt = expect.get("must_mention_type")
if isinstance(mmt, str) and mmt.lower() not in response_low:
    failures.append(f"must_mention_type: {mmt!r} not named")
print(json.dumps({"id": spec["id"], "passed": not failures, "failures": failures, "response_length": len(response)}))
PYEOF
  )

  passed=$(printf '%s' "$result" | python3 -c "import json,sys; print(json.load(sys.stdin)['passed'])")
  failures=$(printf '%s' "$result" | python3 -c "import json,sys; print(' | '.join(json.load(sys.stdin)['failures']) or '(none)')")

  if [[ "$passed" == "True" ]]; then
    pass_count=$((pass_count+1))
    [[ "$OUTPUT_FORMAT" == "text" ]] && printf '✓ %s\n' "$pid" || true
  else
    fail_count=$((fail_count+1))
    [[ "$OUTPUT_FORMAT" == "text" ]] && printf '✗ %s — %s\n' "$pid" "$failures" || true
  fi

  [[ "$first" -eq 1 ]] || results_json+=","
  results_json+=$result
  first=0
done
results_json+="]"

total=$((pass_count + fail_count))

if [[ "$OUTPUT_FORMAT" == "json" ]]; then
  python3 -c "
import json
results = json.loads('''$results_json''')
print(json.dumps({
    'mentor': '$MENTOR',
    'playbook': '$PLAYBOOK',
    'total': len(results),
    'passed': sum(1 for r in results if r['passed']),
    'failed': sum(1 for r in results if not r['passed']),
    'results': results,
}, indent=2, ensure_ascii=False))
"
else
  printf '\n%d/%d prompts passed\n' "$pass_count" "$total"
fi

[[ "$fail_count" -eq 0 ]]
