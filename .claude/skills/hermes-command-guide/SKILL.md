---
name: hermes-command-guide
description: Approved Hermes commands and prompt shortcuts for Gretchen — what she can ask, what Hermes can and cannot do, and how to phrase requests for the best results. Use when Gretchen asks what Hermes can do, when she needs help phrasing a request, or when setting up a new command shortcut.
---

# Hermes Command Guide for Gretchen

The quick-reference for what Gretchen can ask Hermes and how to say it.
Hermes is the daily command center — not a chatbot, not a search box.

## When to use

- Gretchen asks "what can you do?"
- She needs help phrasing a request to get the best result.
- You need to confirm what Hermes can and cannot do before responding.

## What Hermes CAN do (via the Hermes CRM Assistant tool)

| Tool | What it does |
|---|---|
| find_account | Search for a client by name, FEIN, DOT number, or other fields |
| lookup | Look up any CRM record — contacts, clients, policies, opportunities |
| pipeline_report | Show the current sales pipeline summary with stage counts and values |
| data_quality | Run a data quality audit across all CRM modules |
| ping | Check that Hermes is online |
| hermes_command | Send any Hermes command (for actions not covered by other tools) |

## What Hermes can do internally (Hermes agent tools)

These are the real agent capabilities, as registered in
`hermes/core/nl_agent.py`. Anything not on this list does not exist:

**Client (the CRM Desk hub)**
- find_client — search the canonical book by name
- client_policies — a client's policies from the canonical book
- ams_client_snapshot — live NowCerts read: status, in-force policies, open opportunities
- crm_client_activity — the client's open cases and their tasks
- client_documents — list or read a client's Nextcloud documents
- renewals_overview — upcoming renewals and at-risk/retention clients

**Carrier**
- list_carriers — carriers RSG has appointments/data on
- match_carrier_appetite — carriers whose appetite matches a risk

**Commissions**
- commission_summary — expected vs received vs outstanding
- commission_shortfalls — the specific policies RSG is still owed on

**Intake and general**
- intake_lead — process a casual lead intake message (the ONLY write tool;
  always previewed and requires confirmation)
- list_intake_submissions — the intake queue and its statuses
- run_report — run a report or dashboard view
- web_research — research a business/client on the public web
- email_search — search the connected Microsoft 365 mailbox
- list_skills — list the tools Hermes can run

Everything except `intake_lead` is **read-only**.

## What Hermes CANNOT do

- Send anything to a client on its own — Gretchen reviews and sends.
- Bind coverage, issue policies, or make underwriting decisions.
- Auto-issue certificates of insurance.
- Auto-send emails.
- Delete CRM records.
- Bulk-update the CRM.
- Change policy records that are AMS-locked (amsLockState = Synced).
- Make coverage decisions without carrier confirmation.
- Access Medicare health details or eligibility data.
- Write to CRM without an approval token (all writes go through the
  write queue: APPROVE ALL, APPROVE CRM ONLY, APPROVE TASKS ONLY, etc.)

## Daily commands

| Command | What it does |
|---|---|
| "Review my service desk for today." | Shows open tasks, due items, and priorities. |
| "Show my renewals due in the next 30 days." | Lists upcoming renewals with risk level and action needed. |
| "Create an CRM note from this." | Turns rough notes into a clean CRM activity note. |
| "Create a task for me from this client request." | Creates a follow-up task linked to the account. |
| "Draft a client reply." | Drafts a warm, professional email for Gretchen to review. |
| "What information is missing?" | Reviews a request and lists what is needed. |
| "Turn this into a renewal follow-up." | Creates a renewal follow-up note + task + client message. |
| "Prepare this COI request." | Builds a COI checklist and missing-info list. |

## Renewal commands

| Command | What it does |
|---|---|
| "Review this renewal and tell me whether to renew as-is, review, or remarket." | Full renewal triage with recommendation. |
| "Draft a premium increase explanation." | Plain-English explanation for the client. |
| "Create the CRM note and task for this renewal." | Note + task in one step. |
| "Create a 30-day renewal follow-up message." | Client message for the 30-day checkpoint. |

## Onboarding commands

| Command | What it does |
|---|---|
| "Create an onboarding checklist for this new client." | Full checklist by line of business. |
| "Create separate CRM opportunities for each line of business." | One opportunity per LOB. |
| "Create the file folder structure for this client." | Nextcloud folder plan. |
| "Draft the welcome email." | Warm welcome message for the new client. |

## COI commands

| Command | What it does |
|---|---|
| "Review this COI request and tell me what is missing." | Checks for required fields and flags gaps. |
| "Create a COI processing checklist." | Step-by-step COI process. |
| "Draft a response asking for missing certificate information." | Client-facing email requesting missing info. |
| "Create an CRM note for this COI request." | Logs the COI request to the account. |

## Tips for Gretchen

- Be specific: "Review the renewal for Johnson Family auto" beats "check
  my renewals."
- Paste the email or note — Hermes will clean it up.
- Ask for one thing at a time — Hermes will offer the next step.
- If Hermes suggests something and you are not sure, say "show me
  first" before approving.
- When Hermes asks for an approval token (APPROVE ALL, APPROVE CRM ONLY,
  etc.), that is the write queue — it will not write to CRM without your
  say-so.
