"""Map NowCerts PolicyDetailList records onto commission_ledger rows.

NowCerts field casing is inconsistent across endpoints, so extraction is
case-insensitive and tries several candidate keys per logical field. The exact
keys should be confirmed against a live sample before the first production run;
the multi-key approach degrades gracefully (missing carrier/LOB/client become
placeholders + a needs_rule row rather than a crash).
"""

from __future__ import annotations

from typing import Any, Optional

from . import config
from .rules import Rule

Policy = dict[str, Any]


def _ci_get(policy: Policy, *candidates: str) -> Any:
    """Case-insensitive lookup across candidate keys; first non-empty wins."""
    lowered = {str(k).lower(): v for k, v in policy.items()}
    for cand in candidates:
        v = lowered.get(cand.lower())
        if v not in (None, ""):
            return v
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _to_date_iso(value: Any) -> Optional[str]:
    """Normalize an ISO or MM/DD/YYYY date to YYYY-MM-DD."""
    if not value:
        return None
    s = str(value).strip()
    if "T" in s:  # ISO datetime
        return s.split("T", 1)[0]
    if "/" in s:  # MM/DD/YYYY
        parts = s.split("/")
        if len(parts) == 3:
            mm, dd, yyyy = parts
            if len(yyyy) == 4:
                return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
    if len(s) >= 10 and s[4] == "-":  # already YYYY-MM-DD...
        return s[:10]
    return None


def _client_name(policy: Policy) -> Optional[str]:
    direct = _ci_get(
        policy,
        "InsuredCommercialName",
        "CommercialName",
        "InsuredName",
        "InsuredFullName",
        "insuredCommercialName",
        "insuredName",
        "Insured",  # NowCerts normalized feed exposes the client under a bare "insured"
    )
    if direct:
        return str(direct).strip()
    first = _ci_get(policy, "InsuredFirstName", "insuredFirstName")
    last = _ci_get(policy, "InsuredLastName", "insuredLastName")
    name = " ".join(p for p in (first, last) if p).strip()
    return name or None


def _is_renewal(policy: Policy) -> bool:
    flag = _ci_get(policy, "IsRenewal", "isRenewal")
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str) and flag.strip().lower() in ("true", "yes", "1"):
        return True
    kind = _ci_get(
        policy, "BusinessType", "TransactionType", "PolicyBusinessType", "businessType"
    )
    return isinstance(kind, str) and "renew" in kind.lower()


def is_purged(policy: Policy) -> bool:
    """True if the policy carries the PURGE-POLICY-2026-07 marker anywhere plausible."""
    tag = config.PURGE_TAG
    for key in (
        "Tags",
        "TagList",
        "PolicyTags",
        "tags",
        "Note",
        "Notes",
        "Description",
        "Memo",
    ):
        v = _ci_get(policy, key)
        if isinstance(v, str) and tag in v:
            return True
        if isinstance(v, (list, tuple)) and any(tag in str(x) for x in v):
            return True
    return False


def extract_fields(policy: Policy) -> dict[str, Any]:
    """Pull the logical fields the ledger needs out of a raw NowCerts record."""
    effective = _to_date_iso(_ci_get(policy, "EffectiveDate", "effectiveDate"))
    change = _to_date_iso(_ci_get(policy, "ChangeDate", "changeDate"))
    return {
        "nowcerts_policy_id": _ci_get(
            policy, "DatabaseId", "PolicyDatabaseId", "databaseId", "Id", "id"
        ),
        "policy_number": _ci_get(policy, "Number", "PolicyNumber", "policyNumber", "number"),
        "carrier": _ci_get(policy, "CarrierName", "Carrier", "carrierName", "carrier"),
        "lob": _ci_get(policy, "LineOfBusiness", "lineOfBusiness", "Lob", "LOB", "lob"),
        "state": _ci_get(policy, "StateCode", "State", "RiskState", "InsuredState", "state"),
        "gross_premium": _to_float(
            _ci_get(policy, "PremiumAmount", "Premium", "AnnualizedPremium", "premium")
        ),
        "client_name": _client_name(policy),
        "effective_date": effective,
        "expiration_date": _to_date_iso(_ci_get(policy, "ExpirationDate", "expirationDate")),
        "is_renewal": _is_renewal(policy),
        "change_date": change,
    }


def build_ledger_row(
    fields: dict[str, Any], rule: Optional[Rule], expected: Optional[float]
) -> Optional[dict[str, Any]]:
    """Build the commission_ledger upsert payload, or None if unusable.

    Returns None when the policy has no NowCerts id (can't upsert) or no usable
    date (statement_date is NOT NULL); the caller counts these as skipped.
    """
    nc_id = fields.get("nowcerts_policy_id")
    if not nc_id:
        return None

    statement_date = fields.get("effective_date") or fields.get("change_date")
    if not statement_date:
        return None

    matched = rule is not None
    row: dict[str, Any] = {
        "nowcerts_policy_id": str(nc_id),
        "policy_number": (fields.get("policy_number") or str(nc_id)),
        "carrier_name": (fields.get("carrier") or "(unknown carrier)"),
        "lob": (fields.get("lob") or "(unknown lob)"),
        "client_name": (fields.get("client_name") or "(unknown insured)"),
        "state": (fields.get("state") or "GA"),
        "statement_date": statement_date,
        "policy_effective_date": fields.get("effective_date"),
        "is_renewal": bool(fields.get("is_renewal")),
        "gross_premium": fields.get("gross_premium"),
        "expected_commission": expected if matched else None,
        "commission_rule_id": rule.get("id") if matched else None,
        "commission_basis": (rule.get("commission_basis") if matched else None) or "as_earned",
        "reconciliation_status": config.STATUS_PENDING if matched else config.STATUS_NEEDS_RULE,
        "statement_source": config.STATEMENT_SOURCE,
    }

    if matched and expected is not None:
        split = rule.get("revenue_split_percent")
        try:
            split_f = float(split) if split is not None else 100.0
        except (TypeError, ValueError):
            split_f = 100.0
        row["revenue_split_percent"] = split_f
        row["rsg_net_commission"] = round(expected * split_f / 100.0, 2)

    return row
