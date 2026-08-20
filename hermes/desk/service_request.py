"""Create a Desk service ticket from a Zoho CRM Account record.

Used by the CRM Account button's Python twin (Hermes / apply scripts).
Does not write to Momentum.
"""

from __future__ import annotations

from typing import Any

from hermes.desk.crm_button import build_ticket_payload, crm_account_identity
from hermes_integrations.zoho_desk_client import ZohoDeskClient


def _first_id(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        rid = row.get("id")
        if rid:
            return str(rid)
    return ""


def ensure_desk_account(client: ZohoDeskClient, account_name: str) -> str:
    """Reuse a Desk account by name; create a thin name-only record if needed."""
    name = (account_name or "").strip()
    if not name:
        return ""
    existing = client.search_accounts(account_name=name, limit=1)
    found = _first_id(existing)
    if found:
        return found
    created = client.create_account({"accountName": name})
    return str(created.get("id") or "")


def ensure_desk_contact(
    client: ZohoDeskClient,
    *,
    last_name: str,
    email: str = "",
    phone: str = "",
    desk_account_id: str = "",
) -> str:
    """Reuse a Desk contact by email; otherwise create the minimum contact."""
    if email:
        existing = client.search_contacts(email=email, limit=1)
        found = _first_id(existing)
        if found:
            return found
    payload: dict[str, Any] = {"lastName": last_name or "Unknown"}
    if email:
        payload["email"] = email
    if phone:
        payload["phone"] = phone
    if desk_account_id:
        payload["accountId"] = desk_account_id
    created = client.create_contact(payload)
    return str(created.get("id") or "")


def create_from_crm_account(
    client: ZohoDeskClient,
    account: dict[str, Any],
    *,
    category: str | None = None,
    policy_number: str | None = None,
    short_request: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Open a General Service ticket linked to the CRM Account id."""
    ident = crm_account_identity(account)
    desk_account_id = ensure_desk_account(client, ident["account_name"])
    contact_id = ensure_desk_contact(
        client,
        last_name=ident["contact_last_name"],
        email=ident["email"],
        phone=ident["phone"],
        desk_account_id=desk_account_id,
    )
    if not contact_id:
        raise RuntimeError(
            "Could not find or create a Desk contact for "
            f"{ident['account_name']}. Add an email on the CRM Account."
        )
    payload = build_ticket_payload(
        account,
        contact_id=contact_id,
        desk_account_id=desk_account_id or None,
        category=category,
        policy_number=policy_number,
        short_request=short_request,
        description=description,
    )
    ticket = client.create_ticket(payload)
    ticket["deskAccountId"] = desk_account_id
    ticket["deskContactId"] = contact_id
    return ticket
