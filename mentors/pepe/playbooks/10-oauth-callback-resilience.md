# Playbook 10 — OAuth callback servers must be probe-resilient

**Principle:** When you spin up a temporary local web server to receive an OAuth callback, it must survive being hit by random crawlers, probes, and redirects without dying.

## The pattern

OAuth flows commonly work like this:
1. You generate an auth URL and open it in the user's browser
2. You start a small local HTTP server on `localhost:<port>`
3. After the user clicks "approve," the provider redirects to `http://localhost:<port>/callback?code=...`
4. Your server reads the code, exchanges it for a token, closes

The fragile spot is step 2-3: between starting the server and the user clicking approve, **the server is publicly reachable** (kind of — at least for the duration). Things that can hit it:

- Browser pre-fetch / favicon requests
- Crawlers that found the redirect URL somewhere
- Network probes
- The user's antivirus pre-scanning the URL
- Stale URLs from previous attempts

If your server crashes, closes early, or 500's on these probes, the actual callback never lands and the user gets stuck.

## How to make it resilient

- **Don't `process.exit()` or close on the first request.** Wait for the request that actually contains a valid `code` parameter and the matching `state` value.
- **Handle invalid requests gracefully.** Return a 400 with a friendly "Probably not your callback, try the link again" page.
- **Don't crash on unexpected HTTP methods.** Browsers sometimes HEAD before GET.
- **Keep the server running for a reasonable timeout** (e.g. 5 min) and only close on success or timeout.
- **Log all incoming requests with timestamp and headers.** When debugging, you need to know what hit you.

## Anti-pattern

```javascript
// ❌ DON'T DO THIS
const server = http.createServer((req, res) => {
  const code = url.parse(req.url).query.code
  exchange(code).then(...)
  res.end('done')
  server.close()  // first request closes the server
})
```

## Better pattern

```javascript
// ✅ DO THIS
const server = http.createServer((req, res) => {
  const parsed = url.parse(req.url, true)
  if (!parsed.query.code || parsed.query.state !== expectedState) {
    res.writeHead(400)
    res.end('Not the callback. Try again.')
    return  // don't close
  }
  exchange(parsed.query.code).then(...)
  res.end('Success! You can close this tab.')
  server.close()  // close only after the real callback
})
```

## Origin

Built X (Twitter) integration on 2026-05-05. Initial naive callback server crashed when a probe hit it before the user clicked approve, killing the entire OAuth flow. Pattern fixed in `integrations/x/lib/oauth.js`.
