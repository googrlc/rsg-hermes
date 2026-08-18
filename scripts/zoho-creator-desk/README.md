# Playwright — fill existing Renewals Desk

Opens **Renewals Desk** (`renewals-desk`) in the Creator IDE and drives the
build. Does **not** create a new application or a duplicate.

## Mac (you are logged into Zoho)

Use a dedicated Chrome profile so Playwright does not fight your daily Chrome:

```bash
cd scripts/zoho-creator-desk
npm install
npx playwright install chrome
npm run build-desk
```

First run waits on `accounts.zoho.com` until you sign in (Lamar). The profile
is stored in `.pw-profile/` (gitignored). Later runs reuse it.

The live IDE uses **Design / Workflow / Settings as buttons**, not tabs.
**Do not use Ctrl+Space Smart Chat** — that is Zoho Cliq (contacts/channels),
not Creator Zia. Creator form AI is **+ → Form → Using Zia**.

Create path that worked in the live app:

1. **+** next to the app name (header).
2. **Form** → **Using an Integrated Datasource** → **Zoho CRM** → one module.
3. **Page** → **Blank** → name Desk / Card.
4. Page builder **Report** widget (raw HTML snippets fail Deluge validation).

Custom CRM modules (`Policies`, `Renewals`, …) may not appear on the System
Zoho CRM connection. See `docs/zoho/creator-renewals-desk/LIVE_INVENTORY.md`.

Optional: attach to Chrome you started with remote debugging:

```bash
# already running:
# /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
ZOHO_CDP=http://127.0.0.1:9222 npm run build-desk
```

## What it builds

1. Open existing app Edit (development).
2. **+ → Form → Zoho CRM** for modules the System connection exposes
   (Accounts and Deals are live). Custom modules may be missing from the picker.
3. **+ → Page → Blank** for Desk and Card if they are not already there.
4. Does **not** paste into Cliq Smart Chat.

Never publishes production. Never calls NowCerts.

## Env

| Var | Purpose |
|---|---|
| `ZOHO_CDP` | Connect to an existing Chrome DevTools endpoint |
| `ZOHO_EMAIL` | Optional; only fills the email box, never stored in git |
| `DISPLAY` | Cloud/Linux headed Chrome (default `:1` here) |
