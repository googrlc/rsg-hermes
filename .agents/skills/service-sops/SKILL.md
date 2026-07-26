---
name: service-sops
description: RSG service-desk standard operating procedures for Gretchen — customer service, endorsements, billing, claims, cancellations, policy copies, auto ID cards, and renewals. Covers the step-by-step workflow, what to collect, what to update in the CRM, and when to escalate. Use whenever Gretchen asks "how do I handle X?" for a service request, or when a request needs the right SOP applied.
---

# RSG Service SOPs

The playbook for day-to-day service work. Each SOP covers the steps,
what to collect from the client, what to update in the CRM, and when to
escalate to Lamar or the carrier.

## When to use

- Gretchen asks "how do I handle a policy change request?"
- A service request comes in and needs the right process applied.
- You need to confirm the steps for a billing question, claim notice,
  cancellation, or ID card request.

## CRM write queue (applies to every SOP)

All CRM writes (notes, tasks, opportunity updates) go through the
write queue. Hermes will draft the record, ask Gretchen for an approval
token (APPROVE ALL, APPROVE CRM ONLY, APPROVE TASKS ONLY), and only then
execute the write. Never skip this step.

## Service requests are Cases (standard workflow, live 2026-07-10)

Service requests — COI, add/remove vehicle, add driver, endorsement, cert
holder add, mortgagee change, lienholder update, auto ID card, billing/payment,
cancellation, renewal review, claim/FNOL — are logged as **Cases** in the CRM,
one Case per request, tied to the client's Account.

- **Service Request Type** (the Case `type` field): pick the matching one of the
  14 options; use `Other` only when nothing fits.
- **Status flow:** New → In Progress → Pending (waiting on client/carrier) →
  Closed (or Cancelled). Keep it current — status is how anyone sees where the
  request stands.
- **Do NOT double-enter into NowCerts.** Every Case (and every client-linked
  Task) reaches the NowCerts task ledger through the approval-gated
  `outbound_sync_queue`, drained by the casework executor on the Hermes
  scheduler (every 5 minutes). It's idempotent and additive — creates the AMS
  task once, then updates it in place; it never overwrites or deletes AMS data.
  A job that fails is retried with backoff and dead-lettered if it keeps
  failing, with an alert to `#systems-check`.
- **Case vs Task:** a **Case** = a formal service request (typed, above). A
  **Task** = any other client ask or internal to-do. Both reach the AMS only
  when tied to a client (Account / insured GUID `momentum_client_id`). Internal
  auto-generated tasks (syncSource=Hermes) are NOT written back.

Governance: **NowCerts (the AMS) is the system of record.** the CRM is where the
work happens; data flows UP to NowCerts through narrow additive channels only.
(Full detail: the `rsg-ams-source-of-truth-governance` memory + the Service
Request SOP artifact.)

## A. Customer service (general)

1. Identify the client and pull their account in the CRM (use find_account
   or lookup).
2. Classify the request (renewal, COI, billing, claim, endorsement,
   cancellation, ID card, policy copy, general).
3. Determine what information is missing.
4. Take the action or draft the response.
5. Create an CRM ClientNote or ActivityLog with: date, client, line
   of business, request type, summary, action taken, missing info, next
   step.
6. Create an CRM Task if follow-up is needed.
7. Store any documents in Nextcloud (the client's folder under the
   Personal/Commercial lane / [Year] / [LOB]).
8. Escalate to Lamar if: coverage question beyond service, complaint,
   retention risk, or anything requiring producer judgment.

## B. Endorsement / policy change

1. Confirm the client, policy number, and line of business.
2. Get the specific change requested (add/remove vehicle, address
   change, driver change, coverage change).
3. Check if the Policy has amsLockState = Synced — if so, the change
   must go through NowCerts or the carrier, not direct CRM edit.
4. Check if the carrier allows self-service or requires an agent
   endorsement request.
5. Submit the endorsement through the carrier portal or email the
   carrier underwriter.
6. Note the effective date requested.
7. Create CRM note + task for follow-up on confirmation.
8. Inform the client of the expected timeline (typically 3-5 business
   days, carrier-dependent).
9. When the confirmation comes back, update the CRM and notify the
   client.

## C. Billing

1. Identify the client and policy (use lookup).
2. Determine the billing question: payment due, invoice amount, payment
   method, escrow/impound, finance company, returned payment.
3. Check the carrier billing portal or system for current status.
4. Explain the situation in plain English (no jargon).
5. If a payment needs to be made, direct the client to the carrier
   billing line or portal — RSG does not take payments.
6. If escrow/impound, confirm with the mortgagee clause on file.
7. Create CRM note with the billing question and resolution.
8. Escalate to Lamar if: cancellation pending for nonpayment, large
   discrepancy, or client threatening to leave over billing.

## D. Claims

1. Get the facts: who, what, when, where, any injuries, any third party.
2. Identify the policy and carrier.
3. Provide the carrier claims phone number and claim filing
   instructions.
4. If the client prefers, file the claim on their behalf (note: some
   carriers require the insured to call).
5. Create the CRM ActivityLog: date of loss, claim type, claim number
   (when assigned), adjuster contact, status.
6. Create CRM Task for follow-up at 7 days and 30 days.
7. Escalate to Lamar if: bodily injury, fatality, large property loss,
   potential coverage dispute, or commercial claim over $10K.

## E. Cancellation

1. Identify the client, policy, and carrier (use lookup).
2. Determine the reason: nonpayment, at request, underwriter
   cancellation, material change in risk.
3. If nonpayment: check the reinstatement window and payment amount.
   Direct client to pay the carrier directly.
4. If at request: confirm the client wants to cancel, get the effective
   date, check for any earned premium or refund.
5. If underwriter cancellation: review the notice, determine if it is
   contestable, and advise the client.
6. Create CRM note with full details.
7. Create CRM task for replacement coverage if the client still
   needs insurance.
8. Escalate to Lamar immediately for: commercial cancellations, large
   accounts, or retention risk.

## F. Policy copy

1. Identify the client and policy (use lookup).
2. Check if a digital copy is in the client Nextcloud folder (under the
   Personal/Commercial lane).
3. If not, request from the carrier portal or email the carrier.
4. Deliver to the client by email or portal.
5. Create CRM note: policy copy requested and delivered.

## G. Auto ID card

1. Identify the client and policy (use lookup).
2. Check if the ID card is available in the carrier portal.
3. If yes, download and send to the client.
4. If no, request from the carrier.
5. Create CRM note: ID card requested and delivered.

## H. Renewal (service side)

See the `renewal-playbook` skill for the full 90/60/30 workflow. For the
service-desk angle:

1. Client contacts about renewal premium increase.
2. Pull the renewal offer and current policy from the CRM (use lookup
   or renewal_audit).
3. Determine the increase percentage and reason.
4. Check for available discounts or coverage adjustments.
5. Recommend: retain as-is, retain with negotiation, or remarket.
6. Draft the client communication.
7. Create CRM note + task.
8. Escalate to Lamar if: increase over 15%, client mentions shopping, or
   commercial renewal over $5K premium.

## Escalation rules

Always escalate to Lamar when:
- Client mentions leaving or shopping elsewhere (retention risk).
- Coverage question requires producer judgment.
- Commercial claim or cancellation.
- Complaint or regulatory inquiry.
- Anything involving Medicare eligibility decisions.
- Anything you are not confident about — say so and ask.

For renewal escalations, the Revenue Sentinel posts alerts to the
#rsg-hermes-project85-renewals Slack channel at 90/60/30-day
checkpoints.
