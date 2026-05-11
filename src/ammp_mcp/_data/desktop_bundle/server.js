#!/usr/bin/env node
// DXT/MCPB entry point — spawns the bundled `mcp-remote` proxy with
// the current process's node binary, so the connector doesn't depend
// on the host having npx (or any node) on PATH. mcp-remote bridges
// stdio MCP frames to the remote HTTPS endpoint with the Bearer header
// substituted from `user_config.bearer_token` at install time.

const path = require('path');
const { spawn } = require('child_process');

const url = process.env.AMMP_URL || 'https://mcp.helmguild.com/ammp/mcp/';
const token = process.env.BEARER_TOKEN || '';

const proxy = path.join(__dirname, 'node_modules', 'mcp-remote', 'dist', 'proxy.js');

const child = spawn(
  process.execPath,
  [proxy, url, '--header', `Authorization:Bearer ${token}`],
  { stdio: 'inherit' }
);

child.on('error', (err) => {
  process.stderr.write(`helmguild AMMP shim: failed to spawn proxy: ${err.message}\n`);
  process.exit(1);
});

child.on('exit', (code) => process.exit(code === null ? 0 : code));
