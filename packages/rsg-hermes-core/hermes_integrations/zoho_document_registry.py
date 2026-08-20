"""Zoho Document_Registry module — field map, search, and CRM writes.

Hermes is the integration layer (n8n was never deployed). Nextcloud holds the
file; this module writes metadata to Zoho only after a successful PUT and only
when ``Nextcloud_File_URL`` is present (the golden rule).
"""

from __future__ import annotations

import os
from typing import Any

from hermes_integrations.zoho_client import (
    ZohoClient,
    ZohoClientError,
    _escape_criteria_value,
    _present,
)

MODULE_ENV = "ZOHO_DOCUMENT_REGISTRY_MODULE"
DEFAULT_MODULE = "Document_Registry"

# Logical API names (Zoho may append __c / __s — callers can override via env).
FIELD_NEXTCLOUD_URL = "Nextcloud_File_URL"
FIELD_NEXTCLOUD_ID = "Nextcloud_File_ID"
FIELD_FOLDER_PATH = "Nextcloud_Folder_Path"


def module_api_name(env: dict[str, str] | None = None) -> str:
    raw = (env if env is not None else os.environ).get(MODULE_ENV, DEFAULT_MODULE)
    return (raw or DEFAULT_MODULE).strip() or DEFAULT_MODULE


class DocumentRegistryZohoError(RuntimeError):
    """Raised when a Document_Registry CRM write or search fails."""


def to_zoho_record(
    meta: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Build a Zoho record from upload metadata + Nextcloud PUT receipt.

    ``Nextcloud_File_URL`` is mandatory. Prefer the Files UI URL (staff can
    click it); fall back to the WebDAV URL.
    """
    url = (receipt.get("files_url") or receipt.get("webdav_url") or "").strip()
    if not url:
        raise DocumentRegistryZohoError(
            "refusing CRM write: Nextcloud_File_URL is empty "
            "(golden rule — no document record without a real file)"
        )
    name = (
        (meta.get("document_name") or "").strip()
        or (receipt.get("file_name") or "").strip()
        or (meta.get("file_name") or "").strip()
    )
    record: dict[str, Any] = {
        "Name": name,
        "Account_Name": (meta.get("account_name") or "").strip() or None,
        "Document_Type": (meta.get("document_type") or "").strip() or None,
        "Carrier": (meta.get("carrier") or "").strip() or None,
        "Policy_Type": (meta.get("policy_type") or "").strip() or None,
        "Effective_Date": (meta.get("effective_date") or "").strip() or None,
        "Expiration_Date": (meta.get("expiration_date") or "").strip() or None,
        "Renewal_Cycle": str(meta.get("renewal_cycle") or "").strip() or None,
        "Line_of_Business": (meta.get("line_of_business") or "").strip() or None,
        FIELD_NEXTCLOUD_URL: url,
        FIELD_NEXTCLOUD_ID: (receipt.get("file_id") or "").strip() or None,
        FIELD_FOLDER_PATH: (receipt.get("folder_path") or "").strip() or None,
        "File_Name": (receipt.get("file_name") or meta.get("file_name") or "").strip() or None,
        "Status": (meta.get("status") or "Active").strip() or "Active",
        "MIME_Type": (receipt.get("mime_type") or meta.get("content_type") or "").strip() or None,
    }
    size = receipt.get("file_size")
    if size is not None:
        record["File_Size"] = size
    account_id = (meta.get("account_id") or "").strip()
    if account_id:
        record["Account"] = {"id": account_id}
    policy_id = (meta.get("policy_id") or "").strip()
    if policy_id:
        record["Policy"] = {"id": policy_id}
    uploaded_by = (meta.get("uploaded_by") or "").strip()
    if uploaded_by and uploaded_by.isdigit():
        record["Uploaded_By"] = {"id": uploaded_by}
    return {k: v for k, v in record.items() if _present(v) or v is False}


def search_criteria(
    *,
    account_name: str = "",
    document_type: str = "",
    carrier: str = "",
    policy_type: str = "",
    renewal_cycle: str = "",
    line_of_business: str = "",
    status: str = "",
) -> str:
    """Zoho ``(Field:equals:value)and(...)`` criteria. Empty filters are omitted."""
    clauses: list[tuple[str, str]] = []
    if account_name.strip():
        clauses.append(("Account_Name", account_name.strip()))
    if document_type.strip():
        clauses.append(("Document_Type", document_type.strip()))
    if carrier.strip():
        clauses.append(("Carrier", carrier.strip()))
    if policy_type.strip():
        clauses.append(("Policy_Type", policy_type.strip()))
    if str(renewal_cycle).strip():
        clauses.append(("Renewal_Cycle", str(renewal_cycle).strip()))
    if line_of_business.strip():
        clauses.append(("Line_of_Business", line_of_business.strip()))
    if status.strip():
        clauses.append(("Status", status.strip()))
    parts = [
        f"({field}:equals:{_escape_criteria_value(value)})" for field, value in clauses
    ]
    return "and".join(parts)


def upsert_registry_record(
    meta: dict[str, Any],
    receipt: dict[str, Any],
    *,
    client: ZohoClient | None = None,
    module: str | None = None,
) -> dict[str, Any]:
    """Create or update the Document_Registry row. Never called without a URL."""
    record = to_zoho_record(meta, receipt)
    url = record.get(FIELD_NEXTCLOUD_URL)
    if not url:
        raise DocumentRegistryZohoError(
            "refusing CRM write: Nextcloud_File_URL is empty"
        )
    zoho = client or ZohoClient()
    mod = module or module_api_name()
    file_id = record.get(FIELD_NEXTCLOUD_ID)
    match_field = FIELD_NEXTCLOUD_ID if file_id else FIELD_NEXTCLOUD_URL
    match_value = str(file_id or url)
    try:
        result = zoho.upsert_by_field(
            mod, record, match_field=match_field, match_value=match_value
        )
    except ZohoClientError as exc:
        raise DocumentRegistryZohoError(str(exc)) from exc
    return {**result, "module": mod, "record": record}


def search_registry(
    *,
    account_name: str = "",
    document_type: str = "",
    carrier: str = "",
    policy_type: str = "",
    renewal_cycle: str = "",
    line_of_business: str = "",
    status: str = "",
    client: ZohoClient | None = None,
    module: str | None = None,
) -> list[dict[str, Any]]:
    """Search Document_Registry by metadata. Returns [] when nothing matches."""
    criteria = search_criteria(
        account_name=account_name,
        document_type=document_type,
        carrier=carrier,
        policy_type=policy_type,
        renewal_cycle=renewal_cycle,
        line_of_business=line_of_business,
        status=status,
    )
    if not criteria:
        raise DocumentRegistryZohoError(
            "at least one search filter is required "
            "(account_name, document_type, carrier, policy_type, "
            "renewal_cycle, line_of_business, or status)"
        )
    zoho = client or ZohoClient()
    mod = module or module_api_name()
    try:
        return zoho.search_records(mod, criteria)
    except ZohoClientError as exc:
        raise DocumentRegistryZohoError(str(exc)) from exc
