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
| Bound policy data (insureds, premiums, policy details) | NowCerts (mirrored to Supabase `canonical_policies`) |
| Daily assistant / commands | Hermes |
| Automations and workflows | Hermes scheduler + `outbound_sync_queue` (n8n was never deployed and is not in docker-compose) |
| Analytics, snapshots, commission ledger | Supabase |
| Slack messages and alerts | Slack |
| Agency login links | Command Center dashboard (`/command-center/`) |

## Client folder structure

Canonical store is the **Agency Documents** Team Folder (not a share from
one user's Files). See `docs/integrations/nextcloud-team-folders.md`.

```
Agency Documents /
  Commercial Lines /  or  Personal Lines /
    [Client Name] /
      [Policy type] /
        [Document type]s /
          [Year] /
            [Carrier] [file]
```

Examples:
```
Agency Documents / Commercial Lines / ABC Plumbing LLC / Commercial Auto / Policies / 2026 /
Agency Documents / Commercial Lines / ABC Roofing / General Liability / Declaration Pages / 2027 / Travelers GL Dec Page.pdf
Agency Documents / Personal Lines / Johnson Family / Home / Applications / 2026 /
Agency Documents / Claims / ABC Plumbing LLC /
```

Search the file by metadata in Zoho **Document_Registry** (Hermes
`document_registry_search`) — do not hunt folders. The record's
`Nextcloud_File_URL` opens the file. New registry uploads go through Hermes
(`document_registry_upload`); the path is generated from metadata, never typed.

Until the Team Folder cutover finishes, some accounts still live under
the legacy `Clients/{name}/{category}/` tree (Hermes intake/renewal filing).
Do not create a personal folder named Agency Documents — that collides with
the Team Folder mount.

## Rules

1. Every client should have a folder in Nextcloud (the agency's file
   source of truth) under the Personal or Commercial lane.
2. File placement in Nextcloud is either Hermes Document Registry (canonical
   path from metadata) or manual. Do not invent a folder path.
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
