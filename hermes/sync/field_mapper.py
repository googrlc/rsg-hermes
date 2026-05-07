"""Field mapper: applies NowCerts → EspoCRM transforms per rsg-data-schema crosswalk."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Insured → Account mapping (from rsg-data-schema/mappings/nowcerts-to-espocrm.json)
# ---------------------------------------------------------------------------

INSURED_TYPE_MAP: dict[str, str] = {
    "Commercial": "Commercial Lines",
    "Personal": "Personal Lines",
    "Life": "Personal Lines",
    "Health": "Personal Lines",
    "Benefits": "Group Benefits",
}

INSURED_FIELD_MAP: list[dict[str, Any]] = [
    {"src": "database_id", "dst": "momentumClientId", "transform": "direct"},
    {"src": "commercialName", "dst": "name", "transform": "conditional_name"},
    {"src": "firstName", "dst": "primaryFirstName", "transform": "direct"},
    {"src": "lastName", "dst": "primaryLastName", "transform": "direct"},
    {"src": "dateOfBirth", "dst": "dateOfBirth", "transform": "date_only"},
    {"src": "coInsured_FirstName", "dst": "spouseFirstName", "transform": "direct"},
    {"src": "coInsured_LastName", "dst": "spouseLastName", "transform": "direct"},
    {"src": "coInsured_DateOfBirth", "dst": "spouseDob", "transform": "date_only"},
    {"src": "insuredType", "dst": "accountType", "transform": "enum_map", "map": INSURED_TYPE_MAP},
    {"src": "typeOfBusiness", "dst": "businessEntity", "transform": "direct"},
    {"src": "yearBusinessStarted", "dst": "cYearBusinessEst", "transform": "direct"},
    {"src": "yearsInBusiness", "dst": "yearsInBusiness", "transform": "direct"},
    {"src": "naics", "dst": "intelNaics", "transform": "direct"},
    {"src": "sicCode", "dst": "sicCode", "transform": "direct"},
    {"src": "fein", "dst": "fein", "transform": "direct"},
    {"src": "changeDate", "dst": "momentumLastSynced", "transform": "direct"},
    {"src": "createDate", "dst": "clientSince", "transform": "date_only"},
    {"src": "referralSourceCompanyName", "dst": "referralName", "transform": "direct"},
    {"src": "leadSources", "dst": "referralSource", "transform": "first_element"},
    {"src": "personNotes", "dst": "communicationNotes", "transform": "append"},
    {"src": "agentOfRecordDate", "dst": "agentOfRecordDate", "transform": "date_only"},
    # Address fields (supplemental — not in rsg-data-schema mapping but useful)
    {"src": "addressLine1", "dst": "billingAddressStreet", "transform": "direct"},
    {"src": "city", "dst": "billingAddressCity", "transform": "direct"},
    {"src": "state", "dst": "billingAddressState", "transform": "direct"},
    {"src": "zip", "dst": "billingAddressPostalCode", "transform": "direct"},
    {"src": "email", "dst": "emailAddress", "transform": "direct"},
    {"src": "cellPhone", "dst": "phoneNumber", "transform": "direct"},
]

# Dedup key for Insured → Account
INSURED_DEDUP_SOURCE = "database_id"
INSURED_DEDUP_TARGET = "momentumClientId"


def _strip_date(val: Any) -> str | None:
    """Extract date portion from a datetime string."""
    if not val:
        return None
    s = str(val).strip()
    if "T" in s:
        return s.split("T")[0]
    return s[:10] if len(s) >= 10 else s


def _first_element(val: Any) -> Any:
    """Return first element of a list, or the value itself."""
    if isinstance(val, list) and val:
        return val[0]
    return val


def _get_nested(record: dict[str, Any], key: str) -> Any:
    """Get a value from a record, supporting dotted paths and array indexing."""
    if key in record:
        return record[key]
    parts = key.split(".")
    current: Any = record
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return None
        else:
            return None
    return current


def map_insured_to_account(
    nc_record: dict[str, Any],
    *,
    existing_espo: dict[str, Any] | None = None,
    is_first_sync: bool = False,
) -> dict[str, Any]:
    """Transform a NowCerts Insured record into an EspoCRM Account payload.

    Args:
        nc_record: Raw NowCerts insured dict.
        existing_espo: Current EspoCRM Account data (for append transforms).
        is_first_sync: If True, includes clientSince field.

    Returns:
        Dict ready for EspoCRM Account create/update.
    """
    result: dict[str, Any] = {}

    for mapping in INSURED_FIELD_MAP:
        src_key = mapping["src"]
        dst_key = mapping["dst"]
        transform = mapping["transform"]
        raw_val = _get_nested(nc_record, src_key)

        if raw_val is None and transform != "conditional_name":
            continue

        if transform == "direct":
            result[dst_key] = raw_val

        elif transform == "date_only":
            stripped = _strip_date(raw_val)
            if stripped:
                result[dst_key] = stripped

        elif transform == "enum_map":
            enum_map = mapping.get("map", {})
            mapped = enum_map.get(str(raw_val), raw_val)
            if mapped:
                result[dst_key] = mapped

        elif transform == "first_element":
            val = _first_element(raw_val)
            if val:
                result[dst_key] = val

        elif transform == "conditional_name":
            insured_type = nc_record.get("insuredType", "")
            if insured_type == "Commercial":
                name = nc_record.get("commercialName", "")
            else:
                first = nc_record.get("firstName", "")
                last = nc_record.get("lastName", "")
                name = f"{first} {last}".strip()
            if name:
                result[dst_key] = name

        elif transform == "append":
            if raw_val:
                existing_val = ""
                if existing_espo:
                    existing_val = str(existing_espo.get(dst_key, "") or "")
                new_val = str(raw_val)
                if existing_val and new_val not in existing_val:
                    result[dst_key] = f"{existing_val}\n---\n{new_val}"
                else:
                    result[dst_key] = new_val

    # Only include clientSince on first sync
    if not is_first_sync:
        result.pop("clientSince", None)

    # accountType is required on create — ensure it's set
    if "accountType" not in result:
        result["accountType"] = "Commercial Lines"

    return result


def detect_conflicts(
    source_payload: dict[str, Any],
    existing_espo: dict[str, Any],
    *,
    ignore_fields: set[str] | None = None,
) -> list[dict[str, str]]:
    """Compare mapped source fields against existing EspoCRM data.

    Returns a list of field-level conflicts where source != destination
    and neither value is empty/null.
    """
    skip = ignore_fields or {
        "momentumLastSynced",
        "communicationNotes",
        "clientSince",
    }
    conflicts: list[dict[str, str]] = []

    for field, source_val in source_payload.items():
        if field in skip:
            continue
        dest_val = existing_espo.get(field)
        if dest_val is None or source_val is None:
            continue
        if str(source_val).strip() != str(dest_val).strip():
            conflicts.append({
                "field_name": field,
                "source_value": str(source_val),
                "dest_value": str(dest_val),
            })

    return conflicts


def payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 hash of a JSON-serialized payload for dedup/change detection."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
