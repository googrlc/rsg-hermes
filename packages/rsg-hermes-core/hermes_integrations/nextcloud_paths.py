"""Canonical Nextcloud paths for the Zoho Document_Registry pipeline.

Paths are derived from metadata, never typed. The Team Folder prefix
(``Agency Documents``) is added unless ``NEXTCLOUD_BASE_PATH`` already is
that folder, so files land in the shared mount even before the Hermes env
cutover.

Example::

    Commercial Lines/ABC Roofing/General Liability/Declaration Pages/2027/
        Travelers GL Dec Page.pdf
"""

from __future__ import annotations

from typing import Any

from hermes_integrations.nextcloud_client import (
    AGENCY_LINE_ROOTS,
    TEAM_FOLDER_NAME,
    _sanitize_segment,
)

LINE_ALIASES = {
    "commercial": "commercial",
    "commercial lines": "commercial",
    "cl": "commercial",
    "personal": "personal",
    "personal lines": "personal",
    "pl": "personal",
    "claims": "claims",
    "claim": "claims",
}

# Keep irregular plurals readable in the folder tree.
_DOCUMENT_TYPE_PLURALS = {
    "declaration page": "Declaration Pages",
    "dec page": "Declaration Pages",
    "loss run": "Loss Runs",
    "policy": "Policies",
    "certificate of insurance": "Certificates of Insurance",
    "coi": "COIs",
}


class DocumentPathError(ValueError):
    """Raised when registry metadata cannot produce a canonical path."""


def line_root(line_of_business: str) -> str:
    """Map a line label to the Team Folder lane (``Commercial Lines``, …)."""
    key = (line_of_business or "").strip().lower()
    mapped = LINE_ALIASES.get(key)
    if mapped is None:
        raise DocumentPathError(
            f"unknown line of business {line_of_business!r} — expected "
            "Commercial Lines, Personal Lines, or Claims"
        )
    return AGENCY_LINE_ROOTS[mapped]


def line_key(line_of_business: str) -> str:
    """Return the ``AGENCY_LINE_ROOTS`` key (commercial | personal | claims)."""
    key = (line_of_business or "").strip().lower()
    mapped = LINE_ALIASES.get(key)
    if mapped is None:
        raise DocumentPathError(
            f"unknown line of business {line_of_business!r} — expected "
            "Commercial Lines, Personal Lines, or Claims"
        )
    return mapped


def pluralize_document_type(name: str) -> str:
    """Folder name for a document type: ``Declaration Page`` → ``Declaration Pages``.

    Already-plural labels (ending in ``s``) are left alone so callers can pass
    either ``Declaration Page`` or ``Declaration Pages``.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise DocumentPathError("document_type is required")
    special = _DOCUMENT_TYPE_PLURALS.get(cleaned.lower())
    if special:
        return special
    if cleaned.endswith(("s", "S")):
        return cleaned
    if cleaned.endswith("y") and not cleaned.endswith(("ay", "ey", "oy", "uy")):
        return cleaned[:-1] + "ies"
    return cleaned + "s"


def with_team_folder(relative: str, *, base_path: str = "") -> str:
    """Prefix ``Agency Documents/`` unless ``base_path`` already is that folder.

    Also a no-op when ``relative`` already starts with the Team Folder name,
    so we never create a personal ``Agency Documents/Agency Documents/`` path.
    """
    rel = (relative or "").strip().strip("/")
    if not rel:
        raise DocumentPathError("path is empty")
    if rel == TEAM_FOLDER_NAME or rel.startswith(f"{TEAM_FOLDER_NAME}/"):
        return rel
    bp = (base_path or "").strip().strip("/")
    if bp == TEAM_FOLDER_NAME:
        return rel
    return f"{TEAM_FOLDER_NAME}/{rel}"


def canonical_folder(
    *,
    line_of_business: str,
    account: str,
    policy_type: str,
    document_type: str,
    renewal_cycle: str,
    base_path: str = "",
) -> str:
    """Folder path (no filename) relative to the WebDAV user root *before*
    ``NEXTCLOUD_BASE_PATH`` is applied by the client — except we inject the
    Team Folder name when ``base_path`` is empty.
    """
    account_seg = _sanitize_segment(account)
    if account_seg == "unnamed":
        raise DocumentPathError("account_name is required")
    policy_seg = _sanitize_segment(policy_type)
    if policy_seg == "unnamed":
        raise DocumentPathError("policy_type is required")
    cycle_seg = _sanitize_segment(renewal_cycle)
    if cycle_seg == "unnamed":
        raise DocumentPathError("renewal_cycle is required")
    folder = "/".join(
        [
            line_root(line_of_business),
            account_seg,
            policy_seg,
            pluralize_document_type(document_type),
            cycle_seg,
        ]
    )
    return with_team_folder(folder, base_path=base_path)


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
    line_of_business: str,
    account: str,
    policy_type: str,
    document_type: str,
    renewal_cycle: str,
    file_name: str,
    carrier: str = "",
    document_name: str = "",
    base_path: str = "",
) -> dict[str, Any]:
    """Return folder, filename, and full relative path for a registry upload."""
    folder = canonical_folder(
        line_of_business=line_of_business,
        account=account,
        policy_type=policy_type,
        document_type=document_type,
        renewal_cycle=renewal_cycle,
        base_path=base_path,
    )
    fname = canonical_filename(
        file_name=file_name, carrier=carrier, document_name=document_name
    )
    return {
        "folder": folder,
        "file_name": fname,
        "rel_path": f"{folder}/{fname}",
        "document_type_folder": pluralize_document_type(document_type),
        "line_root": line_root(line_of_business),
    }
