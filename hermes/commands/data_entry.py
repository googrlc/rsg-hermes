"""Natural-language style data entry: e.g. 'Add contact John Smith'."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


def _parse_add_contact(text: str) -> dict[str, Any] | None:
    # "Add contact John Smith" / "Add John Smith as contact"
    m = re.search(
        r"add\s+(?:contact\s+)?(.+?)(?:\s+as\s+contact)?\s*$",
        text,
        re.I,
    )
    if not m:
        return None
    name = m.group(1).strip()
    parts = name.split(None, 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""
    return {"firstName": first, "lastName": last, "name": name}


def handle(client: EspoClient, text: str) -> DispatchResult:
    payload = _parse_add_contact(text)
    if not payload:
        return DispatchResult(
            False,
            'Could not parse. Example: "Add contact Jane Doe" or "Add Jane Doe as contact".',
        )
    created = client.post("Contact", json=payload)
    if isinstance(created, dict) and created.get("id"):
        return DispatchResult(True, f"Created Contact {created['id']}.", {"record": created})
    return DispatchResult(True, "Contact create submitted.", {"record": created})
