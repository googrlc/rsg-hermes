"""Merge duplicate CRM records via the EspoCRM Action API.

Supported formats:
  merge contact <id1> into <id2>
  merge account <id1> into <id2>
  merge <entity> <id1> <id2>        (first is discarded, second is kept)
  <Name> (id: <id1>) ... <Name> (id: <id2>) ... can be merged
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient

log = logging.getLogger(__name__)

_ENTITY_ALIASES: dict[str, str] = {
    "contact": "Contact",
    "contacts": "Contact",
    "account": "Account",
    "accounts": "Account",
    "lead": "Lead",
    "leads": "Lead",
    "opportunity": "Opportunity",
    "opportunities": "Opportunity",
}

_MERGE_PATTERN = re.compile(
    r"merge\s+(\w+)\s+([a-f0-9]{5,})\s+(?:into\s+)?([a-f0-9]{5,})",
    re.I,
)

_ID_EXTRACTOR = re.compile(r"\bid:\s*([a-f0-9]{5,})\b", re.I)

_NATURAL_MERGE = re.compile(
    r"(?:can\s+be\s+merged|merge\s+(?:them|these|together|duplicates?))",
    re.I,
)

_ENTITY_HINT = re.compile(
    r"\b(contacts?|accounts?|leads?|opportunit(?:y|ies))\b",
    re.I,
)


def _resolve_entity(hint: str) -> str | None:
    return _ENTITY_ALIASES.get(hint.strip().lower())


def _merge_via_api(
    client: "EspoClient",
    entity_type: str,
    source_id: str,
    target_id: str,
) -> dict:
    """Call EspoCRM Action merge: keeps target_id, discards source_id."""
    payload = {
        "entityType": entity_type,
        "action": "merge",
        "id": target_id,
        "data": {
            "sourceIdList": [source_id],
        },
    }
    return client.post("Action", json=payload)


def handle(client: "EspoClient", text: str) -> DispatchResult:
    match = _MERGE_PATTERN.search(text)
    if match:
        entity_hint, source_id, target_id = match.group(1), match.group(2), match.group(3)
        entity_type = _resolve_entity(entity_hint)
        if not entity_type:
            return DispatchResult(
                False,
                f"Unknown entity type '{entity_hint}'. "
                f"Supported: {', '.join(sorted(_ENTITY_ALIASES.keys()))}",
            )
        return _execute_merge(client, entity_type, source_id, target_id)

    ids = _ID_EXTRACTOR.findall(text)
    if len(ids) >= 2 and _NATURAL_MERGE.search(text):
        entity_hint_match = _ENTITY_HINT.search(text)
        entity_type = _resolve_entity(entity_hint_match.group(1)) if entity_hint_match else None
        if not entity_type:
            entity_type = _infer_entity_type(client, ids[0])
        if not entity_type:
            return DispatchResult(
                False,
                f"Found IDs {ids[0]} and {ids[1]} but couldn't determine the entity type. "
                "Try: `merge contact <id1> into <id2>`",
            )
        source_id, target_id = ids[0], ids[1]
        return _execute_merge(client, entity_type, source_id, target_id)

    return DispatchResult(
        False,
        "Could not parse merge command. Try:\n"
        "• `merge contact <id1> into <id2>`\n"
        "• `merge account <id1> into <id2>`\n"
        "• Or mention two record IDs with 'can be merged'",
    )


def _infer_entity_type(client: "EspoClient", record_id: str) -> str | None:
    for entity in ("Contact", "Account", "Lead", "Opportunity"):
        try:
            result = client.get(f"{entity}/{record_id}", params={"select": "id"})
            if isinstance(result, dict) and result.get("id"):
                return entity
        except Exception:
            continue
    return None


def _execute_merge(
    client: "EspoClient",
    entity_type: str,
    source_id: str,
    target_id: str,
) -> DispatchResult:
    source_name = _get_record_name(client, entity_type, source_id)
    target_name = _get_record_name(client, entity_type, target_id)
    try:
        _merge_via_api(client, entity_type, source_id, target_id)
    except Exception as e:
        log.exception("Merge failed: %s %s -> %s", entity_type, source_id, target_id)
        return DispatchResult(
            False,
            f"Merge failed for {entity_type}: {e}\n"
            f"Source: {source_name} (`{source_id}`)\n"
            f"Target: {target_name} (`{target_id}`)",
        )

    return DispatchResult(
        True,
        f"Merged {entity_type} records.\n"
        f"• Kept: {target_name} (`{target_id}`)\n"
        f"• Discarded: {source_name} (`{source_id}`)\n"
        f"All related records from the discarded entry have been moved to the kept record.",
        data={
            "entity_type": entity_type,
            "source_id": source_id,
            "target_id": target_id,
        },
    )


def _get_record_name(client: "EspoClient", entity_type: str, record_id: str) -> str:
    try:
        result = client.get(f"{entity_type}/{record_id}", params={"select": "id,name"})
        if isinstance(result, dict):
            return str(result.get("name", record_id))
    except Exception:
        pass
    return record_id


def execute_approved_merge(
    client: "EspoClient",
    *,
    entity_type: str,
    source_id: str,
    target_id: str,
) -> dict:
    """Execute approved Espo merge action."""
    return _merge_via_api(client, entity_type, source_id, target_id)
