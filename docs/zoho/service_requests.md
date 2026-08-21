# Service Requests (Zoho CRM)

Zoho CRM is the **only** system of record for RSG service work.

```
Zoho CRM
  Accounts
  Contacts
  Policies
  Renewals
  Claims          ← only if it already exists live; this pack does not create it
  Service_Requests ← this pack
```

**Catalyst Service Desk** is a dashboard / work queue over CRM data. It must
not store records. **Zoho Desk is not the system of record.** Do not create
Desk tickets from the CRM buttons in this pack. Do not stamp
`cf_crm_account_id` onto Desk tickets.

Hermes README already states Zoho CRM is the CRM system of record. This pack
matches that.

Draft PR #358 (`docs/zoho-desk/`, `hermes/desk/`) encoded a Desk-owned model.
Leave that code in place; do not delete it. The buttons here write
`Service_Requests` in CRM instead.

## Live inspection (this change)

Inspected 2026-08-21 from this repo's `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` /
`ZOHO_REFRESH_TOKEN` and from the deployed Catalyst app.

| Check | Result |
|---|---|
| REST `GET /crm/v2/settings/modules` | **Failed.** Token exchange returned `invalid_code`. The refresh token in this environment is 35 characters and matches the client-id shape — it is not a usable Zoho refresh token. Re-run `scripts/zoho_apply_service_requests.py` on Elestio / a sandbox with a real refresh token before `--apply`. |
| Catalyst app `service-desk-935150771` | Live. Meta: "RSG Service Desk — work client Cases. Hermes writes the AMS." Bundle `main.d2e4dc07.js`. |
| Catalyst store | **Cases**, not `Service_Requests` (zero hits for that API name in the bundle). |
| Catalyst fields | `Desk_Stage`, `Request_Type`, `Policy_Number`, `Client_Name`, `Account_Name`, `Owner_Name`, `Due_Date`, `Service_Time`, `Completion_Time`, `Age_Days`, `Completed_At`, `Closed_Time`, `Next_Step`, `Contact_Name`, `Subject`, `Description`, `Overdue`. |
| Catalyst tabs | Open `/`, Waiting `#/waiting`, Completed `#/completed`, Overdue. |
| Catalyst API | `/api/desk`, `/api/desk/cases/{id}`, `/api/desk/cases/{id}/email`, `/api/desk/cases/{id}/close`, `/api/desk/tasks/{id}`. |
| Catalyst web tab | `module=Cases` and `${Cases.id}`. OAuth scopes: `ZohoCRM.modules.ALL`, `ZohoCRM.settings.ALL`, `ZohoCRM.users.READ`, `ZohoCRM.send_mail.all.CREATE`. Connection name `zoho_crm`. |
| Cases layout in this repo | `scripts/zoho_update_layouts.py` has live Cases layout `7529682000000091027` — Cases exists in the RSG org. |
| `docs/zoho/modules_custom.csv` (pack) | Policies, Renewal_Events, Renewals, AMS_Write_Queue. **No Service_Requests. No Claims.** |
| Claims | Do not create. If inspect later finds a Claims module, add the Account related list in the CRM UI; this pack never invents it. |
| Hermes `/api/desk` (this repo, before this pack) | **Absent.** `/api/cases` lives in `googrlc/rsg-hermes-cases` (Supabase `agency_crm_cases`) and is not this queue. |

### Decision

Create custom module **Service_Requests** as specified (API name requested
explicitly). Do **not** silently extend or rename Cases.

Cases is the current Catalyst store and already carries insurance-shaped
fields (`Request_Type`, `Policy_Number`, `Desk_Stage`, timers). Extending
Cases would keep native Emails/Activities with less work, but the requested
API name is `Service_Requests`. If Zoho rejects that API name, the apply
script prints the closest legal alternative (`ServiceRequests`, org-suffixed
`Service_Requests__s`, etc.) and **does not** fall back to Cases without
saying so.

**Migration:** Catalyst currently points at Cases. Retarget it to
`Service_Requests` using `docs/zoho/catalyst_field_map.csv`. Do not dual-write
to Desk. Existing Cases rows are not migrated by this pack (no silent copy).

## Module

| | |
|---|---|
| Display name | Service Requests |
| API name | `Service_Requests` |
| Account lookup | **Required.** Related list Accounts → Service Requests |
| Contact lookup | Optional. Related list Contacts → Service Requests |
| Policy lookup | Custom module `Policies` (`docs/zoho/fields_policies.csv`). Related list Policies → Service Requests |
| Renewal lookup | Optional, custom module `Renewals`. Related list Renewals → Service Requests |

Enable standard related lists on the Service Request: Activities, Tasks,
Emails, Notes, Attachments, Calls, Meetings.

On Account: Policies, Renewals, Service Requests, Claims (only if Claims
already exists), Tasks.

## Picklists (exact)

**Request_Type** — do not invent extras; do not import the 1d endorsement seed
as-is (`Certificate of Insurance`, `Replace Driver`, `Address Change`,
`Coverage Change` stay on that seed, not here):

Certificate Request, Policy Change, Add Vehicle, Remove Vehicle, Add Driver,
Remove Driver, Billing Question, Claims Question, Coverage Question, Renewal
Service, Cancellation, Reinstatement, ID Card Request, Mortgagee Change,
Document Request, Other

**Status:** New, In Progress, Waiting, Completed

**Priority:** Low, Standard, High

**Team:** Personal Lines, Commercial, Unassigned

## Workflows (CRM, not Desk)

Keep these as simple workflow rules / a short Blueprint on
`Service_Requests`. No Desk blueprints.

1. **On create:** Status = New. Open_Date = now if empty. Assign Owner =
   Account Owner (Accounts has no CSR column in `fields_accounts.csv`). If
   the Account has no Owner, leave Owner empty — do not invent an Unassigned
   user.
2. **When opened / worked** (Status → In Progress): keep Open_Date; Service_Time
   formula starts from Open_Date.
3. **Status = Waiting:** leave Service_Time as wall-clock. A true pause needs
   accumulated wait minutes and a custom store; this pack does not add one.
4. **Status = Completed:** set Closed_Date = now if empty. Completion_Time
   formula uses Closed_Date − Open_Date.

Last_Activity: workflow on create/edit → Last_Activity = now.

Paste-ready Deluge for (1) and (4) is in `hermes/zoho/crm_buttons.py`
(`render_workflow_on_create`, `render_workflow_on_complete`).

## Buttons (CRM, not Desk)

Install as custom buttons (View Page). Generated Deluge lives in
`hermes/zoho/crm_buttons.py`. Print with:

```bash
python scripts/zoho_apply_service_requests.py --print-deluge
```

| Module | Label | Prefill |
|---|---|---|
| Accounts | New Service Request | Account, primary Contact, Owner = Account Owner |
| Contacts | New Service Request | Contact, Account |
| Policies | Service This Policy | Policy, Policy_Number, Account, Named Insured, Carrier |
| Emails | Create Service Request | Subject, body → Description, Account, Contact, Policy_Number if found, Request_Type suggestion |

Each button calls `zoho.crm.create("Service_Requests", …)` and opens the new
record. None of them call `zoho.desk.*`.

If the custom-buttons API lacks scope, paste the Deluge in Setup → Modules →
{module} → Links & Buttons. The apply script tries the API, then prints
paste steps.

## Catalyst retarget

The deployed React app is **not** in this repo. Point it at
`Service_Requests`:

1. Change the Zoho Embedded App / web-tab URL from `module=Cases` /
   `${Cases.id}` to `module=Service_Requests` / `${Service_Requests.id}`.
2. Read/write the field map in `docs/zoho/catalyst_field_map.csv`.
3. Keep `/api/desk` and `/api/desk/cases/{id}` path shapes so the minified
   bundle can keep calling them — but the records must be CRM
   `Service_Requests`, not Desk tickets and not a new table.
4. Hermes now serves those paths (hub) by reading Zoho CRM
   `Service_Requests`. Status updates PATCH the same module. No Supabase
   service-desk table.

Query params the bundle already sends: `view` (`desk` / `waiting` /
`completed`), `stage`, `type`, `q`, `mine`, `window` (completed list,
default `month`).

## Apply

Dry-run by default. Writes to CRM only with `--apply`. Prefer a Zoho sandbox
token; do not `--apply` against production from this Cloud environment.

```bash
source .venv/bin/activate
python scripts/zoho_apply_service_requests.py
python scripts/zoho_apply_service_requests.py --apply   # sandbox / approved org only
```

Idempotent: skips an existing `Service_Requests` module and fields whose
label or logical API name already exists (including `__c` / `__s` suffixes).

## What this pack does not do

- Store service records in Supabase
- Create a Service Desk database
- Create a Claims module
- Delete or rewrite `docs/zoho-desk/` / `hermes/desk/`
- Open Zoho Desk or write Desk tickets
- Dual-write Cases and Service_Requests
- Copy existing Cases into Service_Requests
