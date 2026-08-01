"""Hermes Files store (Command Center Files panel).

Durable store for files Hermes creates (notes, reports, save-lists, proposals,
saved answers) with full content, so the Command Center can list and download them.
Backed by the server-only ``hermes_files`` table (service-role access).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from hermes_integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

VALID_KINDS = ("note", "report", "save-list", "proposal", "answer", "other")
_LIST_COLUMNS = "id,title,kind,content_type,file_ext,source,created_by,created_at"


def save_file(
    supa: SupabaseClient,
    *,
    title: str,
    content: str,
    kind: str = "note",
    content_type: str = "text/markdown",
    file_ext: str = "md",
    source: str = "command-center",
    created_by: str = "hermes",
) -> dict[str, Any]:
    """Persist a Hermes-created file and return the saved row."""
    if not (content or "").strip():
        raise ValueError("Cannot save an empty file.")
    return supa.insert(
        "hermes_files",
        {
            "title": (title or "Untitled").strip()[:200],
            "kind": kind if kind in VALID_KINDS else "other",
            "content": content,
            "content_type": content_type,
            "file_ext": file_ext,
            "source": source,
            "created_by": created_by,
        },
    )


def list_files(supa: SupabaseClient, *, limit: int = 100) -> list[dict[str, Any]]:
    """List Hermes files (newest first), without the heavy content body."""
    return supa.select(
        "hermes_files",
        columns=_LIST_COLUMNS,
        params={"order": "created_at.desc"},
        limit=limit,
    )


def get_file(supa: SupabaseClient, file_id: str) -> dict[str, Any] | None:
    """Fetch one file with full content for download."""
    rows = supa.select("hermes_files", params={"id": f"eq.{file_id}"}, limit=1)
    return rows[0] if rows else None


def download_filename(file_row: dict[str, Any]) -> str:
    """Safe attachment filename from a file row."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (file_row.get("title") or "file")).strip("_")[:80] or "file"
    ext = (file_row.get("file_ext") or "md").lstrip(".")
    return f"{safe}.{ext}"
