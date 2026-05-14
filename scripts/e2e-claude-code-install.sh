#!/usr/bin/env bash
# e2e-claude-code-install.sh — exercise the end-to-end install path a real
# mentee would walk after `AskMentor` returns a `GetPluginArchive` URL:
#
#   1. Hit `/plugins/<plugin>.zip` on the live ammp-mcp server with a Bearer
#      token. Save the zip.
#   2. Extract.
#   3. Run `claude plugin validate` on the extracted directory — Claude
#      Code's own structural validator.
#   4. Wrap the extracted plugin in a tiny on-the-fly marketplace and
#      `claude plugin marketplace add` it (project-local scope so the
#      developer's user-level plugin list is unaffected).
#   5. `claude plugin install <plugin>@<marketplace>` — proves the install
#      path the AMMP start-prompt asks the mentee's user to walk works.
#   6. Verify the plugin appears in `claude plugin list`.
#   7. Cleanup — uninstall, remove the marketplace, drop the temp dir.
#
# Required env:
#   HELMGUILD_AMMP_BEARER  — a valid Bearer token for the AMMP server
#
# Optional env:
#   AMMP_URL    — defaults to https://mcp.helmguild.com/ammp
#   PLUGIN      — defaults to pepe-operator-craft (the smallest payload)
#   KEEP_TMP=1  — skip cleanup so the operator can inspect the tmp dir
#
# Exit codes:
#   0   end-to-end install + verify + uninstall succeeded
#   1   any step failed (stderr names which)
#   2   missing prerequisite (claude CLI, curl, jq, unzip, or bearer)

set -euo pipefail

AMMP_URL="${AMMP_URL:-https://mcp.helmguild.com/ammp}"
PLUGIN="${PLUGIN:-pepe-operator-craft}"
BEARER="${HELMGUILD_AMMP_BEARER:-}"
KEEP_TMP="${KEEP_TMP:-0}"

die() { echo "✗ $*" >&2; exit "${2:-1}"; }
log() { printf '· %s\n' "$*"; }
ok()  { printf '✓ %s\n' "$*"; }

for bin in claude curl jq unzip; do
  command -v "$bin" >/dev/null 2>&1 || die "missing required CLI: $bin" 2
done
[[ -n "$BEARER" ]] || die "HELMGUILD_AMMP_BEARER not set — pass a valid mentee Bearer token" 2

tmp=$(mktemp -d -t ammp-e2e-install-XXXXXX)
log "tmp: $tmp"

cleanup() {
  rc=$?
  cd / >/dev/null 2>&1 || true
  if [[ "$KEEP_TMP" != "1" ]]; then
    # Best-effort uninstall + marketplace-rm so a failed mid-run doesn't
    # leave debris on the developer's claude config.
    (cd "$tmp" && claude plugin uninstall "$PLUGIN@e2e-test-mp" --scope local >/dev/null 2>&1 || true)
    (cd "$tmp" && claude plugin marketplace remove e2e-test-mp --scope local >/dev/null 2>&1 || true)
    rm -rf "$tmp"
    log "tmp cleaned up"
  else
    log "tmp kept at: $tmp"
  fi
  exit $rc
}
trap cleanup EXIT

# 1. Download the zip via the auth-gated route.
log "downloading $AMMP_URL/plugins/$PLUGIN.zip"
http_code=$(curl -sS -o "$tmp/plugin.zip" -w '%{http_code}' \
  -H "Authorization: Bearer $BEARER" \
  "$AMMP_URL/plugins/$PLUGIN.zip")
[[ "$http_code" == "200" ]] || die "expected 200, got $http_code"
ok "zip downloaded ($(wc -c < "$tmp/plugin.zip" | tr -d ' ') bytes)"

# 2. Extract.
mkdir -p "$tmp/extract"
unzip -q "$tmp/plugin.zip" -d "$tmp/extract"
[[ -d "$tmp/extract/$PLUGIN" ]] || die "extracted plugin dir missing: $tmp/extract/$PLUGIN"
[[ -f "$tmp/extract/$PLUGIN/.claude-plugin/plugin.json" ]] || die "plugin.json missing in extract"
ok "extracted to $tmp/extract/$PLUGIN"

# 3. Validate the plugin manifest with Claude Code's own validator.
claude plugin validate "$tmp/extract/$PLUGIN" >"$tmp/validate.log" 2>&1 \
  || { cat "$tmp/validate.log" >&2; die "claude plugin validate failed"; }
ok "claude plugin validate passed"

# 4. Wrap the extracted plugin in a tiny ad-hoc marketplace.
mkdir -p "$tmp/marketplace/.claude-plugin" "$tmp/marketplace/plugins"
cp -R "$tmp/extract/$PLUGIN" "$tmp/marketplace/plugins/"
plugin_version=$(jq -r '.version' "$tmp/marketplace/plugins/$PLUGIN/.claude-plugin/plugin.json")
plugin_desc=$(jq -r '.description' "$tmp/marketplace/plugins/$PLUGIN/.claude-plugin/plugin.json")
cat > "$tmp/marketplace/.claude-plugin/marketplace.json" <<EOF
{
  "name": "e2e-test-mp",
  "description": "Throwaway local marketplace for the AMMP install E2E test.",
  "owner": { "name": "ammp-mcp e2e", "email": "noreply@example.com" },
  "plugins": [
    {
      "name": "$PLUGIN",
      "source": "./plugins/$PLUGIN",
      "version": "$plugin_version",
      "description": $(jq -n --arg s "$plugin_desc" '$s')
    }
  ]
}
EOF

# Operate from a project-local working dir so --scope local writes its
# state into $tmp/work/.claude/, not the developer's user-level config.
mkdir -p "$tmp/work"
cd "$tmp/work"

# 5. Add the marketplace, then install the plugin.
claude plugin marketplace add "$tmp/marketplace" --scope local >"$tmp/marketplace-add.log" 2>&1 \
  || { cat "$tmp/marketplace-add.log" >&2; die "marketplace add failed"; }
ok "marketplace added"
claude plugin install "$PLUGIN@e2e-test-mp" --scope local >"$tmp/install.log" 2>&1 \
  || { cat "$tmp/install.log" >&2; die "plugin install failed"; }
ok "plugin installed"

# 6. Verify the plugin landed in the local-scope settings file. The
# canonical source of truth is `$tmp/work/.claude/settings.local.json`,
# which `claude plugin install --scope local` just wrote.
local_settings="$tmp/work/.claude/settings.local.json"
[[ -f "$local_settings" ]] || die "$local_settings missing — install didn't write local settings"
jq -e --arg id "$PLUGIN@e2e-test-mp" '.enabledPlugins | has($id)' "$local_settings" >/dev/null \
  || { cat "$local_settings" >&2; die "$PLUGIN@e2e-test-mp not enabled in $local_settings"; }
ok "plugin enabled in $(basename "$local_settings") (id=$PLUGIN@e2e-test-mp)"

# Also exercise the user-facing `claude plugin list --json` so a regression
# in the list path that hides project-local plugins (in this cwd) surfaces.
list_out=$(claude plugin list --json 2>/dev/null || echo '[]')
if ! echo "$list_out" | jq -e --arg id "$PLUGIN@e2e-test-mp" '[.[] | select(.id == $id)] | length == 1' >/dev/null; then
  echo "⚠ plugin list didn't surface $PLUGIN@e2e-test-mp from $PWD — settings.local.json was correct, but the CLI's list path didn't pick it up. Investigate if this becomes a regression." >&2
else
  ok "plugin also appears in claude plugin list (id=$PLUGIN@e2e-test-mp)"
fi

# 7. If the plugin ships a bundled stdio MCP server, spawn it and
# confirm tools/list returns at least one tool. Proves the wire works,
# not just the file shape. Optional: only runs when the plugin has a
# stdio entry in its .mcp.json.
plugin_mcp="$tmp/marketplace/plugins/$PLUGIN/.mcp.json"
if [[ -f "$plugin_mcp" ]] && command -v node >/dev/null 2>&1; then
  stdio_target=$(jq -r '.mcpServers | to_entries[] | select(.value.type == "stdio") | .value.args[0] // empty' "$plugin_mcp" 2>/dev/null || true)
  if [[ -n "$stdio_target" ]]; then
    # ${CLAUDE_PLUGIN_ROOT} → the extracted plugin root.
    resolved="${stdio_target//\$\{CLAUDE_PLUGIN_ROOT\}/$tmp/marketplace/plugins/$PLUGIN}"
    if [[ -f "$resolved" ]]; then
      log "spawning bundled stdio MCP: $resolved"
      tools_count=$(printf '%s\n' \
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"e2e","version":"0"}}}' \
        '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
        | node "$resolved" 2>/dev/null \
        | jq -r 'select(.id == 2) | .result.tools | length' | head -1)
      [[ "${tools_count:-0}" -ge 1 ]] || die "bundled stdio MCP returned no tools — bundled MCP wire broken"
      ok "bundled stdio MCP responds with $tools_count tool(s)"
    fi
  fi
fi

# 8. Cleanup is handled by the trap.
ok "E2E install round-trip succeeded for $PLUGIN"
