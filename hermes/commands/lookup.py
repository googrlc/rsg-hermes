"""Universal lookups: contacts, accounts, and any field on any entity via schema registry."""

from __future__ import annotations

import re
import os
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult
from hermes.core.field_utils import get_first_available
from hermes.core.schema_registry import get_registry

if TYPE_CHECKING:
    from hermes.core.client import EspoClient

ADDRESS_FIELDS = (
    "billingAddressStreet", "billingAddressCity",
    "billingAddressState", "billingAddressPostalCode",
)

COMMON_ALIASES: dict[str, str] = {
    "fein": "fein",
    "ein": "fein",
    "tax id": "fein",
    "dot": "caDotNumber",
    "dot number": "caDotNumber",
    "mc number": "caMcNumber",
    "sic": "sicCode",
    "naics": "naicsCode",
    "website": "website",
    "address": "billingAddressStreet",
    "city": "billingAddressCity",
    "state": "billingAddressState",
    "zip": "billingAddressPostalCode",
    "industry": "industry",
    "description": "description",
    "policy number": "policyNumber",
    "carrier": "carrier",
    "premium": "amount",
    "commission rate": "commissionRate",
    "effective date": "effectiveDate",
    "expiration date": "expirationDate",
    "lob": "lineOfBusiness",
    "line of business": "lineOfBusiness",
}


def _search_contacts(client: "EspoClient", term: str) -> list[dict[str, Any]]:
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


def _search_entity(
    client: "EspoClient",
    entity: str,
    term: str,
    extra_fields: str = "",
    *,
    include_all_fields: bool = False,
) -> list[dict[str, Any]]:
    select = "id,name"
    if extra_fields:
        select = f"{select},{extra_fields}"
    params: dict[str, Any] = {
        "maxSize": 5,
        "where": [
            {"type": "contains", "attribute": "name", "value": term},
        ],
    }
    if not include_all_fields:
        params["select"] = select
    try:
        body = client.get(
            entity,
            params=params,
        )
    except Exception:
        return []
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [x for x in body["list"] if isinstance(x, dict)]
    return []


def _search_policies(client: "EspoClient", term: str) -> list[dict[str, Any]]:
    try:
        body = client.get(
            "Policy",
            params={
                "maxSize": 5,
                "where": [
                    {
                        "type": "or",
                        "value": [
                            {"type": "contains", "attribute": "name", "value": term},
                            {"type": "contains", "attribute": "policyNumber", "value": term},
                            {"type": "contains", "attribute": "policy_number", "value": term},
                        ],
                    }
                ],
            },
        )
    except Exception:
        return []
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [x for x in body["list"] if isinstance(x, dict)]
    return []


def _resolve_field_name(raw: str) -> str:
    """Map a natural-language field hint to an actual CRM field name."""
    key = raw.lower().strip()
    if key in COMMON_ALIASES:
        return COMMON_ALIASES[key]

    registry = get_registry()
    matches = registry.find_field(raw)
    if matches:
        return matches[0].field_name
    return raw


def _resolve_entity(field_name: str, entity_hint: str | None = None) -> str:
    """Figure out which entity owns this field."""
    if entity_hint:
        clean = entity_hint.strip().title()
        if clean in ("Account", "Contact", "Lead", "Opportunity", "Policy", "Renewal", "Commission", "Task"):
            return clean

    registry = get_registry()
    matches = registry.find_field(field_name)
    if matches:
        entity_priority = ["Account", "Policy", "Opportunity", "Contact", "Lead", "Renewal", "Commission", "Task"]
        for prio in entity_priority:
            for m in matches:
                if m.entity == prio:
                    return prio
        return matches[0].entity
    return "Account"


def _field_lookup(client: "EspoClient", field_hint: str, name_query: str, entity_hint: str | None = None) -> DispatchResult:
    """Look up a specific field value by searching an entity by name."""
    field_name = _resolve_field_name(field_hint)
    entity = _resolve_entity(field_name, entity_hint)

    extra = field_name
    if field_name == "billingAddressStreet":
        extra = ",".join(ADDRESS_FIELDS)

    hits = _search_entity(client, entity, name_query, extra_fields=extra)
    if not hits:
        return DispatchResult(True, f'No {entity} matching "{name_query}".', {"results": []})

    label = field_hint.upper() if len(field_hint) <= 5 else field_hint.title()
    lines = []
    for rec in hits:
        name = rec.get("name", "?")
        if field_name == "billingAddressStreet":
            parts = [rec.get(f) or "" for f in ADDRESS_FIELDS]
            value = ", ".join(p for p in parts if p) or "\u2014"
        else:
            value = rec.get(field_name) or "\u2014"
        lines.append(f"*{name}* ({entity}) \u2014 {label}: {value}")
    return DispatchResult(True, "\n".join(lines), {"results": hits, "entity": entity, "field": field_name})


def _as_money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace("$", "").replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def _total_premium(client: "EspoClient", account_name: str) -> DispatchResult:
    account_hits = _search_entity(client, "Account", account_name)
    if not account_hits:
        return DispatchResult(True, f'No account matching "{account_name}".', {"accounts": []})
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


def handle(client: "EspoClient", text: str) -> DispatchResult:
    premium_match = re.search(r"\b(?:total\s+premium|sum\s+premium|premium\s+for)\s+(?:for\s+)?(.+?)\??$", text, re.I)
    if premium_match:
        return _total_premium(client, premium_match.group(1).strip())

    # Universal field lookup: "find the fein for Acme" / "what is the dot number for Trucking Inc"
    field_for_match = re.search(
        r"\b(?:the\s+)?(\w[\w\s]{0,30}?)\s+(?:for|of|on)\s+(.+?)\s*\??$",
        re.sub(r"^\s*(what|who|find|lookup|search|get)\s+(is\s+)?(the\s+)?", "", text, flags=re.I),
        re.I,
    )
    if field_for_match:
        field_hint = field_for_match.group(1).strip()
        name_query = field_for_match.group(2).strip()
        if field_hint.lower() not in ("contact", "account", "policy", "lead"):
            return _field_lookup(client, field_hint, name_query)

    # "find account Acme" / "lookup account Acme"
    acct_match = re.search(r"\baccount\s+(.+?)\s*\??$", text, re.I)
    if acct_match:
        term = acct_match.group(1).strip()
        hits = _search_entity(client, "Account", term, include_all_fields=True)
        if not hits:
            return DispatchResult(True, f'No account matching "{term}".', {"accounts": []})
        lines = []
        for a in hits:
            name = a.get("name", "?")
            phone = a.get("phoneNumber") or "\u2014"
            web = a.get("website") or "\u2014"
            fein = a.get("fein") or "\u2014"
            lines.append(f"*{name}* | Phone: {phone} | FEIN: {fein} | {web}")
        return DispatchResult(True, "\n".join(lines), {"accounts": hits})

    # "find policy P-12345" / "lookup policy Acme"
    policy_match = re.search(r"\bpolicy\s+(.+?)\s*\??$", text, re.I)
    if policy_match:
        term = policy_match.group(1).strip()
        hits = _search_policies(client, term)
        if not hits:
            return DispatchResult(True, f'No policy matching "{term}".', {"policies": []})
        lines = []
        for p in hits:
            name = p.get("name", "?")
            pnum = get_first_available(p, "policy_number", "policyNumber") or "\u2014"
            carrier = get_first_available(p, "carrier") or "\u2014"
            eff = get_first_available(p, "effective_date", "effectiveDate") or "\u2014"
            exp = get_first_available(p, "expiration_date", "expirationDate") or "\u2014"
            lob = get_first_available(p, "line_of_business", "lineOfBusiness") or "\u2014"
            prem = get_first_available(p, "premium_amount", "premiumAmount", "premium", "amount") or "\u2014"
            lines.append(f"*{name}* | #{pnum} | {carrier} | LOB: {lob} | Eff: {eff} | Exp: {exp} | ${prem}")
        return DispatchResult(True, "\n".join(lines), {"policies": hits})

    # Contact search (default)
    cleaned = re.sub(
        r"^\s*(what|who|is|find|lookup|search|get)\s+(is\s+)?(the\s+)?",
        "",
        text,
        flags=re.I,
    ).strip()
    m = re.match(r"^(.+?)(?:'s)?\s+(phone|number|email)\b", cleaned, re.I)
    term = (m.group(1) if m else cleaned).strip().strip("?").strip()
    if len(term) < 2:
        return DispatchResult(False, "Say who/what to look up. Examples:\n"
            "- find the fein for Acme\n"
            "- what is the dot number for Trucking Inc\n"
            "- find account Peterbilt\n"
            "- find policy P-12345\n"
            "- what is Jane Doe phone")

    hits = _search_contacts(client, term)
    if not hits:
        acct_hits = _search_entity(client, "Account", term, extra_fields="phoneNumber,website,fein")
        if acct_hits:
            lines = []
            for a in acct_hits:
                name = a.get("name", "?")
                phone = a.get("phoneNumber") or "\u2014"
                fein = a.get("fein") or "\u2014"
                lines.append(f"*{name}* | Phone: {phone} | FEIN: {fein}")
            return DispatchResult(True, "\n".join(lines), {"accounts": acct_hits})
        return DispatchResult(True, f'No contacts or accounts matching "{term}".', {"contacts": []})

    lines = []
    for h in hits:
        name = h.get("name") or f"{h.get('firstName','')} {h.get('lastName','')}".strip()
        phone = h.get("phoneNumber") or "\u2014"
        email = h.get("emailAddress") or "\u2014"
        lines.append(f"{name} | {phone} | {email}")
    return DispatchResult(True, "\n".join(lines), {"contacts": hits})
