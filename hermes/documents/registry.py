"""Document Registry pipeline — Nextcloud first, Zoho metadata second.

Phase 3 of the agency document architecture:

1. Caller supplies metadata + file bytes (never a typed folder path).
2. Hermes derives the canonical Team Folder path from that metadata.
3. PUT the file to Nextcloud (``X-NC-WebDAV-AutoMkcol: 1``) and capture
   ``OC-FileId``.
4. Only then write Zoho ``Document_Registry``, and only if
   ``Nextcloud_File_URL`` is present.

Hermes is the integration layer. n8n is not deployed; Deluge is not required.
"""

from __future__ import annotations

import logging
from typing import Any

from hermes.intake.commit import ENV_WRITE_TO_ZOHO, writes_to_zoho
from hermes_integrations.nextcloud_client import NextcloudClient, NextcloudError
from hermes_integrations.nextcloud_paths import (
    DocumentPathError,
    canonical_rel_path,
)
from hermes_integrations.zoho_client import ZohoClientError
from hermes_integrations.zoho_document_registry import (
    DocumentRegistryZohoError,
    search_registry as zoho_search_registry,
    to_zoho_record,
    upsert_registry_record,
)

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class DocumentRegistryError(RuntimeError):
    """Raised when a registry upload cannot complete."""


def _want_zoho(write_to_zoho: bool | None) -> bool:
    if write_to_zoho is None:
        return writes_to_zoho()
    return bool(write_to_zoho)


def register_document(
    *,
    content: bytes,
    file_name: str,
    account_name: str,
    document_type: str,
    policy_type: str,
    line_of_business: str,
    renewal_cycle: str,
    carrier: str = "",
    document_name: str = "",
    content_type: str = "application/octet-stream",
    effective_date: str = "",
    expiration_date: str = "",
    account_id: str = "",
    policy_id: str = "",
    uploaded_by: str = "",
    status: str = "Active",
    write_to_zoho: bool | None = None,
    nc: NextcloudClient | None = None,
    zoho_upsert=upsert_registry_record,
) -> dict[str, Any]:
    """Upload to Nextcloud, then optionally upsert Zoho Document_Registry.

    The Zoho write is skipped (never attempted) when the PUT fails or the
    receipt has no URL. A failed CRM write does not roll back the file.
    """
    if not content:
        raise DocumentRegistryError("file content is required")
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentRegistryError("file exceeds 25 MiB")

    client = nc or NextcloudClient()
    if not client.is_configured():
        raise DocumentRegistryError(
            "Nextcloud is not configured — set NEXTCLOUD_URL, NEXTCLOUD_USER, "
            "and NEXTCLOUD_APP_PASSWORD"
        )

    try:
        planned = canonical_rel_path(
            line_of_business=line_of_business,
            account=account_name,
            policy_type=policy_type,
            document_type=document_type,
            renewal_cycle=str(renewal_cycle),
            file_name=file_name,
            carrier=carrier,
            document_name=document_name,
            base_path=client.base_path,
        )
    except DocumentPathError as exc:
        raise DocumentRegistryError(str(exc)) from exc

    try:
        receipt = client.put_file_receipt(
            planned["rel_path"],
            content,
            content_type=content_type,
            auto_mkcol=True,
        )
    except NextcloudError as exc:
        raise DocumentRegistryError(f"Nextcloud PUT failed: {exc}") from exc

    url = (receipt.get("files_url") or receipt.get("webdav_url") or "").strip()
    meta = {
        "account_name": account_name,
        "account_id": account_id,
        "policy_id": policy_id,
        "document_type": document_type,
        "policy_type": policy_type,
        "line_of_business": line_of_business,
        "renewal_cycle": str(renewal_cycle),
        "carrier": carrier,
        "document_name": document_name or planned["file_name"],
        "file_name": file_name,
        "effective_date": effective_date,
        "expiration_date": expiration_date,
        "uploaded_by": uploaded_by,
        "status": status or "Active",
        "content_type": content_type,
    }

    result: dict[str, Any] = {
        "ok": True,
        "path": planned,
        "nextcloud": receipt,
        "crm": None,
        "crm_skipped": None,
    }

    if not url:
        result["ok"] = False
        result["crm_skipped"] = (
            "refusing CRM write: Nextcloud_File_URL is empty "
            "(golden rule — no document record without a real file)"
        )
        raise DocumentRegistryError(result["crm_skipped"])

    if not _want_zoho(write_to_zoho):
        result["crm_skipped"] = (
            f"{ENV_WRITE_TO_ZOHO} is off — file is in Nextcloud; "
            "CRM record was not written"
        )
        result["crm_payload"] = to_zoho_record(meta, receipt)
        return result

    try:
        crm = zoho_upsert(meta, receipt)
    except (DocumentRegistryZohoError, ZohoClientError) as exc:
        log.warning("document-registry: Nextcloud OK, Zoho write failed: %s", exc)
        result["ok"] = False
        result["crm_skipped"] = f"Zoho write failed after Nextcloud PUT: {exc}"
        result["crm_payload"] = to_zoho_record(meta, receipt)
        return result

    result["crm"] = crm
    return result


def search_documents(**filters: Any) -> list[dict[str, Any]]:
    """Proxy Zoho Document_Registry search."""
    try:
        return zoho_search_registry(**filters)
    except DocumentRegistryZohoError as exc:
        raise DocumentRegistryError(str(exc)) from exc
