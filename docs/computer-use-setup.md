# Hermes Setup — Mac Computer-Use + VPS over Tailscale

Private setup: VPS services stay on the tailnet, no public HTTPS.
Mac handles desktop control. Phone and other tailnet devices access
the web UI and dashboard directly via Tailscale Serve.

## Architecture

```
Mac (lamars-mac-mini, 100.82.142.43)
├── Hermes local install (computer-use, browser control)
├── SOUL.md with RSG privacy rules
├── CuaDriver 0.6.7 (desktop control)
└── Voice extras (faster-whisper, /voice on + Ctrl+B)

VPS (hermes-gretch, 100.75.67.72)
├── Hermes dashboard — Tailscale Serve on :9119
├── Hermes webui    — Tailscale Serve on :8787
├── Hermes gateway  — Docker bridge only (172.17.0.1:8642, internal)
├── Slack gateway, CRM orchestration, cron, dashboards
└── No public exposure — Tailscale only, no Caddy/NGINX
```

## Access URLs (from any tailnet device)

| Service | URL | Status |
|---------|-----|--------|
| Dashboard | `http://hermes-gretch:9119` | 200 OK |
| WebUI | `http://hermes-gretch:8787` | 302 → login |
| Gateway (internal) | `172.17.0.1:8642` (VPS only) | not HTTP-served |

Use the hostname `hermes-gretch` (or `hermes-gretch.tail1cbc83.ts.net`),
not the raw IP — the webui checks the Host header and returns 404 for
unknown hosts.

## What's already done

1. **Tailscale Serve** — dashboard (:9119) and webui (:8787) proxied
   from the Tailscale interface to the Docker bridge (172.17.0.1).
   Configured on the VPS with `tailscale serve --bg --http <port>`.

2. **Computer-use installed** — CuaDriver 0.6.7 at
   `/Applications/CuaDriver.app`, symlinked to `~/.local/bin/cua-driver`.

3. **Privacy rules** — `~/.hermes/SOUL.md` updated with RSG-specific
   persona and privacy rules for the Mac-local Hermes.

4. **Voice extras** — `hermes-agent[voice]` + `faster-whisper` installed
   in the Hermes venv. Use `/voice on` + `Ctrl+B` in `hermes chat`.

5. **SSH tunnel removed** — no longer needed; Tailscale Serve handles
   access directly. The launchd plist was unloaded and deleted.

## Remaining manual steps

### 1. Grant macOS TCC permissions

```bash
cua-driver permissions grant
```

Then approve in System Settings:
- **Privacy & Security → Accessibility** — allow CuaDriver.app
- **Privacy & Security → Screen Recording** — allow CuaDriver.app

Verify:
```bash
hermes computer-use doctor
cua-driver check_permissions
```

### 2. Enable computer-use toolset

```bash
hermes tools
```
Select **Computer Use**.

### 3. Phone access

Install the Tailscale app on your phone (already done for lc-personal
and lc-work). Open a browser and navigate to:
- `http://hermes-gretch:8787` — Hermes web UI
- `http://hermes-gretch:9119` — Dashboard

## Two-mode operating model

| Mode | Location | Role |
|------|----------|------|
| VPS Hermes | hermes-gretch (always on) | CRM, Slack gateway, cron, dashboards, web UI |
| Mac Hermes | lamars-mac-mini (local) | Browser/app control, carrier portals, PDF handling, proposals |

The VPS coordinates; the Mac executes desktop actions. Sensitive
actions require human approval — enforced in `~/.hermes/SOUL.md`.

## Privacy rules (enforced via SOUL.md)

- All client/policy/quote/carrier/billing/claim data is confidential.
- No uploading client documents to public tools.
- No sending emails, binding, canceling, or submitting without explicit approval.
- Prefer local processing on the Mac.
- Honest, neutral, service-focused — no manipulative tactics.
- Log material client-facing actions.

## Tailscale Serve management (on VPS)

```bash
# View current config
tailscale serve status

# Add a service
tailscale serve --bg --http <port> --yes http://172.17.0.1:<port>

# Remove a service
tailscale serve --http=<port> off
```

## VPS docker-compose port bindings

Services bind to `172.17.0.1` (Docker bridge, VPS-internal).
Tailscale Serve proxies tailnet traffic to the bridge. This avoids
conflicts with the DOCKER-USER iptables chain, which drops new
connections to Docker ports not in its allowlist.

## Rollback

- **Undo Tailscale Serve:** `tailscale serve reset` on the VPS.
- **Undo computer-use:** `hermes computer-use uninstall` or remove
  `/Applications/CuaDriver.app`.
- **Undo SOUL.md:** restore from the template comments in the original file.
- **Restore docker-compose.yml:** backup at
  `/opt/app/docker-compose.yml.bak-pre-tailscale-bind`.
