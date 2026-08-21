---
name: file-storage-guide
description: File storage index for RSG — where client documents, internal SOPs, templates, COIs, CRM records, and automation configs live across Nextcloud, NowCerts, the CRM, and Supabase. Use when Gretchen asks "where do I put this?" or "where is that file?" or when recommending a file storage location.
---

# File Storage Guide

The simple map of where things live at RSG. Use this to answer "where
do I put this?" and "where is that file?"

## When to use

- "Where do I store this client document?"
- "Where are our templates?"
- "Where does the COI go?"
- "Where is the SOP for [process]?"
- Recommending a file storage location in a service note.

## Where things live

| What | Where |
|---|---|
| Client documents (policies, apps, quotes) | Nextcloud `Clients/{Lead or Account name}/…` (file source of truth). Zoho **Document Registry** holds the `/f/{fileid}` link — not a second copy of the PDF. |
| Internal SOPs | Nextcloud |
| Templates (emails, forms, checklists) | Nextcloud |
| Certificates of Insurance | NowCerts |
| CRM records (accounts, contacts, opportunities, tasks, notes) | the CRM |
| Bound policy data (insureds, premiums, policy details) | NowCerts (mirrored to Supabase `canonical_policies`) |
| Daily assistant / commands | Hermes |
| Automations and workflows | Hermes scheduler + `outbound_sync_queue` (n8n was never deployed and is not in docker-compose) |
| Analytics, snapshots, commission ledger | Supabase |
| Slack messages and alerts | Slack |
| Agency login links | Command Center dashboard (`/command-center/`) |

## Client folder structure

One tree. Hermes Document Registry files here. `NEXTCLOUD_BASE_PATH` (often
`Agency Documents`) is only a mount prefix.

```
[Agency Documents/]Clients/{Lead or Account display name}/
  Intake/
  Quotes/
  Proposals/
  Policies/
  COIs/
  Claims/
  Correspondence/
  Renewal Reviews/
```

File in **Zoho Document Registry** (drop the PDF, label the metadata). Do not
go to Nextcloud first. Do not use Zoho Attachments as the library. Do not use
a second `Filed_Documents` module if it exists.

Leads use the same `Clients/{name}/` folder. Converting the lead later fills
the Account lookup; the files stay put.

## Rules

1. Every client (and every lead that has a file) should have a folder in
   Nextcloud under `Clients/{display name}/`. Hermes creates it on upload
   if it is missing.
2. Staff file through Zoho **Document Registry**. Hermes PUTs the bytes.
3. COIs stay in NowCerts — do not duplicate to Nextcloud unless the
   client specifically requests a copy.
4. CRM records (notes, tasks, opportunities) live in the CRM — do not
   create separate documents for them.
5. If a document is referenced in a CRM note, include the Document
   Registry row or the `/f/{fileid}` permalink.

## When to create a new folder

- New client onboarding (see the `client-onboarding` skill).
- New line of business added to an existing client.
- New year (create year subfolder for existing clients).

## Escalation

If a file cannot be found:
1. Check the CRM Account for file references in notes.
2. Check Nextcloud by client name.
3. Check NowCerts for COIs and policy documents.
4. If still not found, ask Gretchen or Lamar where it was saved.
