"""Natural-language style data entry: e.g. 'Add contact John Smith'."""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient


_READ_ONLY_FIELDS = {
    "id",
    "createdAt",
    "createdById",
    "createdByName",
    "modifiedAt",
    "modifiedById",
    "modifiedByName",
    "deleted",
}


def _entity_defs(client: "EspoClient") -> dict[str, Any]:
    metadata = client.get_metadata()
    if not isinstance(metadata, dict):
        return {}
    entity_defs = metadata.get("entityDefs")
    return entity_defs if isinstance(entity_defs, dict) else {}


def _resolve_entity(client: "EspoClient", entity_hint: str) -> tuple[str | None, dict[str, Any] | None]:
    entity_defs = _entity_defs(client)
    normalized = entity_hint.strip().lower()
    for entity, entity_def in entity_defs.items():
        if entity.lower() == normalized:
            return str(entity), entity_def if isinstance(entity_def, dict) else {}
    return None, None


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() in {"null", "none"}:
        return None
    return value


def _parse_key_values(parts: list[str]) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    for part in parts:
        if "=" not in part:
            return None
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            return None
        payload[key] = _parse_value(value)
    return payload


def _validate_payload(entity: str, entity_def: dict[str, Any] | None, payload: dict[str, Any]) -> str | None:
    fields = entity_def.get("fields") if isinstance(entity_def, dict) else None
    if not isinstance(fields, dict) or not fields:
        return None
    for field_name in payload:
        if field_name in _READ_ONLY_FIELDS:
            return f"Field `{field_name}` is read-only for {entity}."
        field_def = fields.get(field_name)
        if field_def is None and not _is_relation_write_key(entity_def, field_name):
            return f"Unknown field `{field_name}` for {entity}. Use exact EspoCRM field keys."
        if isinstance(field_def, dict) and field_def.get("readOnly"):
            return f"Field `{field_name}` is read-only for {entity}."
    return None


def _is_relation_write_key(entity_def: dict[str, Any] | None, field_name: str) -> bool:
    """Espo writes links as accountId/assignedUserId even when metadata names account/assignedUser."""
    if not isinstance(entity_def, dict):
        return False
    fields = entity_def.get("fields") or {}
    links = entity_def.get("links") or {}
    if not isinstance(fields, dict):
        fields = {}
    if not isinstance(links, dict):
        links = {}
    for suffix in ("Ids", "Names", "Id", "Name"):
        if not field_name.endswith(suffix):
            continue
        base = field_name[: -len(suffix)]
        base_def = fields.get(base)
        if base in links:
            return True
        if isinstance(base_def, dict) and str(base_def.get("type", "")).lower().startswith("link"):
            return True
    return False


def _current_user_id(client: "EspoClient") -> str | None:
    try:
        body = client.ping()
    except Exception:
        return None
    if isinstance(body, dict):
        user = body.get("user")
        if isinstance(user, dict):
            return str(user.get("id") or "") or None
        return str(body.get("id") or "") or None
    return None


def _apply_workflow_defaults(client: "EspoClient", entity: str, payload: dict[str, Any]) -> dict[str, Any]:
    updated = dict(payload)
    if entity in {"Task", "Lead", "Opportunity"} and "assignedUserId" not in updated:
        user_id = _current_user_id(client)
        if user_id:
            updated["assignedUserId"] = user_id
    if entity == "Lead" and "status" not in updated:
        updated["status"] = "New"
    if entity == "Opportunity" and "stage" not in updated:
        updated["stage"] = "New"
    return updated


def _parse_generic_write(text: str) -> tuple[str, str, str | None, dict[str, Any]] | None:
    try:
        parts = shlex.split(text)
    except ValueError:
        return None
    if len(parts) < 3:
        return None
    action = parts[0].lower()
    if action not in {"add", "create", "update"}:
        return None
    entity = parts[1]
    if action in {"add", "create"}:
        payload = _parse_key_values(parts[2:])
        if payload is None:
            return None
        return "create", entity, None, payload
    if len(parts) < 4:
        return None
    record_id = parts[2]
    payload = _parse_key_values(parts[3:])
    if payload is None:
        return None
    return action, entity, record_id, payload


def _parse_move_opportunity(text: str) -> tuple[str, str] | None:
    m = re.match(r"^\s*move\s+opportunit(?:y|ie)\s+(\S+)\s+to\s+(.+?)\s*$", text, re.I)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip().strip('"')


def _find_existing(client: "EspoClient", entity: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    candidate_fields: tuple[str, ...]
    if entity == "Lead":
        candidate_fields = ("emailAddress", "phoneNumber", "name")
    elif entity == "Opportunity":
        candidate_fields = ("name",)
    elif entity == "Account":
        candidate_fields = ("fein", "name")
    elif entity == "Contact":
        candidate_fields = ("emailAddress", "name")
    else:
        return None
    for field in candidate_fields:
        value = payload.get(field)
        if value in (None, ""):
            continue
        body = client.get(
            entity,
            params={
                "maxSize": 1,
                "select": "id,name",
                "where": [{"type": "equals", "attribute": field, "value": value}],
            },
        )
        rows = body.get("list", []) if isinstance(body, dict) else []
        if rows and isinstance(rows[0], dict) and rows[0].get("id"):
            return rows[0]
    return None


def _handle_generic_write(client: "EspoClient", text: str) -> DispatchResult | None:
    move = _parse_move_opportunity(text)
    if move:
        record_id, stage = move
        record = client.update("Opportunity", record_id, {"stage": stage})
        return DispatchResult(True, f"Moved Opportunity {record_id} to {stage}.", {"record": record if isinstance(record, dict) else {"result": record}})
    parsed = _parse_generic_write(text)
    if not parsed:
        return None
    action, entity_hint, record_id, payload = parsed
    entity, entity_def = _resolve_entity(client, entity_hint)
    if not entity:
        return DispatchResult(False, f"Unknown entity `{entity_hint}`. Use the exact EspoCRM entity name.")
    if not payload:
        return DispatchResult(False, "No fields supplied.")
    validation_error = _validate_payload(entity, entity_def, payload)
    if validation_error:
        return DispatchResult(False, validation_error)
    if action == "create":
        existing = _find_existing(client, entity, payload)
        if existing:
            existing_id = str(existing["id"])
            record = client.update(entity, existing_id, payload)
            return DispatchResult(
                True,
                f"Updated existing {entity} {existing_id}.",
                {"record": record if isinstance(record, dict) else {"result": record}, "dedupe": existing},
            )
        payload = _apply_workflow_defaults(client, entity, payload)
        validation_error = _validate_payload(entity, entity_def, payload)
        if validation_error:
            return DispatchResult(False, validation_error)
        record = client.create(entity, payload)
        record_id_out = record.get("id") if isinstance(record, dict) else None
        suffix = f" {record_id_out}" if record_id_out else ""
        return DispatchResult(True, f"Created {entity}{suffix}.", {"record": record if isinstance(record, dict) else {"result": record}})
    if not record_id:
        return DispatchResult(False, "Missing record id for update.")
    record = client.update(entity, record_id, payload)
    return DispatchResult(True, f"Updated {entity} {record_id}.", {"record": record if isinstance(record, dict) else {"result": record}})


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
    generic = _handle_generic_write(client, text)
    if generic:
        return generic
    parsed = _parse_add_contact(text)
    if not parsed:
        return DispatchResult(
            False,
            'Could not parse. Examples: "Add contact Jane Doe email jane@example.com to account Acme" '
            'or `create Task name="Call client" status=Inbox`.',
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
