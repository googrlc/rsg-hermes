# Install — Creator Renewals Desk

The empty app **already exists** in workspace `lamar_risksolutionsgroup668`:
link name `renewals-desk`, development environment. Fill **that** app. Do not
create a new Creator application and do not duplicate this one.

Preferred fill: open Edit mode → **+ → Form → Using an Integrated Datasource
→ Zoho CRM** (one module per form). Pages: **+ → Page → Blank**. Do **not**
paste [`ZIA_PASTE_PROMPT.md`](ZIA_PASTE_PROMPT.md) into Cliq Smart Chat
(`Ctrl+Space`); that searches contacts/channels. Creator Zia for forms is
**+ → Form → Using Zia**. Fallback: click through below and paste Deluge by hand.

Creator MCP in Cursor can inventory the live app (see
[`LIVE_INVENTORY.md`](LIVE_INVENTORY.md)) but cannot create pages, reports,
CRM integrations, or workflows. Paste [`pages/desk.html`](pages/desk.html)
(palette included) into the Desk page HTML snippet. Do not create a second
application.

## Prerequisites

1. Zoho CRM custom modules from [`../FIELD_CREATE_CHECKLIST.md`](../FIELD_CREATE_CHECKLIST.md):
   `Policies`, `Renewal_Events`, `Renewals`, `AMS_Write_Queue`.
2. Renewals desk fields from [`../fields_renewals.csv`](../fields_renewals.csv)
   (Desk_Stage, Disposition, Recommended_Action, Window_Bucket, lookups, touch dates).
3. Picklists imported from [`../picklists_hermes_vocab.csv`](../picklists_hermes_vocab.csv)
   (`desk_stage`, `desk_disposition`, `recommended_action`, `window_bucket`).
4. Hermes jobs available:
   - `hermes --sync-zoho-renewals`
   - `hermes --sync-zoho-ams-queue`
5. OAuth scopes on the Hermes Zoho client include CRM modules used here
   (`ZohoCRM.modules.ALL` or the custom-module equivalents).

## Open the existing Creator application

1. Zoho Creator → **Renewals Desk** (`renewals-desk`) → Edit (development).
   Not a new app. Not a duplicate.
2. **+** next to the app name → **Form** → **Using an Integrated Datasource**
   → **Zoho CRM**. One module per form. Live today: Accounts, Deals. Custom
   modules Policies / Renewal_Events / Renewals / AMS_Write_Queue (and Tasks)
   did not appear in the System connection module picker — see
   [`LIVE_INVENTORY.md`](LIVE_INVENTORY.md).
3. Do **not** create Creator forms that duplicate Policies / Renewals as the
   system of record. Integration reports against CRM are the worklist.
4. Pages: **+ → Page → Blank** named `Desk` and `Card`. Embed CRM reports with
   the page-builder **Report** widget. Raw HTML snippets in this builder
   validate as Deluge and reject the files in `pages/`.

## Pages

Create two Pages (HTML snippet / panel layout):

| Page | Source | Default |
|---|---|---|
| `Desk` | [`pages/desk.html`](pages/desk.html) | Application home |
| `Card` | [`pages/card.html`](pages/card.html) | Opened with `renewal_id` query |

Paste the HTML into the page editor. Replace `{{CRM_REPORT_URL}}` placeholders
with the published integration-report permalinks from the next step.

## Reports

Create the integration reports in [`reports.md`](reports.md). Each report is a
CRM view Creator embeds on Desk / Card. Publish them to Gretchen and Lamar
only (same users as `agency_crm_users` emails).

## Workflows (Deluge)

| Trigger | Script |
|---|---|
| Renewals — on edit of `Desk_Stage` (validate) | [`deluge/stage_guard.dg`](deluge/stage_guard.dg) |
| Renewals — on create, and Card page load | [`deluge/task_seed.dg`](deluge/task_seed.dg) |
| Custom buttons on Card: Request terms / Prepare options / Follow up / Update AMS | [`deluge/ams_enqueue.dg`](deluge/ams_enqueue.dg) |
| Custom button: Dismiss | [`deluge/dismiss.dg`](deluge/dismiss.dg) |
| AMS pending report — custom button Approve | [`deluge/approve.dg`](deluge/approve.dg) |
| Renewals — on create/edit of Expiration_Date or Line_of_Business | [`deluge/window_bucket.dg`](deluge/window_bucket.dg) |
| Standalone function `cursor_api` (Custom API **Cursor**) | [`deluge/cursor_api.dg`](deluge/cursor_api.dg) |

Creator custom buttons on an integration form call `zoho.crm.createRecord` /
`updateRecord` against CRM, not Creator tables.

For AMS enqueue, the button form must collect `expected_result` (required text)
and optional note. Deluge builds `Payload` JSON — operators never type JSON.

On Approve (AMS report): set `Approved_By` = `zoho.loginuserid`, `Approved_At`
= `zoho.currenttime`, `Status` = `queued`. Hermes `--sync-zoho-ams-queue` then
mirrors into `outbound_sync_queue`. **Do not call NowCerts from Deluge.**

## Custom API Cursor (standalone function)

The Custom API wizard lists **no functions** until a standalone Deluge
function exists in **this** app. Create it before associating:

1. Stay in **Renewals Desk** (`renewals-desk`) Edit. Not a new app.
2. Workflows → Functions → New Function.
3. Function name `cursor_api`, display name `Cursor`, return type **Map**.
4. Arguments (all string): `action`, `id`, `expected_result`, `note`,
   `policy_number`, `desk_stage`, `disposition`, `producer_confirmed`.
5. Paste the body from [`deluge/cursor_api.dg`](deluge/cursor_api.dg). Save.
6. Return to Microservices → Custom APIs → Cursor and associate `cursor_api`.

`action`: `ping`, `request_terms`, `prepare_options`, `client_follow_up`,
`update_ams`, `approve`, `dismiss`, `set_stage`. AMS enqueue still writes
`needs_approval` only. Hermes drains NowCerts.

## Users

| User | Access |
|---|---|
| Gretchen | Desk, Card, worklist, needs-verification, AMS enqueue (create). No AMS approve if a producer should sign off. |
| Lamar | All of the above + AMS approve + backward stage confirmation. |

Map Creator roles after publish. `Approved_By` must be a real login, never a
shared robot user.

## Smoke test

1. Run `hermes --sync-zoho-renewals --sync-zoho-renewals-dry-run` then live.
2. Open Desk: KPI strip has counts; worklist is expiration-ascending.
3. Open a SAFE commercial row: card shows Policy + Account; five tasks exist.
4. Try to jump Identified → Negotiating: refused.
5. Enqueue `prepare_options` with an expected result: AMS_Write_Queue row in
   `needs_approval` (or `queued` after approve). Payload is JSON with
   `action`, `renewal_id`, `policy_number`, `expected_result`.
6. Approve as Lamar. `hermes --sync-zoho-ams-queue` inserts one
   `outbound_sync_queue` row. Re-run mirrors 0 new rows (idempotent).
7. Dismiss a junk row: `Dismissed` true; next `--renewal-refresh` keeps it off
   `project_85_renewals`.

## Cron

After `--renewal-refresh` (2:30am ET):

```
35 2 * * *  hermes --sync-zoho-renewals
40 2 * * *  hermes --sync-zoho-ams-queue
```

(The crontab in this repo already carries these once this branch is deployed.)
