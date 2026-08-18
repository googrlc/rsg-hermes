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
| Forms / CRM integration forms | missing (`getForms` 3910 No forms available) |
| Reports Worklist, Needs verification, AMS pending/failed, Open tasks | missing (`getReports` 3930 No reports available) |
| Sections / Chat Agent | empty |
| Deluge (stage, window, tasks, enqueue, approve, dismiss) | missing |
| CRM integrations inside Creator | missing (`is_connection_referenced`: false) |

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
