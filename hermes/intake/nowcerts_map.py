"""Map an intake account into a NowCerts Insured (prospect) payload.

The read side of NowCerts returns camelCase (commercialName, prospectType,
insuredType, …); the Insert endpoints take PascalCase (CommercialName, FirstName,
FEIN, AddressLine1, … per NowCertsClient.create_insured's docstring). This module
produces the PascalCase Insert payload.

⚠️  WRITE-FIELD CASING IS UNVERIFIED for a few fields — notably ProspectType and
InsuredType. NowCerts silently drops unknown insert fields (same failure mode as
EspoCRM casing). Before the FIRST live insert, run one dry-run insert and confirm
these keys stick; do not trust this mapping blind. Nothing here writes — it only
builds the payload for the approval-gated intake executor.
"""

from __future__ import annotations

from typing import Any

from hermes.intake.opportunities import INSURED_TYPES, PROSPECT_TYPES

DEFAULT_PROSPECT_TYPE = "Prospect"


def _first(account: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = account.get(k)
        if v not in (None, ""):
            return v
    return None


def normalize_insured_type(value: Any) -> str | None:
    """Map a free segment label to Personal | Commercial, else None."""
    s = str(value or "").strip().lower()
    if not s:
        return None
    if s in ("personal", "personal lines", "pl", "home", "auto"):
        return "Personal"
    if s in ("commercial", "commercial lines", "cl", "business", "group benefits", "benefits", "medicare", "life"):
        return "Commercial"
    if value in INSURED_TYPES:
        return value  # already canonical
    return None


def normalize_prospect_type(value: Any) -> str:
    """Map a lead temperature to a NowCerts prospect_type; default 'Prospect'."""
    s = str(value or "").strip().lower().replace(" ", "_")
    mapping = {
        "hot": "Hot_Prospect", "hot_prospect": "Hot_Prospect", "warm": "Hot_Prospect",
        "cold": "Cold_Prospect", "cold_prospect": "Cold_Prospect",
        "prospect": "Prospect",
    }
    result = mapping.get(s, DEFAULT_PROSPECT_TYPE)
    return result if result in PROSPECT_TYPES else DEFAULT_PROSPECT_TYPE


def map_to_insured(
    account: dict[str, Any],
    *,
    insured_type: str | None = None,
    prospect_type: str = DEFAULT_PROSPECT_TYPE,
) -> dict[str, Any]:
    """Build a NowCerts Insured Insert payload (prospect) from an intake account.

    ``insured_type`` (Personal|Commercial) decides CommercialName vs First/Last.
    """
    itype = normalize_insured_type(insured_type) or normalize_insured_type(
        _first(account, "insured_type", "segment", "type")
    )
    ptype = normalize_prospect_type(prospect_type)

    name = _first(account, "commercial_name", "account_name", "name")
    first = _first(account, "first_name", "firstName", "primary_first_name")
    last = _first(account, "last_name", "lastName", "primary_last_name")

    payload: dict[str, Any] = {
        "FEIN": _first(account, "fein", "ein", "FEIN"),
        "AddressLine1": _first(account, "address_line1", "address", "street", "AddressLine1"),
        "City": _first(account, "city", "City"),
        "State": _first(account, "state", "State"),
        "ZipCode": _first(account, "zip", "zip_code", "postal_code", "ZipCode"),
        "EMail": _first(account, "email", "eMail", "email_address"),
        "CellPhone": _first(account, "phone", "cell_phone", "mobile", "phoneNumber"),
        # ⚠️ verify these two keys stick on a dry-run insert before going live.
        "ProspectType": ptype,
        "InsuredType": itype,
    }

    # Commercial => CommercialName; Personal => First/Last (fall back sensibly).
    if itype == "Personal" and (first or last):
        payload["FirstName"] = first
        payload["LastName"] = last
    else:
        payload["CommercialName"] = name or (f"{first or ''} {last or ''}".strip() or None)
        if first:
            payload["FirstName"] = first
        if last:
            payload["LastName"] = last

    # Drop empties so we never send blank keys that could clobber existing data.
    return {k: v for k, v in payload.items() if v not in (None, "")}
