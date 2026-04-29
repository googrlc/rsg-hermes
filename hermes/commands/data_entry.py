"""Natural-language style data entry: e.g. 'Add contact John Smith'."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


def _parse_add_contact(text: str) -> tuple[dict[str, Any], str | None] | None:
    # "Add contact John Smith email jane@example.com to account Acme"
    m = re.search(
        r"add\s+(?:contact\s+)?(.+?)(?:\s+as\s+contact)?\s*$",
        text,
        re.I,
    )
    if not m:
        return None
    name = m.group(1).strip()
    account_name = None
    account_match = re.search(r"\s+to\s+account\s+(.+?)\s*$", name, re.I)
    if account_match:
        account_name = account_match.group(1).strip()
        name = name[: account_match.start()].strip()
    email = None
    email_match = re.search(r"\bemail\s+([^\s,;]+@[^\s,;]+)", name, re.I)
    if email_match:
        email = email_match.group(1).strip()
        name = (name[: email_match.start()] + name[email_match.end() :]).strip()
    parts = name.split(None, 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""
    payload = {"firstName": first, "lastName": last, "name": name}
    if email:
        payload["emailAddress"] = email
    return payload, account_name


def handle(client: EspoClient, text: str) -> DispatchResult:
    parsed = _parse_add_contact(text)
    if not parsed:
        return DispatchResult(
            False,
            'Could not parse. Example: "Add contact Jane Doe email jane@example.com to account Acme".',
        )
    payload, account_name = parsed
    account = None
    if account_name:
        hits = client.search("Account", account_name, max_size=1, select="id,name")
        if hits and hits[0].get("id"):
            account = hits[0]
            payload["accountId"] = account["id"]
            payload["accountName"] = account.get("name", account_name)
    record = client.upsert_contact(payload)
    action = "Upserted"
    suffix = f" linked to {account.get('name')}" if account else ""
    if isinstance(record, dict) and record.get("id"):
        return DispatchResult(True, f"{action} Contact {record['id']}{suffix}.", {"record": record})
    return DispatchResult(True, f"{action} contact submitted{suffix}.", {"record": record})
