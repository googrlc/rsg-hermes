# Live Zoho Desk portal (RSG)

Verified against the Desk MCP on 2026-08-20. IDs are strings. Executable
constants: `hermes/desk/live.py`.

**Portal:** [https://desk.zoho.com/agent/rsg10761](https://desk.zoho.com/agent/rsg10761)
**Org:** `935382122` (company RSG, edition CRMPLUS, TZ America/New_York)

## Department — do not create a second one

The design calls for one department named Agency Service. This portal already
has one enabled department:

| Name | ID | Notes |
|---|---|---|
| RSG | `1435573000000006907` | Default. Assign-to-team is on. Treat as Agency Service. |

Rename RSG → Agency Service in Desk Setup if you want the label to match the
spec. Queues stay as ticket fields and teams, not extra departments.

## Layouts (launch four + primary)

| Layout | ID | Default layout | Classification default |
|---|---|---|---|
| General Service | `1435573000000074011` | Yes (standard layout, renamed) | General Service (mandatory) |
| Certificate Request | `1435573000000460002` | No | Certificate Request (mandatory) |
| Auto or Driver Change | `1435573000000453002` | No | Policy Change (mandatory) |
| General Policy Change | `1435573000000463001` | No | Policy Change (mandatory) |
| Billing and Cancellation | `1435573000000464001` | No | Billing and Payments (mandatory) |

Billing and Cancellation is one live layout covering both billing and
cancellation CSVs. Claims Assistance and Renewal Service layouts wait until
the four launch workflows run cleanly.

Light Agent cannot be associated with the cloned layouts (`LightAgentCannotBeAssociated`).
The default General Service layout still includes Light Agent.

## Native picklists already written

Classification (`classification`, `1435573000000022001`) keeps Zoho's system
values (`-None-`, Question, Problem, Feature, Others) **plus** the 12 RSG
categories from `hermes/desk/spec.py`.

Priority (`priority`, `1435573000000000437`): `-None-`, Urgent, High, Medium,
Normal, Low. Default **Normal**. ASAP is still not Urgent — record an urgency
reason once `cf_urgency_reason` exists.

Channel (`channel`, `1435573000000000439`): system channels plus Portal,
Internal, Slack, AMS, CRM. Default **Email**. Twitter / Facebook / Instagram
cannot be removed (`SystemPickListValueCannotRemoved`).

Language was removed from General Service.

## Phase 1 applied (fields and teams)

`--apply` created the spec fields and 11 teams in org `935382122`. Billing
**Due Date** is the native Due Date field (not a second custom date).

CRM Plus allows **20 Boolean fields**. The first 20 flags are Boolean; the
rest are Yes/No picklists (default No). Field type cannot be changed later.

Custom fields are on General Service and cloned onto the four launch layouts.
Certificate Request includes holder name/address and AMS Client ID.

### Teams

| Team | ID |
|---|---|
| Service Intake | `1435573000000456002` |
| Certificates | `1435573000000464542` |
| Commercial Auto Service | `1435573000000467001` |
| Commercial Lines Service | `1435573000000452002` |
| Personal Lines Service | `1435573000000468001` |
| Claims Support | `1435573000000469001` |
| Billing and Retention | `1435573000000464548` |
| Renewals | `1435573000000470001` |
| New Business Support | `1435573000000467007` |
| Compliance | `1435573000000456008` |
| Management Escalations | `1435573000000471001` |

## Status — cannot change via API

Still: Open, On Hold, Escalated, Closed. Default Open. Add New, Triaged,
Information Needed, and the rest from `SETUP_CHECKLIST.md` in Desk Setup UI
(Setup → Customization → Status).

## Still in Setup UI

- CRM Account **Create Service Request** button (View Page + each list row). Deluge: `crm_account_button.md`. Connection `zohodesk`.
- Custom statuses
- Blueprints
- Workflows AUT-01 … AUT-14
- Email templates / views / knowledge base
- Optional rename RSG → Agency Service

Do not invent those objects. Field **type** cannot be changed after create.
