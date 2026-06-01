"""Field mapper: applies NowCerts → EspoCRM transforms per rsg-data-schema crosswalk."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
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
    {"src": "id", "dst": "momentum_client_id", "transform": "direct"},
    {"src": "commercialName", "dst": "name", "transform": "conditional_name"},
    {"src": "firstName", "dst": "primaryFirstName", "transform": "direct"},
    {"src": "lastName", "dst": "primaryLastName", "transform": "direct"},
    {"src": "dateOfBirth", "dst": "dateOfBirth", "transform": "date_only"},
    {"src": "coInsured_FirstName", "dst": "spouseFirstName", "transform": "direct"},
    {"src": "coInsured_LastName", "dst": "spouseLastName", "transform": "direct"},
    {"src": "coInsured_DateOfBirth", "dst": "spouseDob", "transform": "date_only"},
    {"src": "insuredType", "dst": "account_type", "transform": "enum_map", "map": INSURED_TYPE_MAP},
    {"src": "typeOfBusiness", "dst": "businessEntity", "transform": "direct"},
    {"src": "yearBusinessStarted", "dst": "cYearBusinessEst", "transform": "direct"},
    {"src": "yearsInBusiness", "dst": "years_in_business", "transform": "direct"},
    {"src": "naics", "dst": "intel_naics", "transform": "direct"},
    {"src": "sicCode", "dst": "sicCode", "transform": "direct"},
    {"src": "fein", "dst": "fein", "transform": "direct"},
    {"src": "changeDate", "dst": "momentum_last_synced", "transform": "direct"},
    {"src": "createDate", "dst": "client_since", "transform": "date_only"},
    {"src": "referralSourceCompanyName", "dst": "referral_name", "transform": "direct"},
    {"src": "leadSources", "dst": "referral_source", "transform": "first_element"},
    {"src": "personNotes", "dst": "communication_notes", "transform": "append"},
    {"src": "agentOfRecordDate", "dst": "agent_of_record_date", "transform": "date_only"},
    # Address fields (supplemental — not in rsg-data-schema mapping but useful)
    {"src": "addressLine1", "dst": "billingAddressStreet", "transform": "direct"},
    {"src": "city", "dst": "billingAddressCity", "transform": "direct"},
    {"src": "state", "dst": "billingAddressState", "transform": "direct"},
    {"src": "zipCode", "dst": "billingAddressPostalCode", "transform": "direct"},
    {"src": "eMail", "dst": "emailAddress", "transform": "direct"},
    {"src": "cellPhone", "dst": "phoneNumber", "transform": "phone_e164"},
]

# Dedup key for Insured → Account
INSURED_DEDUP_SOURCE = "id"
INSURED_DEDUP_TARGET = "momentum_client_id"


def _strip_date(val: Any) -> str | None:
    """Extract date portion from a datetime string."""
    if not val:
        return None
    s = str(val).strip()
    if "T" in s:
        return s.split("T")[0]
    return s[:10] if len(s) >= 10 else s


def _to_e164_us(raw: Any) -> str | None:
    """Normalize a US phone to E.164 (`+1XXXXXXXXXX`).

    EspoCRM's phoneNumber validator rejects every non-E.164 form: NowCerts
    sends `678-230-5750` style raw strings, which produce
    `validationFailure {field: phoneNumber, type: valid}` on PUT/POST and
    fail the entire Account/Contact update. We therefore normalize US 10/11-
    digit numbers to `+1XXXXXXXXXX`, pass through values that are already
    valid E.164 (e.g. international `+44…`), and return None for anything we
    cannot normalize — omitting the field is safe (the raw value stays in
    NowCerts + inbound staging for review), whereas sending a non-E.164
    string fails the entire account/contact write.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+1{digits}"
    # Already-valid E.164 (e.g. international): canonicalize to +<digits>.
    if s.startswith("+") and 7 <= len(digits) <= 15:
        return f"+{digits}"
    # Un-normalizable → omit rather than send a value EspoCRM will reject.
    return None


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
        is_first_sync: If True, includes client_since field.

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

        elif transform == "phone_e164":
            val = _to_e164_us(raw_val)
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

    # Only include client_since on first sync
    if not is_first_sync:
        result.pop("client_since", None)

    # account_type is required on create — ensure it's set
    if "account_type" not in result:
        result["account_type"] = "Commercial Lines"

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
        "momentum_last_synced",
        "communication_notes",
        "client_since",
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


# ---------------------------------------------------------------------------
# Reverse mapping: EspoCRM Account → NowCerts Insured
# ---------------------------------------------------------------------------

ACCOUNT_TYPE_REVERSE_MAP: dict[str, str] = {
    "Commercial Lines": "Commercial",
    "Personal Lines": "Personal",
    "Group Benefits": "Benefits",
}

ACCOUNT_TO_INSURED_FIELD_MAP: list[dict[str, Any]] = [
    {"src": "name", "dst": "CommercialName", "transform": "direct"},
    {"src": "primaryFirstName", "dst": "FirstName", "transform": "direct"},
    {"src": "primaryLastName", "dst": "LastName", "transform": "direct"},
    {"src": "fein", "dst": "FEIN", "transform": "direct"},
    {"src": "billingAddressStreet", "dst": "AddressLine1", "transform": "direct"},
    {"src": "billingAddressCity", "dst": "City", "transform": "direct"},
    {"src": "billingAddressState", "dst": "State", "transform": "direct"},
    {"src": "billingAddressPostalCode", "dst": "ZipCode", "transform": "direct"},
    {"src": "emailAddress", "dst": "EMail", "transform": "direct"},
    {"src": "phoneNumber", "dst": "Phone", "transform": "direct"},
    {"src": "account_type", "dst": "Type", "transform": "enum_map", "map": ACCOUNT_TYPE_REVERSE_MAP},
    {"src": "businessEntity", "dst": "TypeOfBusiness", "transform": "direct"},
    {"src": "cYearBusinessEst", "dst": "YearBusinessStarted", "transform": "direct"},
    {"src": "website", "dst": "Website", "transform": "direct"},
    {"src": "spouseFirstName", "dst": "CoInsured_FirstName", "transform": "direct"},
    {"src": "spouseLastName", "dst": "CoInsured_LastName", "transform": "direct"},
    {"src": "dateOfBirth", "dst": "DateOfBirth", "transform": "date_only"},
    {"src": "spouseDob", "dst": "CoInsured_DateOfBirth", "transform": "date_only"},
]


def map_account_to_insured(
    espo_record: dict[str, Any],
    *,
    nowcerts_database_id: str | None = None,
) -> dict[str, Any]:
    """Transform an EspoCRM Account record into a NowCerts Insured payload.

    Args:
        espo_record: EspoCRM Account dict.
        nowcerts_database_id: If known, include DatabaseId for upsert matching.

    Returns:
        Dict ready for NowCerts POST /api/Insured/Insert.
    """
    result: dict[str, Any] = {}

    if nowcerts_database_id:
        result["DatabaseId"] = nowcerts_database_id

    for mapping in ACCOUNT_TO_INSURED_FIELD_MAP:
        src_key = mapping["src"]
        dst_key = mapping["dst"]
        transform = mapping["transform"]
        raw_val = espo_record.get(src_key)

        if raw_val is None or raw_val == "":
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

    # Ensure Active flag
    result.setdefault("Active", True)

    return result


# ---------------------------------------------------------------------------
# EspoCRM Account → Supabase golden record
# ---------------------------------------------------------------------------

def map_account_to_golden(espo_record: dict[str, Any]) -> dict[str, Any]:
    """Transform an EspoCRM Account into a crm_accounts row."""
    return {
        "espocrm_id": espo_record.get("id", ""),
        "name": espo_record.get("name", ""),
        "first_name": espo_record.get("primaryFirstName"),
        "last_name": espo_record.get("primaryLastName"),
        "account_type": espo_record.get("account_type"),
        "fein": espo_record.get("fein"),
        "address_street": espo_record.get("billingAddressStreet"),
        "address_city": espo_record.get("billingAddressCity"),
        "address_state": espo_record.get("billingAddressState"),
        "address_zip": espo_record.get("billingAddressPostalCode"),
        "email": espo_record.get("emailAddress"),
        "phone": espo_record.get("phoneNumber"),
        "website": espo_record.get("website"),
        "business_entity": espo_record.get("businessEntity"),
        "year_business_started": espo_record.get("cYearBusinessEst"),
        "nowcerts_id": espo_record.get("momentum_client_id"),
        "source_system": "espocrm",
        "raw_espo_payload": espo_record,
    }


def _first_non_none(record: dict[str, Any], *keys: str) -> Any:
    """Return the first value that is not None from a sequence of dict keys."""
    for key in keys:
        val = record.get(key)
        if val is not None:
            return val
    return None


def map_policy_to_commission(
    policy_record: dict[str, Any],
    account_id: str | None = None,
) -> dict[str, Any]:
    """Transform an EspoCRM Policy (or Opportunity) into a crm_commissions row."""
    return {
        "account_id": account_id,
        "policy_number": policy_record.get("policyNumber") or policy_record.get("name"),
        "carrier": policy_record.get("carrier") or policy_record.get("carrierName"),
        "line_of_business": _first_non_none(
            policy_record, "lineOfBusiness", "line_of_business", "lineOfBusinessName",
        ),
        "premium": _first_non_none(policy_record, "premium", "premium_amount", "amount"),
        "commission_rate": _first_non_none(policy_record, "commissionRate", "agencyCommissionPercent"),
        "commission_amount": _first_non_none(policy_record, "commissionAmount", "agencyCommissionValue"),
        "agency_fee": policy_record.get("agencyFee"),
        "effective_date": _strip_date(policy_record.get("effectiveDate")),
        "expiration_date": _strip_date(
            _first_non_none(policy_record, "expirationDate", "expiration_date"),
        ),
        "policy_status": policy_record.get("status"),
        "source_system": "espocrm",
        "espocrm_id": policy_record.get("id"),
    }


def map_commission_to_nowcerts_policy(
    commission: dict[str, Any],
    insured_database_id: str | None = None,
) -> dict[str, Any]:
    """Transform a crm_commissions row into a NowCerts Policy/Insert payload."""
    payload: dict[str, Any] = {}

    if insured_database_id:
        payload["InsuredDatabaseId"] = insured_database_id

    if commission.get("policy_number"):
        payload["Number"] = commission["policy_number"]
    if commission.get("carrier"):
        payload["CarrierName"] = commission["carrier"]
    if commission.get("line_of_business"):
        payload["LineOfBusinessName"] = commission["line_of_business"]
    if commission.get("premium") is not None:
        payload["Premium"] = float(commission["premium"])
    if commission.get("commission_rate") is not None:
        payload["AgencyCommissionPercent"] = float(commission["commission_rate"])
    if commission.get("commission_amount") is not None:
        payload["AgencyCommissionValue"] = float(commission["commission_amount"])
    if commission.get("agency_fee") is not None:
        payload["AgencyFee"] = float(commission["agency_fee"])
    if commission.get("effective_date"):
        payload["EffectiveDate"] = str(commission["effective_date"])
    if commission.get("expiration_date"):
        payload["ExpirationDate"] = str(commission["expiration_date"])

    return payload


# ---------------------------------------------------------------------------
# NowCerts Insured → EspoCRM Contact
# ---------------------------------------------------------------------------

def map_insured_to_contact(
    nc_record: dict[str, Any],
    *,
    account_id: str | None = None,
    role: str = "primary",
) -> dict[str, Any] | None:
    """Extract a Contact payload from a NowCerts Insured record.

    Args:
        nc_record: Raw NowCerts insured dict.
        account_id: EspoCRM Account ID to link the Contact to.
        role: 'primary' for the named insured, 'co_insured' for co-insured/spouse.

    Returns:
        Dict ready for EspoCRM Contact create/update, or None if no usable name.
    """
    result: dict[str, Any] = {}

    if role == "co_insured":
        first = (nc_record.get("coInsured_FirstName") or "").strip()
        last = (nc_record.get("coInsured_LastName") or "").strip()
        dob = nc_record.get("coInsured_DateOfBirth")
    else:
        first = (nc_record.get("firstName") or "").strip()
        last = (nc_record.get("lastName") or "").strip()
        dob = nc_record.get("dateOfBirth")

    if not first and not last:
        return None

    if first:
        result["firstName"] = first
    if last:
        result["lastName"] = last

    # Composite name enables upsert_contact dedup by name-search fallback
    result["name"] = f"{first} {last}".strip()

    if role == "primary":
        email = (nc_record.get("eMail") or nc_record.get("email") or "").strip()
        phone = _to_e164_us(nc_record.get("cellPhone"))
        if email:
            result["emailAddress"] = email
        if phone:
            result["phoneNumber"] = phone

        address = (nc_record.get("addressLine1") or "").strip()
        city = (nc_record.get("city") or "").strip()
        state = (nc_record.get("state") or "").strip()
        zip_code = (nc_record.get("zipCode") or nc_record.get("zip") or "").strip()
        if address:
            result["addressStreet"] = address
        if city:
            result["addressCity"] = city
        if state:
            result["addressState"] = state
        if zip_code:
            result["addressPostalCode"] = zip_code

    if dob:
        stripped = _strip_date(dob)
        if stripped:
            result["dateOfBirth"] = stripped

    if account_id:
        result["accountId"] = account_id

    if role == "co_insured":
        result["description"] = "Co-Insured / Spouse (synced from NowCerts)"

    return result


# ---------------------------------------------------------------------------
# NowCerts Policy → EspoCRM Opportunity
# ---------------------------------------------------------------------------

# Map NowCerts LOB strings to EspoCRM lineOfBusiness enum values.
_NC_LOB_MAP: dict[str, str] = {
    "commercial auto": "Commercial Auto",
    "general liability": "General Liability",
    "workers compensation": "Workers Comp",
    "workers comp": "Workers Comp",
    "commercial property": "Commercial Property",
    "bop": "BOP",
    "business owners": "BOP",
    "professional liability": "Professional Liability",
    "umbrella": "Umbrella",
    "inland marine": "Inland Marine",
    "builders risk": "Builders Risk",
    "personal auto": "Personal Auto",
    "homeowners": "Homeowners",
    "renters": "Renters",
    "life": "Life",
    "health": "Health",
    "medicare": "Medicare",
    "group benefits": "Group Benefits",
}


def map_policy_to_opportunity(
    nc_policy: dict[str, Any],
    *,
    account_id: str | None = None,
    account_name: str | None = None,
) -> dict[str, Any] | None:
    """Transform a NowCerts Policy record into an EspoCRM Opportunity payload.

    Args:
        nc_policy: Raw NowCerts policy dict from /api/PolicyDetailList.
        account_id: EspoCRM Account ID to link the Opportunity to.
        account_name: Account name for the Opportunity name.

    Returns:
        Dict ready for EspoCRM Opportunity create/update, or None if unusable.
    """
    policy_number = (
        nc_policy.get("number")
        or nc_policy.get("policyNumber")
        or nc_policy.get("Number")
        or ""
    )
    # NowCerts returns LOB as an array in lineOfBusinesses; fall back to scalar fields.
    lob_raw = ""
    lob_list = nc_policy.get("lineOfBusinesses")
    if isinstance(lob_list, list) and lob_list:
        lob_raw = lob_list[0].get("lineOfBusinessName", "") if isinstance(lob_list[0], dict) else ""
    if not lob_raw:
        lob_raw = (
            nc_policy.get("lineOfBusinessName")
            or nc_policy.get("lineOfBusiness")
            or nc_policy.get("LineOfBusinessName")
            or nc_policy.get("description")
            or ""
        )

    if not policy_number and not lob_raw:
        return None

    lob = _NC_LOB_MAP.get(lob_raw.strip().lower(), lob_raw) if lob_raw else "Other"

    eff_date = _strip_date(
        nc_policy.get("effectiveDate")
        or nc_policy.get("EffectiveDate")
    )
    exp_date = _strip_date(
        nc_policy.get("expirationDate")
        or nc_policy.get("ExpirationDate")
    )

    name_parts = [account_name or "Insured", lob]
    if eff_date:
        name_parts.append(eff_date)
    opp_name = " - ".join(name_parts)

    result: dict[str, Any] = {
        "name": opp_name,
        "stage": "Closed Won",
        "lineOfBusiness": lob,
    }

    if policy_number:
        result["policyNumber"] = policy_number
    result["closeDate"] = eff_date or datetime.date.today().isoformat()
    if eff_date:
        result["proposedEffectiveDate"] = eff_date
        # bindDate and effectiveDate are both layout/workflow-required on
        # Opportunity create — POST 400s with `validationFailure {field: …,
        # type: required}` for each. NowCerts only carries one effective
        # date so all three (proposed/bind/effective) mirror it.
        result["bindDate"] = eff_date
        result["effectiveDate"] = eff_date
    if exp_date:
        result["expirationDate"] = exp_date

    premium = nc_policy.get("totalPremium")
    if premium is None:
        premium = nc_policy.get("premium") if nc_policy.get("premium") is not None else nc_policy.get("Premium")
    if premium is not None:
        try:
            premium_f = float(premium)
        except (TypeError, ValueError):
            premium_f = None
        if premium_f is not None:
            result["amount"] = premium_f
            # writtenPremium is layout-required on Opportunity create — POST
            # 400s with `validationFailure {field: writtenPremium, type: required}`
            # without it. NowCerts only carries one premium value, so mirror.
            result["writtenPremium"] = premium_f

    carrier = (
        nc_policy.get("carrierName")
        or nc_policy.get("CarrierName")
        or nc_policy.get("carrier")
        or ""
    )
    if carrier:
        result["carrier"] = carrier

    if account_id:
        result["accountId"] = account_id

    return result
