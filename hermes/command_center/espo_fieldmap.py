"""EspoCRM Account field map — the authoritative write contract.

Source of truth: rsg-espocrm/field-reference (live `/Metadata` pull, 2026-06-06).
This is what makes a CRM write actually land instead of 400-ing.

Two things that break naive writes:

1. **Casing is mixed.** Core Account fields are camelCase; RSG custom fields are
   snake_case. (And ``sicCode`` core vs ``sic_code`` custom are *different* fields.)
2. **Enums are strict.** ``businessEntity`` only accepts a fixed set — sending a
   NowCerts ``insuredType`` ("Personal") or a freeform string is exactly the 400
   ``validationFailure`` that was failing the old sync. Map or send empty.

Auth (also from the field reference):
  - GET            -> header ``X-Api-Key: {key}``  (no Content-Type)
  - POST/PATCH/DEL -> header ``Authorization: Basic base64("{key}:")`` + JSON
"""
from __future__ import annotations

import base64
from typing import Any, Optional

API_BASE = "https://rrespocrm-rsg-u69864.vm.elestio.app/api/v1"

# ---- enum option sets (reject anything not in here) ----------------------

BUSINESS_ENTITY_OPTIONS = frozenset({
    "Sole Proprietor", "LLC", "Corporation", "S-Corp",
    "Partnership", "Non-Profit", "Other",
})

ACCOUNT_TYPE_OPTIONS = frozenset({
    "Prospect", "Commercial Lines", "Personal Lines", "Group Benefits",
    "Medicare", "Life Insurance", "Carrier", "MGA",
})

# our SubmissionObject.EntityType / canonical business_type -> Espo businessEntity
ENTITY_TYPE_TO_BUSINESS_ENTITY = {
    "individual": None,            # a person is not a business entity -> leave blank
    "llc": "LLC",
    "corporation": "Corporation",
    "corp": "Corporation",
    "c-corp": "Corporation",
    "s_corp": "S-Corp",
    "s-corp": "S-Corp",
    "scorp": "S-Corp",
    "partnership": "Partnership",
    "joint_venture": "Partnership",
    "not_for_profit": "Non-Profit",
    "non-profit": "Non-Profit",
    "nonprofit": "Non-Profit",
    "trust": "Other",
    "sole proprietor": "Sole Proprietor",
    "sole_proprietor": "Sole Proprietor",
}

# NowCerts insuredType / our client_type -> Espo account_type
INSURED_TYPE_TO_ACCOUNT_TYPE = {
    "commercial": "Commercial Lines",
    "commercial lines": "Commercial Lines",
    "personal": "Personal Lines",
    "personal lines": "Personal Lines",
    "life": "Personal Lines",
    "health": "Personal Lines",
    "benefits": "Group Benefits",
    "group benefits": "Group Benefits",
    "medicare": "Medicare",
    "prospect": "Prospect",
}

BILLING_TYPE_MAP = {
    "Direct_Bill_100": "Direct Bill 100",
    "Agency_Bill_100": "Agency Bill 100",
    "Direct_Bill": "Direct Bill",
    "Agency_Bill": "Agency Bill",
}


def map_business_entity(value: Optional[str]) -> Optional[str]:
    """Coerce any entity-type string to a valid Espo ``businessEntity`` option,
    or None (which means: omit the field). Never returns an invalid option."""
    if not value:
        return None
    v = str(value).strip()
    if v in BUSINESS_ENTITY_OPTIONS:
        return v
    return ENTITY_TYPE_TO_BUSINESS_ENTITY.get(v.lower())


def map_account_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = str(value).strip()
    if v in ACCOUNT_TYPE_OPTIONS:
        return v
    return INSURED_TYPE_TO_ACCOUNT_TYPE.get(v.lower())


def basic_auth_header(api_key: str) -> dict[str, str]:
    """Header for POST/PATCH/DELETE (the write path)."""
    token = base64.b64encode(f"{api_key}:".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def api_key_header(api_key: str) -> dict[str, str]:
    """Header for GET (the read path)."""
    return {"X-Api-Key": api_key}


# logical field -> Espo Account API attribute (correct casing baked in)
ACCOUNT_FIELD = {
    "name": "name",                               # core, camel
    "email": "emailAddress",                      # core, camel
    "phone": "phoneNumber",                       # core, camel
    "street": "billingAddressStreet",             # core, camel
    "city": "billingAddressCity",
    "state": "billingAddressState",
    "zip": "billingAddressPostalCode",
    "business_entity": "businessEntity",          # core enum, camel
    "first_name": "primaryFirstName",             # custom, camel
    "last_name": "primaryLastName",
    "fein": "fein",                               # custom
    "account_type": "account_type",               # custom, snake
    "sic": "sic_code",                            # custom, snake (NOT sicCode)
    "xdate": "x_date",                            # custom, snake  <- the XDATE field
    "next_xdate": "next_x_date",
    "total_premium": "total_active_premium",      # custom, snake
    "momentum_client_id": "momentum_client_id",   # custom, snake (NowCerts insured GUID)
}


def account_write_payload(canonical: dict[str, Any]) -> dict[str, Any]:
    """Build a valid EspoCRM Account write body from our canonical client fields.

    - maps logical names -> correct Espo attribute (right casing)
    - coerces ``business_entity``/``account_type`` to valid enum options (or drops)
    - omits ``None``/empty values (never clobbers Espo with blanks)
    """
    body: dict[str, Any] = {}

    def put(logical: str, value: Any) -> None:
        if value in (None, ""):
            return
        body[ACCOUNT_FIELD[logical]] = value

    put("name", canonical.get("name") or canonical.get("insured_name"))
    put("first_name", canonical.get("first_name"))
    put("last_name", canonical.get("last_name"))
    put("email", canonical.get("email"))
    put("phone", canonical.get("phone"))
    put("street", canonical.get("street") or canonical.get("address_line1"))
    put("city", canonical.get("city"))
    put("state", canonical.get("state"))
    put("zip", canonical.get("zip"))
    put("fein", canonical.get("fein"))
    put("sic", canonical.get("sic"))
    put("xdate", canonical.get("xdate") or canonical.get("current_policy_expiration"))
    put("total_premium", canonical.get("total_premium"))
    put("momentum_client_id", canonical.get("momentum_client_id") or canonical.get("nowcerts_id"))

    # Try the explicit business_entity, but if it's present-yet-invalid fall back
    # to entity_type rather than dropping the signal entirely.
    be = map_business_entity(canonical.get("business_entity")) or map_business_entity(canonical.get("entity_type"))
    if be:
        body[ACCOUNT_FIELD["business_entity"]] = be
    at = map_account_type(canonical.get("account_type")) or map_account_type(canonical.get("client_type"))
    if at:
        body[ACCOUNT_FIELD["account_type"]] = at

    return body
