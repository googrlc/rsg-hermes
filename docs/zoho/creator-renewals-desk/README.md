# Creator Renewals Desk

Gretchen's live workstation for the Zoho CRM **Renewals** module. This is a
Zoho Creator application that binds to CRM — it is not a second policy book
and not a second eligibility engine.

Hermes remains the runner and the **only** NowCerts writer. Creator drafts;
Gretchen sends. Cadence auto-send stays off.

A later Creator app (Commissions) should copy this same pattern: KPI strip,
exception buckets, worklist, one-record card, human approval before side
effects. Do not fold commissions into this app.

## Role split

| System | Role |
|---|---|
| NowCerts | AMS system of record |
| Hermes | Nightly refresh, Zoho upsert, AMS executor |
| Zoho CRM | Records: Policies, Renewal_Events, Renewals, AMS_Write_Queue, Deals |
| **This Creator app** | Desk UI + Deluge guards |
| Gretchen | Client-facing hands |

## Surfaces

| Surface | File | CRM source |
|---|---|---|
| Desk home | [`pages/desk.html`](pages/desk.html) | Renewals worklist + KPI counts |
| Desk palette / CSS | [`pages/desk.css`](pages/desk.css) | Copy into Catalyst `renewals/src/desk.css` |
| Catalyst React desk | [`catalyst/App.js`](catalyst/App.js) | Replace `~/catalyst-renewals-desk/renewals/src/App.js` |
| Catalyst copy steps | [`catalyst/README.md`](catalyst/README.md) | `npm start` on the Mac Mini |
| Desk preview | [`pages/desk_preview.html`](pages/desk_preview.html) | Open locally to review Commercial/Personal colors |
| Renewal card | [`pages/card.html`](pages/card.html) | One Renewal + Policy + Account + Event + Deal |
| Needs verification | [`reports.md`](reports.md) | Renewal_Events.Eligibility = needs_verification |
| AMS approval | [`reports.md`](reports.md) | AMS_Write_Queue awaiting Approved_By / failed |

Palette: muted blue (`#DCEAF7` / `#245A86`) is **Commercial only**; muted sage (`#E3F0E7` / `#2F6B4F`) is **Personal only**. Amber is attention; rose is overdue. Status is a separate column.

## Deluge

| Script | When | Rule |
|---|---|---|
| [`deluge/stage_guard.dg`](deluge/stage_guard.dg) | On Desk_Stage change | Never skip; backward needs producer |
| [`deluge/task_seed.dg`](deluge/task_seed.dg) | On card open / Renewal create | Seed the five default tasks once |
| [`deluge/ams_enqueue.dg`](deluge/ams_enqueue.dg) | AMS action buttons | Four executor actions only; structured payload |
| [`deluge/dismiss.dg`](deluge/dismiss.dg) | Dismiss button | `Dismissed=true`; never delete |
| [`deluge/approve.dg`](deluge/approve.dg) | AMS pending Approve | Sets Approved_By / Approved_At / Status=`queued` |
| [`deluge/cursor_api.dg`](deluge/cursor_api.dg) | Custom API **Cursor** | Standalone Map function the Custom API can associate |
| [`deluge/window_bucket.dg`](deluge/window_bucket.dg) | On Expiration / LOB edit | Same buckets as `hermes/renewals/desk.py` |

Python is the tested source of truth for stage/window/action rules
([`hermes/renewals/desk.py`](../../../hermes/renewals/desk.py)). Deluge copies
those rules; [`deluge/fixtures.md`](deluge/fixtures.md) is the checklist that
they stayed in sync.

## Data flow

```
canonical_policies --renewal-refresh--> renewal_candidates / project_85_renewals
        --sync-zoho-renewals--> Zoho Renewal_Events + Renewals + Deals (Renewals pipeline)
Creator reads/writes Zoho
Creator AMS action --> Zoho AMS_Write_Queue (Approved_By + Approved_At)
        --sync-zoho-ams-queue--> outbound_sync_queue
        --renewal-executor--> NowCerts
```

The desk table and the CRM Renewals pipeline are the same book, linked by
`Related_Deal`. If a Deal is on the Renewals pipeline, Hermes creates the
desk row. If a desk row has no pipeline Deal, it is not on the worklist.
Hermes fills `Related_Deal` when empty; after that it is left alone.

Desk-owned fields Hermes must **not** overwrite on update: `Desk_Stage`,
`Disposition`, `Recommended_Action`, `Touch_Early` / `Touch_Mid` /
`Touch_Decision`.

Correctable fields (portal overlay, keyed by policy number): `Client_Name`,
`Premium_Current`, `Premium_Renewal`, `Risk_Status`, `Expiration_Date`,
`Last_Contact_Date`, `Strategy_Notes`. `Increase_Percent` is a formula.
`Policy_Number` is the natural key.

## Install

See [`INSTALL.md`](INSTALL.md). Zia paste for the existing `renewals-desk`
app: [`ZIA_PASTE_PROMPT.md`](ZIA_PASTE_PROMPT.md). Live MCP inventory:
[`LIVE_INVENTORY.md`](LIVE_INVENTORY.md).
