# Document Registry — one CRM catalog, one Nextcloud tree

Staff file a document in **Zoho CRM Document Registry**. Hermes puts the PDF in
Nextcloud. Zoho stores metadata plus a `/f/{fileid}` permalink. The PDF is
never the Zoho Attachment library.

## Decision (PRs #356 and #357)

Those PRs were parallel and unmerged:

| | #356 Filed_Documents | #357 Document_Registry |
|---|---|---|
| Catalog | Custom module `Filed_Documents` + related list **Nextcloud Files** | Custom module `Document_Registry` with richer metadata + upload API |
| Tree | `Clients/{name}/{category}/` (live Berrios `/f/15700`) | `Agency Documents/Commercial Lines/{account}/{policy type}/…` |
| Party | Account (and Policy/Deal/Renewal lookups) | Account required (`account_name` 400) |
| Entry | Open Nextcloud, drop file, then catalog the row | Hermes PUT then CRM write (golden rule) |

**Keep Document_Registry as the one catalog.** Use related lists on Account,
Lead, Policy, Deal, and Renewal (the useful part of #356). **Do not create or
keep filing into `Filed_Documents`** — it is a second list of the same files.
If that module already exists in the org, hide it; do not migrate by copying
rows into a third module.

**Keep `Clients/{name}/…` as the one client file tree.** That is what
`NextcloudClient.ensure_client_folders` and the live Berrios folder already
use. `NEXTCLOUD_BASE_PATH=Agency Documents` (Team Folder mount from #357) is a
prefix, not a second tree: files land at

```
[Agency Documents/]Clients/{Lead or Account display name}/{Intake|Policies|Quotes|…}/file.pdf
```

Line of business is **metadata on the CRM row**, not a `Commercial Lines/`
path segment. Leads use the same `Clients/{name}/` folder so conversion later
fills the Account lookup instead of moving files.

`POST /api/nextcloud/upload` still has a `Commercial Lines/Clients` helper for
an older Agent OS path. Document Registry does **not** write there.

## Staff flow (CRM is the entry point)

Do not send people to Nextcloud first. Upload is the entry point; creating
the client folder is a side effect.

1. Open **Document Registry** in Zoho, or click **File in Nextcloud** on a
   Lead / Account / Policy / Deal / Renewal.
2. Drop the file and label it (document type, policy type, renewal cycle,
   line of business, party).
3. Hermes finds or creates `Clients/{name}/`, PUTs the file, stamps
   `Nextcloud_File_URL` = `https://{host}/f/{fileid}`.

Zoho cannot native-POST file bytes to Hermes from a custom-module create
form. Two bridges, smallest that works:

| Bridge | When |
|---|---|
| **Custom button → Hermes drop page** (preferred, golden-rule clean) | Button URL `{HERMES_PUBLIC_BASE_URL}/command-center/document-registry?lead_id=${Leads.Id}&lead_name=${Leads.Full_Name}` (or `account_id` / `account_name` on Accounts). The page POSTs `/api/document-registry/upload`. Hermes writes the CRM row only after the permalink exists. |
| **Webhook on create → `/api/document-registry/from-zoho`** | Staff create a Document Registry row, attach the PDF (Zoho Attachments as a **temp drop**), workflow notifies Hermes. Hermes files it, stamps the permalink, **deletes the attachment**. Create layout must **not** require `Nextcloud_File_URL`. Status `Pending File` until stamped; `Failed Filing` if PUT fails (no URL). |

## API

```
POST /api/document-registry/upload
{
  "lead_id": "...",          // XOR account_id
  "lead_name": "Jane Lead",  // required to name Clients/{name}
  "account_id": "",
  "account_name": "",        // empty is fine when a lead is provided
  "document_type": "Intake",
  "policy_type": "Home",
  "line_of_business": "Personal Lines",
  "renewal_cycle": "2026",
  "file_name": "app.pdf",
  "content_base64": "...",
  "write_to_zoho": true
}
```

Party rules: Lead **or** Account, exactly one. Folder segment = that party's
display name. `GET /api/document-registry/search` filters the catalog.

Golden rule: no `Nextcloud_File_URL` / file id → no CRM write.

## Field pack (no live `--apply` required to ship)

- CSV: `docs/zoho/fields_document_registry.csv`
- Module row: `docs/zoho/modules_custom.csv`
- Picklists: `docs/zoho/picklists_hermes_vocab.csv` (`document_registry_*`)
- Ensure script: `python scripts/zoho_document_registry_setup.py` (dry-run)
  then `--apply` when OAuth has `ZohoCRM.settings.*`.

Still needs `--apply` in the org: create `Document_Registry` if missing, add
the Lead lookup and optional Account/Policy/Deal/Renewal lookups, mark
`Nextcloud_File_URL` required on the **view** layout (not the create layout
if using the attachment webhook), add the custom button, hide Attachments
and `Filed_Documents` if present.

## MCP

`document_registry_upload` and `document_registry_search` on the rsg-hermes
bridge proxy the same HTTP routes.
