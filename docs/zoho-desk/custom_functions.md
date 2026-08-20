# Desk custom functions (CF-01 … CF-06)

Zoho Desk custom functions use Deluge and can be triggered from workflows,
Blueprint transitions, and schedules. Use native workflows first. Use a
custom function only when conditional logic or cross-system processing cannot
be managed cleanly with standard rules.

Hermes already implements the decision logic in Python (`hermes/desk/`).
Deluge bodies below are the Desk-side stubs to paste when integration starts.
They call Hermes or set fields; they do not invent AMS or CRM data.

## CF-01 Find the account and policy

**Input:** sender email, policy number, phone, business name

**Output:** Desk contact, Desk account, AMS client ID, matching policy,
assigned producer, assigned service owner

If multiple matches exist, **flag for manual selection**. Do not pick a
winner. Python: `hermes.desk.matching.resolve_account`.

```deluge
// Stub: replace invokeurl with the approved Hermes lookup when live.
ticketID = ticket.get("id");
// Set cf_integration_status = "Pending" while lookup runs.
// On 0 hits: notify Service Intake (AUT-01 no_account_match).
// On 1 hit: stamp AMS Client ID, CRM Account ID, producer, service owner.
// On 2+ hits: leave unassigned and add a comment "Manual account selection required".
```

## CF-02 Generate standardized case title

Format: `[Category] | [Client] | [Policy Number] | [Short Request]`

Example: `Certificate | ABC Trucking LLC | CA123456 | Holder request`

Python: `hermes.desk.titles.case_title`.

```deluge
category = ifnull(ticket.get("cf_request_category"), "Service");
client = ifnull(account.get("accountName"), "Unknown client");
policy = ifnull(ticket.get("cf_policy_number"), "No policy");
short = ticket.get("subject");
subject = category + " | " + client + " | " + policy + " | " + short;
```

## CF-03 Create secure document folder

- Locate the client folder in Nextcloud / SharePoint
- Create a case subfolder when appropriate
- Store the folder link on `cf_document_folder_link`
- **Avoid copying documents** unnecessarily between systems

## CF-04 Post AMS activity

Send a structured resolution note to Momentum: ticket number, contact, policy,
category, summary, owner, disposition.

Mark `cf_ams_activity_posted` = Yes **only after a successful response**.
Python / workflow: AUT-14.

## CF-05 Integration error handler

- Capture the failed request
- Store the error on `cf_integration_error`
- Set `cf_integration_status` = Failed
- Create an internal resolution task
- Prevent a false “sync complete” status

## CF-06 Renewal case generation

Generate **one** renewal case from the AMS expiration feed. Do not generate
three unrelated tickets for 90 / 60 / 30 days.

| Stage | Days out | Work |
|---|---|---|
| 90-day | ≥ 75 | Eligibility and information review |
| 60-day | ≥ 45 | Client / exposure follow-up |
| 30-day | ≥ 1 | Escalation and final renewal handling |
| Completion | ≤ 0 | Confirm renewal, AMS update, document delivery |

Identity: `REN|{policy_number}|{expiration_date}`. Python:
`hermes.desk.renewals.renewal_identity`.
