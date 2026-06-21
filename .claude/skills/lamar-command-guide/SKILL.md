---
name: lamar-command-guide
description: Lamar's daily command shortcuts and priority framework for Hermes and OpenWebUI — approved commands, priority ordering, daily workflow loop, and how to phrase requests for maximum focus. Use when Lamar asks what to do next, needs help phrasing a request, or wants to know what Hermes can do for him.
---

# Lamar Command Guide

The quick-reference for Lamar's daily workflow. Hermes is the command
interface — not a chatbot, not a place to think about work forever.
Capture, decide, update, move.

## When to use

- Lamar asks "what should I work on?"
- He needs help phrasing a request to get the best result.
- He wants to know what Hermes can do for him.

## Priority ordering (always use this)

When Lamar asks what to do next, show no more than five actions. Prioritize:

1. Revenue at risk (quotes going stale, clients mentioning shopping)
2. Quotes close to binding
3. Stale quotes (no activity for 3+ business days)
4. Effective dates close to today
5. High-premium opportunities
6. Missing information blocking submission
7. Renewals inside 30 days
8. Client service emergencies
9. Administrative cleanup

Before broad planning questions, ask: "Is there an active quote,
renewal, or client follow-up that should be handled first?"

## Daily commands

| Command | What it does |
|---|---|
| "What are my top 5 revenue actions today?" | Pulls open quotes, stale quotes, renewals inside 30 days, missing info, high-premium opps. Caps at five. |
| "Show my stale quotes." | Lists quoted/submitted/waiting opps with no recent activity. |
| "Show opportunities with no next follow-up date." | Flags opps missing the follow-up field. |
| "Show quotes waiting on client." | Lists opps in Waiting on Client status. |
| "Show renewals due in the next 30 days." | Lists upcoming renewals with risk level and action needed. |
| "Show missing information blocking quotes." | Lists opps with missing info preventing submission. |

## Intake commands

| Command | What it does |
|---|---|
| "Start a new intake from messy notes." | Converts pasted notes into structured intake data. |
| "Create one opportunity for each line of business." | Splits a multi-LOB intake into separate opportunities. |
| "Identify missing information from this intake." | Lists what is needed to move forward. |
| "Create an EspoCRM note and follow-up task." | Drafts a CRM note + task through the write queue. |
| "Create client folder structure and file storage recommendation." | Plans the Google Drive folder layout. |

## Quote commands

| Command | What it does |
|---|---|
| "Review this quote and tell me the next action." | Full quote review with status, revenue at risk, next step. |
| "Mark this quote as stale and create a recovery plan." | Flags stale, drafts recovery follow-up. |
| "Draft a quote follow-up email." | Client-facing follow-up message. |
| "Create a quote presentation summary." | Summary for client or internal use. |
| "Update this opportunity stage." | Stage update through the write queue. |

## Dashboard commands

| Command | What it does |
|---|---|
| "Build my pipeline dashboard." | Pipeline by stage with counts and values. |
| "Build stale quote dashboard." | Stale quotes ranked by revenue at risk. |
| "Build missing info dashboard." | Opps blocked by missing information. |
| "Build renewal dashboard." | Renewals by urgency (90/60/30). |
| "Build today's command dashboard." | Top 5 actions + stale quotes + urgent renewals. |

## Refocus command

| Command | What it does |
|---|---|
| "I am distracted. Tell me the one highest-value thing to do next." | Returns one action, why it matters, estimated value, time block, exact next step, Hermes command. |

Use this when your brain starts opening side quests.

## What Hermes CAN do

- Search CRM records by name across any entity type
- Look up specific field values for CRM records
- Run CRM reports and dashboard views
- Calculate total premium for an account
- Show upcoming renewals and at-risk/retention clients
- Research a business/client on the public web
- Create CRM records (Contact, Lead, Account, Opportunity, Task)
- Update existing CRM records by ID
- Process casual lead intake
- Merge duplicate CRM records

## What Hermes CANNOT do

- Send anything to a client on its own — Lamar reviews and sends.
- Bind coverage, issue policies, or make underwriting decisions.
- Auto-send emails or auto-issue COIs.
- Delete CRM records or bulk-update the CRM.
- Edit Policy records with amsLockState = Synced.
- Write to CRM without an approval token (all writes go through the
  write queue).

## The core loop

Every request should push toward:

1. What is the client?
2. What line of business?
3. What stage?
4. What is missing?
5. What is the next action?
6. When is the follow-up?
7. Where is it stored?
8. Is EspoCRM updated?

Not motivation. Not memory. Not "I'll circle back." A real loop.
