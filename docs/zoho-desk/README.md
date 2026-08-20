# Zoho Desk case-management pack (RSG)

Operator artifacts for standing up **Zoho Desk** as the agency case and
workflow layer. Hermes encodes the same rules in `hermes/desk/` so Amy and
future Desk API jobs apply one operating model.

**Desk owns the work. Momentum owns the policy record. Zoho CRM owns the
sales opportunity. Documents remain in the approved repository.**

Live portal IDs and what the Desk MCP already applied: `LIVE.md`.

Hermes can later read and update Tickets, Contacts, and Accounts through the
Desk API (`hermes_integrations.zoho_desk_client`). Custom fields, teams,
statuses, Blueprints, and workflows still need Setup (or
`scripts/zoho_desk_setup.py --apply` with Desk field-create scopes).

## Systems of record

| System | Primary responsibility |
|---|---|
| Momentum / NowCerts | Clients, policies, coverages, policy transactions, carriers, documents of record |
| Zoho CRM | Leads, prospects, opportunities, referral partners, new-business pipeline |
| Zoho Desk | Service cases, ownership, status, communication, approvals, follow-up, SLA tracking |
| Nextcloud or SharePoint | Controlled document storage and agency knowledge |
| Supabase | Integration mapping, event history, queues, AI-ready structured data |
| Amy / Copilot | Search, summarize, draft, classify, help staff complete cases |

## Department and queues

Start with **one** department: **Agency Service**. The live portal already
has department **RSG** — treat that as Agency Service and do not create
another.

Too many departments fragment customer histories and complicate automation.
Create another department only when it needs a truly separate email address,
security model, customer portal, or SLA policy.

Route work with ticket fields and teams:

Certificate Requests · Policy Changes · Billing and Payments · Claims
Assistance · Renewals · Policy Documents · Cancellations and Reinstatements ·
New Business Support · Carrier and Underwriting · Licensing and Compliance ·
General Service · Internal Operations

Teams (a person may belong to several):

Service Intake · Certificates · Commercial Auto Service · Commercial Lines
Service · Personal Lines Service · Claims Support · Billing and Retention ·
Renewals · New Business Support · Compliance · Management Escalations

## Layouts

| Layout | File | When |
|---|---|---|
| General Service (primary / shared fields) | `fields_shared.csv` | Every ticket |
| Certificate Request | `fields_certificate.csv` | Category = Certificate Request |
| Auto or Driver Change | `fields_auto_driver.csv` | Policy Change + vehicle/driver subtype |
| General Policy Change | `fields_policy_change.csv` | Other policy changes |
| Claims Assistance | `fields_claims.csv` | Claims coordination (carrier/AMS claim remains authoritative) |
| Billing and Payment | `fields_billing.csv` | Billing / payment / reinstatement |
| Cancellation or Nonrenewal | `fields_cancellation.csv` | Cancellation / nonrenewal |
| Renewal Service | `fields_renewal.csv` | One case per expiration; 90/60/30 are stages, not extra tickets |

Custom fields can be renamed later; **field type cannot**. Choose types from
the CSVs before rollout. Driver DOB and license number are marked Sensitive
and belong only where approved access and retention controls apply.

Picklists: `picklists.csv`. Statuses, teams, views: `SETUP_CHECKLIST.md` and
`views.csv`.

## Launch sequence

Configure Desk in four phases (`SETUP_CHECKLIST.md`). **Launch with these four
workflows first:**

1. Certificate Requests
2. Vehicle and Driver Changes
3. Billing, Cancellation, and Reinstatement
4. General Policy Changes

Those are structured enough to automate, important enough to track, and common
enough to expose weaknesses in routing, field design, and carrier follow-up.
Add claims and renewals after those run cleanly.

## Hermes rules engine

| Area | Module |
|---|---|
| Catalog (department, teams, statuses, automations) | `hermes/desk/spec.py` |
| Field-create CSVs | `hermes/desk/fields.py` |
| AUT-01 … AUT-14 | `hermes/desk/routing.py` |
| Blueprints | `hermes/desk/blueprints.py` |
| Priority (ASAP is not Urgent) | `hermes/desk/priority.py` |
| Closure control | `hermes/desk/closure.py` |
| Duplicate flagging | `hermes/desk/duplicates.py` |
| CF-02 titles | `hermes/desk/titles.py` |
| CF-01 matching | `hermes/desk/matching.py` |
| CF-06 one renewal case | `hermes/desk/renewals.py` |
| CF-07 CRM Account service-request button | `hermes/desk/crm_button.py` |

Related docs:

- `automations.md` — workflow rules AUT-01 … AUT-14
- `blueprints.md` — four Blueprints and transition requirements
- `email_templates.md` — reusable client templates
- `custom_functions.md` — Deluge / Hermes functions CF-01 … CF-07
- `crm_account_button.md` — CRM Account **Create Service Request** button
- `docs/zoho/` — Zoho CRM field-create pack (sales, not service)
- `docs/zoho-supabase-sync-design.md` — CRM ↔ Supabase sync

## Priority model

**Urgent** only with a recorded urgency reason:

- Cancellation or lapse imminent
- Proof of insurance blocking active work
- Vehicle or driver needs coverage before operation
- Active claim has an immediate service problem
- Binding or effective-date issue
- Regulatory or contractual deadline imminent

Do not let keywords such as “ASAP” automatically make every ticket Urgent.

## Status rules

| Status | Rule |
|---|---|
| New | Must not remain assigned to an unmonitored general queue |
| Information Needed | Must identify the missing item |
| Waiting on Carrier | Must have a next follow-up date |
| Waiting on Client | Must have a next follow-up date |
| Resolved | Operational work is complete |
| Closed | Confirmation and recordkeeping are complete |
| Cancelled | Requires a cancellation reason |
| Duplicate | Requires the related ticket number |

## Desk API (later)

When a refresh token has Desk scopes and `ZOHO_DESK_ORG_ID` is set (`935382122`
for the RSG portal), Hermes can fetch and edit Tickets, Contacts, and
Accounts. Layouts for the four launch workflows already exist in the live
org (`LIVE.md`). Blueprints, custom fields, teams, and statuses stay in the
Desk admin UI until a token with field-create scopes can run
`scripts/zoho_desk_setup.py --apply`.
