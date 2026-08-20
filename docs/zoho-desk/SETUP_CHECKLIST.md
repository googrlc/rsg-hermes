# Zoho Desk setup checklist (RSG)

Use with the CSVs in this folder and the rules in `hermes/desk/`. Check items
off in the Desk admin UI (Setup). Field types cannot be changed after create.

## 0. Prerequisites

- [ ] Zoho Desk org with Tickets, Knowledge Base, and workflow access
- [ ] Decide the single service mailbox that will open tickets
- [ ] Confirm Momentum remains policy SOR and Zoho CRM remains sales SOR
- [ ] Confirm Nextcloud (or SharePoint) is the document repository
- [ ] OAuth client with Desk ticket scopes **only when** API integration starts (`ZOHO_DESK_ORG_ID`)

## Phase 1 — Foundation

- [ ] Create department **Agency Service** (do not create a department per queue)
- [ ] Create teams: Service Intake, Certificates, Commercial Auto Service, Commercial Lines Service, Personal Lines Service, Claims Support, Billing and Retention, Renewals, New Business Support, Compliance, Management Escalations
- [ ] Create shared ticket fields from `fields_shared.csv`
- [ ] Create picklists from `picklists.csv`
- [ ] Create statuses: New, Triaged, Information Needed, Ready for Processing, In Progress, Submitted to Carrier, Waiting on Carrier, Waiting on Client, Pending Internal Approval, Ready for Delivery, Delivered, Monitoring, Resolved, Closed, Cancelled, Duplicate
- [ ] Build the General Service layout (shared fields + native subject/status/priority/contact/account)
- [ ] Connect one service email channel to Agency Service
- [ ] Create essential views from `views.csv` (at least Unassigned New Tickets, My Open Cases, Urgent and High Priority, Waiting on Client, Waiting on Carrier)

## Phase 2 — High-volume workflows (launch four)

Launch first:

1. Certificate Requests
2. Vehicle and Driver Changes
3. Billing, Cancellation, and Reinstatement
4. General Policy Changes

- [ ] Certificate layout (`fields_certificate.csv`) + Blueprint (`blueprints.md`)
- [ ] Auto/Driver layout (`fields_auto_driver.csv`) — sensitive DOB / license fields restricted
- [ ] General Policy Change layout (`fields_policy_change.csv`) + Blueprint
- [ ] Billing layout (`fields_billing.csv`) and Cancellation layout (`fields_cancellation.csv`) + Blueprint
- [ ] Email templates (`email_templates.md`)
- [ ] Workflows AUT-01 … AUT-10 (`automations.md`)
- [ ] Required-by reminders (AUT-09)
- [ ] Waiting-state follow-up rules (AUT-05 / AUT-06)

Claims and Renewals layouts wait until the four launch workflows run cleanly.

## Phase 3 — Integration

- [ ] Contact and account synchronization with Zoho CRM
- [ ] AMS client and policy lookup (CF-01)
- [ ] CRM opportunity handoff (AUT-13) — keep the service ticket open
- [ ] Document-folder linking (CF-03) — store the link, do not copy files
- [ ] AMS activity posting (AUT-14 / CF-04) — mark posted only after success
- [ ] Integration exception view (CF-05)
- [ ] Duplicate detection flag (AUT-12) — never auto-delete

## Phase 4 — Intelligence

- [ ] Ticket classification (Hermes `classify_request` / Desk workflow)
- [ ] Suggested replies and missing-information detection
- [ ] Knowledge article recommendations (internal KB only for credentials and agency codes)
- [ ] Amy / Copilot case search
- [ ] Management case briefings

## Status rules to enforce in Blueprints

- [ ] New does not remain in an unmonitored general queue
- [ ] Information Needed identifies the missing item
- [ ] Waiting on Carrier and Waiting on Client require Next Follow-Up Date
- [ ] Resolved = operational work complete; Closed = confirmation and recordkeeping complete
- [ ] Cancelled requires a cancellation reason
- [ ] Duplicate requires the related ticket number
