# Live inventory — Creator Renewals Desk

Captured via Zoho Creator MCP against workspace `lamar_risksolutionsgroup668`.
Do not invent link names. Update this file after a successful IDE/Zia fill.

## Application

| Field | Live value |
|---|---|
| Display name | Renewals Desk |
| Production link name | `renewals-desk` |
| Development link name | `renewals-desk-development` |
| Workspace | `lamar_risksolutionsgroup668` |
| Workspace id | `935748147` |
| Application id (dev) | `4958918000000018002` |
| Production application id | `4958918000000018026` |
| Created | 18-Aug-2026 |
| Current environment | development |
| Development status | Created |
| Stage | Not Yet Published |
| Production | Not Yet Published |

One app only. No duplicate listed by `getApplications`.

## CRM modules (Zoho CRM, same org)

Workflow API accepts these module API names (INVALID_MODULE would mean missing):

| Module | Present |
|---|---|
| Accounts | yes (Hermes Account Client Queue A) |
| Deals | yes (Big Deal Rule) |
| Tasks | yes |
| Policies | yes (Hermes Policy Queue B; module id `7529682000000692001`) |
| Renewal_Events | yes |
| Renewals | yes (workflow config returned) |
| AMS_Write_Queue | yes |

## Creator components (development)

| Piece | Live |
|---|---|
| Pages Desk / Card | missing (`getPages` 3920 No pages available) |
| Native form | `Renewals_Desk` (type 1) — do **not** treat as a second book |
| Native report | `All_Renewals_Desks` (type 1) |
| CRM integration forms (Accounts, Deals, Policies, Renewal_Events, Renewals, AMS_Write_Queue, Tasks) | missing |
| Reports Worklist, Needs verification, AMS pending/failed, Open tasks | missing |
| Sections | one section `Renewals_Desks` with the native form + report |
| Deluge (stage, window, tasks, enqueue, approve, dismiss) | missing from pages/reports; `cursor_api` exists as Custom API |
| CRM integrations inside Creator | missing |

Re-checked 18-Aug-2026 via Creator MCP (`environment: development`): still one
app (`renewals-desk`), no Desk/Card pages, no CRM integration forms. A native
`Renewals_Desk` form is present from the original app create — add CRM
integrations; do not add more native book forms.

## IDE fill blocker

Creator metadata MCP is authenticated (this inventory) but cannot create
pages, reports, CRM integrations, or workflows. The builder is a logged-in
browser session. Playwright (`scripts/zoho-creator-desk`) reuses a Chrome
profile already signed in as LC. A **Upgrade to Creator 5** modal
(`Upgrade later from Setup`) can sit on top of Design / Smart Chat and must
be dismissed before Zia or the `+` menu will accept clicks. Never publish
production from this fill.

## Custom API Cursor (live)

| Field | Value |
|---|---|
| Endpoint | `https://www.zohoapis.com/creator/custom/lamar_risksolutionsgroup668/Cursor` |
| Method | POST |
| Auth | OAuth2, admin |
| Header | `environment: development` (required until production is published) |
| Function | `desk.cursor_api` |
| Ping body | `{"action":"ping"}` |

## How to fill

Open **this** app in the Creator IDE. Paste
[`ZIA_PASTE_PROMPT.md`](ZIA_PASTE_PROMPT.md) into Zia after adding the seven
CRM integrations. Do not create a new application.

## After fill (blank until live)

| Logical name | Live link name |
|---|---|
| Desk page | |
| Card page | |
| Worklist report | |
| Needs verification report | |
| AMS pending report | |
| AMS failed report | |
| Open tasks report | |
