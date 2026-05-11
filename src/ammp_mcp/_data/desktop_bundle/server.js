#!/usr/bin/env node
// DXT/MCPB entry point — delegates to the `mcp-remote` npm shim so we
// don't have to bundle node_modules. Claude Desktop launches this via
// the manifest's `server.entry_point` field; the manifest passes the
// user's Bearer token in via the BEARER_TOKEN env var (substituted from
// `user_config.bearer_token` at install time).

const { spawn } = require('child_process');

const url = process.env.AMMP_URL || 'https://mcp.helmguild.com/ammp/mcp';
const token = process.env.BEARER_TOKEN || '';

const child = spawn(
  'npx',
  ['-y', 'mcp-remote', url, '--header', `Authorization:Bearer ${token}`],
  { stdio: 'inherit' }
);

child.on('exit', (code) => process.exit(code === null ? 0 : code));
