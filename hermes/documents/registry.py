"""Document Registry pipeline — Nextcloud first, Zoho metadata second.

Staff drop a file in Document Registry (or POST this API). Hermes:

1. Resolves party as Lead XOR Account (exactly one).
2. Finds or creates ``Clients/{display name}/`` (same tree for leads).
3. PUTs the file into the metadata subfolder (Intake, Policies, Quotes, …).
4. Stamps the ``/f/{fileid}`` permalink on Zoho Document_Registry only when
   that URL exists (golden rule). Zoho Attachments are never the library.

Upload is the entry point. An existing ``Nextcloud_Folder_Link`` is not
required; creating the folder is a side effect of the upload.
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
from hermes_integrations.zoho_client import ZohoClient, ZohoClientError
from hermes_integrations.zoho_document_registry import (
    DEFAULT_MODULE,
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


def resolve_party(
    *,
    lead_id: str = "",
    lead_name: str = "",
    account_id: str = "",
    account_name: str = "",
) -> dict[str, str]:
    """Party is Lead OR Account (exactly one). Folder name = display name."""
    lead_id = (lead_id or "").strip()
    lead_name = (lead_name or "").strip()
    account_id = (account_id or "").strip()
    account_name = (account_name or "").strip()
    has_lead = bool(lead_id or lead_name)
    has_account = bool(account_id or account_name)
    if has_lead and has_account:
        raise DocumentRegistryError(
            "party is Lead OR Account (exactly one) — do not send both"
        )
    if not has_lead and not has_account:
        raise DocumentRegistryError(
            "party is required: lead_id/lead_name or account_id/account_name"
        )
    if has_lead:
        if not lead_name:
            raise DocumentRegistryError(
                "lead_name is required to name Clients/{name} (lead_id alone is not enough)"
            )
        return {
            "kind": "lead",
            "lead_id": lead_id,
            "lead_name": lead_name,
            "account_id": "",
            "account_name": "",
            "party_name": lead_name,
        }
    if not account_name:
        raise DocumentRegistryError(
            "account_name is required to name Clients/{name} (account_id alone is not enough)"
        )
    return {
        "kind": "account",
        "lead_id": "",
        "lead_name": "",
        "account_id": account_id,
        "account_name": account_name,
        "party_name": account_name,
    }


def _ensure_party_folder(nc: NextcloudClient, party_name: str, category: str) -> dict[str, Any]:
    """Find-or-create Clients/{name}/ then the category subfolder.

    Missing client folder → ``ensure_client_folders`` (full standard tree).
    Existing client folder → reuse it; only MKCOL the target category.
    """
    from hermes_integrations.nextcloud_client import _sanitize_segment

    client_rel = f"Clients/{_sanitize_segment(party_name)}"
    existed = nc.path_exists(client_rel)
    if existed:
        nc.ensure_dirs(f"{client_rel}/{category}")
        created = False
    else:
        nc.ensure_client_folders(party_name)
        created = True
    return {
        "client_rel": client_rel,
        "folder_existed": existed,
        "folder_created": created,
        "stored_client_path": nc._rel_with_base(client_rel),
    }


def register_document(
    *,
    content: bytes,
    file_name: str,
    document_type: str,
    policy_type: str,
    line_of_business: str,
    renewal_cycle: str,
    lead_id: str = "",
    lead_name: str = "",
    account_id: str = "",
    account_name: str = "",
    carrier: str = "",
    document_name: str = "",
    content_type: str = "application/octet-stream",
    effective_date: str = "",
    expiration_date: str = "",
    policy_id: str = "",
    deal_id: str = "",
    renewal_id: str = "",
    uploaded_by: str = "",
    status: str = "Active",
    write_to_zoho: bool | None = None,
    zoho_record_id: str = "",
    nc: NextcloudClient | None = None,
    zoho_upsert=upsert_registry_record,
) -> dict[str, Any]:
    """Upload to Nextcloud, then optionally upsert Zoho Document_Registry.

    The Zoho write is skipped (never attempted) when the PUT fails or the
    receipt has no ``/f/{fileid}`` URL. A failed CRM write does not roll back
    the file.
    """
    if not content:
        raise DocumentRegistryError("file content is required")
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentRegistryError("file exceeds 25 MiB")

    party = resolve_party(
        lead_id=lead_id,
        lead_name=lead_name,
        account_id=account_id,
        account_name=account_name,
    )

    client = nc or NextcloudClient()
    if not client.is_configured():
        raise DocumentRegistryError(
            "Nextcloud is not configured — set NEXTCLOUD_URL, NEXTCLOUD_USER, "
            "and NEXTCLOUD_APP_PASSWORD"
        )

    try:
        planned = canonical_rel_path(
            party_name=party["party_name"],
            document_type=document_type,
            file_name=file_name,
            carrier=carrier,
            document_name=document_name,
        )
    except DocumentPathError as exc:
        raise DocumentRegistryError(str(exc)) from exc

    try:
        folder_info = _ensure_party_folder(
            client, party["party_name"], planned["category"]
        )
    except NextcloudError as exc:
        raise DocumentRegistryError(f"Nextcloud folder find-or-create failed: {exc}") from exc

    try:
        receipt = client.put_file_receipt(
            planned["rel_path"],
            content,
            content_type=content_type,
            auto_mkcol=True,
        )
    except NextcloudError as exc:
        raise DocumentRegistryError(f"Nextcloud PUT failed: {exc}") from exc

    url = (receipt.get("files_url") or "").strip()
    meta = {
        "party_name": party["party_name"],
        "account_name": party["account_name"],
        "account_id": party["account_id"],
        "lead_name": party["lead_name"],
        "lead_id": party["lead_id"],
        "policy_id": policy_id,
        "deal_id": deal_id,
        "renewal_id": renewal_id,
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
        "party": party,
        "path": planned,
        "folder": folder_info,
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
        crm = zoho_upsert(meta, receipt, record_id=zoho_record_id)
    except TypeError:
        # Tests may inject a simple MagicMock that does not accept record_id.
        crm = zoho_upsert(meta, receipt)
    except (DocumentRegistryZohoError, ZohoClientError) as exc:
        log.warning("document-registry: Nextcloud OK, Zoho write failed: %s", exc)
        result["ok"] = False
        result["crm_skipped"] = f"Zoho write failed after Nextcloud PUT: {exc}"
        result["crm_payload"] = to_zoho_record(meta, receipt)
        return result

    result["crm"] = crm
    return result


def file_zoho_attachment(
    record_id: str,
    *,
    module: str = DEFAULT_MODULE,
    write_to_zoho: bool | None = True,
    nc: NextcloudClient | None = None,
    zoho: ZohoClient | None = None,
    zoho_upsert=upsert_registry_record,
) -> dict[str, Any]:
    """Drain a temp Zoho attachment into Nextcloud, stamp the permalink, delete the attachment.

    CRM-native drop zone: staff create a Document_Registry row with metadata +
    an Attachment. Hermes files the bytes. The PDF is never kept as the library.
    """
    rid = (record_id or "").strip()
    if not rid:
        raise DocumentRegistryError("record_id is required")
    client = zoho or ZohoClient()
    try:
        record = client.get_record(module, rid)
    except ZohoClientError as exc:
        raise DocumentRegistryError(f"Zoho record read failed: {exc}") from exc

    existing_url = str(record.get("Nextcloud_File_URL") or "").strip()
    if existing_url:
        return {
            "ok": True,
            "crm_skipped": "already filed — Nextcloud_File_URL is set",
            "record_id": rid,
        }

    try:
        attachments = client.list_attachments(module, rid)
    except ZohoClientError as exc:
        raise DocumentRegistryError(f"Zoho attachment list failed: {exc}") from exc
    if not attachments:
        raise DocumentRegistryError(
            "no Zoho attachment to file — drop the PDF on Document Registry "
            "or POST /api/document-registry/upload"
        )
    att = attachments[0]
    att_id = str(att.get("id") or "").strip()
    file_name = str(att.get("File_Name") or att.get("file_name") or "upload.bin").strip()
    if not att_id:
        raise DocumentRegistryError("Zoho attachment is missing an id")
    try:
        content = client.download_attachment(module, rid, att_id)
    except ZohoClientError as exc:
        raise DocumentRegistryError(f"Zoho attachment download failed: {exc}") from exc

    account = record.get("Account") if isinstance(record.get("Account"), dict) else {}
    lead = record.get("Lead") if isinstance(record.get("Lead"), dict) else {}
    policy = record.get("Policy") if isinstance(record.get("Policy"), dict) else {}
    deal = record.get("Deal") if isinstance(record.get("Deal"), dict) else {}
    renewal = record.get("Renewal") if isinstance(record.get("Renewal"), dict) else {}
    derived_name = str(record.get("Account_Name") or "").strip()
    lead_id = str(lead.get("id") or "").strip()
    account_id = str(account.get("id") or "").strip()
    # Account_Name is derived for both parties — only send it on the matching side.
    if lead_id or (not account_id and lead.get("name")):
        party_lead_id, party_lead_name = lead_id, str(lead.get("name") or derived_name)
        party_account_id, party_account_name = "", ""
    else:
        party_lead_id, party_lead_name = "", ""
        party_account_id, party_account_name = account_id, str(account.get("name") or derived_name)

    try:
        result = register_document(
            content=content,
            file_name=file_name,
            document_type=str(record.get("Document_Type") or "Correspondence"),
            policy_type=str(record.get("Policy_Type") or "Other"),
            line_of_business=str(record.get("Line_of_Business") or "Commercial Lines"),
            renewal_cycle=str(record.get("Renewal_Cycle") or ""),
            lead_id=party_lead_id,
            lead_name=party_lead_name,
            account_id=party_account_id,
            account_name=party_account_name,
            carrier=str(record.get("Carrier") or ""),
            document_name=str(record.get("Name") or ""),
            content_type=str(record.get("MIME_Type") or "application/octet-stream"),
            effective_date=str(record.get("Effective_Date") or ""),
            expiration_date=str(record.get("Expiration_Date") or ""),
            policy_id=str(policy.get("id") or ""),
            deal_id=str(deal.get("id") or ""),
            renewal_id=str(renewal.get("id") or ""),
            status="Active",
            write_to_zoho=write_to_zoho,
            zoho_record_id=rid,
            nc=nc,
            zoho_upsert=zoho_upsert,
        )
    except DocumentRegistryError:
        raise

    try:
        client.delete_attachment(module, rid, att_id)
        result["attachment_deleted"] = att_id
    except ZohoClientError as exc:
        log.warning("document-registry: filed OK but could not delete Zoho attachment: %s", exc)
        result["attachment_deleted"] = None
        result["attachment_delete_error"] = str(exc)
    result["record_id"] = rid
    return result


def search_documents(**filters: Any) -> list[dict[str, Any]]:
    """Proxy Zoho Document_Registry search."""
    try:
        return zoho_search_registry(**filters)
    except DocumentRegistryZohoError as exc:
        raise DocumentRegistryError(str(exc)) from exc
