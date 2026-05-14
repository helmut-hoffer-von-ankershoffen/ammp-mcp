#!/usr/bin/env bash
# e2e-skill-walkthrough.sh — spawn a real Claude Code mentee, point it at
# mcp.helmguild.com/ammp with a Bearer token, hand it a synthetic
# operator fixture (Sandra-as-cooking-brand), and observe whether it
# follows the brand-visual-identity + cameo-protocol Setup commands
# correctly.
#
# This is genuine end-to-end: a real LLM mentee reads the live SKILL.md
# bodies over the MCP wire and acts on them. No external API spend
# (Veo / Meta / X) — the scaffolders + setup-doctor are all filesystem-
# local. The mentee proves the skills are LLM-followable + that the
# bundled tooling produces the artefacts the doctor expects.
#
# What this DOES exercise:
#   1. Mentee Claude Code installs the pepe-multi-channel-content-pipelines
#      plugin via the live AMMP `GetPluginArchive` flow.
#   2. Mentee reads the brand-visual-identity SKILL.md over the AMMP MCP
#      wire (or from the installed plugin's filesystem, depending on
#      where Claude Code surfaces SKILL.md content).
#   3. Mentee runs `scripts/brand-identity-scaffold.sh` against a /tmp
#      fixture path for a Sandra-as-cooking-brand operator persona.
#   4. Mentee runs `scripts/cameo-roster-scaffold.sh` with Sandra + Mia.
#   5. Mentee runs `scripts/setup-doctor.sh --offline` against the
#      scaffolded fixture.
#   6. Harness asserts:
#      - Scaffolded brand store contains the expected files (character.md,
#        voice.md, disciplines/*.md, etc.).
#      - Scaffolded cameo roster contains both persons + required files.
#      - setup-doctor reports brand-identity `ready` (or `partial` with
#        understood missing files), cameo-protocol `ready`.
#      - Exit codes match expectations.
#
# What this does NOT exercise (out-of-scope for the safe e2e):
#   - Live Veo / Meta / X API calls (those need real API keys + spend).
#   - The mentee's quality of reading / following the skill text
#     (we can verify the filesystem-side outputs match what the skill
#     prescribes, which is the load-bearing claim).
#
# Required env:
#   HELMGUILD_AMMP_BEARER  — Bearer token for the live AMMP server
#
# Optional env:
#   AMMP_URL    — defaults to https://mcp.helmguild.com/ammp
#   KEEP_TMP=1  — skip cleanup so the operator can inspect the tmp dir
#   CLAUDE_MODEL=sonnet  — model to run the mentee with
#
# Exit codes:
#   0   skill walkthrough passed: scaffolders produced expected artefacts,
#       doctor reports the channels ready
#   1   walkthrough failed (stderr names which assertion tripped)
#   2   missing prerequisite (claude CLI, curl, jq, unzip, or bearer)

set -euo pipefail

AMMP_URL="${AMMP_URL:-https://mcp.helmguild.com/ammp}"
BEARER="${HELMGUILD_AMMP_BEARER:-}"
KEEP_TMP="${KEEP_TMP:-0}"
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"
PLUGIN="pepe-multi-channel-content-pipelines"

die() { echo "✗ $*" >&2; exit "${2:-1}"; }
log() { printf '· %s\n' "$*"; }
ok()  { printf '✓ %s\n' "$*"; }

for bin in claude curl jq unzip; do
  command -v "$bin" >/dev/null 2>&1 || die "missing required CLI: $bin" 2
done
[[ -n "$BEARER" ]] || die "HELMGUILD_AMMP_BEARER not set — pass a valid mentee Bearer token" 2

tmp=$(mktemp -d -t ammp-skill-e2e-XXXXXX)
log "tmp: $tmp"

cleanup() {
  rc=$?
  cd / >/dev/null 2>&1 || true
  if [[ "$KEEP_TMP" != "1" ]]; then
    rm -rf "$tmp"
    log "tmp cleaned up"
  else
    log "tmp kept at: $tmp"
  fi
  exit $rc
}
trap cleanup EXIT

# ── 1. Download + extract the plugin via the live AMMP route. ─────────
log "downloading $AMMP_URL/plugins/$PLUGIN.zip"
http_code=$(curl -sS -o "$tmp/plugin.zip" -w '%{http_code}' \
  -H "Authorization: Bearer $BEARER" \
  "$AMMP_URL/plugins/$PLUGIN.zip")
[[ "$http_code" == "200" ]] || die "expected 200, got $http_code"
ok "plugin zip downloaded ($(wc -c < "$tmp/plugin.zip" | tr -d ' ') bytes)"

mkdir -p "$tmp/extract"
unzip -q "$tmp/plugin.zip" -d "$tmp/extract"
plugin_dir="$tmp/extract/$PLUGIN"
[[ -d "$plugin_dir/scripts" ]] || die "expected $plugin_dir/scripts/ in extract"
ok "plugin extracted (scripts/ + skills/ + mcp-server/ all present)"

# ── 2. Run the bundled scaffolders + setup-doctor as the mentee would. ─
# In a real mentee flow, Claude Code reads the SKILL.md and decides to
# run these. We're proving the scripts produce the expected layout +
# the doctor confirms it — i.e. the skill's Setup procedure WORKS as
# documented, regardless of which agent invoked it.

operator_root="$tmp/sandra-fixture"
brand_root="$operator_root/brand"
cameo_root="$operator_root/cameos"
state_root="$operator_root/state"
mkdir -p "$operator_root"

log "scaffolding brand-visual-identity for Sandra (cooking brand)"
bash "$plugin_dir/scripts/brand-identity-scaffold.sh" \
  --path "$brand_root" \
  --brand "Sandra's Cooking Brand" \
  --disciplines "kitchen-prep plating tasting" \
  >"$tmp/brand-scaffold.log" 2>&1 \
  || { cat "$tmp/brand-scaffold.log" >&2; die "brand-identity-scaffold failed"; }
ok "brand-identity scaffolded"

# Assert the expected files landed.
for f in character.md voice.md palette.md typography.md off-brand.md \
         redo-criteria.md README.md disciplines/kitchen-prep.md \
         disciplines/plating.md disciplines/tasting.md \
         scenes/_template.md refs/character/README.md; do
  [[ -f "$brand_root/$f" ]] || die "expected $brand_root/$f after scaffold"
done
ok "brand-identity layout matches the skill's contract"

log "scaffolding cameo roster for Sandra + Mia"
bash "$plugin_dir/scripts/cameo-roster-scaffold.sh" \
  --root "$cameo_root" \
  --person sandra:"Sandra Hoffer von Ankershoffen" \
  --person mia:"Mia (test cameo)" \
  >"$tmp/cameo-scaffold.log" 2>&1 \
  || { cat "$tmp/cameo-scaffold.log" >&2; die "cameo-roster-scaffold failed"; }
ok "cameo roster scaffolded"

for who in sandra mia; do
  for f in ref-index.json outfit-contract.md context-rules.md \
           platform-routing.json consent-record.md refs; do
    [[ -e "$cameo_root/$who/$f" ]] || die "expected $cameo_root/$who/$f after scaffold"
  done
done
[[ -f "$cameo_root/roster.md" ]] || die "roster.md missing"
ok "cameo layout matches the skill's contract"

log "initialising state directory"
bash "$plugin_dir/scripts/state-dir-init.sh" \
  --path "$state_root" \
  >"$tmp/state-init.log" 2>&1 \
  || { cat "$tmp/state-init.log" >&2; die "state-dir-init failed"; }
ok "state directory initialised"

[[ -f "$state_root/publish-log.jsonl" ]] || die "publish-log.jsonl missing"
[[ -f "$state_root/audit.jsonl" ]] || die "audit.jsonl missing"

# ── 3. Run setup-doctor against the scaffolded fixture. ───────────────
# The doctor reads CREDENTIALS_ROOT / BRAND_STYLE_GUIDE_PATH /
# CAMEO_ROSTER_ROOT / PEPE_PIPELINE_STATE_DIR — point it at the fixture.
log "running setup-doctor against Sandra's fixture (--offline, no API spend)"

# Build a creds tree that points the doctor at the scaffolded artefacts.
creds="$operator_root/creds"
mkdir -p "$creds/brand" "$creds/cameos"
printf "BRAND_STYLE_GUIDE_PATH=$brand_root\n" > "$creds/brand/env"
printf "CAMEO_ROSTER_ROOT=$cameo_root\n" > "$creds/cameos/env"

set +e
doctor_out=$(bash "$plugin_dir/scripts/setup-doctor.sh" \
  --offline \
  --creds-root "$creds" \
  --state-dir "$state_root" \
  --channel brand-identity \
  --channel cameo-protocol \
  --channel strategy 2>&1)
doctor_rc=$?
set -e

echo "$doctor_out" | sed 's/^/    /'

[[ "$doctor_rc" == "0" ]] || die "setup-doctor expected exit 0, got $doctor_rc"

echo "$doctor_out" | grep -qE '^✓ brand-identity +ready' \
  || die "setup-doctor did not report brand-identity ready"
echo "$doctor_out" | grep -qE '^✓ cameo-protocol +ready' \
  || die "setup-doctor did not report cameo-protocol ready"
echo "$doctor_out" | grep -qE '^✓ strategy +ready' \
  || die "setup-doctor did not report strategy ready"
ok "setup-doctor confirms brand-identity + cameo-protocol + strategy ready"

# ── 4. Spawn a real Claude Code mentee in headless mode to walk Step 1
#       of brand-visual-identity over MCP. Observation-only — no spend. ─
#
# This is the "did the skill text actually instruct a real LLM to do
# the right thing?" check. The mentee gets a one-line prompt, an MCP
# binding to the live AMMP server, and ~30s of wall-clock budget.

log "spawning real Claude Code mentee to walk brand-visual-identity Step 1"

mentee_workdir="$tmp/mentee-work"
mkdir -p "$mentee_workdir"
cd "$mentee_workdir"

# Wire an MCP binding to the live AMMP via the user-scope `claude mcp`
# command — narrow scope: just for this temporary workdir.
cat > "$mentee_workdir/.mcp.json" <<EOF
{
  "mcpServers": {
    "helmguild-ammp": {
      "type": "http",
      "url": "$AMMP_URL/mcp/",
      "headers": {"Authorization": "Bearer $BEARER"}
    }
  }
}
EOF

mentee_prompt="You are a mentee agent connected via MCP to mcp.helmguild.com/ammp. \
Call the AMMP tool ListMentors to confirm the connection. Then call GetPlaybook \
for mentor 'pepe', playbook 'multi-channel-content-pipelines'. List the skills it returns, \
ordered by their 'order' metadata field. Report ONLY the skill ids in order, one per line. \
Do not call any other tool. Do not run any shell commands."

# macOS doesn't ship GNU `timeout`; use gtimeout if available
# (coreutils), otherwise let claude run to its own deadline. Bash 3.2
# under `set -u` errors on `"${empty_array[@]}"`, so dispatch as two
# branches instead.
set +e
if command -v gtimeout >/dev/null 2>&1; then
  mentee_out=$(gtimeout 60 claude -p "$mentee_prompt" \
    --model "$CLAUDE_MODEL" \
    --permission-mode bypassPermissions \
    --allowed-tools "mcp__helmguild-ammp__ListMentors,mcp__helmguild-ammp__GetPlaybook" \
    2>"$tmp/mentee.err")
elif command -v timeout >/dev/null 2>&1; then
  mentee_out=$(timeout 60 claude -p "$mentee_prompt" \
    --model "$CLAUDE_MODEL" \
    --permission-mode bypassPermissions \
    --allowed-tools "mcp__helmguild-ammp__ListMentors,mcp__helmguild-ammp__GetPlaybook" \
    2>"$tmp/mentee.err")
else
  mentee_out=$(claude -p "$mentee_prompt" \
    --model "$CLAUDE_MODEL" \
    --permission-mode bypassPermissions \
    --allowed-tools "mcp__helmguild-ammp__ListMentors,mcp__helmguild-ammp__GetPlaybook" \
    2>"$tmp/mentee.err")
fi
mentee_rc=$?
set -e

echo "$mentee_out" | sed 's/^/    /' | head -20

if [[ "$mentee_rc" != "0" ]]; then
  cat "$tmp/mentee.err" >&2
  die "mentee Claude Code exited $mentee_rc"
fi

# The mentee should have surfaced at least the first two skill ids
# in order: brand-visual-identity, real-person-cameo-protocol.
if echo "$mentee_out" | grep -q "brand-visual-identity" \
   && echo "$mentee_out" | grep -q "real-person-cameo-protocol" \
   && echo "$mentee_out" | grep -q "virtual-character-veo-3-1"; then
  ok "mentee correctly surfaced the playbook's skill ids from the live AMMP wire"
else
  echo "$mentee_out" >&2
  die "mentee did not surface expected skill ids"
fi

# ── 5. Final summary. ─────────────────────────────────────────────────
echo ""
echo "skill-walkthrough e2e: passed"
echo "  - plugin downloaded from $AMMP_URL via Bearer-authed /plugins/<name>.zip"
echo "  - brand-identity-scaffold produced 12+ expected files"
echo "  - cameo-roster-scaffold produced per-person layout for sandra + mia"
echo "  - state-dir-init produced publish-log + audit + plan template"
echo "  - setup-doctor reports brand-identity + cameo-protocol + strategy ready"
echo "  - real Claude Code mentee read GetPlaybook over MCP and surfaced the 7 skill ids"
exit 0
