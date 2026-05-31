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

## Status / follow-ons

- ✅ Supermemory + Supabase index + CLI + `save_document` — live and tested.
- ⏳ **Drive mirror** — needs the Drive API enabled and scope
  `https://www.googleapis.com/auth/drive` added to the Gmail service account's
  domain-wide delegation (client ID `108633220303303849535`).
- ⏳ **Producers** — wire proposal-builder / crm-note-structurer /
  renewal-review / comparison+appetite to call `save_document`.
- ⏳ **Agent OS panel** — a Documents view listing `hermes_documents` as
  folders → documents.
