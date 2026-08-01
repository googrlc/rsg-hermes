"""File a quote PDF into the client's Nextcloud ``Quotes/`` folder and stamp the
resulting URL onto the quote row.

Provisioning is idempotent: ``ensure_client_folders`` MKCOLs the standard
``Clients/{client}/{category}/`` tree (creating the client folder the first time
they're quoted, reusing it otherwise). The PDF is named so re-entering never
overwrites a prior quote.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hermes_integrations.nextcloud_client import QUOTES_CATEGORY, NextcloudClient
from hermes.quotes import store

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient


def _sanitize(part: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "-" for c in (part or "").strip()) or "x"


def build_filename(quote: dict[str, Any], *, original: str | None = None) -> str:
    """``Quote_<Carrier>_<LOB>_<effective-or-quote#>.pdf`` — unique per carrier/term."""
    carrier = _sanitize(str(quote.get("carrier") or "Carrier"))
    lob = _sanitize(str(quote.get("line_of_business") or "LOB"))
    tag = _sanitize(str(quote.get("effective_date") or quote.get("quote_number") or quote.get("id") or ""))
    ext = "pdf"
    if original and "." in original:
        ext = original.rsplit(".", 1)[-1].lower()[:8] or "pdf"
    stem = f"Quote_{carrier}_{lob}"
    if tag:
        stem = f"{stem}_{tag}"
    return f"{stem}.{ext}"


def file_quote_pdf(
    supa: "SupabaseClient",
    quote: dict[str, Any],
    *,
    content: bytes,
    original_filename: str | None = None,
    content_type: str = "application/pdf",
    client: str | None = None,
) -> dict[str, Any]:
    """File *content* into the client's Nextcloud Quotes/ folder and stamp the quote.

    Returns ``{"url": ..., "path": ..., "filename": ..., "quote": <updated row>}``.
    Raises NextcloudError if Nextcloud isn't configured.
    """
    client_name = client or quote.get("insured_name") or quote.get("client_identifier") or "Unknown Client"
    nc = NextcloudClient()
    nc.ensure_client_folders(client_name)  # idempotent; creates the client folder if new
    filename = build_filename(quote, original=original_filename)
    filed = nc.file_document(
        content=content,
        filename=filename,
        content_type=content_type,
        client=client_name,
        category=QUOTES_CATEGORY,
    )
    updated = store.attach_document(
        supa,
        str(quote["id"]),
        url=filed["url"],
        path=filed["path"],
        filename=filename,
    )
    return {"url": filed["url"], "path": filed["path"], "filename": filename, "quote": updated}
