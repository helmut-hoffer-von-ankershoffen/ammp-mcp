#!/usr/bin/env node
// Minimal stdio → Streamable-HTTP MCP proxy.
//
// Replaces `mcp-remote` for the Bearer-token case. `mcp-remote` binds
// an OAuth-callback HTTP server on localhost at startup; that bind
// fails under Claude Desktop's extension sandbox, killing the
// connector before the first MCP frame can flow. We don't need OAuth
// — AMMP is Bearer-only — so we skip all of that and proxy directly.
//
// Reads newline-delimited JSON-RPC frames from stdin, POSTs each to
// AMMP_URL with the Bearer header from BEARER_TOKEN, and emits each
// server response (JSON or SSE) back to stdout as one JSON line.
//
// Requires Node 18+ for built-in `fetch` / `ReadableStream`.

'use strict';

const URL_str = process.env.AMMP_URL || 'https://mcp.helmguild.com/ammp/mcp/';
const token = process.env.BEARER_TOKEN || '';

let sessionId = null;

function headers() {
  const h = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
    'Authorization': `Bearer ${token}`,
  };
  if (sessionId) h['Mcp-Session-Id'] = sessionId;
  return h;
}

async function emitFromResponse(res) {
  const sid = res.headers.get('mcp-session-id');
  if (sid) sessionId = sid;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('text/event-stream')) {
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    function emitEvent(event) {
      for (const rawLine of event.split('\n')) {
        const line = rawLine.replace(/\r$/, '');
        if (line.startsWith('data:')) {
          const t = line.replace(/^data:\s?/, '');
          if (t) process.stdout.write(t + '\n');
        }
      }
    }

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      // SSE events are separated by a blank line; tolerate both \n\n
      // and \r\n\r\n. Loop until no more terminator in the buffer.
      while (true) {
        const a = buf.indexOf('\n\n');
        const b = buf.indexOf('\r\n\r\n');
        const hit = a < 0 ? b : b < 0 ? a : Math.min(a, b);
        if (hit < 0) break;
        const sepLen = buf.startsWith('\r\n\r\n', hit) ? 4 : 2;
        idx = hit;
        const event = buf.slice(0, idx);
        buf = buf.slice(idx + sepLen);
        emitEvent(event);
      }
    }
    // Flush a final event even if the stream closed without the
    // trailing blank line (FastMCP's transport ends the stream right
    // after the response — no `\n\n` terminator on the last frame).
    if (buf.trim()) emitEvent(buf);
    return;
  }
  if (ct.includes('application/json')) {
    const text = await res.text();
    const t = text.trim();
    if (t) process.stdout.write(t + '\n');
    return;
  }
  // Other / empty (e.g. 202 Accepted for notifications) → no output.
  // Drain the body so the underlying socket can be reused.
  try {
    await res.text();
  } catch (_e) {
    /* ignore */
  }
}

async function postMessage(line) {
  try {
    const res = await fetch(URL_str, {
      method: 'POST',
      headers: headers(),
      body: line,
    });
    if (!res.ok && res.status !== 202) {
      let body = '';
      try {
        body = await res.text();
      } catch (_e) {
        /* ignore */
      }
      process.stderr.write(`helmguild AMMP: HTTP ${res.status} from ${URL_str}: ${body}\n`);
      return;
    }
    await emitFromResponse(res);
  } catch (err) {
    process.stderr.write(`helmguild AMMP: POST failed: ${err && err.message ? err.message : err}\n`);
  }
}

let stdinBuf = '';
process.stdin.on('data', (chunk) => {
  stdinBuf += chunk.toString();
  let idx;
  while ((idx = stdinBuf.indexOf('\n')) >= 0) {
    const line = stdinBuf.slice(0, idx).trim();
    stdinBuf = stdinBuf.slice(idx + 1);
    if (line) {
      // Fire-and-forget; multiple in-flight requests are fine because
      // the MCP server reads concurrent POSTs from the same session id.
      postMessage(line);
    }
  }
});

process.stdin.on('end', () => process.exit(0));
process.on('SIGINT', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));
