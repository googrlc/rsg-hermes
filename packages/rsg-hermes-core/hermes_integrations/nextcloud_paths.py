"""Canonical Nextcloud paths for the Zoho Document_Registry pipeline.

Client files live in **one** tree::

    Clients/{Lead or Account display name}/{category}/{file}

``NEXTCLOUD_BASE_PATH`` (live: often ``Agency Documents``) is a Team Folder
mount prefix applied by ``NextcloudClient``. It is not a second catalog and
not a second client tree. PR #357's ``Commercial Lines/{account}/{policy
type}/…`` layout is retired for registry uploads so the same PDF is not
filed twice.

Folder names come from the party display name (Lead or Account). Conversion
later fills the Account lookup; the files stay put.
"""

from __future__ import annotations

from typing import Any

from hermes_integrations.nextcloud_client import CLIENT_CATEGORIES, _sanitize_segment

CLIENT_ROOT = "Clients"


class DocumentPathError(ValueError):
    """Raised when registry metadata cannot produce a canonical path."""


# Document_Type picklist → Clients/{name}/{this}/. Unknown types fall back
# to Correspondence so a new picklist value still files somewhere real.
FOLDER_BY_DOCUMENT_TYPE = {
    "intake": "Intake",
    "application": "Intake",
    "quote": "Quotes",
    "proposal": "Proposals",
    "policy": "Policies",
    "declaration page": "Policies",
    "declaration pages": "Policies",
    "dec page": "Policies",
    "binder": "Policies",
    "endorsement": "Policies",
    "audit": "Policies",
    "invoice": "Policies",
    "certificate of insurance": "COIs",
    "coi": "COIs",
    "claim": "Claims",
    "claims": "Claims",
    "correspondence": "Correspondence",
    "other": "Correspondence",
    "renewal review": "Renewal Reviews",
    "loss run": "Renewal Reviews",
    "loss runs": "Renewal Reviews",
}


def category_for_document_type(document_type: str) -> str:
    """Map a Document_Type label to a Clients/{name} subfolder."""
    cleaned = (document_type or "").strip()
    if not cleaned:
        raise DocumentPathError("document_type is required")
    mapped = FOLDER_BY_DOCUMENT_TYPE.get(cleaned.lower())
    if mapped:
        return mapped
    # Already a known category (staff picked "Policies" instead of "Policy").
    for category in CLIENT_CATEGORIES:
        if category.lower() == cleaned.lower():
            return category
    return "Correspondence"


def party_folder_name(name: str) -> str:
    """Sanitize the Lead or Account display name used as Clients/{this}."""
    segment = _sanitize_segment(name)
    if segment == "unnamed":
        raise DocumentPathError(
            "party display name is required (lead_name or account_name) "
            "to derive Clients/{name}"
        )
    return segment


def canonical_folder(*, party_name: str, document_type: str) -> str:
    """Folder path relative to the WebDAV user root *before* base_path."""
    name = party_folder_name(party_name)
    category = category_for_document_type(document_type)
    return f"{CLIENT_ROOT}/{name}/{category}"


def canonical_filename(*, file_name: str, carrier: str = "", document_name: str = "") -> str:
    """``{Carrier} {file}`` unless the file name already starts with the carrier."""
    raw = (file_name or document_name or "").strip()
    if not raw:
        raise DocumentPathError("file_name is required")
    if "/" in raw or "\\" in raw:
        raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in raw and not raw.startswith("."):
        stem, ext = raw.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = raw, ""
    stem = _sanitize_segment(stem)
    carrier_seg = _sanitize_segment(carrier) if (carrier or "").strip() else ""
    if carrier_seg and carrier_seg != "unnamed":
        if not stem.lower().startswith(carrier_seg.lower()):
            stem = f"{carrier_seg} {stem}"
    if not stem or stem == "unnamed":
        raise DocumentPathError("file_name is required")
    return f"{stem}{ext}"


def canonical_rel_path(
    *,
    party_name: str,
    document_type: str,
    file_name: str,
    carrier: str = "",
    document_name: str = "",
) -> dict[str, Any]:
    """Return folder, filename, and full relative path for a registry upload."""
    folder = canonical_folder(party_name=party_name, document_type=document_type)
    fname = canonical_filename(
        file_name=file_name, carrier=carrier, document_name=document_name
    )
    return {
        "folder": folder,
        "file_name": fname,
        "rel_path": f"{folder}/{fname}",
        "category": category_for_document_type(document_type),
        "client_root": f"{CLIENT_ROOT}/{party_folder_name(party_name)}",
        "party_name": party_folder_name(party_name),
    }
