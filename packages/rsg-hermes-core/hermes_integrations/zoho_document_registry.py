"""Zoho Document_Registry module — field map, search, and CRM writes.

Hermes is the integration layer. Nextcloud holds the file; this module writes
metadata to Zoho only after a successful PUT and only when
``Nextcloud_File_URL`` is present (the golden rule).

Party is Lead XOR Account. ``Account_Name`` is a derived searchable copy of
whichever display name named the Clients/{name} folder — not a staff-mandatory
gate when a Lead is set.
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

    ``Nextcloud_File_URL`` is mandatory. Prefer the ``/f/{fileid}`` permalink.
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
    party_name = (
        (meta.get("party_name") or "").strip()
        or (meta.get("account_name") or "").strip()
        or (meta.get("lead_name") or "").strip()
    )
    record: dict[str, Any] = {
        "Name": name,
        "Account_Name": party_name or None,
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
        # Zoho integer fields max out at 9 digits.
        try:
            record["File_Size"] = min(int(size), 999_999_999)
        except (TypeError, ValueError):
            pass
    account_id = (meta.get("account_id") or "").strip()
    lead_id = (meta.get("lead_id") or "").strip()
    if account_id:
        record["Account"] = {"id": account_id}
    if lead_id:
        record["Lead"] = {"id": lead_id}
    policy_id = (meta.get("policy_id") or "").strip()
    if policy_id:
        record["Policy"] = {"id": policy_id}
    deal_id = (meta.get("deal_id") or "").strip()
    if deal_id:
        record["Deal"] = {"id": deal_id}
    renewal_id = (meta.get("renewal_id") or "").strip()
    if renewal_id:
        record["Renewal"] = {"id": renewal_id}
    uploaded_by = (meta.get("uploaded_by") or "").strip()
    if uploaded_by and uploaded_by.isdigit():
        record["Uploaded_By"] = {"id": uploaded_by}
    return {k: v for k, v in record.items() if _present(v) or v is False}


def search_criteria(
    *,
    account_name: str = "",
    lead_name: str = "",
    document_type: str = "",
    carrier: str = "",
    policy_type: str = "",
    renewal_cycle: str = "",
    line_of_business: str = "",
    status: str = "",
) -> str:
    """Zoho ``(Field:equals:value)and(...)`` criteria. Empty filters are omitted."""
    clauses: list[tuple[str, str]] = []
    party = (account_name or lead_name or "").strip()
    if party:
        clauses.append(("Account_Name", party))
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
    record_id: str = "",
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
    try:
        existing_id = (record_id or "").strip()
        if existing_id:
            result = zoho.update_record(mod, existing_id, record)
        else:
            file_id = record.get(FIELD_NEXTCLOUD_ID)
            match_field = FIELD_NEXTCLOUD_ID if file_id else FIELD_NEXTCLOUD_URL
            match_value = str(file_id or url)
            result = zoho.upsert_by_field(
                mod, record, match_field=match_field, match_value=match_value
            )
    except ZohoClientError as exc:
        raise DocumentRegistryZohoError(str(exc)) from exc
    return {**result, "module": mod, "record": record}


def search_registry(
    *,
    account_name: str = "",
    lead_name: str = "",
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
        lead_name=lead_name,
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
            "(account_name, lead_name, document_type, carrier, policy_type, "
            "renewal_cycle, line_of_business, or status)"
        )
    zoho = client or ZohoClient()
    mod = module or module_api_name()
    try:
        return zoho.search_records(mod, criteria)
    except ZohoClientError as exc:
        raise DocumentRegistryZohoError(str(exc)) from exc
