# Elestio nginx snippets (hermes-gretch)

Configs here are applied manually on the box under `/opt/elestio/nginx/conf.d/`
(see `FOLLOWUPS.md` §6 for the Nextcloud embed precedent).

## Hermes MCP egress (`hermes-mcp.risksolutionsgroup.net`)

**Locked hostname for Copilot Studio:** `https://hermes-mcp.risksolutionsgroup.net/mcp`

| Step | Action |
|---|---|
| 1 | Wix DNS: **A** `hermes-mcp` → `152.53.201.154` (Elestio box; verify with `dig +short hermes-gretch-u69864.vm.elestio.app`) |
| 2 | TLS cert for `hermes-mcp.risksolutionsgroup.net` on the box (Elestio UI or certbot) |
| 3 | Run `scripts/install_mcp_egress.sh` on hermes-gretch |
| 4 | Smoke from a machine **outside** Tailscale (see egress plan) |

Bridge env on the box (`/opt/app/.env`):

```bash
MCP_PUBLIC_BASE_URL=https://hermes-mcp.risksolutionsgroup.net
```

Copilot connector URL: same origin + `/mcp`, Bearer `API_SERVER_KEY`.
