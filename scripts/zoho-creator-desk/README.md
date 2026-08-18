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
Dismiss **Upgrade later from Setup** if the Creator 5 modal is covering the
builder. Smart Chat is the bottom bar (`Ctrl+Space`).

Optional: attach to Chrome you started with remote debugging:

```bash
# already running:
# /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
ZOHO_CDP=http://127.0.0.1:9222 npm run build-desk
```

## What it builds

1. Open existing app Edit (development).
2. Add Zoho CRM integrations: Accounts, Deals, Policies, Renewal_Events,
   Renewals, AMS_Write_Queue, Tasks.
3. Pages Desk + Card from `docs/zoho/creator-renewals-desk/pages/`.
4. Paste `ZIA_PASTE_PROMPT.md` into Zia if the chat is visible.

Never publishes production. Never calls NowCerts.

## Env

| Var | Purpose |
|---|---|
| `ZOHO_CDP` | Connect to an existing Chrome DevTools endpoint |
| `ZOHO_EMAIL` | Optional; only fills the email box, never stored in git |
| `DISPLAY` | Cloud/Linux headed Chrome (default `:1` here) |
