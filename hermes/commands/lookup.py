"""Lookups: e.g. 'What is John's phone' / 'Find account Acme'."""

from __future__ import annotations

import re
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


def handle(client: EspoClient, text: str) -> DispatchResult:
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
