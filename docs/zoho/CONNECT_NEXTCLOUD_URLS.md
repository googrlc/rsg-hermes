# Connect Zoho records to Nextcloud files

The file lives **once** in Nextcloud. Zoho stores metadata plus a clickable
https link. Clicking the field in Zoho opens the Files app in the browser.

```
Zoho Policy  CA123456
  Document URL  →  Nextcloud / Clients / ABC Trucking / Policies / Progressive Policy 2026.pdf

Zoho Account  ABC Trucking
  Nextcloud Folder URL  →  Nextcloud / Clients / ABC Trucking
```

Do not attach the PDF to the Zoho record. Do not copy it into SharePoint or
Outlook. NowCerts stays the AMS (who is insured, what is in force).

## What you create in Zoho

| Module | Fields | Zoho type |
|---|---|---|
| **Accounts** | Nextcloud Folder URL | URL / Website |
| **Policies** | Primary Folder URL, Document URL | URL / Website |
| **Deals** | Primary Folder URL, Document URL | URL / Website |
| **Renewals** | Primary Folder URL, Document URL | URL / Website |
| **Claims** | same two URL fields (after the module exists) | URL / Website |
| **Certificates** | same two URL fields (after the module exists) | URL / Website |

Field definitions: `fields_accounts.csv`, `fields_policies.csv`,
`fields_deals.csv`, `fields_renewals.csv`, `fields_claims.csv`,
`fields_certificates.csv`.

## 1. Create the URL fields (this is the actual connect)

Two ways to create the fields. Both send the same Settings API payloads
(Website / URL, length 450, Documents section on the Standard layout).
Claims and Certificates are skipped until those custom modules exist.

### A. Playwright (log into CRM in a browser)

Use this when you can sign into the RSG Zoho org but the OAuth token does
not have `ZohoCRM.settings.ALL`. A headed Chromium window opens; finish
sign-in (and 2FA). The script then uses that session.

```bash
cd /opt/rsg-hermes   # or this repo root
source .venv/bin/activate
pip install playwright
playwright install chromium
PYTHONPATH=packages/rsg-hermes-core:. \
  python scripts/playwright_zoho_document_url_fields.py
PYTHONPATH=packages/rsg-hermes-core:. \
  python scripts/playwright_zoho_document_url_fields.py --apply
```

The session is saved to `state/zoho-playwright.json` (gitignored) so later
runs can skip login. Optional: `ZOHO_EMAIL` / `ZOHO_PASSWORD` pre-fill the
form; 2FA still needs you.

A Cursor Playwright MCP tab pointed at Zoho is the same gate: if the tab
is sitting on `accounts.zoho.com/signin`, the fields cannot be created
until someone completes login in that browser.

### B. OAuth ensure script

Needs `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN` with
`ZohoCRM.settings.ALL` (fields + layouts + modules).

```bash
cd /opt/rsg-hermes   # or this repo root
source .venv/bin/activate
PYTHONPATH=packages/rsg-hermes-core:. \
  python scripts/ensure_zoho_document_url_fields.py
PYTHONPATH=packages/rsg-hermes-core:. \
  python scripts/ensure_zoho_document_url_fields.py --apply
```

Either path will:

1. `POST /crm/v8/settings/fields?module=…` — create Website fields
2. `PATCH /crm/v8/settings/layouts/{id}` — put them in a **Documents**
   section on the Standard layout so they are visible and clickable

### Manual backup (if neither script can authenticate)

Zoho CRM → Setup → Customization → Modules and Fields → pick the module →
**New Field** → type **URL** (sometimes labeled Website):

- Label exactly `Nextcloud Folder URL` on Accounts
- Label exactly `Primary Folder URL` and `Document URL` on the others
- Length 450
- Then drag them onto the layout (a Documents section is fine)

After create, open **Setup → Developer Space → APIs → API Names**. If Zoho
named the field `Nextcloud_Folder_URL__s`, that is normal — Hermes accepts
both.

## 2. Claims and Certificates modules

These are not in the org until you create them.

Zoho CRM → Setup → Customization → Modules and Fields → **New Module**:

| Module API name | Singular | Plural |
|---|---|---|
| `Claims` | Claim | Claims |
| `Certificates` | Certificate | Certificates |

Then create the rest of the fields from `fields_claims.csv` /
`fields_certificates.csv` (lookups to Accounts and Policies, claim number,
date of loss, holder, …) **or** re-run the ensure script so at least the
two URL fields land. Related lists: Claims and Certificates on the Account.

NowCerts still *issues* certificates. The filed PDF goes to Nextcloud
`Clients/{name}/COIs`. The Zoho Certificate row stores the link.

## 3. What Hermes writes (no extra glue)

Once the fields exist, these paths fill them:

| When | Field | Value |
|---|---|---|
| Intake (`HERMES_WRITE_TO_ZOHO=1`) | Account `Nextcloud_Folder_URL` | `https://{nextcloud}/apps/files/files?dir=/Clients/{name}` |
| Intake | Deal `Primary_Folder_URL` | `…/Clients/{name}/Quotes` |
| Book backfill | Account folder URL, Policy `Primary_Folder_URL` (`…/Policies`), Renewal `Primary_Folder_URL` (`…/Renewal Reviews`) | same Files-app links |
| Quote / proposal / renewal PDF file | Hermes stores the file in Nextcloud; stamp `Document_URL` onto the Zoho row when that writer already has the record id | `…/apps/files/files?dir=/…&scrollto={filename}` |

Hermes will **not** write a relative path like `Clients/ABC Trucking` into
a URL field — Zoho would not open it. It also skips Zoho file attachments
when a Nextcloud folder URL is present.

Nextcloud 34 serves folders at `/apps/files/files?dir=/…`. Hermes stamps
that form. Do not pre-encode commas (`Berrios%2C%20Edwin`): Zoho website
fields and the Nextcloud login redirect both encode the URL again, which
turns `%2C` into `%252C` and the Files app shows **Folder not found** even
though `Clients/Berrios, Edwin` exists. If a click 404s, open Files →
Clients → the client name, or paste:

`https://{nextcloud}/apps/files/files?dir=/Clients/{name}`

with the comma and space left as-is.

## 4. How you know it connected

1. Open an Account in Zoho.
2. Click **Nextcloud Folder URL**.
3. The browser opens Nextcloud on that client's folder.
4. Open a Policy, click **Document URL** (after a PDF has been filed) or
   **Primary Folder URL** (after backfill / folder create).

If the field is empty, the folder was never created or Zoho write was off.
If click does nothing, the value is not `https://` — check API names and
re-run intake/backfill.

## 5. OAuth scope note

Record writes (`ZohoCRM.modules.ALL`) are not enough to *create* fields.
The ensure script needs **settings** scope. If `--apply` returns
`OAUTH_SCOPE_MISMATCH`, recreate the refresh token with
`ZohoCRM.settings.ALL` and `ZohoCRM.modules.ALL`.
