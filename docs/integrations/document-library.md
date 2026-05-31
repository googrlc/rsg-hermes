# Hermes document library ("Holographic Memory")

A library for documents Hermes creates — proposals, intake notes, renewal
reviews, carrier comparison/appetite packets — plus a freeform space for
internal references. Agent OS renders it as **folders → documents**.

## Where documents live (three stores, one write)

`hermes.documents.store.save_document()` writes to all three:

| Store | Role |
|---|---|
| **Supermemory** | Source of truth + the agent's searchable "Holographic Memory". Content stored with container tags that encode the folder. |
| **Google Drive** | Human-browsable mirror, per-client folders. *(Mirror is guarded — enabled once the Drive scope is granted; until then docs still save to the other two.)* |
| **`hermes_documents`** (Supabase) | Fast index Agent OS reads to build the folder tree. |

## Folder model

- **Client space** — one folder per EspoCRM account (`account_name`), e.g.
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
SUPERMEMORY_API_KEY=...                 # same workspace Agent OS uses
SUPERMEMORY_BASE_URL=https://api.supermemory.ai
HERMES_DRIVE_ROOT_FOLDER=Hermes Docs    # Drive mirror root (when enabled)
```

## Saving via the API

```
POST /api/documents/save
{ "title": "...", "content": "...", "account_name": "Acme Co",
  "doc_type": "proposal", "source": "proposal-builder" }
```
Read side (Agent OS): `GET /api/documents/folders`, `GET /api/documents?space=&name=`.

## Status

- ✅ Supermemory + Supabase index + CLI + `save_document`.
- ✅ **Drive mirror** — uploads each doc as a Google Doc into per-client folders
  under `HERMES_DRIVE_ROOT_FOLDER` (auto-enabled; `HERMES_DRIVE_MIRROR=false`
  to disable). Owner set by `HERMES_DRIVE_SUBJECT`.
- ✅ **Producers** — proposal-builder, crm-note-structurer, renewal-review, and
  carrier-appetite each carry a "Save to the document library" step.
- ✅ **Agent OS panel** — Documents view (folders → documents, preview + Drive
  link) in Mission Control.

### Server deployment note

The Drive mirror needs the service-account JSON readable inside the container
(`GMAIL_SA_KEY_PATH`) and `HERMES_DRIVE_SUBJECT` set to the Drive-owning
Workspace user (the `.com` Google user, NOT the `.net` 365 mailbox).
