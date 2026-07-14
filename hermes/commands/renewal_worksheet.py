"""Prepare a client-specific renewal worksheet on demand.

Recognises variants such as:
  - prepare a renewal worksheet for <client>
  - prepare renewal worksheet for policy <policy number>
  - create/build/generate a renewal worksheet for <client>

Route precedence: this handler is registered BEFORE the broad
renewal/revenue route so it intercepts worksheet requests first.

Resolution rules
----------------
* Exact policy-number match is preferred when a policy number is
  supplied.  Policy numbers are normalised (strip whitespace, fold to
  upper-case) before comparison, but a normalised key may never match
  a *different* normalised key (no fuzzy / partial matching).
* When only a client name is supplied and multiple active/renewing
  policies match, all candidates are returned and no worksheet is
  generated until the caller selects one.
* When a policy is absent from EspoCRM the handler returns an explicit
  reconciliation-needed response and never creates or merges a record.
* Repeated identical requests are idempotent — no duplicate PDF, task,
  or renewal row is created.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult
from hermes.renewals import worksheet

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


# ---------------------------------------------------------------------------
# Text parsing helpers
# ---------------------------------------------------------------------------

_POLICY_NUMBER_RE = re.compile(
    r"\bpolic(?:y|ies)\s+(?:number\s+|#\s*|no\.?\s+)?([A-Z0-9][-A-Z0-9/ ]{2,})",
    re.I,
)
_FOR_CLIENT_RE = re.compile(r"\bfor\s+([A-Za-z0-9 &',.\-]{1,150})$", re.I)


def _normalise_policy_number(raw: str) -> str:
    """Strip surrounding whitespace and fold to upper-case for exact comparison."""
    return " ".join(raw.split()).upper()


def parse_request(text: str) -> dict[str, str | None]:
    """Return ``{"policy_number": ..., "client_name": ...}`` from *text*.

    At least one key will be non-``None`` when the route pattern matched.
    """
    policy_number: str | None = None
    client_name: str | None = None

    m = _POLICY_NUMBER_RE.search(text)
    if m:
        policy_number = _normalise_policy_number(m.group(1))

    # Remove the matched policy fragment before looking for client name.
    remaining = _POLICY_NUMBER_RE.sub("", text) if m else text

    for_match = _FOR_CLIENT_RE.search(remaining)
    if for_match:
        candidate = for_match.group(1).strip()
        # Strip trailing noise that might be left after policy extraction.
        # Use a fixed-length bounded pattern to avoid ReDoS.
        candidate = re.sub(r"\s+(policy|number|#|no\.?)[ \t]*$", "", candidate, flags=re.I).strip()
        if candidate:
            client_name = candidate

    return {"policy_number": policy_number, "client_name": client_name}


# ---------------------------------------------------------------------------
# EspoCRM lookups
# ---------------------------------------------------------------------------

_INACTIVE_STATUSES = {"Expired", "Cancelled", "Flat Cancel", "Non-Renewed", "Lapsed"}


def _list_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [r for r in body["list"] if isinstance(r, dict)]
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    return []


def _get_field(row: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _lookup_by_policy_number(
    client: "EspoClient",
    policy_number: str,
) -> list[dict[str, Any]]:
    """Return all Policy rows whose normalised policy number exactly matches *policy_number*."""
    try:
        body = client.get(
            "Policy",
            params={"maxSize": 200, "select": "id,name,accountName,accountId,policyNumber,policy_number,status,expirationDate,expiration_date,carrier,lineOfBusiness,line_of_business,premiumAmount,premium_amount"},
        )
    except Exception:
        return []
    rows = _list_rows(body)
    matches = []
    for row in rows:
        raw = _get_field(row, "policyNumber", "policy_number") or ""
        if _normalise_policy_number(str(raw)) == policy_number:
            matches.append(row)
    return matches


def _lookup_by_client_name(
    client: "EspoClient",
    client_name: str,
) -> list[dict[str, Any]]:
    """Return active/renewing Policy rows whose account name contains *client_name* (case-insensitive)."""
    try:
        body = client.get(
            "Policy",
            params={"maxSize": 200, "select": "id,name,accountName,accountId,policyNumber,policy_number,status,expirationDate,expiration_date,carrier,lineOfBusiness,line_of_business,premiumAmount,premium_amount"},
        )
    except Exception:
        return []
    rows = _list_rows(body)
    needle = client_name.lower().strip()
    matches = []
    for row in rows:
        status = str(row.get("status") or "")
        if status in _INACTIVE_STATUSES:
            continue
        account = str(_get_field(row, "accountName") or row.get("name") or "").lower()
        if needle in account:
            matches.append(row)
    return matches


# ---------------------------------------------------------------------------
# Worksheet text builder
# ---------------------------------------------------------------------------

def _policy_row_to_renewal_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Map a Policy CRM row into the shape that ``worksheet.build_worksheet_content`` expects."""
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "accountName": _get_field(row, "accountName"),
        "accountId": _get_field(row, "accountId"),
        "carrier": row.get("carrier"),
        "line_of_business": _get_field(row, "lineOfBusiness", "line_of_business"),
        "expiration_date": _get_field(row, "expirationDate", "expiration_date"),
        "current_premium": _get_field(row, "premiumAmount", "premium_amount"),
        "pipeline_stage": row.get("status"),
        "policyNumber": _get_field(row, "policyNumber", "policy_number"),
    }


def _policy_summary(row: dict[str, Any]) -> str:
    acct = _get_field(row, "accountName") or row.get("name") or "Unknown"
    lob = _get_field(row, "lineOfBusiness", "line_of_business") or "?"
    carrier = row.get("carrier") or "?"
    exp = _get_field(row, "expirationDate", "expiration_date") or "?"
    pnum = _get_field(row, "policyNumber", "policy_number") or "?"
    return f"- {acct} | {lob} | {carrier} | policy #{pnum} | exp {exp}"


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

def handle(client: "EspoClient", text: str) -> DispatchResult:
    """Prepare a renewal worksheet for the requested client or policy."""
    req = parse_request(text)
    policy_number = req["policy_number"]
    client_name = req["client_name"]

    if not policy_number and not client_name:
        return DispatchResult(
            False,
            "Could not identify a client name or policy number in your request.\n"
            "Try: `prepare renewal worksheet for <client name>` or "
            "`prepare renewal worksheet for policy <policy number>`.",
        )

    # --- Exact policy-number lookup ---
    if policy_number:
        rows = _lookup_by_policy_number(client, policy_number)
        if not rows:
            return DispatchResult(
                False,
                f"⚠️ Reconciliation needed — policy **{policy_number}** was not found in EspoCRM.\n"
                "The record may exist only in NowCerts or the renewal table.\n"
                "No worksheet was generated and no record was created.",
                {"reconciliation_needed": True, "policy_number": policy_number},
            )
        if len(rows) > 1:
            lines = [
                f"⚠️ Ambiguous match — {len(rows)} policies share policy number **{policy_number}**. "
                "No worksheet was generated. Please select the exact record:",
            ]
            lines.extend(_policy_summary(r) for r in rows)
            lines.append("\nRe-submit with the specific record ID to proceed.")
            return DispatchResult(
                False,
                "\n".join(lines),
                {"ambiguous": True, "candidates": rows, "policy_number": policy_number},
            )
        row = rows[0]
        renewal = _policy_row_to_renewal_dict(row)
        content = worksheet.build_worksheet_content(renewal)
        acct = renewal.get("accountName") or renewal.get("name") or policy_number
        return DispatchResult(
            True,
            f"📄 Renewal Worksheet — {acct} (policy #{policy_number})\n\n{content}",
            {"worksheet": renewal, "source": "espocrm"},
        )

    # --- Client-name lookup ---
    rows = _lookup_by_client_name(client, client_name)  # type: ignore[arg-type]
    if not rows:
        return DispatchResult(
            False,
            f"⚠️ Reconciliation needed — no active/renewing policies found for **{client_name}** in EspoCRM.\n"
            "The record may exist only in NowCerts or the renewal table.\n"
            "No worksheet was generated and no record was created.",
            {"reconciliation_needed": True, "client_name": client_name},
        )
    if len(rows) > 1:
        lines = [
            f"Multiple active policies match **{client_name}** ({len(rows)} found). "
            "No worksheet was generated. Please select one:",
        ]
        lines.extend(_policy_summary(r) for r in rows)
        lines.append(
            "\nRe-submit with the specific policy number: "
            "`prepare renewal worksheet for policy <policy number>`."
        )
        return DispatchResult(
            False,
            "\n".join(lines),
            {"ambiguous": True, "candidates": rows, "client_name": client_name},
        )

    row = rows[0]
    renewal = _policy_row_to_renewal_dict(row)
    content = worksheet.build_worksheet_content(renewal)
    acct = renewal.get("accountName") or renewal.get("name") or client_name
    return DispatchResult(
        True,
        f"📄 Renewal Worksheet — {acct}\n\n{content}",
        {"worksheet": renewal, "source": "espocrm"},
    )
