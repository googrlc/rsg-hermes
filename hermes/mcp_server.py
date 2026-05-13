"""EspoCRM MCP server — exposes EspoClient operations as Claude tools."""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from hermes.core.client import EspoClient, EspoClientError

load_dotenv()

mcp = FastMCP(
    "EspoCRM",
    instructions=(
        "Tools for reading and writing data in EspoCRM. "
        "Entity names are PascalCase (e.g. Contact, Account, Lead, Opportunity, Meeting, Task). "
        "Always confirm destructive operations (delete/update) before executing."
    ),
)


def _client() -> EspoClient:
    return EspoClient()


def _ok(data: Any) -> str:
    return json.dumps(data, default=str)


def _err(e: Exception) -> str:
    return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

@mcp.tool()
def ping() -> str:
    """Verify the EspoCRM connection and return the current API user info."""
    try:
        return _ok(_client().ping())
    except EspoClientError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

@mcp.tool()
def get_record(entity: str, record_id: str) -> str:
    """Fetch a single EspoCRM record by entity type and ID.

    Args:
        entity: PascalCase entity name (e.g. Contact, Account, Lead).
        record_id: The EspoCRM record ID.
    """
    try:
        return _ok(_client().get(f"{entity}/{record_id}"))
    except EspoClientError as e:
        return _err(e)


@mcp.tool()
def list_records(
    entity: str,
    max_size: int = 20,
    offset: int = 0,
    order_by: str = "createdAt",
    order: str = "desc",
    select: str | None = None,
    where: str | None = None,
) -> str:
    """List EspoCRM records with optional filtering and ordering.

    Args:
        entity: PascalCase entity name (e.g. Contact, Account, Lead).
        max_size: Number of records to return (default 20, max 200).
        offset: Pagination offset.
        order_by: Field to sort by (default createdAt).
        order: Sort direction — "asc" or "desc".
        select: Comma-separated field names to return (e.g. "id,name,emailAddress").
        where: JSON-encoded EspoCRM where clause array (advanced filtering).
    """
    try:
        params: dict[str, Any] = {
            "maxSize": max_size,
            "offset": offset,
            "orderBy": order_by,
            "order": order,
        }
        if select:
            params["select"] = select
        if where:
            params["where"] = json.loads(where)
        return _ok(_client().get(entity, params=params))
    except (EspoClientError, json.JSONDecodeError) as e:
        return _err(e)


@mcp.tool()
def search_records(
    entity: str,
    query: str,
    max_size: int = 10,
    select: str = "id,name",
    search_fields: str = "name",
) -> str:
    """Search EspoCRM records by a text query across one or more fields.

    Args:
        entity: PascalCase entity name (e.g. Contact, Account, Lead).
        query: Text to search for.
        max_size: Maximum results to return.
        select: Comma-separated fields to include in results.
        search_fields: Comma-separated fields to search within (default: name).
    """
    try:
        fields = [f.strip() for f in search_fields.split(",") if f.strip()]
        results = _client().search(entity, query, max_size=max_size, select=select, fields=fields)
        return _ok(results)
    except EspoClientError as e:
        return _err(e)


@mcp.tool()
def find_by_field(
    entity: str,
    field: str,
    value: str,
    select: str = "id,name",
) -> str:
    """Find a single EspoCRM record that exactly matches a field value.

    Args:
        entity: PascalCase entity name.
        field: The field to match on (e.g. emailAddress, phoneNumber).
        value: The exact value to match.
        select: Comma-separated fields to return.
    """
    try:
        result = _client().find_one_by_field(entity, field, value, select=select)
        return _ok(result)
    except EspoClientError as e:
        return _err(e)


@mcp.tool()
def get_metadata(key: str | None = None) -> str:
    """Retrieve EspoCRM metadata — entity definitions, field types, and relationships.

    Args:
        key: Optional top-level metadata key (e.g. "entityDefs", "fields").
             Omit to return full metadata.
    """
    try:
        return _ok(_client().get_metadata(key))
    except EspoClientError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

@mcp.tool()
def create_record(entity: str, payload: str) -> str:
    """Create a new EspoCRM record.

    Args:
        entity: PascalCase entity name (e.g. Contact, Account, Lead).
        payload: JSON object of field name → value pairs.
    """
    try:
        data = json.loads(payload)
        return _ok(_client().create(entity, data))
    except (EspoClientError, json.JSONDecodeError) as e:
        return _err(e)


@mcp.tool()
def update_record(entity: str, record_id: str, payload: str) -> str:
    """Update an existing EspoCRM record.

    Args:
        entity: PascalCase entity name.
        record_id: The EspoCRM record ID.
        payload: JSON object of fields to update.
    """
    try:
        data = json.loads(payload)
        return _ok(_client().update(entity, record_id, data))
    except (EspoClientError, json.JSONDecodeError) as e:
        return _err(e)


@mcp.tool()
def delete_record(entity: str, record_id: str) -> str:
    """Delete an EspoCRM record by ID.

    Args:
        entity: PascalCase entity name.
        record_id: The EspoCRM record ID to delete.
    """
    try:
        return _ok(_client().delete(f"{entity}/{record_id}"))
    except EspoClientError as e:
        return _err(e)


@mcp.tool()
def upsert_contact(payload: str) -> str:
    """Create or update a Contact, matching on emailAddress then name.

    Args:
        payload: JSON object with contact fields. Include emailAddress for reliable matching.
                 Common fields: firstName, lastName, emailAddress, phoneNumber, accountId.
    """
    try:
        data = json.loads(payload)
        return _ok(_client().upsert_contact(data))
    except (EspoClientError, json.JSONDecodeError) as e:
        return _err(e)


@mcp.tool()
def upsert_account(payload: str) -> str:
    """Create or update an Account, matching on FEIN then account name.

    Args:
        payload: JSON object with account fields.
                 Common fields: name, fein, phoneNumber, billingAddressStreet,
                 billingAddressCity, billingAddressState, billingAddressPostalCode.
    """
    try:
        data = json.loads(payload)
        return _ok(_client().upsert_account(data))
    except (EspoClientError, json.JSONDecodeError) as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Relationship operations
# ---------------------------------------------------------------------------

@mcp.tool()
def get_related_records(entity: str, record_id: str, relationship: str, max_size: int = 20) -> str:
    """Fetch records related to a given record via a named relationship.

    Args:
        entity: PascalCase entity name (e.g. Account).
        record_id: The parent record ID.
        relationship: Relationship name (e.g. contacts, opportunities, meetings).
        max_size: Maximum related records to return.
    """
    try:
        return _ok(_client().get(
            f"{entity}/{record_id}/{relationship}",
            params={"maxSize": max_size},
        ))
    except EspoClientError as e:
        return _err(e)


@mcp.tool()
def link_records(entity: str, record_id: str, relationship: str, foreign_id: str) -> str:
    """Link two EspoCRM records via a relationship.

    Args:
        entity: PascalCase entity name of the parent record.
        record_id: The parent record ID.
        relationship: Relationship name (e.g. contacts, documents).
        foreign_id: The ID of the record to link.
    """
    try:
        return _ok(_client().post(
            f"{entity}/{record_id}/{relationship}",
            json={"id": foreign_id},
        ))
    except EspoClientError as e:
        return _err(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
