# Install — Catalyst Renewals Desk (Creator is an empty stub)

The live product is the **Zoho Catalyst SPA** (project `935150771`,
function `renewals_desk_function`). Copy and merge steps:
[`catalyst/README.md`](catalyst/README.md).

The Creator app **already exists** in workspace `lamar_risksolutionsgroup668`:
link name `renewals-desk`, development environment. It is an empty stub
(0 records). **Do not fill that app. Do not create a new Creator application
and do not duplicate this one.** Not a new app. Not a duplicate.

Leave Creator alone. Do not paste [`ZIA_PASTE_PROMPT.md`](ZIA_PASTE_PROMPT.md)
into Zia. Do not paste `pages/` HTML or `deluge/*.dg` (including
`deluge/approve.dg` and `deluge/cursor_api.dg`) into Creator. Those files
are leftover from the stub.

Hermes remains the only NowCerts writer. No production Catalyst publish
until Lamar asks.

## Prerequisites (CRM + Hermes, not Creator)

1. Zoho CRM custom modules from [`../FIELD_CREATE_CHECKLIST.md`](../FIELD_CREATE_CHECKLIST.md):
   `Policies`, `Renewal_Events`, `Renewals`, `AMS_Write_Queue`.
2. Optional desk-owned field `Renewals.Checkpoint_State` (multi-line text).
   Hermes `--sync-zoho-renewals` must not overwrite it.
3. Hermes jobs:
   - `hermes --sync-zoho-renewals`
   - `hermes --sync-zoho-ams-queue`

## Smoke test (Catalyst)

1. Run `hermes --sync-zoho-renewals --sync-zoho-renewals-dry-run` then live.
2. Open the Catalyst desk: KPI tiles are **filters** (90/60/30/Personal/Past
   due, CRITICAL/AT_RISK/SAFE, Needs verification, Pending/Failed AMS).
3. Open a row: scorecard replaces "Step n of 5". Continue stays disabled
   until the stage CRM task is Completed. Completing checkpoints on the
   card marks that task.
4. Enqueue an AMS action: `AMS_Write_Queue` in `needs_approval`. Hermes
   `--sync-zoho-ams-queue` drains NowCerts. The desk never writes AMS.

## Cron

After `--renewal-refresh` (2:30am ET):

```
35 2 * * *  hermes --sync-zoho-renewals
40 2 * * *  hermes --sync-zoho-ams-queue
```
