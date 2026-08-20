# Hermes document library

An index of the documents **Hermes creates** — proposals, intake notes, renewal
reviews, carrier comparison/appetite packets — plus a freeform space for internal
references. It renders as **folders → documents**.

> **This is not the file store.** Client files (policies, applications, quotes,
> signed docs, COIs' source material) live in **Nextcloud**, the agency's file
> source of truth. Metadata for those files belongs in Zoho **Document_Registry**
> (see `docs/integrations/nextcloud-team-folders.md`). This library only holds
> the write-ups Hermes authors, so they are searchable and recallable by the
> agent. There is **no Google Drive** in this pipeline (the former Drive mirror
> was removed 2026-07-10).

## Where documents live (two stores, one write)

`hermes.documents.store.save_document()` writes to both:

| Store | Role |
|---|---|
| **Supermemory** | Searchable content + the agent's recall. Content stored with container tags that encode the folder. |
| **`hermes_documents`** (Supabase) | Fast index the document-library API reads to build the folder tree. |

## Folder model

- **Client space** — one folder per CRM account (`account_name`), e.g.
  `1195 Holdings LLC`. Supermemory tag: `client:<slug>`.
- **Internal space** — freeform folders for internal references
  (`folder`, default `General`). Supermemory tag: `internal` + `folder:<slug>`.

All docs also carry `hermes-docs` and `type:<doc_type>` tags.

## Saving programmatically

```python
from hermes.documents.store import save_document

save_document(
    title="1195 Holdings LLC — Builders Risk Proposal",
    content=markdown_or_text,
    doc_type="proposal",
    account_name="1195 Holdings LLC",   # -> client folder
    source="proposal-builder",
)

save_document(
    title="Carrier appetite cheat sheet",
    content=notes,
    doc_type="reference",
    folder="Underwriting Cheat Sheets",  # -> internal freeform folder
    source="manual",
)
```

## CLI (handy for internal references)

```bash
# List the folder tree
hermes --doc-folders

# Add an internal reference from a file
hermes --doc-add --doc-title "E&S submission checklist" \
  --doc-folder "Checklists" --doc-type reference --doc-file ./checklist.md

# Add a client document from stdin
cat proposal.md | hermes --doc-add --doc-title "Acme GL Proposal" \
  --doc-account "Acme Co" --doc-type proposal
```

## Config

```bash
SUPERMEMORY_API_KEY=...                 # the Supermemory workspace
SUPERMEMORY_BASE_URL=https://api.supermemory.ai
```

## Saving via the API

```
POST /api/documents/save
{ "title": "...", "content": "...", "account_name": "Acme Co",
  "doc_type": "proposal", "source": "proposal-builder" }
```
Read side: `GET /api/documents/folders`, `GET /api/documents?space=&name=`.

## Status

- ✅ Supermemory + Supabase index + CLI + `save_document`.
- ✅ **Producers** — proposal-builder, crm-note-structurer, renewal-review, and
  carrier-appetite each carry a "Save to the document library" step.
- ❌ **Google Drive mirror** — **removed 2026-07-10.** Client files live in
  Nextcloud (file source of truth); this library indexes Hermes-authored write-ups
  only. `hermes/integrations/gdrive_client.py` and the `HERMES_DRIVE_*` config were
  deleted.
