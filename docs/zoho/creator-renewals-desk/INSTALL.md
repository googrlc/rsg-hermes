# Install — Creator Renewals Desk

This environment cannot click through the Zoho Creator IDE. Assemble the app
in the org from these files after the CRM field pack is live.

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

## Create the Creator application

1. Zoho Creator → **New application** → **From scratch**.
2. Name: **Renewals Desk**. Link to the same Zoho org as CRM.
3. Add **Integrations → Zoho CRM** for modules:
   - Accounts
   - Deals
   - Policies
   - Renewal_Events
   - Renewals
   - AMS_Write_Queue
   - Tasks (for the five default desk tasks)
4. Do **not** create Creator forms that duplicate Policies / Renewals as the
   system of record. Integration reports against CRM are the worklist.

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
| Renewals — on create/edit of Expiration_Date or Line_of_Business | [`deluge/window_bucket.dg`](deluge/window_bucket.dg) |

Creator custom buttons on an integration form call `zoho.crm.createRecord` /
`updateRecord` against CRM, not Creator tables.

For AMS enqueue, the button form must collect `expected_result` (required text)
and optional note. Deluge builds `Payload` JSON — operators never type JSON.

On Approve (AMS report): set `Approved_By` = `zoho.loginuserid`, `Approved_At`
= `zoho.currenttime`, `Status` = `queued`. Hermes `--sync-zoho-ams-queue` then
mirrors into `outbound_sync_queue`. **Do not call NowCerts from Deluge.**

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
