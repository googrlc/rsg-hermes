---
name: client-onboarding
description: Client onboarding workflow for Gretchen — new client checklist, separate opportunity creation per line of business, file folder structure, welcome email draft, and first-touch follow-up. Use when a new client is signed or when Gretchen asks "set up this new client in the system."
---

# Client Onboarding

The new-client setup playbook. When a client is signed, this walks
through everything that needs to happen to get them fully into the
system.

## When to use

- A new client is signed and needs to be set up.
- "Create an onboarding checklist for this new client."
- "Create separate EspoCRM opportunities for each line of business."
- "Create the file folder structure for this client."
- "Draft the welcome email."

## Onboarding checklist

### 1. EspoCRM Account

- Search for duplicates first.
- Create Account: Personal Household or Commercial Business.
- Fill in: Account Type, Primary Line of Business, Assigned Service Rep
  (Gretchen for personal lines, Lamar for commercial), Client Status
  (Active), Renewal Month, Preferred Contact Method, Retention Risk
  (Low for new clients).
- Save the account.

### 2. EspoCRM Contacts

- Create a Contact for the primary client.
- Create Contacts for spouse, business owner, office manager, or other
  relevant people.
- Link each to the Account.
- Fill in email, phone, address.

### 3. EspoCRM Opportunities

Create one opportunity per line of business:

- [Client Name] - Personal Auto - New Business - [Year]
- [Client Name] - Home - New Business - [Year]
- [Client Name] - Umbrella - New Business - [Year]
- [Client Name] - Commercial Auto - New Business - [Year]

Set stage to Closed-Won if already bound, or to Proposal Sent if still
in process. Fill in: Expiration Date, Current Premium, Assigned To.

### 4. File folder structure

Create in Google Drive or SharePoint:

```
Client Documents /
  [Client Name] /
    2026 /
      Personal Auto /
      Home /
      Umbrella /
      Commercial Auto /
```

Paste the folder link into the Account File Storage Link field in
EspoCRM.

### 5. Welcome email

Draft a warm welcome email to the client:

```
Hi [Client],

Welcome to Risk Solutions Group! We are glad to have you.

Here is what happens next:
1. Your policies are being set up with [Carrier].
2. Your renewal date is [Date].
3. I will be your main point of contact for any questions, changes, or
   certificates of insurance.
4. You can reach me at [phone] or [email].

If you need anything before your policy starts, just let me know.

Thanks,
Gretchen
Risk Solutions Group
```

### 6. First-touch follow-up

- Create an EspoCRM task: "Welcome call to [Client]" due in 3 business
  days.
- Create an EspoCRM task: "Check policy bind confirmation" due in 5
  business days.
- Create an EspoCRM task: "30-day check-in" due in 30 days.

### 7. Cross-sell review

After onboarding, check for cross-sell opportunities:
- Has auto but no home? Flag home cross-sell.
- Has home but no umbrella? Flag umbrella cross-sell.
- Has auto + home but no life? Flag life cross-sell.
- Create a cross-sell opportunity if appropriate.

## n8n automation suggestion

If onboarding is a repeat workflow, suggest:
- Auto-create opportunities per LOB from the onboarding form.
- Auto-create file folders from the new account.
- Auto-create welcome + follow-up tasks.
