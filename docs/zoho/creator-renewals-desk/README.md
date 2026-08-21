# Renewals Desk

Gretchen's live workstation is the **Zoho Catalyst SPA** (project
`935150771`, function `renewals_desk_function`) over Zoho CRM **Renewals**.
It is not a second policy book and not a second eligibility engine.

The Zoho Creator app `renewals-desk` (workspace `lamar_risksolutionsgroup668`)
is an empty stub. **Do not fill Creator. Do not rewrite the OS in Creator.**

Hermes remains the runner and the **only** NowCerts writer. The desk drafts;
Gretchen sends. Cadence auto-send stays off.

## Role split

| System | Role |
|---|---|
| NowCerts | AMS system of record |
| Hermes | Nightly refresh, Zoho upsert, AMS executor |
| Zoho CRM | Records: Policies, Renewal_Events, Renewals, AMS_Write_Queue, Deals, Tasks |
| **Catalyst Renewals Desk** | Live desk UI + `renewals_desk_function` |
| Gretchen | Client-facing hands |

## Surfaces

| Surface | File | Notes |
|---|---|---|
| Live Catalyst SPA | [`catalyst/`](catalyst/) | Recovered from the live source map, then overlaid with scorecard / checkpoints |
| Catalyst function patch | [`catalyst/functions/renewals_desk_function/`](catalyst/functions/renewals_desk_function/) | Merge into live `renewals_desk_function` |
| Copy steps | [`catalyst/README.md`](catalyst/README.md) | `npm start` on the Mac Mini |

Creator HTML / Deluge under `pages/` and `deluge/` is leftover from the stub.
Do not paste it into Creator.

Palette reminder if you touch CSS: muted blue is **Commercial only**; muted
sage is **Personal only**. Amber is attention; rose is overdue.

## Data flow

```
canonical_policies --renewal-refresh--> renewal_candidates / project_85_renewals
        --sync-zoho-renewals--> Zoho Renewal_Events + Renewals + Deals (Renewals pipeline)
Catalyst SPA reads/writes Zoho CRM Renewals
Catalyst AMS action --> Zoho AMS_Write_Queue (Approved_By + Approved_At)
        --sync-zoho-ams-queue--> outbound_sync_queue
        --renewal-executor--> NowCerts
```

The desk table and the CRM Renewals pipeline are the same book, linked by
`Related_Deal` / live `Deal_Id`. If a Deal is on the Renewals pipeline, Hermes
creates the desk row. If a desk row has no pipeline Deal, it is not on the
worklist.

Checkpoint flags persist on Zoho Renewals `Checkpoint_State` (desk-owned JSON).
Do **not** stand up `renewals_master` or `renewal_checklist_items` as an OS
store — those schemas are empty and retired.

Stored `Desk_Stage` / live `Stage` stays
Identified → Outreach Sent → Quote Requested → Proposal Sent → Negotiating →
Closed. Live WORK_STEPS labels: Review account / Request terms / Build options
/ Contact client / Close renewal, then Closed (lock). Continue stays gated by
the stage CRM task (`taskIsDone`). Completing checkpoints on the card marks
that task in the background. Hermes / `--sync-zoho-renewals` never
auto-advances.

Desk-owned fields Hermes must **not** overwrite on update: `Desk_Stage`,
`Stage`, `Disposition`, `Recommended_Action`, `Checkpoint_State`, touch dates.

KPI tiles (90/60/30/Personal/Past due, CRITICAL/AT_RISK/SAFE, Needs
verification, Pending/Failed AMS) stay **list filters**. Renewal Health %
replaces the "Step n of 5" chip.
