---
name: file-storage-guide
description: File storage index for RSG — where client documents, internal SOPs, templates, COIs, CRM records, and automation configs live across Google Drive, SharePoint, NowCerts, EspoCRM, and n8n. Use when Gretchen asks "where do I put this?" or "where is that file?" or when recommending a file storage location.
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
| Client documents (policies, apps, quotes) | Google Drive / SharePoint |
| Internal SOPs | SharePoint |
| Templates (emails, forms, checklists) | Google Drive / SharePoint |
| Certificates of Insurance | NowCerts |
| CRM records (accounts, contacts, opportunities, tasks, notes) | EspoCRM |
| Bound policy data (insureds, premiums, policy details) | NowCerts |
| Daily assistant / commands | Hermes |
| Automations and workflows | n8n |
| Analytics, snapshots, commission ledger | Supabase |
| Slack messages and alerts | Slack |

## Client folder structure

```
Client Documents /
  [Client Name] /
    [Year] /
      [Line of Business] /
```

Examples:
```
Client Documents / Johnson Family / 2026 / Personal Auto /
Client Documents / Johnson Family / 2026 / Home /
Client Documents / ABC Plumbing LLC / 2026 / Commercial Auto /
Client Documents / ABC Plumbing LLC / 2026 / COI /
```

## Rules

1. Every client should have a folder in Google Drive or SharePoint.
2. The folder link should be in the EspoCRM Account File Storage Link
   field.
3. COIs stay in NowCerts — do not duplicate to Google Drive unless the
   client specifically requests a copy.
4. CRM records (notes, tasks, opportunities) live in EspoCRM — do not
   create separate documents for them.
5. If a document is referenced in an EspoCRM note, include the file
   location (folder path or link) in the note.

## When to create a new folder

- New client onboarding (see the `client-onboarding` skill).
- New line of business added to an existing client.
- New year (create year subfolder for existing clients).

## Escalation

If a file cannot be found:
1. Check the EspoCRM Account File Storage Link.
2. Check Google Drive / SharePoint by client name.
3. Check NowCerts for COIs and policy documents.
4. If still not found, ask Gretchen or Lamar where it was saved.
