---
name: espocrm-workflow-guide
description: Step-by-step EspoCRM workflow guide for Gretchen — how to create accounts, contacts, opportunities, renewals, activities, and tasks; naming standards; required fields by line of business; tagging; and file linking. Use when Gretchen asks "how do I create/update X in EspoCRM" or when a workflow needs the right CRM steps. Pair with espocrm-field-reference for field names and types.
---

# EspoCRM Workflow Guide for Gretchen

The working CRM is EspoCRM. This is the step-by-step guide for
Gretchen's daily CRM work — how to create and update records, what
fields matter, and how to keep the data clean. For field names and types,
pair this with the `espocrm-field-reference` skill.

## When to use

- "How do I create a new account for this client?"
- "How do I add an opportunity for the home renewal?"
- "What fields do I need for a personal auto renewal?"
- "How should I name this opportunity?"
- "How do I tag a client record?"

## Creating an Account

1. Search first — check for duplicates by name, email, or phone.
2. Account type: Personal Household or Commercial Business.
3. Required fields:
   - Name (Household: "Johnson Family"; Commercial: legal entity name)
   - Account Type
   - Assigned Service Rep (Gretchen unless commercial -> Lamar)
   - Primary Line of Business
   - Client Status (Active, Prospect, Inactive)
4. Recommended fields:
   - Renewal Month
   - Preferred Contact Method
   - File Storage Link (Google Drive or SharePoint folder URL)
   - Retention Risk (Low / Medium / High)
   - Cross-Sell Opportunities
5. Save the account.

## Creating a Contact

1. Search first — check for duplicates by name, email, or phone.
2. Link to the existing Account.
3. Required fields:
   - First Name, Last Name
   - Account (link)
   - Role (Primary Client, Spouse, Business Owner, Office Manager, etc.)
4. Recommended:
   - Email, Phone, Address
   - Date of Birth (if available — needed for life/Medicare, not required
     for P&C)
5. Save the contact.

## Creating an Opportunity

One opportunity per line of business. This gives clean pipeline visibility
instead of one blob.

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
- Line of Business
- Opportunity Type (New Business, Renewal, Cross-Sell, Service)
- Stage
- Assigned To
- Expiration Date (for renewals)
- Current Premium (if known)
- Renewal Premium (when received)

### Stages (renewal pipeline)

Identified -> Outreach Sent -> Quote Requested -> Proposal Sent ->
Negotiating -> Renewed-Won | Lost

### Stages (new business)

Lead -> Qualified -> Quote Requested -> Proposal Sent ->
Negotiating -> Closed-Won | Closed-Lost

## Creating a Task

1. Link to the Account or Opportunity.
2. Required fields:
   - Name (short, action-oriented: "Call Mary Johnson re: driver update")
   - Task Type (Follow-up, Missing Info, Renewal Check-in, Quote Deadline,
     COI Due Date, Client Callback, Carrier Follow-up)
   - Due Date
   - Priority (High, Medium, Low)
   - Assigned To (Gretchen unless it needs Lamar)
3. Related Line (Personal Auto, Home, Commercial Auto, etc.)
4. Save the task.

### Valid task statuses

Inbox -> In Progress -> Waiting on Client -> Waiting on Carrier ->
Completed -> Cancelled

## Creating an Activity Note

Use this standard format for every note:

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
2. Keep the summary to 2-3 sentences — what happened, not a novel.
3. Tag with relevant tags (Renewal, COI, Billing, Claim, etc.).

## Tagging

Use tags consistently:
- Line of business: Personal Auto, Home, Umbrella, Commercial Auto, etc.
- Request type: Renewal, COI, Billing, Claim, Endorsement, Cancellation
- Priority: High Priority, Retention Risk
- Assigned: Gretchen, Lamar

## Linking Files

1. Upload or create the file in Google Drive or SharePoint.
2. Copy the folder or file link.
3. Paste into the Account File Storage Link field.
4. Reference the file location in the activity note.

## Required fields by line of business

### Personal Auto
- Drivers (name, DOB, license number)
- Vehicles (year, make, model, VIN)
- Current carrier
- Current premium
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
- Drivers schedule
- Vehicle schedule
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
