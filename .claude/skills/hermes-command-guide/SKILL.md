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
| find_account | Search for an account by name, FEIN, DOT number, or other fields |
| lookup | Look up any CRM record — contacts, accounts, policies, opportunities |
| renewal_audit | Run a renewal audit — surface upcoming renewals and gaps |
| pipeline_report | Show the current sales pipeline summary with stage counts and values |
| data_quality | Run a data quality audit across all CRM modules |
| crm_changelog | Show recent CRM changes — new and updated records |
| ping | Check if Hermes and the CRM connection are online |
| hermes_command | Send any Hermes command (for actions not covered by other tools) |

## What Hermes can do internally (Hermes agent tools)

These are the underlying Hermes agent capabilities that the CRM Assistant
tool routes to:

- search_records — search CRM records by name across any entity type
- get_field_value — look up a specific field value for a CRM record
- run_report — run a CRM report or dashboard view
- total_premium — calculate total premium for a specific account
- renewals_overview — upcoming renewals and at-risk/retention clients
- web_research — research a business/client on the public web
- create_record — create a new CRM record (Contact, Lead, Account, Opportunity, Task)
- update_record — update an existing CRM record by ID
- intake_lead — process a casual lead intake message
- merge_records — merge duplicate CRM records

## Other tools available

| Tool | What it does |
|---|---|
| RSG n8n Bridge | Trigger an RSG n8n workflow via webhook (if n8n is running) |
| RSG Supabase | Query the Supabase data warehouse — commission parity, CRM change proposals, sync control |
| RSG Launchpad | Get the RSG agency launchpad URL (carrier login links) |

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
