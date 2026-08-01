"""The canonical book's schema contract.

``canonical_policies`` and ``canonical_clients`` are the mirror of the NowCerts
book. This module holds what it takes to *speak* about that book — the table
names, the key columns, and the pure functions that map a NowCerts record onto
a canonical row. No I/O, no sync logic, no domain.

It is split out for the same reason the queue contract was split out of the
renewal executor: the reader and the writer both need the shape, and the reader
should not have to import the writer to get it. ``canonical_book_sync`` (which
maintains the mirror, and imports renewals for lineage) re-exports these, so it
stays the one place the sync itself lives.

Twelve modules across nearly every app read this book — the hub, finance,
renewals, intake, the jobs and the agent. That is why the shape lives in the
shared core rather than behind one app's HTTP endpoint: routing it through the
hub would make finance and intake depend on the hub to read a table they
already have credentials for.
"""

from __future__ import annotations

from typing import Any

from hermes_core.field_utils import strip_date

CLIENTS_TABLE = "canonical_clients"
POLICIES_TABLE = "canonical_policies"

CLIENT_KEY = "nowcerts_insured_guid"
POLICY_KEY = "policy_guid"

_CLIENT_COLS = {
    "nowcerts_insured_guid", "insured_name", "insured_name_normalized",
    "first_name", "last_name", "client_type", "business_type",
    "phone", "cell_phone", "email", "address_line1", "city", "state", "zip", "updated_at",
}
_POLICY_COLS = {
    "policy_guid", "nowcerts_insured_guid", "policy_number", "lines_of_business",
    "business_type", "carrier", "status", "active", "effective_date", "expiration_date",
    "current_term_amount", "premium_amount", "annualized_premium", "renewed_policy", "state",
}

def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

def _insured_guid(record: dict[str, Any]) -> str:
    return str(
        record.get("id") or record.get("databaseId") or record.get("insuredDatabaseId") or ""
    ).strip()

def _insured_name(ins: dict[str, Any]) -> str:
    commercial = ins.get("commercialName") or ins.get("insuredCommercialName")
    if commercial:
        return str(commercial).strip()
    parts = [str(ins.get("firstName") or "").strip(), str(ins.get("lastName") or "").strip()]
    return " ".join(p for p in parts if p).strip()

def _policy_number(p: dict[str, Any]) -> str:
    return str(p.get("number") or p.get("policyNumber") or p.get("Number") or "").strip()

def _policy_guid(p: dict[str, Any]) -> str:
    return str(p.get("databaseId") or p.get("DatabaseId") or p.get("id") or "").strip()

def _policy_lob(p: dict[str, Any]) -> str | None:
    lob_list = p.get("lineOfBusinesses")
    if isinstance(lob_list, list) and lob_list and isinstance(lob_list[0], dict):
        name = lob_list[0].get("lineOfBusinessName")
        if name:
            return str(name)
    for key in ("lineOfBusinessName", "lineOfBusiness", "LineOfBusinessName"):
        if p.get(key):
            return str(p[key])
    return None

def _policy_premium(p: dict[str, Any]) -> float | None:
    for key in ("totalPremium", "premium", "Premium", "annualizedPremium"):
        val = _num(p.get(key))
        if val is not None:
            return val
    return None

def _policy_active(p: dict[str, Any], status: Any) -> bool:
    for key in ("active", "isActive", "Active"):
        val = p.get(key)
        if isinstance(val, bool):
            return val
    return normalize_status(status) in CURRENT_STATUSES

def _map_policy_volatile(p: dict[str, Any]) -> dict[str, Any]:
    """Fields a refresh always overwrites from live NowCerts (excludes lineage)."""
    status = p.get("status") or p.get("Status")
    premium = _policy_premium(p)
    return {
        "nowcerts_insured_guid": str(p.get("insuredDatabaseId") or p.get("insuredId") or "").strip() or None,
        "policy_number": _policy_number(p) or None,
        "lines_of_business": _policy_lob(p),
        "business_type": p.get("businessType") or p.get("BusinessType") or None,
        "carrier": p.get("carrierName") or p.get("CarrierName") or p.get("carrier") or None,
        "status": status,
        "active": _policy_active(p, status),
        "effective_date": strip_date(p.get("effectiveDate") or p.get("EffectiveDate")),
        "expiration_date": strip_date(p.get("expirationDate") or p.get("ExpirationDate")),
        "current_term_amount": premium,
        "premium_amount": premium,
        "annualized_premium": premium,
        "state": p.get("state") or p.get("State") or None,
    }


# --- policy lifecycle status --------------------------------------------------
# Whether a policy is in force is a fact about the book, not a renewals opinion:
# canonical_book_sync stamps `active` with it and commission_sync filters on it.
# It lived in renewals/eligibility.py, so reading the book meant importing the
# renewal rules. The rules stay there and import these.

# --- normalized lifecycle status (docs/integrations/nowcerts-import-mapping.md §2) ---
_STATUS_NORMALIZE = {
    "active": "Active", "in force": "Active", "inforce": "Active", "bound": "Active",
    "up for renewal": "Up for Renewal", "renewal pending": "Up for Renewal",
    "renewing": "Renewing", "in renewal": "Renewing", "renewal": "Renewing",
    "renewed": "Renewed", "rewritten": "Rewritten",
    "expired": "Expired",
    "cancelled": "Cancelled", "canceled": "Cancelled",
    "flat cancel": "Flat Cancel", "flat-cancel": "Flat Cancel", "flat cancelled": "Flat Cancel",
    "pending cancel": "Pending Cancel", "pending cancellation": "Pending Cancel", "cxl pending": "Pending Cancel",
    "non-renewed": "Non-Renewed", "non renewed": "Non-Renewed", "nonrenewed": "Non-Renewed",
    "lapsed": "Lapsed",
}

CURRENT_STATUSES = frozenset({"Active"})              # active / in force / bound
STAGED_STATUSES = frozenset({"Up for Renewal", "Renewing"})
# The spec's always-exclude set (Pending Cancel is deliberately NOT here — it is
# ambiguous and routes to needs_verification via the catch-all).
EXCLUDE_STATUSES = frozenset({"Expired", "Cancelled", "Flat Cancel", "Non-Renewed", "Lapsed"})
SUPERSEDED_STATUSES = frozenset({"Renewed", "Rewritten"})


def normalize_status(raw: Any) -> str:
    """Map any AMS status spelling to the canonical enum, or '' if unknown."""
    return _STATUS_NORMALIZE.get(str(raw or "").strip().lower(), "")
