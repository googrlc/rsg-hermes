---
name: file-storage-guide
description: File storage index for RSG — where client documents, internal SOPs, templates, COIs, CRM records, and automation configs live across Google Drive, NowCerts, EspoCRM, and Supabase. Use when Gretchen asks "where do I put this?" or "where is that file?" or when recommending a file storage location.
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
| Client documents (policies, apps, quotes) | Google Drive (root folder: "Hermes Docs") |
| Internal SOPs | Google Drive / Hermes Docs |
| Templates (emails, forms, checklists) | Google Drive / Hermes Docs |
| Certificates of Insurance | NowCerts |
| CRM records (accounts, contacts, opportunities, tasks, notes) | EspoCRM |
| Bound policy data (insureds, premiums, policy details) | NowCerts (synced to EspoCRM Policy entity) |
| Daily assistant / commands | Hermes |
| Automations and workflows | n8n (defined in docker-compose, check if running) |
| Analytics, snapshots, commission ledger | Supabase |
| Slack messages and alerts | Slack |
| Agency login links | RSG Launchpad (in OpenWebUI) |

## Client folder structure

```
Hermes Docs /
  [Client Name] /
    [Year] /
      [Line of Business] /
```

Examples:
```
Hermes Docs / Johnson Family / 2026 / Personal Auto /
Hermes Docs / Johnson Family / 2026 / Home /
Hermes Docs / ABC Plumbing LLC / 2026 / Commercial Auto /
Hermes Docs / ABC Plumbing LLC / 2026 / COI /
```

## Rules

1. Every client should have a folder in Google Drive under "Hermes Docs".
2. Hermes mirrors Drive files automatically when HERMES_DRIVE_MIRROR is
   enabled.
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
1. Check the EspoCRM Account for file references in notes.
2. Check Google Drive (Hermes Docs) by client name.
3. Check NowCerts for COIs and policy documents.
4. If still not found, ask Gretchen or Lamar where it was saved.
