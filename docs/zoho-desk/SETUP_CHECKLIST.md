# Zoho Desk setup checklist (RSG)

Use with the CSVs in this folder and the rules in `hermes/desk/`. Check items
off in the Desk admin UI (Setup). Field types cannot be changed after create.

Live portal inventory and IDs: `LIVE.md`.

## 0. Prerequisites

- [x] Zoho Desk org with Tickets access (CRM Plus portal `rsg10761`, org `935382122`)
- [ ] Decide the single service mailbox that will open tickets
- [x] Confirm Momentum remains policy SOR and Zoho CRM remains sales SOR
- [x] Confirm Nextcloud (or SharePoint) is the document repository
- [ ] OAuth client with Desk ticket **and** field-create scopes when API apply starts (`ZOHO_DESK_ORG_ID=935382122`)

## Phase 1 — Foundation

- [x] One department in use: live name **RSG** (`1435573000000006907`). Treat as Agency Service. Do not create a second department.
- [ ] Optional: rename department RSG → Agency Service in Setup
- [x] Create teams: Service Intake, Certificates, Commercial Auto Service, Commercial Lines Service, Personal Lines Service, Claims Support, Billing and Retention, Renewals, New Business Support, Compliance, Management Escalations
- [x] Create shared ticket fields from `fields_shared.csv` (billing Due Date uses native Due Date)
- [x] Native picklists: Classification (12 RSG categories), Priority (Urgent + Normal), Channel (Portal, Internal, Slack, AMS, CRM)
- [ ] Create statuses: New, Triaged, Information Needed, Ready for Processing, In Progress, Submitted to Carrier, Waiting on Carrier, Waiting on Client, Pending Internal Approval, Ready for Delivery, Delivered, Monitoring, Resolved, Closed, Cancelled, Duplicate
- [x] General Service layout (`1435573000000074011`) — native fields plus `cf_*` from Phase 1 apply. Billing Due Date is native Due Date.
- [ ] Connect one service email channel to the RSG department
- [ ] Create essential views from `views.csv` (at least Unassigned New Tickets, My Open Cases, Urgent and High Priority, Waiting on Client, Waiting on Carrier)

## Phase 2 — High-volume workflows (launch four)

Launch first:

1. Certificate Requests
2. Vehicle and Driver Changes
3. Billing, Cancellation, and Reinstatement
4. General Policy Changes

- [x] Certificate layout cloned (`1435573000000460002`) with `fields_certificate.csv` — Blueprint still needed (`blueprints.md`)
- [x] Auto/Driver layout cloned (`1435573000000453002`) with `fields_auto_driver.csv`; license number is encrypted; DOB is Date (not encryptable)
- [x] General Policy Change layout cloned (`1435573000000463001`) with `fields_policy_change.csv` — Blueprint still needed
- [x] Billing and Cancellation layout cloned (`1435573000000464001`) with billing/cancellation fields — Blueprint still needed
- [ ] Email templates (`email_templates.md`)
- [ ] Workflows AUT-01 … AUT-10 (`automations.md`)
- [ ] Required-by reminders (AUT-09)
- [ ] Waiting-state follow-up rules (AUT-05 / AUT-06)

Claims and Renewals layouts wait until the four launch workflows run cleanly.

## Phase 3 — Integration

- [ ] Contact and account synchronization with Zoho CRM
- [ ] CRM Account button **Create Service Request** (View Page + list-view row). Spec: `crm_account_button.md`. Connection name `zohodesk`.
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
