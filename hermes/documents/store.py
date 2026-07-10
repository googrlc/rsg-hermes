"""Document store — one entry point to persist a Hermes-created document.

``save_document`` writes to the stores that make up the library:
  1. Supermemory  — searchable content + agent recall
  2. ``hermes_documents`` (Supabase) — the fast index rendered as
     folders -> documents

Client file storage itself lives in **Nextcloud** (the agency's file source of
truth); this store is Hermes' searchable index of documents it authors, not the
file store. (A former Google Drive mirror was removed 2026-07-10.)

Folder model:
  - client space:   one folder per EspoCRM account (``account_name``)
  - internal space: freeform folders for internal references (``folder``)
"""

from __future__ import annotations

import logging
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient
from hermes.integrations.supermemory_client import (
    SupermemoryClient,
    client_tags,
    internal_tags,
)

log = logging.getLogger(__name__)

TABLE = "hermes_documents"
PREVIEW_CHARS = 500

VALID_DOC_TYPES = (
    "proposal", "note", "renewal", "comparison", "appetite", "reference", "other",
)


class DocumentStoreError(Exception):
    """Raised when a document cannot be persisted."""


def save_document(
    *,
    title: str,
    content: str,
    doc_type: str = "other",
    account_name: str | None = None,
    account_id: str | None = None,
    folder: str | None = None,
    summary: str | None = None,
    source: str | None = None,
    created_by: str | None = None,
    supa: SupabaseClient | None = None,
    sm: SupermemoryClient | None = None,
) -> dict[str, Any]:
    """Persist one document. Returns the inserted ``hermes_documents`` row.

    Space is inferred: an ``account_name`` => client space; otherwise the
    document lands in the internal space under ``folder`` (default 'General').
    """
    if not title or not content:
        raise DocumentStoreError("title and content are required")
    if doc_type not in VALID_DOC_TYPES:
        raise DocumentStoreError(f"doc_type must be one of {VALID_DOC_TYPES}")

    if account_name:
        space = "client"
        tags = client_tags(account_name, doc_type)
        folder = None
    else:
        space = "internal"
        folder = folder or "General"
        tags = internal_tags(folder, doc_type)

    supa = supa or SupabaseClient()
    sm = sm or SupermemoryClient()

    metadata = {
        "doc_type": doc_type,
        "title": title,
        "space": space,
        "account_name": account_name or "",
        "folder": folder or "",
        "source": source or "",
    }

    # 1) Supermemory (source of truth + recall)
    sm_result = sm.add_document(content, container_tags=tags, metadata=metadata)
    supermemory_id = sm_result.get("id")
    log.info("doc-store: supermemory id=%s status=%s (%s)",
             supermemory_id, sm_result.get("status"), title)

    # 2) Index row (the folder tree is rendered from this)
    row = supa.insert(TABLE, {
        "space": space,
        "account_name": account_name,
        "account_id": account_id,
        "folder": folder,
        "doc_type": doc_type,
        "title": title,
        "summary": summary,
        "content_preview": content[:PREVIEW_CHARS],
        "supermemory_id": supermemory_id,
        "supermemory_tags": tags,
        "source": source,
        "created_by": created_by,
    })
    log.info("doc-store: indexed %s/%s '%s' (id=%s)",
             space, account_name or folder, title, row.get("id"))
    return row


def list_folders(supa: SupabaseClient | None = None) -> list[dict[str, Any]]:
    """Return folder summaries for the Agent OS tree: one entry per
    (space, folder-name) with a document count."""
    supa = supa or SupabaseClient()
    rows = supa.select(
        TABLE,
        columns="space,account_name,folder",
        params={"order": "created_at.desc"},
        limit=1000,
    )
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        name = r.get("account_name") if r["space"] == "client" else r.get("folder")
        key = (r["space"], name or "General")
        counts[key] = counts.get(key, 0) + 1
    return [
        {"space": space, "name": name, "document_count": n}
        for (space, name), n in sorted(counts.items())
    ]


def list_documents(
    *,
    space: str,
    name: str,
    supa: SupabaseClient | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List documents in one folder. ``name`` is the account (client space)
    or the freeform folder (internal space)."""
    supa = supa or SupabaseClient()
    field = "account_name" if space == "client" else "folder"
    return supa.select(
        TABLE,
        params={"space": f"eq.{space}", field: f"eq.{name}", "order": "created_at.desc"},
        limit=limit,
    )


def get_document(
    doc_id: str, supa: SupabaseClient | None = None
) -> dict[str, Any] | None:
    """Fetch one document index row by id."""
    supa = supa or SupabaseClient()
    rows = supa.select(TABLE, params={"id": f"eq.{doc_id}"}, limit=1)
    return rows[0] if rows else None
