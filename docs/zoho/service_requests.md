# Service Requests (Zoho CRM)

Zoho CRM is the **only** system of record for RSG service work.

```
Zoho CRM
  Accounts
  Contacts
  Policies
  Renewals
  Claims            ← only if it already exists live; this pack does not create it
  Service_Requests  ← this pack (target queue)
  Cases             ← live Catalyst queue today; retarget, do not dual-write
```

**Catalyst Service Desk** is a dashboard / work queue over CRM data. It does
not store records (no Catalyst Data Store). **Zoho Desk is not the system of record.**
Do not create Desk tickets from the CRM buttons in this pack. Do not stamp
`cf_crm_account_id` onto Desk tickets.

Draft PR #358 (`docs/zoho-desk/`, `hermes/desk/`) encoded a Desk-owned model.
Leave that code in place; do not delete it. The buttons here write
`Service_Requests` in CRM instead.

## Live Catalyst queue (source of truth)

Inspected 2026-08-21. Do not guess past this.

| | |
|---|---|
| App | https://service-desk-935150771.development.catalystserverless.com/app/index.html |
| Function | `service_desk_function` (id `95147000000018011`) |
| Connection | `zoho_crm` |
| Health | `GET /server/service_desk_function/api/health` → `{ok:true, app:"service-desk", crm_connection:"zoho_crm"}` |
| Store | **Zoho CRM Cases.** Not Desk. Not a Catalyst Data Store. Not Hermes. |
| Queue at inspect | Empty (all KPIs 0). That is not an auth failure. |
| Source map | https://service-desk-935150771.development.catalystserverless.com/app/static/js/main.d2e4dc07.js.map |
| Originals in the map | `api.js`, `crmLaunch.js`, `App.js`, `components/{DeskHome,Worklist,KpiStrip,CrmShell,CaseCard,ClientEmail,CloseOut,DeskTips}.js` |

The Catalyst **client source is not in this repo.** Retarget from the public
source map. Do not invent a Catalyst repo.

This repo does **not** host `service_desk_function`. Hermes
`GET /api/desk` (hub) is a Service_Requests-backed implementation of the same
client contract so the app can point `REACT_APP_API_BASE` at Hermes, or so the
Catalyst function can be edited in Catalyst Console to read
`Service_Requests`. Search of this repo: `/api/desk` exists only as that
helper (`hermes/routers/desk.py`). `/api/cases` is the departed
`rsg-hermes-cases` app (Supabase) and is a different queue.

### Client API (`api.js`)

Base: `process.env.REACT_APP_API_BASE || '/server/service_desk_function'`

| Method | Path | Live behavior |
|---|---|---|
| GET | `/api/health` | `{ok, app, crm_connection}` |
| GET | `/api/desk` | Work queue |
| GET/PATCH | `/api/desk/cases/:id` | Card + save |
| POST | `/api/desk/cases/:id/close` | Close-out |
| POST | `/api/desk/cases/:id/email` | Client email via CRM send-mail |
| PATCH | `/api/desk/tasks/:id` | Complete a CRM Task |

List query (`DeskHome.js` → `getDesk`):

- `view=worklist\|waiting\|completed` (hash `#/` is UI `desk`, API `worklist`)
- `overdue` is accepted by the function per inspect; the UI never sends it.
  Overdue KPI click opens unfiltered Open (`view=desk` → API `worklist`).
- `stage`, `type`, `q`
- `mine` default **1** (`crmLaunch.parseRoute`: `mine === '0' ? '0' : '1'`)
- `window=month\|30` on Completed

List payload the UI reads: `rows`, `shown`, `total`, `kpis.{open,waiting,overdue,done_month}`,
`request_types`, `request_type_labels`, `empty_reason`.

Card payload: `case`, `vocab.{stages,request_types,request_type_labels,dispositions,disposition_labels}`,
`related.{account,policy,notes,contact}`, top-level `tasks` (Open Tasks reads
`a.tasks`, not `related.tasks`), `steps_complete`. Save PATCHes
`{Desk_Stage, Request_Type, Policy_Number, Subject, Description}` and expects
the same card shape back.

Missing card (live): `{"error":"Cases 1 not found"}` — module is Cases.
After retarget use `{"error":"Service_Requests {id} not found"}` (`api.js`
`ApiError` reads `payload.error`, not FastAPI `detail`).

Web tab: `?id=${Cases.id}&module=Cases`. Widget `PageLoad` only opens the card
when `!entity \|\| /case/i.test(entity)` (`App.js`). **`Service_Requests` does
not match `/case/i`.** Retarget must change that regex (and the web-tab URL)
or the embedded tab will not open an SR.

### Live Cases field map (API → UI)

Client_Name, Policy_Number, Request_Type / Request_Type_Label, Owner_Name,
Due_Date, Next_Step, Age_Days (computed in the function), Service_Time
(computed in the function: **runs In progress, pauses Waiting**),
Completion_Time (computed: opened → now until Done), **Desk_Stage (CUSTOM
picklist, not CRM Status)**, Overdue (computed vs due), Completed_At \|\|
Closed_Time \|\| Modified_Time, Case_Number, Subject, Description,
Account_Name, Contact_Name, related Account `NowCerts_Insured_GUID`, related
Policy (`Policy_Number`, `Status`, `Carrier`), Notes, Tasks.

Full column map: `docs/zoho/catalyst_field_map.csv`.

### Live vocab vs this pack (map; do not silently replace)

**Desk_Stage (UI / Cases custom picklist):** New, In progress, Waiting on
carrier, Waiting on client, Done.

Buckets (`KpiStrip.js` / inspect):

| Bucket | Live Desk_Stage | Service_Requests |
|---|---|---|
| Open (Worklist) | New + In progress | Status `New` + `In Progress` |
| Waiting | Waiting on carrier + Waiting on client | Status `Waiting` |
| Completed | Done | Status `Completed` |
| Overdue | computed flag / KPI only | `Overdue` formula; **not a tab** |

**Request_Type live codes → labels:** `coi` COI, `endorsement` Endorsement,
`id_cards` ID cards, `billing` Billing, `claim` Claim, `cancellation`
Cancellation, `coverage_change` Coverage change, `other` Other.

**Service_Requests.Request_Type** is the user's longer list (Certificate
Request, Add Vehicle, …). Mapping: `docs/zoho/cases_request_type_map.csv`.

| Live code | Live label | Service_Requests.Request_Type |
|---|---|---|
| coi | COI | Certificate Request |
| endorsement | Endorsement | Policy Change |
| id_cards | ID cards | ID Card Request |
| billing | Billing | Billing Question |
| claim | Claim | Claims Question |
| cancellation | Cancellation | Cancellation |
| coverage_change | Coverage change | Coverage Question |
| other | Other | Other |

`endorsement` is one live bucket. The user's list splits Policy Change / Add
Vehicle / Remove Vehicle / Add Driver / Remove Driver. New work uses the split
labels. Migrating an existing `endorsement` Case becomes **Policy Change**
unless a human picks a tighter type.

### UI vs spec

| | Live UI (`CrmShell` / `Worklist`) | User spec |
|---|---|---|
| Nav tabs | Worklist \| Waiting \| Completed | Open / Waiting / Completed / Overdue |
| Overdue | KPI tile only; click → unfiltered Open | Overdue tab |
| Columns | Client Name, Policy Number, Request Type, Assignee, Due/Completed, Next Step, Age, Service Time, Completion Time, Desk Stage | Client, Policy Number, Request Type, Assigned To, Due Date, Status, Service Time |
| Mine only | Default **on** | (unspecified) |
| Close-out | Creates CRM Tasks (client email + AMS file-by-hand). Does **not** write NowCerts. | CRM writes on the SR if kept |

Keep Next Step / Age / Completion Time / projected Desk Stage on the queue
payload so the current Worklist does not go blank. Spec columns are all
present; the extras stay.

## Write gap (document; do not hide)

The live function **writes** CRM Cases, CRM Tasks, and CRM send-mail. The
user target is a **display-only** queue over `Service_Requests`.

| Live write | After retarget |
|---|---|
| PATCH Cases (`Desk_Stage`, Request_Type, …) | Prefer **reads** of Service_Requests. If Save stays, PATCH Service_Requests (`Status` + `Waiting_On`, not Desk_Stage). Not Desk. Not a new DB. |
| POST close → Cases + Tasks | Optional: Status=Completed + Closed_Date on the SR. Tasks, if kept, are CRM Tasks related to the SR. Never NowCerts, never Desk. |
| POST email → ZohoCRM.send_mail | Optional: same CRM send-mail on the SR record. |
| PATCH Tasks | Optional: CRM Tasks. |

Hermes `/api/desk` in this repo implements the **read** contract against
Service_Requests and the optional CRM writes above. Writes require
`HERMES_API_TOKEN`. Errors are `{"error":"..."}` (the live `ApiError` reads
`payload.error`, not FastAPI `detail`). It does not call the Catalyst
function, Desk, or NowCerts. The deployed UI still talks to
`service_desk_function` until `REACT_APP_API_BASE` or that function changes.

## Decision

Create custom module **Service_Requests** (API name requested explicitly).
Do **not** silently extend or rename Cases. Do **not** create `Desk_Stage` on
Service_Requests — project it from `Status` + `Waiting_On`.

If Zoho rejects the API name, the apply script prints the closest live
alternative and does not fall back to Cases without saying so.

Existing Cases rows are not copied by this pack.

## Module

| | |
|---|---|
| Display name | Service Requests |
| API name | `Service_Requests` |
| Account lookup | **Required.** Related list Accounts → Service Requests |
| Contact lookup | Optional. Related list Contacts → Service Requests |
| Policy lookup | Custom module `Policies`. Related list Policies → Service Requests |
| Renewal lookup | Optional, `Renewals`. Related list Renewals → Service Requests |

Enable standard related lists on the Service Request: Activities, Tasks,
Emails, Notes, Attachments, Calls, Meetings.

On Account: Policies, Renewals, Service Requests, Claims (only if Claims
already exists), Tasks.

## Picklists (exact)

**Request_Type** — user's list; do not invent extras; do not import 1d
(`Certificate of Insurance`, `Replace Driver`, `Address Change`,
`Coverage Change`):

Certificate Request, Policy Change, Add Vehicle, Remove Vehicle, Add Driver,
Remove Driver, Billing Question, Claims Question, Coverage Question, Renewal
Service, Cancellation, Reinstatement, ID Card Request, Mortgagee Change,
Document Request, Other

**Status:** New, In Progress, Waiting, Completed

**Waiting_On** (optional; only when Status = Waiting): carrier, client

**Priority:** Low, Standard, High

**Team:** Personal Lines, Commercial, Unassigned

Projected **Desk_Stage** for the current UI (not stored):

| Status | Waiting_On | Desk_Stage emitted |
|---|---|---|
| New | — | New |
| In Progress | — | In progress |
| Waiting | carrier | Waiting on carrier |
| Waiting | client (or empty) | Waiting on client |
| Completed | — | Done |

## Workflows (CRM, not Desk)

1. **On create:** Status = New. Open_Date = now if empty. Owner = Account
   Owner (Accounts has no CSR column). If none, leave Owner empty.
2. **In Progress:** Service_Time in the *live function* starts. CRM formula is
   wall-clock fallback only.
3. **Waiting:** set Waiting_On to carrier or client. Live function **pauses**
   Service_Time. Completion_Time keeps running.
4. **Completed:** Closed_Date = now. Projected Desk_Stage = Done.

Paste-ready Deluge: `hermes/zoho/crm_buttons.py`.

## Buttons (CRM, not Desk)

```bash
python scripts/zoho_apply_service_requests.py --print-deluge
```

| Module | Label | Prefill |
|---|---|---|
| Accounts | New Service Request | Account, primary Contact, Owner = Account Owner |
| Contacts | New Service Request | Contact, Account |
| Policies | Service This Policy | Policy, Policy_Number, Account, Named Insured, Carrier |
| Emails | Create Service Request | Subject, body → Description, Account, Contact, Policy_Number if found, Request_Type suggestion |

Each button calls `zoho.crm.create("Service_Requests", …)`. None call
`zoho.desk.*`.

## Catalyst retarget (not in this repo)

Public map: `main.d2e4dc07.js.map`. Change in Catalyst Console / the app that
owns that bundle:

1. CRM connection stays `zoho_crm`. Point reads at module `Service_Requests`.
2. Web tab: `id=${Service_Requests.id}&module=Service_Requests`.
3. `App.js` / `crmLaunch.js`: widget `PageLoad` `/case/i` must also accept
   Service_Requests (e.g. `/case|service_request/i`).
4. Map `Desk_Stage` ↔ `Status` + `Waiting_On` (table above). Do not keep a
   stored Desk_Stage on the new module.
5. Map live Request_Type codes via `cases_request_type_map.csv`. Dropdown
   values become the user's labels.
6. Keep computing Age_Days / Service_Time (pause on Waiting) /
   Completion_Time / Overdue in the function if Save/queue stay honest.
7. Writes: Cases → Service_Requests. Tasks stay CRM Tasks on the SR. Email
   stays CRM send-mail. **Never Desk. Never NowCerts. Never a new table.**
8. Optional: set `REACT_APP_API_BASE` to Hermes if the function is not
   edited; Hermes `/api/desk` already speaks this contract against
   Service_Requests.

## Apply

Dry-run by default. `--apply` writes CRM. Prefer a sandbox token.

```bash
source .venv/bin/activate
python scripts/zoho_apply_service_requests.py
python scripts/zoho_apply_service_requests.py --apply
```

Idempotent on module + field label / logical API name (`__c` / `__s`).

This environment's `ZOHO_REFRESH_TOKEN` still returns `invalid_code` — re-run
inspect on Elestio / sandbox before `--apply`.

## What this pack does not do

- Store service records in Supabase or a Catalyst Data Store
- Create a Claims module
- Delete or rewrite `docs/zoho-desk/` / `hermes/desk/`
- Open Zoho Desk or write Desk tickets
- Dual-write Cases and Service_Requests
- Copy existing Cases into Service_Requests
- Edit the deployed Catalyst function (not in this repo)
