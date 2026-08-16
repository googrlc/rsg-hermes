# Copilot Studio → Hermes MCP egress plan

Microsoft Copilot Studio runs in Microsoft's cloud. The Hermes MCP bridge on
`hermes-gretch` is on the Tailscale tailnet and is **not** reachable from
Microsoft by default. This doc plans the path so Amy (Phase 2) can call
`tools/list` and `tools/call` against the bridge.

Related: [`amy-getting-started.md`](amy-getting-started.md) ·
[`deploy/mcp-bridge/README.md`](../deploy/mcp-bridge/README.md)

---

## Requirements

| Requirement | Detail |
|---|---|
| **URL** | HTTPS endpoint Microsoft can reach (TLS cert trusted by public CA) |
| **Path** | `POST` + `GET` on `/mcp` (bridge implements JSON-RPC + stream keepalive) |
| **Auth** | `Authorization: Bearer <API_SERVER_KEY>` (or `X-API-Key`) |
| **Upstream** | Bridge proxies to `HERMES_API_URL` with `HERMES_API_TOKEN` |
| **Latency** | Copilot tool calls should complete in &lt; 30s typical |

---

## Option A — Reverse proxy on a public host (recommended)

Expose only the MCP bridge through nginx/Caddy on a small VPS or Elestio edge
with a public hostname, e.g. `https://hermes-mcp.risksolutionsgroup.net/mcp`.

```text
Copilot Studio  →  HTTPS (public)  →  nginx/Caddy  →  http://hermes-gretch:8081/mcp
                                              ↑
                                    Tailscale or private link to box
```

**Steps**

1. DNS A/AAAA for `hermes-mcp.*` → proxy host.
2. TLS via Let's Encrypt on the proxy.
3. Proxy config: forward `/mcp` and `/healthz` only; no Command Center UI.
4. Rate limit + IP allowlist if Microsoft publishes egress IPs (optional).
5. `API_SERVER_KEY` in Copilot connector; rotate via 1Password `rsg_infrastructure`.
6. Smoke from outside tailnet:

```bash
curl -s https://hermes-mcp.example.com/healthz
curl -s -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ping","arguments":{}}}' \
  https://hermes-mcp.example.com/mcp
```

**Pros:** Simple, one door, matches existing bridge design.  
**Cons:** Public surface — must keep bridge auth strict; no UI on same host.

---

## Option B — Azure Relay / private connector

Use Microsoft-hosted relay or a Power Platform **custom connector** with an
on-premises data gateway installed on `hermes-gretch` or a relay VM.

**Pros:** No public inbound to the box.  
**Cons:** More moving parts; gateway maintenance; may not support Streamable HTTP
MCP without adapter work.

Evaluate only if Option A is rejected for security policy.

---

## Option C — Tailscale Funnel (quick test, not production default)

Tailscale Funnel can expose `8081` temporarily for connector smoke tests.

**Pros:** Fast proof for Copilot connector UI.  
**Cons:** Funnel URLs change; not ideal for production Amy; audit with security.

---

## Secrets checklist

| Secret | Where | Used by |
|---|---|---|
| `API_SERVER_KEY` | Bridge `.env`, Copilot connector | Copilot → bridge |
| `HERMES_API_TOKEN` | Bridge `.env` | Bridge → `hermes-api` |
| TLS cert | Proxy host | Public HTTPS |

Back up all three in 1Password — see bridge README empty-token guard.

---

## Copilot Studio wiring (after egress works)

1. Add **MCP connector** (Streamable HTTP) with public `/mcp` URL.
2. Auth: Bearer `API_SERVER_KEY`.
3. Smoke in Copilot: ask Amy to run equivalent of `sync_health` or `list_renewals`.
4. Identity (future): pass authenticated M365 user to bridge for per-operator gates.

---

## Decision log

| Date | Decision |
|---|---|
| *(pending)* | Choose Option A vs B after security review |
| *(pending)* | Public hostname + proxy host provisioned |

Update this table when Operations locks the path.
