# Microsoft tenant setup — 3 MCP servers (Hermes + SharePoint + Power Platform)

Checklist for IT / L to configure **Risk Solutions Group’s Microsoft tenant** so three
Cursor MCP servers (and hosted Amy on hermes-gretch) can run with least privilege.

| MCP in Cursor | Entra app | Auth model | Runs on |
|---|---|---|---|
| **`hermes`** | *(none — shared secret only)* | `API_SERVER_KEY` → Hermes bridge | hermes-gretch `:8081` |
| **`sharepoint`** | **RSG-Hermes-Service** | App-only client credentials | Mac stdio + hermes-gretch `:8082` |
| **`power-platform`** | **RSG-Power-Platform-MCP** | Delegated (you sign in) | Mac / Cursor only |

Related: [`microsoft-mcp-cursor-config.md`](microsoft-mcp-cursor-config.md) ·
[`sharepoint-knowledge-consolidation.md`](sharepoint-knowledge-consolidation.md) ·
[`integrations/email-triage-365.md`](integrations/email-triage-365.md)

---

## What you need from the tenant (collect once)

| Item | Example / where to find |
|---|---|
| **Tenant ID** | Entra → Overview → Tenant ID (`MS365_TENANT_ID`) |
| **Primary domain** | `risk-solutionsgroup.com` (or your `.onmicrosoft.com` tenant name) |
| **SharePoint root** | `https://<tenant>.sharepoint.com` |
| **Default Power Platform environment** | [Power Platform admin center](https://admin.powerplatform.com) → Environments → pick prod/default name + URL |
| **Global Admin or Application Admin** | Required once for admin consent |

Store all secrets in **1Password (`rsg_infrastructure`)** — not in git.

---

## App 1 — `RSG-Hermes-Service` (unattended / server)

One registration for **hermes-gretch**, email triage, and the Hermes SharePoint MCP.
Single-tenant, **client secret**, no interactive login.

### Create the registration

1. [Entra admin center](https://entra.microsoft.com) → **App registrations** → **New registration**
2. **Name:** `RSG-Hermes-Service`
3. **Supported account types:** Accounts in this organizational directory only (single tenant)
4. **Redirect URI:** leave blank (daemon app)

### API permissions (Application — not Delegated)

**Microsoft Graph → Application permissions:**

| Permission | Required for |
|---|---|
| `Sites.Read.All` | SharePoint MCP — site search, libraries, read files |
| `Files.Read.All` | SharePoint MCP — document content |
| `Mail.ReadWrite` | Email triage + Hermes `email_search` (move mail to quarantine folder) |

Optional later (only if you add features):

| Permission | Required for |
|---|---|
| `Mail.Read` | Read-only mail if you drop triage moves |
| `User.Read.All` | Directory lookups (not needed today) |

Click **Grant admin consent for [tenant]**. Status must show green checkmarks for Application permissions.

> **Least privilege for mail:** `Mail.ReadWrite` as an *application* permission is
> tenant-wide. Scope it with an Exchange **Application Access Policy** (see below).

### Client secret

1. **Certificates & secrets** → **New client secret** → 12–24 month expiry
2. Copy the **Value** immediately → 1Password
3. Record **Application (client) ID** and **Directory (tenant) ID**

### Exchange Application Access Policy (recommended)

Limits which mailboxes the service app can touch.

```powershell
# Exchange Online PowerShell (Connect-ExchangeOnline)
New-DistributionGroup -Name "Hermes-Mail-Triage" -Type Security
Add-DistributionGroupMember -Identity "Hermes-Mail-Triage" -Member "lamar@risk-solutionsgroup.com"
# Add intake@ / other triaged mailboxes as members

New-ApplicationAccessPolicy -AppId "<RSG-Hermes-Service-client-id>" `
  -PolicyScopeGroupId "Hermes-Mail-Triage" `
  -AccessRight RestrictAccess `
  -Description "Hermes email triage + email_search only"

Test-ApplicationAccessPolicy -Identity "lamar@risk-solutionsgroup.com" `
  -AppId "<RSG-Hermes-Service-client-id>"
```

### SharePoint access

`Sites.Read.All` + `Files.Read.All` (application) allow **read** of all site collections.
For Amy Phase 1 you still point tools at **one site** via `SHAREPOINT_SITE_URL`; the
permission is broader than one site, but the config is not.

No extra SharePoint “app-only” grant is required beyond Graph admin consent for most tenants.

### Put on hermes-gretch (`/opt/app/.env`)

```bash
MS365_TENANT_ID=<tenant-id>
MS365_CLIENT_ID=<RSG-Hermes-Service-app-id>
MS365_CLIENT_SECRET=<secret-value>

# Mail (existing Hermes jobs)
MS365_MAILBOXES=lamar@risk-solutionsgroup.com,intake@risksolutionsgroup.net
HERMES_ASK_MAILBOX=lamar@risk-solutionsgroup.com

# SharePoint knowledge (Amy Phase 1)
SHAREPOINT_SITE_URL=https://<tenant>.sharepoint.com/sites/RSG-Knowledge

# MCP bridge auth (generate long random strings — not Entra)
API_SERVER_KEY=<openssl rand -hex 32>
# HERMES_API_TOKEN already on box for hermes-api upstream
```

Smoke after deploy:

```bash
curl -s http://127.0.0.1:8082/healthz
# MCP ping with API_SERVER_KEY — see deploy/sharepoint_mcp/README.md
hermes --email-triage-dry-run --email-since-hours 24
```

---

## App 2 — `RSG-Power-Platform-MCP` (interactive / Cursor)

Delegated permissions for **L’s Mac** — Power Automate, OneDrive, Excel, optional
SharePoint write during site cleanup. **Do not** put this secret on hermes-gretch.

### Easiest path (recommended)

On your Mac:

```bash
npx -y powerautomate-mcp@latest --setup --client cursor
```

The wizard will:

- Create **or** accept an existing Entra app (`RSG-Power-Platform-MCP`)
- Enable **Allow public client flows**
- Add **delegated** API permissions for the preset you choose
- Run admin consent when your account is allowed
- Write Cursor MCP config

Then:

```bash
powerautomate-mcp --doctor
```

### Permission preset guidance for RSG

| Preset | Choose when |
|---|---|
| **Power Automate + connectors** | Building/fixing sync flows, flow diagnostics |
| **Power Automate only** | Minimal — flows only, no SharePoint write |
| **All tool surfaces** | Full cleanup (SharePoint + OneDrive + PA + apps) — use only for admins |

Suggested for site consolidation: **Power Automate + connectors** (includes Graph
delegated SharePoint/OneDrive helpers for your signed-in user).

### Manual registration (if wizard fails)

1. **New registration:** `RSG-Power-Platform-MCP`, single tenant
2. **Authentication** → **Allow public client flows:** Yes  
   Redirect URI (mobile/desktop): `https://login.microsoftonline.com/common/oauth2/nativeclient`
3. **API permissions → Delegated** (add what you need):

| API | Delegated permission | Used for |
|---|---|---|
| Microsoft Graph | `User.Read` | Sign-in |
| Microsoft Graph | `Sites.ReadWrite.All` | SharePoint cleanup (your user context) |
| Microsoft Graph | `Files.ReadWrite.All` | OneDrive / file upload during migration |
| Power Automate / Flow service | `Flows.Read.All`, `Flows.Manage.All`, `Activity.Read.All` | Flow list/run/history |
| PowerApps Service | `User` | Connections / connectors (if preset includes) |
| Dynamics CRM | `user_impersonation` | Dataverse (only if you use those tools) |

4. **Grant admin consent**
5. Set in Cursor MCP env: `PA_MCP_CLIENT_ID=<application-id>`

### Who signs in

- **L** (Operations) — primary for cleanup and flow work
- Lamar / Gretchen — only if they need Power Automate MCP on their own machines (separate sign-in, same app)

---

## App 0 — Hermes MCP bridge (not Entra)

The **`hermes`** MCP is not a Microsoft app. It is a **shared bearer token** gating access
to `rsg-hermes` on hermes-gretch.

| Secret | Where |
|---|---|
| `API_SERVER_KEY` | MCP bridge container (`app-rsg-hermes-mcp-1`) — Copilot/Cursor presents this |
| `HERMES_API_TOKEN` | Bridge → `hermes-api` upstream (already on box; backup in 1Password) |

Generate:

```bash
openssl rand -hex 32
```

Use **different** values for Hermes bridge (`:8081`) and SharePoint MCP (`:8082`) if both
are exposed.

---

## SharePoint tenant work (non-Entra)

Do in SharePoint admin / site settings. **Inventory first** — do not create
**RSG-Knowledge** until every source site is mapped.

| Step | Action |
|---|---|
| 1 | **Inventory** — run `python scripts/sharepoint_site_inventory.py --deep` (or MCP `list_sites` / Power Platform `search_sharepoint_sites`); review [`sharepoint-site-inventory.md`](sharepoint-site-inventory.md) |
| 2 | **Approve map** — assign keep / merge / archive / delete / exclude per site |
| 3 | Create (or rename) site **`RSG-Knowledge`** — Team site or Communication site |
| 4 | Build folder tree from [`sharepoint-knowledge-consolidation.md`](sharepoint-knowledge-consolidation.md) |
| 5 | Add `00-meta/site-index.md` |
| 6 | Migrate content from mapped source sites (copy, never cut); log in `migration-log.md` |
| 7 | Retire old site URLs; set `SHAREPOINT_SITE_URL` to the final site everywhere |

**Copilot Studio (Amy Phase 1):** ground the agent on **this site only** after consolidation.

---

## Power Platform tenant work

| Step | Action |
|---|---|
| 1 | Confirm default **environment** (Production) in Power Platform admin center |
| 2 | Note environment URL / name for flow tools |
| 3 | Ensure **DLP policies** allow connectors you need (SharePoint, Dataverse, HTTP, Teams) |
| 4 | **Service account** flows (future): separate discussion — scheduled glue, not Cursor MCP |

Power Automate **scheduled sync** (Zoho ↔ Supabase, etc.) lives here — not in the Hermes
SharePoint MCP. See [`zoho-supabase-sync-design.md`](zoho-supabase-sync-design.md).

---

## Licensing (confirm with M365 admin)

| Capability | Typical requirement |
|---|---|
| SharePoint sites | Microsoft 365 with SharePoint (Business Standard/Premium or E3/E5) |
| Power Automate cloud flows | Per-user PA license or included M365 SKU — verify in admin center |
| Copilot Studio (Amy UX) | Copilot Studio license / trial |
| Graph app-only | No extra license for daemon apps beyond data access |

---

## Admin consent quick reference

If consent fails with `AADSTS65001`, a Global Admin opens:

```text
https://login.microsoftonline.com/<tenant-id>/adminconsent?client_id=<app-client-id>
```

Run once per app registration (`RSG-Hermes-Service`, `RSG-Power-Platform-MCP`).

---

## Verification checklist

### Entra

- [ ] `RSG-Hermes-Service` created — **Application** permissions consented (Sites, Files, Mail)
- [ ] Exchange Application Access Policy applied (if using mail)
- [ ] Client secret in 1Password; expires on calendar
- [ ] `RSG-Power-Platform-MCP` created — **Delegated** permissions consented
- [ ] Public client flows enabled on Power Platform MCP app

### hermes-gretch

- [ ] `MS365_*` + `SHAREPOINT_SITE_URL` in `/opt/app/.env`
- [ ] `API_SERVER_KEY` on SharePoint MCP (`:8082`) and Hermes MCP (`:8081`)
- [ ] SharePoint MCP health + `ping` tool OK
- [ ] Hermes MCP health + `ping` tool OK

### Cursor (Mac)

- [ ] Three MCP entries: `hermes`, `sharepoint`, `power-platform`
- [ ] `powerautomate-mcp --doctor` passes
- [ ] `list_sites` / `search_sharepoint_sites` returns your tenant sites
- [ ] Signed-in user can list Power Automate flows in default environment

### SharePoint content

- [ ] Site inventory generated and reviewed ([`sharepoint-site-inventory.md`](sharepoint-site-inventory.md))
- [ ] Keep / merge / archive decisions approved for every source site
- [ ] Single **RSG-Knowledge** site live (after inventory approved)
- [ ] Old knowledge sites read-only or deleted
- [ ] Copilot Studio grounded on RSG-Knowledge only

---

## What to send back (for Hermes ops)

Fill in for the repo / 1Password (no secrets in Slack/git):

```text
Tenant ID:
Tenant domain:
RSG-Hermes-Service client ID:
SHAREPOINT_SITE_URL (final):
Power Platform default environment name:
Power Platform MCP client ID:
Hermes MCP URL (Tailscale or public):
SharePoint MCP URL (if exposed):
```

Secrets (`MS365_CLIENT_SECRET`, `API_SERVER_KEY`) → **1Password only**.

---

## Summary — two Entra apps + one shared secret layer

```text
                    Microsoft Entra tenant
                              |
        +---------------------+---------------------+
        |                                           |
 RSG-Hermes-Service                    RSG-Power-Platform-MCP
 (application / daemon)                (delegated / public client)
        |                                           |
   hermes-gretch                          L's Mac / Cursor
   - email triage                         - Power Automate
   - sharepoint_mcp :8082                 - OneDrive / Excel
   - Amy read-only knowledge              - SharePoint cleanup (write)
        |
   API_SERVER_KEY (not Entra)
   - hermes MCP :8081
   - sharepoint MCP :8082
```

You do **not** need a third Entra app for Hermes MCP — only the bearer tokens above.
