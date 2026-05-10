# Deployment guide — `ammp.helmguild.com`

Two viable paths, depending on whether you want to keep DNS at `domaindiscount24`. The recommendation is **Path A** (small VPS + Caddy + Tailscale) because it doesn't require moving DNS providers and keeps the trust boundary tight.

In both paths, the mentor backend (live Pepe-on-OpenClaw) reaches across **Tailscale** so OpenClaw never has to expose port 80 / 443 on the Mac.

---

## Path A — Small VPS + Caddy + Tailscale (recommended)

**One-time:**

1. Provision a tiny VPS (Hetzner CX22 €4/mo / DigitalOcean basic / Fly.io machine — whatever you trust). 1 vCPU, 1 GB RAM is plenty. Note its public IPv4 address (call it `$VPS_IP`).
2. **At `domaindiscount24`**, add **one** record on `helmguild.com`:

   ```
   Type:  A
   Name:  ammp
   Value: $VPS_IP
   TTL:   300
   ```

   That's the only DNS change you make.
3. On the VPS:
   ```bash
   # Tailscale (installs and joins your tailnet — same one your Mac is on)
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up

   # Caddy (auto Let's Encrypt for ammp.helmguild.com)
   sudo apt install -y caddy

   # Docker, then run ammp-mcp
   sudo apt install -y docker.io
   docker run -d --name ammp-mcp \
     -p 127.0.0.1:8765:8765 \
     -v /etc/ammp-mcp/mentees.json:/srv/ammp-mcp/mentees.json:ro \
     -v /etc/ammp-mcp/audit.log:/srv/ammp-mcp/audit.log \
     -v /etc/ammp-mcp/mentors:/srv/ammp-mcp/mentors:ro \
     -e AMMP_REQUIRE_AUTH=true \
     -e AMMP_PUBLIC_URL=https://ammp.helmguild.com \
     -e OPENCLAW_BEARER=$(cat /etc/ammp-mcp/openclaw-bearer) \
     ghcr.io/helmut-hoffer-von-ankershoffen/ammp-mcp:latest
   ```
4. Configure Caddy (`/etc/caddy/Caddyfile`):
   ```caddyfile
   ammp.helmguild.com {
     reverse_proxy 127.0.0.1:8765
   }
   ```
   `sudo systemctl reload caddy` — Caddy auto-issues a Let's Encrypt cert via HTTP-01 (needs port 80 + 443 open on the VPS firewall).
5. On the **Mac**: configure Pepe's mentor backend to point at OpenClaw's webhook over Tailscale:
   ```bash
   uv run ammp system setup --backend openclaw \
     --openclaw-url "http://${MAC_TAILSCALE_HOSTNAME}.tail-xxxx.ts.net/ammp/ask" \
     --auth-bearer-env OPENCLAW_BEARER
   ```
   The Mac never has to expose anything publicly — Tailscale handles the auth-and-encryption between the VPS and the Mac.

**Verify:**
```bash
curl -s https://ammp.helmguild.com/.well-known/agent.json | jq .name
# → "ammp-mcp"
uv run ammp system health --url https://ammp.helmguild.com
# → Health: OK.
```

## Path B — Cloudflare Tunnel (no VPS, but moves DNS)

Cleaner ops, but it does require moving the `helmguild.com` zone to Cloudflare's nameservers (Cloudflare's free plan requires the apex zone — they don't accept a sub-zone alone).

1. Sign up at cloudflare.com (free).
2. Add `helmguild.com` — Cloudflare auto-imports your existing DNS records.
3. **At `domaindiscount24`**: change nameservers for `helmguild.com` to the two Cloudflare nameservers Cloudflare displays. Wait for propagation (~1-24h).
4. On the **Mac**:
   ```bash
   brew install cloudflared
   cloudflared tunnel login                                 # browser opens → authorize
   cloudflared tunnel create ammp
   cloudflared tunnel route dns ammp ammp.helmguild.com
   cloudflared tunnel run --url http://127.0.0.1:8765 ammp
   ```
   Cloudflare auto-creates DNS for `ammp.helmguild.com` pointing to the tunnel + issues TLS automatically.
5. On the **Mac**, run `ammp-mcp` with `AMMP_PUBLIC_URL=https://ammp.helmguild.com` and the OpenClaw backend pointing to whatever local URL OpenClaw exposes its webhook on (no Tailscale needed since it's all on the same machine).

## Path C — One-off testing only: `cloudflared --url`

For a 10-minute spike before deciding A vs B:

```bash
brew install cloudflared
.venv/bin/ammp-server &
cloudflared tunnel --url http://127.0.0.1:8765
# prints a https://*.trycloudflare.com URL — random, ephemeral, but immediately reachable
```

This is *not* a production option (the URL rotates every restart) but it's useful for confirming the rest of your wiring is sound before paying for a VPS or moving DNS.

---

## Common: provisioning the OpenClaw webhook

Whichever path: OpenClaw must expose an HTTP endpoint that accepts the AMMP webhook contract (documented in [`src/ammp_mcp/backends/openclaw.py`](../src/ammp_mcp/backends/openclaw.py)):

```
POST <openclaw-url>
Authorization: Bearer <token-from-OPENCLAW_BEARER-env-var>
Content-Type: application/json

{
  "name": "Pepe Arturo",
  "persona": "...",
  "question": "...",
  "playbooks": [{"title": "...", "body": "..."}, ...],
  "ammp_version": "0.2.0",
  "ammp_draft": "draft-ammp-01"
}

Response 200:
{ "answer": "...", "confidence": 0.85 }
```

Until that webhook is wired, set `backend.kind = "anthropic"` (stateless persona-prompted Claude call) or `"stub"` (deterministic low-confidence response, useful for tests).
