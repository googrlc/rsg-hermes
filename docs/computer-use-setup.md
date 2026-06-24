# Hermes Computer-Use Setup — Mac + VPS over Tailscale

This documents the private, Tailscale-only setup for running Hermes with
computer-use on the Mac while the VPS stays unexposed to the public internet.

## Architecture

```
Mac (lamars-mac-mini, 100.82.142.43)
├── Hermes local install (computer-use, browser control)
├── SOUL.md with RSG privacy rules
├── SSH tunnel → VPS dashboard
└── CuaDriver 0.6.7 (desktop control)

VPS (hermes-gretch, 100.75.67.72)
├── Hermes dashboard (172.17.0.1:9119, private)
├── Hermes gateway/agent (Slack, CRM, cron)
├── No public HTTPS — Tailscale only
└── No Caddy/NGINX needed
```

## What's already done

1. **SSH tunnel** — Mac reaches VPS dashboard at `http://127.0.0.1:9119`
   via `ssh -f -N -L 9119:172.17.0.1:9119 hermes`.

2. **Computer-use installed** — `hermes computer-use install` succeeded.
   CuaDriver 0.6.7 at `/Applications/CuaDriver.app`, symlinked to
   `~/.local/bin/cua-driver`.

3. **Privacy rules** — `~/.hermes/SOUL.md` updated with RSG-specific persona
   and privacy rules for the Mac-local Hermes.

## Remaining manual steps

### 1. Grant macOS TCC permissions

CuaDriver needs Accessibility and Screen Recording. Run the grant helper,
then approve in System Settings:

```bash
cua-driver permissions grant
```

Or manually:

- **System Settings → Privacy & Security → Accessibility** — allow CuaDriver.app
- **System Settings → Privacy & Security → Screen Recording** — allow CuaDriver.app

Then verify:

```bash
hermes computer-use doctor
cua-driver check_permissions
```

Both TCC checks should show green.

### 2. Enable computer-use toolset in Hermes

```bash
hermes tools
```

Select **Computer Use** from the toolset list.

### 3. Make the SSH tunnel persistent (optional but recommended)

Create a launchd plist so the tunnel survives reboots:

```bash
cat > ~/Library/LaunchAgents/com.rsg.hermes-tunnel.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.rsg.hermes-tunnel</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/ssh</string>
    <string>-N</string>
    <string>-L</string>
    <string>9119:172.17.0.1:9119</string>
    <string>-o</string>
    <string>ExitOnForwardFailure=yes</string>
    <string>-o</string>
    <string>ServerAliveInterval=30</string>
    <string>-o</string>
    <string>ServerAliveCountMax=3</string>
    <string>hermes</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardErrorPath</key>
  <string>/tmp/hermes-tunnel.err.log</string>
</dict>
</plist>
PLIST
launchctl load ~/Library/LaunchAgents/com.rsg.hermes-tunnel.plist
```

### 4. Connect Hermes Desktop to VPS (if using the Desktop app)

In Hermes Desktop on the Mac:

- **Settings → Gateway → Remote gateway**
- URL: `http://127.0.0.1:9119` (via SSH tunnel)

### 5. Tailscale ACLs (optional hardening)

In the Tailscale admin console, restrict which devices can reach the Hermes
VPS port 9119. Only Lamar's Mac should have access.

### 6. Optional: VPS triggers Mac computer-use

If you want the VPS to be able to kick off local computer-use sessions on the
Mac over Tailscale:

- **Mac:** System Settings → General → Sharing → Remote Login (restrict to
  Lamar's user)
- **From VPS:** `ssh lamar@100.82.142.43 'hermes -t computer_use chat'`

Use this carefully — it gives the VPS click-around-your-Mac power.

## Two-mode operating model

| Mode | Location | Role |
|------|----------|------|
| VPS Hermes | hermes-gretch (always on) | CRM, Slack gateway, cron, dashboards, no computer-use |
| Mac Hermes | lamars-mac-mini (local) | Browser/app control, carrier portals, PDF handling, proposals |

The VPS coordinates; the Mac executes desktop actions. Sensitive actions
require human approval — this is enforced in `~/.hermes/SOUL.md`.

## Privacy rules (enforced via SOUL.md)

- All client/policy/quote/carrier/billing/claim data is confidential.
- No uploading client documents to public tools.
- No sending emails, binding, canceling, or submitting without explicit approval.
- Prefer local processing on the Mac.
- Honest, neutral, service-focused — no manipulative tactics.
- Log material client-facing actions.

## Rollback

- **Undo tunnel:** `launchctl unload ~/Library/LaunchAgents/com.rsg.hermes-tunnel.plist` and `kill` the ssh process.
- **Undo computer-use:** `hermes computer-use uninstall` (or remove `/Applications/CuaDriver.app`).
- **Undo SOUL.md:** restore from the template comments in the original file.

## Voice mode (optional)

Voice extras are installed in the Hermes venv at `~/.hermes/hermes-agent/venv/`:

```bash
# Already installed:
# ~/.hermes/hermes-agent/venv/bin/pip install "hermes-agent[voice]"
# faster-whisper comes bundled with the voice extras
```

To use voice in the Hermes CLI:

1. Start a chat session: `hermes chat`
2. Enable voice input: type `/voice on`
3. Press `Ctrl+B` to record — Hermes transcribes and replies
4. Enable TTS (text-to-speech) if you want replies read aloud: `/voice tts`
