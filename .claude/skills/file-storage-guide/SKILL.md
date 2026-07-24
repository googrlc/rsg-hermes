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
| Client documents (policies, apps, quotes) | Nextcloud (the agency's file source of truth) — the client's folder under the Personal/Commercial lane |
| Internal SOPs | Nextcloud |
| Templates (emails, forms, checklists) | Nextcloud |
| Certificates of Insurance | NowCerts |
| CRM records (accounts, contacts, opportunities, tasks, notes) | the CRM |
| Bound policy data (insureds, premiums, policy details) | NowCerts (synced to CRM Policy entity) |
| Daily assistant / commands | Hermes |
| Automations and workflows | n8n (defined in docker-compose, check if running) |
| Analytics, snapshots, commission ledger | Supabase |
| Slack messages and alerts | Slack |
| Agency login links | RSG Launchpad (in OpenWebUI) |

## Client folder structure

```
RSG /
  🏠 Personal (Gretchen)  or  🏢 Commercial (Lamar) /
    [Client Name] /
      [Year] /
        [Line of Business] /
```

Examples:
```
RSG / 🏠 Personal (Gretchen) / Johnson Family / 2026 / Personal Auto /
RSG / 🏠 Personal (Gretchen) / Johnson Family / 2026 / Home /
RSG / 🏢 Commercial (Lamar) / ABC Plumbing LLC / 2026 / Commercial Auto /
```

## Rules

1. Every client should have a folder in Nextcloud (the agency's file
   source of truth) under the Personal or Commercial lane.
2. File placement in Nextcloud is manual (via WebDAV) — there is no
   automatic mirror.
3. COIs stay in NowCerts — do not duplicate to Nextcloud unless the
   client specifically requests a copy.
4. CRM records (notes, tasks, opportunities) live in the CRM — do not
   create separate documents for them.
5. If a document is referenced in an CRM note, include the file
   location (folder path or link) in the note.

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
