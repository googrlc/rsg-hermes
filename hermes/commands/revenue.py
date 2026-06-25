"""Cross-sell / renewal oriented views (Opportunities)."""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult
from hermes.core.field_utils import get_first_available

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


def _as_money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace("$", "").replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def _open_opportunities(client: EspoClient, limit: int = 15) -> list[dict[str, Any]]:
    """Recent opportunities; add a `where` filter when your pipeline stage names are known."""
    body = client.get(
        "Opportunity",
        params={
            "maxSize": limit,
            "orderBy": [["modifiedAt", "desc"]],
        },
    )
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [x for x in body["list"] if isinstance(x, dict)]
    return []


def _window_rows(client: EspoClient, start_days: int, end_days: int) -> list[dict[str, Any]]:
    entity = os.environ.get("HERMES_RENEWAL_ENTITY", "Opportunity")
    date_field = os.environ.get("HERMES_RENEWAL_DATE_FIELD", "closeDate")
    start = (date.today() + timedelta(days=start_days)).isoformat()
    end = (date.today() + timedelta(days=end_days)).isoformat()
    body = client.get(
        entity,
        params={
            "maxSize": 100,
            "orderBy": [[date_field, "asc"]],
            "select": f"id,name,amount,stage,{date_field},accountName",
            "where": [
                {"type": "greaterThanOrEquals", "attribute": date_field, "value": start},
                {"type": "lessThanOrEquals", "attribute": date_field, "value": end},
            ],
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _completed_renewal_review_accounts(client: EspoClient) -> set[str]:
    body = client.get(
        "Task",
        params={
            "maxSize": 200,
            "select": "id,name,status,accountId,linkedAccountId,parentId,parentType",
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    reviewed: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status") != "Completed":
            continue
        name = str(row.get("name") or "").lower()
        if "renewal review" not in name and "renewal" not in name:
            continue
        for field in ("accountId", "linkedAccountId"):
            if row.get(field):
                reviewed.add(str(row[field]))
        if row.get("parentType") == "Account" and row.get("parentId"):
            reviewed.add(str(row["parentId"]))
    return reviewed


def renewal_audit(client: EspoClient, days: int = 90) -> DispatchResult:
    """Project 85 Renewal Sentinel: policies expiring soon without a completed review task."""
    today = date.today()
    cutoff = today + timedelta(days=days)
    body = client.get(
        "Policy",
        params={
            "maxSize": 200,
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    reviewed_accounts = _completed_renewal_review_accounts(client)
    ignored_statuses = {"Expired", "Cancelled", "Flat Cancel", "Non-Renewed", "Lapsed"}

    risks: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        exp = _parse_iso_date(get_first_available(row, "expiration_date", "expirationDate"))
        if exp is None or exp < today or exp > cutoff:
            continue
        if row.get("status") in ignored_statuses:
            continue
        account_id = str(get_first_available(row, "accountId") or "")
        if account_id and account_id in reviewed_accounts:
            continue
        risks.append({**row, "_expiration": exp, "_days": (exp - today).days})

    risks.sort(key=lambda r: (_parse_iso_date(get_first_available(r, "expiration_date", "expirationDate")) or cutoff, -_as_money(get_first_available(r, "premium_amount", "premiumAmount", "premium", "amount"))))
    lines = [f"*Project 85 Renewal Sentinel* — Retention Risk ({len(risks)} policies in next {days} days without completed renewal review)"]
    if not risks:
        lines.append("No open renewal-review gaps found.")
        return DispatchResult(True, "\n".join(lines), {"rows": []})

    total_premium = sum((_as_money(get_first_available(r, "premium_amount", "premiumAmount", "premium", "amount")) for r in risks), Decimal("0"))
    lines.append(f"Premium at risk: ${total_premium:,.0f}")
    for row in risks[:10]:
        account = get_first_available(row, "accountName") or "Unknown account"
        lob = get_first_available(row, "line_of_business", "lineOfBusiness") or "Unknown LOB"
        carrier = get_first_available(row, "carrier") or "Unknown carrier"
        premium = _as_money(get_first_available(row, "premium_amount", "premiumAmount", "premium", "amount"))
        exp = get_first_available(row, "expiration_date", "expirationDate") or "?"
        days_left = row.get("_days", "?")
        lines.append(f"- {account} | {lob} | {carrier} | ${premium:,.0f} | expires {exp} ({days_left} days)")
    if len(risks) > 10:
        lines.append(f"- ... +{len(risks) - 10} more")
    lines.append("Next move: create/complete a Renewal Review task for each listed account.")
    return DispatchResult(True, "\n".join(lines), {"rows": risks})


def handle(client: EspoClient, text: str) -> DispatchResult:
    _ = text  # reserved for filters, e.g. renewal Q2
    if re.search(r"\b(expir(?:e|ing|y)|renewal[-\s]?audit|renewals?|audit\s*90)\b", text, re.I):
        return renewal_audit(client)
    if re.search(r"renewal", text, re.I):
        msg = "Renewal pipeline (open stages, by close date):"
    elif re.search(r"cross", text, re.I):
        msg = "Cross-sell / open opportunities:"
    else:
        msg = "Open opportunities:"

    rows = _open_opportunities(client)
    if not rows:
        return DispatchResult(True, f"{msg}\n(none in default open stages — tune stages in revenue.py)", {"rows": []})

    lines = [msg]
    for r in rows:
        name = r.get("name", "?")
        amt = r.get("amount")
        stage = r.get("stage", "?")
        close = get_first_available(r, "closeDate", "close_date") or "?"
        lines.append(f"- {name} | {amt} | {stage} | close {close}")
    return DispatchResult(True, "\n".join(lines), {"rows": rows})
