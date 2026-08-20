"""Zoho document-link field spec: Nextcloud holds the file; Zoho stores https URLs.

Used by ``scripts/ensure_zoho_document_url_fields.py`` to create URL fields and
by Hermes writers to stamp clickable Files-app links (not WebDAV, not relative
paths).
"""

from __future__ import annotations

from typing import Any

# Zoho website/URL fields cap at 450 characters.
# Tooltip static_text max is 32 (live Settings API rejected 35).
URL_FIELD_LENGTH = 450
TOOLTIP_MAX_LENGTH = 32
DOCUMENTS_SECTION = "Documents"

# module API name -> fields to create. Accounts gets the client folder;
# every file-bearing record gets Primary Folder URL + Document URL.
DOCUMENT_URL_FIELDS: dict[str, tuple[dict[str, str], ...]] = {
    "Accounts": (
        {
            "field_label": "Nextcloud Folder URL",
            "api_name": "Nextcloud_Folder_URL",
            "tooltip": "Client master folder in Nextcloud. The files live there, not in Zoho.",
        },
    ),
    "Policies": (
        {
            "field_label": "Primary Folder URL",
            "api_name": "Primary_Folder_URL",
            "tooltip": "Nextcloud Policies folder for this client.",
        },
        {
            "field_label": "Document URL",
            "api_name": "Document_URL",
            "tooltip": "The policy PDF in Nextcloud. Click to open; do not attach a copy.",
        },
    ),
    "Deals": (
        {
            "field_label": "Primary Folder URL",
            "api_name": "Primary_Folder_URL",
            "tooltip": "Nextcloud Quotes folder for this client.",
        },
        {
            "field_label": "Document URL",
            "api_name": "Document_URL",
            "tooltip": "The quote or proposal PDF in Nextcloud.",
        },
    ),
    "Renewals": (
        {
            "field_label": "Primary Folder URL",
            "api_name": "Primary_Folder_URL",
            "tooltip": "Nextcloud Renewal Reviews folder for this client.",
        },
        {
            "field_label": "Document URL",
            "api_name": "Document_URL",
            "tooltip": "The renewal worksheet PDF in Nextcloud.",
        },
    ),
    "Claims": (
        {
            "field_label": "Primary Folder URL",
            "api_name": "Primary_Folder_URL",
            "tooltip": "Nextcloud Claims folder for this client.",
        },
        {
            "field_label": "Document URL",
            "api_name": "Document_URL",
            "tooltip": "A claim PDF in Nextcloud.",
        },
    ),
    "Certificates": (
        {
            "field_label": "Primary Folder URL",
            "api_name": "Primary_Folder_URL",
            "tooltip": "Nextcloud COIs folder for this client.",
        },
        {
            "field_label": "Document URL",
            "api_name": "Document_URL",
            "tooltip": "The filed certificate PDF in Nextcloud. Issued in NowCerts.",
        },
    ),
}


def normalize_api_name(name: str | None) -> str:
    """Strip Zoho org suffixes so Nextcloud_Folder_URL__s matches Nextcloud_Folder_URL."""
    raw = str(name or "").strip()
    for suffix in ("__s", "__c"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def existing_field_index(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index Zoho field metadata by normalized API name and by label."""
    index: dict[str, dict[str, Any]] = {}
    for field in fields:
        api = str(field.get("api_name") or "")
        label = str(field.get("field_label") or field.get("display_label") or "")
        if api:
            index[normalize_api_name(api)] = field
            index[api] = field
        if label:
            index[label.strip().lower()] = field
    return index


def missing_document_fields(
    module: str, fields: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Return spec rows that are not already on the module."""
    wanted = DOCUMENT_URL_FIELDS.get(module) or ()
    index = existing_field_index(fields)
    missing: list[dict[str, str]] = []
    for spec in wanted:
        api = spec["api_name"]
        label = spec["field_label"].strip().lower()
        if api in index or normalize_api_name(api) in index or label in index:
            continue
        missing.append(spec)
    return missing


def website_create_payload(spec: dict[str, str]) -> dict[str, Any]:
    """POST /settings/fields body for one URL/website field."""
    payload: dict[str, Any] = {
        "field_label": spec["field_label"],
        "data_type": "website",
        "length": URL_FIELD_LENGTH,
    }
    tooltip = spec.get("tooltip")
    if tooltip:
        payload["tooltip"] = {"name": "static_text", "value": tooltip[:TOOLTIP_MAX_LENGTH]}
    return payload


def is_http_url(value: Any) -> bool:
    text = str(value or "").strip()
    return text.lower().startswith(("http://", "https://"))
