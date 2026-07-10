---
name: espocrm-workflow-guide
description: Step-by-step EspoCRM workflow guide for Gretchen — how to create accounts, contacts, opportunities, renewals, activities, and tasks; naming standards; required fields by line of business; tagging; file linking; and the CRM write queue. Use when Gretchen asks "how do I create/update X in EspoCRM" or when a workflow needs the right CRM steps. Pair with espocrm-field-reference for field names and types.
---

# EspoCRM Workflow Guide for Gretchen

The working CRM is EspoCRM. This is the step-by-step guide for
Gretchen's daily CRM work. For field names and types, pair this with
the `espocrm-field-reference` skill.

## When to use

- "How do I create a new account for this client?"
- "How do I add an opportunity for the home renewal?"
- "What fields do I need for a personal auto renewal?"
- "How should I name this opportunity?"
- "How do I tag a client record?"

## CRM entities at a glance

| Entity | What it is | Key fields |
|---|---|---|
| Account | Companies and households (clients, prospects, carriers) | name, account_status, account_type, fein, annual_premium |
| Contact | People attached to an Account | name, emailAddress, phoneNumber, contactType, clientType, householdRole |
| Lead | Unqualified prospect not yet converted | name, emailAddress, source, insuranceInterest |
| Opportunity | Revenue pipeline item (quote, deal) | name, stage, amount, lineOfBusiness, businessType, closeDate |
| Policy | Bound insurance policy | policy_number, carrier, line_of_business, effective_date, expiration_date, premium, amsLockState |
| Renewal | Upcoming policy renewal (Project 85) | stage, expiration_date, current_premium, urgency, line_of_business |
| Commission | Revenue tracking per policy/opportunity | commissionType, commissionRate, estimatedCommission |
| Task | Action items and follow-ups | name, status, taskType, urgency, assignedUserName |
| ActivityLog | Interaction history (calls, emails, changes) | activityType, dateTime, direction, changeSummary |
| ClientNote | Free-text notes attached to an Account | (body text) |

## lineOfBusiness vocabulary

Used on Opportunity, Policy, and Renewal. Use these exact values:
Commercial Auto, General Liability, Workers Comp, Homeowners,
Personal Auto, Umbrella, Renters, Boat/RV, Medicare, Life,
Group Benefits, Final Expense, Professional Liability, Cyber,
Inland Marine, Pollution.

## Creating an Account

1. Search first — check for duplicates by name, email, or phone.
2. Account type: Personal Household or Commercial Business.
3. Required fields:
   - Name (Household: "Johnson Family"; Commercial: legal entity name)
   - account_type (Personal Household, Commercial Business, Carrier, MGA, Prospect)
   - account_status (Active, Urgent, Inactive)
   - assignedUserName (Gretchen for personal lines, Lamar for commercial)
4. Recommended fields:
   - fein (for commercial accounts)
   - annual_premium
   - industry
5. Save the account.

## Creating a Contact

1. Search first — check for duplicates by name, email, or phone.
2. Link to the existing Account.
3. Required fields:
   - First Name, Last Name
   - Account (link) — a Contact can belong to multiple Accounts
   - contactType, clientType
4. Recommended:
   - emailAddress, phoneNumber
   - householdRole (Primary Client, Spouse, etc.)
   - dateOfBirth (if available — needed for life/Medicare)
5. Save the contact.

## Creating an Opportunity

One opportunity per line of business. This gives clean pipeline visibility.

### Naming standard

`[Client Name] - [Line of Business] - [Type] - [Year]`

Examples:
- Johnson Family - Personal Auto Renewal - 2026
- Johnson Family - Home Renewal - 2026
- Johnson Family - Umbrella Cross-Sell - 2026
- ABC Plumbing LLC - Commercial Auto Renewal - 2026
- ABC Plumbing LLC - COI Service - 2026

### Required fields

- Name (follow the naming standard above)
- Account (link)
- lineOfBusiness (use the vocabulary above)
- stage (follow the pipeline below)
- amount (premium or estimated premium)
- assignedUserName
- closeDate (for new business) or expiration date context (for renewals)

### New business stages (do not skip or invent)

Discovery -> Quoting -> Markets Out / Shopping -> Proposal Presented ->
Negotiation -> Closed Won | Closed Lost

### Renewal stages (Project 85)

Identified -> Outreach Sent -> Quote Requested -> Proposal Sent ->
Negotiating -> Renewed - Won | Lost

## Creating a Renewal record

Renewals are tracked in the Renewal entity, not just as Opportunities.
Create a Renewal record for each upcoming policy expiration:

1. Link to the Account, Contact, and Policy.
2. Required fields:
   - stage (start at Identified)
   - expiration_date
   - current_premium
   - line_of_business
   - carrier
3. The Revenue Sentinel monitors these records at 90/60/30-day
   checkpoints and posts alerts to Slack.

## Creating a Task

1. Link to the parent (Account, Contact, Opportunity, etc.) — Tasks
   are polymorphic, they can attach to any entity.
2. Required fields:
   - Name (short, action-oriented: "Call Mary Johnson re: driver update")
   - taskType (Follow-up, Missing Info, Renewal Check-in, Quote Deadline,
     COI Due Date, Client Callback, Carrier Follow-up)
   - status (see below)
   - urgency
   - assignedUserName
3. dateEnd (due date)
4. Save the task.

### Valid task statuses

Inbox -> In Progress -> Waiting on Client -> Waiting on Carrier ->
Completed -> Cancelled

## Creating an Activity Note (ActivityLog or ClientNote)

Use ClientNote for free-text notes attached to an Account. Use
ActivityLog for structured interaction records (calls, emails, changes).

Standard format for every note:

```
Date:
Handled By:
Client:
Line of Business:
Request Type:
Summary:
Action Taken:
Missing Information:
Next Step:
Follow-Up Date:
System Updated:
Files Stored:
```

1. Link the note to the Account (and Opportunity if applicable).
2. Keep the summary to 2-3 sentences.
3. Tag with relevant tags.

## The CRM write queue (critical)

All CRM mutations flow through the write queue — no direct writes from
AI tools.

1. Hermes parses the intent and fields.
2. Payload is inserted into the `crm_write_queue` (status: PENDING).
3. Gretchen (or Lamar) reviews and sends an approval token:
   - `APPROVE ALL` — approve everything
   - `APPROVE CRM ONLY` — approve CRM writes only
   - `APPROVE TASKS ONLY` — approve task creation only
   - `REVISE` — send back for changes
   - `CANCEL` — cancel the write
4. The CRM queue worker dequeues and executes the write to EspoCRM.
5. Receipts are logged with transaction_id and raw response.
6. Status updated: SUCCESS, FAILED, or BLOCKED_BY_GUARDRAIL.

Never skip this step. Even a simple task creation goes through the
queue. Hermes will ask for the approval token before executing.

## amsLockState (policies synced from NowCerts)

Policies synced from NowCerts AMS have an `amsLockState` field:
- `Pending Sync` — policy is being synced, data may be incomplete
- `Synced` — policy data is locked from manual edits; changes must go
  through NowCerts or the carrier

Do not manually edit a Policy record that has `amsLockState = Synced`.
If the policy data is wrong, flag it to Lamar — the fix goes through
NowCerts, not direct CRM edits.

## Linking Files

1. Place the file in Nextcloud (the agency's file source of truth) — the
   client's folder under the Personal/Commercial lane.
2. Copy the file or folder link.
3. Reference the file location in the activity note.
4. File placement in Nextcloud is manual (via WebDAV) — there is no
   automatic mirror.

## Required fields by line of business

### Personal Auto
- Drivers (name, DOB, license number)
- Vehicles (year, make, model, VIN)
- Current carrier
- Current premium (amount field on Opportunity)
- Expiration date

### Home
- Named insured
- Property address
- Year built
- Roof age
- Current carrier
- Current premium
- Expiration date

### Commercial Auto
- Business name + FEIN
- Drivers schedule (OpportunityDriver records)
- Vehicle schedule (OpportunityVehicle records)
- caDotNumber, caMcNumber (on Opportunity)
- Radius of operations
- Current carrier
- Current premium
- Expiration date

### COI
- Named insured
- Certificate holder
- Holder address
- Special wording requirements
- Due date
