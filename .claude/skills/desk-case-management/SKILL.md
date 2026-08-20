---
name: desk-case-management
description: Zoho Desk case-management operating model for RSG — Desk owns service work, Momentum owns the policy record, Zoho CRM owns sales. Use when configuring Desk, classifying a service ticket, routing certificates / vehicle-driver changes / billing-cancellation / policy changes, checking closure, or asking how cases should work. Complements service-sops (how Gretchen handles a request) with the Desk layout, status, Blueprint, and automation contract.
---

# Zoho Desk case management (RSG)

Desk is the case and workflow layer. **Do not** treat Desk as a second client
database.

| System | Owns |
|---|---|
| Momentum / NowCerts | Policy, insured, coverages, AMS documents of record |
| Zoho CRM | Leads, opportunities, referral partners |
| Zoho Desk | Service cases, ownership, status, communication, SLA |
| Nextcloud / SharePoint | Documents |
| Supabase | Mapping, queues, event history |
| Amy | Classify, draft, search, help complete the case |

Executable rules: `hermes/desk/`. Operator pack: `docs/zoho-desk/`.

## Department

One department: **Agency Service**. The live portal already has department
**RSG** — treat that as Agency Service and do not create another. Queues are
ticket fields and teams, not extra departments.

Live IDs: `docs/zoho-desk/LIVE.md` and `hermes/desk/live.py`.

## Launch workflows (first)

1. Certificate Requests
2. Vehicle and Driver Changes
3. Billing, Cancellation, and Reinstatement
4. General Policy Changes

Claims and renewals come after those run cleanly. One renewal **case** per
policy expiration; 90/60/30 are stages on that case.

## Classify and route

- Uncertain classification → Service Intake (AUT-01). Never leave New in an
  unmonitored general queue.
- Certificate Request → Certificates team, Certificate layout.
- Policy Change + vehicle/driver subtype → Auto/Driver layout; Personal vs
  Commercial Auto team. Block Ready for Processing until effective date and
  VIN/driver fields are present.
- Cancellation warning → Billing and Retention, High or Urgent from the
  **recorded deadline**, disposition required before close.
- “ASAP” in the subject is **not** Urgent. Urgent needs a recorded urgency
  reason.

## Waiting and close

- Waiting on Client / Carrier requires Next Follow-Up Date.
- Waiting on Client requires Missing Information.
- Resolved = the work is done. Closed = confirmation and AMS posting and
  documents are done. Failed checks roll back to In Progress (AUT-10).
- Duplicate = flag for review, never auto-delete (AUT-12).
- Sales interest → flag CRM handoff, **keep the service ticket open** (AUT-13).
- AMS Activity Posted = Yes only after Momentum accepts the note (AUT-14).

## Title

`[Category] | [Client] | [Policy Number] | [Short Request]`

Example: `Certificate | ABC Trucking LLC | CA123456 | Holder request`

## Sensitive data

Driver DOB and license number are Desk fields marked Sensitive. Store them
only where approved access and retention controls apply. Prefer the AMS for
durable storage of license data.
