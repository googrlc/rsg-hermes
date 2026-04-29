"""Lookups: e.g. 'What is John's phone' / 'Find account Acme'."""

from __future__ import annotations

import re
import os
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


def _search_contacts(client: EspoClient, term: str) -> list[dict[str, Any]]:
    body = client.get(
        "Contact",
        params={
            "maxSize": 10,
            "select": "id,name,firstName,lastName,phoneNumber,emailAddress",
            "where": [
                {
                    "type": "or",
                    "value": [
                        {"type": "contains", "attribute": "name", "value": term},
                        {"type": "contains", "attribute": "firstName", "value": term},
                        {"type": "contains", "attribute": "lastName", "value": term},
                    ],
                }
            ],
        },
    )
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [x for x in body["list"] if isinstance(x, dict)]
    return []


def _as_money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace("$", "").replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def _total_premium(client: EspoClient, account_name: str) -> DispatchResult:
    account_hits = client.search("Account", account_name, max_size=5, select="id,name")
    if not account_hits:
        return DispatchResult(True, f"No account matching “{account_name}”.", {"accounts": []})
    account = account_hits[0]
    entity = os.environ.get("HERMES_POLICY_ENTITY", "Opportunity")
    premium_field = os.environ.get("HERMES_PREMIUM_FIELD", "amount")
    account_field = os.environ.get("HERMES_POLICY_ACCOUNT_ID_FIELD", "accountId")
    body = client.get(
        entity,
        params={
            "maxSize": 200,
            "select": f"id,name,{premium_field},{account_field}",
            "where": [{"type": "equals", "attribute": account_field, "value": account["id"]}],
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    records = [row for row in rows if isinstance(row, dict)]
    total = sum((_as_money(row.get(premium_field)) for row in records), Decimal("0"))
    return DispatchResult(
        True,
        f"{account.get('name', account_name)} total premium: ${total:,.2f} across {len(records)} {entity} record(s).",
        {"account": account, "rows": records, "total": str(total)},
    )


def handle(client: EspoClient, text: str) -> DispatchResult:
    premium_match = re.search(r"\b(?:total\s+premium|sum\s+premium|premium\s+for)\s+(?:for\s+)?(.+?)\??$", text, re.I)
    if premium_match:
        account_name = premium_match.group(1).strip()
        return _total_premium(client, account_name)

    # Strip leading question words
    cleaned = re.sub(
        r"^\s*(what|who|is|find|lookup|search)\s+(is\s+)?",
        "",
        text,
        flags=re.I,
    ).strip()
    # "john's number" -> john
    m = re.match(r"^(.+?)(?:'s)?\s+(phone|number|email)\b", cleaned, re.I)
    term = (m.group(1) if m else cleaned).strip().strip("?").strip()
    if len(term) < 2:
        return DispatchResult(False, "Say who to look up, e.g. What is Jane Doe phone?")

    hits = _search_contacts(client, term)
    if not hits:
        return DispatchResult(True, f"No contacts matching “{term}”.", {"contacts": []})

    lines = []
    for h in hits:
        name = h.get("name") or f"{h.get('firstName','')} {h.get('lastName','')}".strip()
        phone = h.get("phoneNumber") or "—"
        email = h.get("emailAddress") or "—"
        lines.append(f"{name} | {phone} | {email}")
    return DispatchResult(True, "\n".join(lines), {"contacts": hits})
