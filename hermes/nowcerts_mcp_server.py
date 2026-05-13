"""NowCerts MCP server — exposes NowCertsClient operations as Claude tools."""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError

load_dotenv()

mcp = FastMCP(
    "NowCerts",
    instructions=(
        "Tools for reading and writing data in NowCerts AMS. "
        "NowCerts manages insureds (clients/prospects) and their policies. "
        "Key identifiers: DatabaseId (insured or policy unique ID), "
        "Number (policy number). "
        "Always confirm writes before executing."
    ),
)


def _client() -> NowCertsClient:
    return NowCertsClient()


def _ok(data: Any) -> str:
    return json.dumps(data, default=str)


def _err(e: Exception) -> str:
    return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Insureds
# ---------------------------------------------------------------------------

@mcp.tool()
def search_insureds(query: str, max_results: int = 20) -> str:
    """Search NowCerts insureds by commercial name, first name, or last name.

    Args:
        query: Name text to search for.
        max_results: Maximum number of results to return (default 20).
    """
    try:
        return _ok(_client().search_insureds(query, top=max_results))
    except NowCertsClientError as e:
        return _err(e)


@mcp.tool()
def get_insured(database_id: str) -> str:
    """Fetch a single NowCerts insured record by DatabaseId.

    Args:
        database_id: The NowCerts insured DatabaseId.
    """
    try:
        return _ok(_client().get_insured(database_id))
    except NowCertsClientError as e:
        return _err(e)


@mcp.tool()
def list_insureds(
    max_results: int = 50,
    since: str | None = None,
) -> str:
    """List NowCerts insureds, optionally filtered by change date.

    Args:
        max_results: Number of insureds to return (default 50).
        since: ISO datetime string — only return records changed after this date
               (e.g. "2026-01-01T00:00:00").
    """
    try:
        records = _client().fetch_insureds(page_size=max_results, since=since, max_pages=1)
        return _ok(records)
    except NowCertsClientError as e:
        return _err(e)


@mcp.tool()
def create_insured(payload: str) -> str:
    """Create or update a NowCerts insured. Upserts on DatabaseId, CommercialName,
    or FirstName + LastName.

    Args:
        payload: JSON object with insured fields. Common fields:
                 CommercialName, FirstName, LastName, FEIN,
                 AddressLine1, City, State, ZipCode,
                 Phone, EmailAddress, ProducerName.
    """
    try:
        data = json.loads(payload)
        return _ok(_client().create_insured(data))
    except (NowCertsClientError, json.JSONDecodeError) as e:
        return _err(e)


@mcp.tool()
def create_insured_with_policies(payload: str) -> str:
    """Create or update a NowCerts insured together with their policies in one call.

    Args:
        payload: JSON object with insured fields plus optional "Policies" and
                 "Quotes" arrays. Example:
                 {
                   "CommercialName": "Acme LLC",
                   "Policies": [{"Number": "POL-001", "EffectiveDate": "2026-01-01", ...}]
                 }
    """
    try:
        data = json.loads(payload)
        return _ok(_client().create_insured_with_policies(data))
    except (NowCertsClientError, json.JSONDecodeError) as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

@mcp.tool()
def search_policies(query: str, max_results: int = 20) -> str:
    """Search NowCerts policies by policy number or insured name.

    Args:
        query: Policy number or insured name text to search for.
        max_results: Maximum number of results to return (default 20).
    """
    try:
        return _ok(_client().search_policies(query, top=max_results))
    except NowCertsClientError as e:
        return _err(e)


@mcp.tool()
def get_policies_for_insured(insured_id: str, max_results: int = 50) -> str:
    """Fetch all policies for a specific NowCerts insured.

    Args:
        insured_id: The NowCerts insured DatabaseId.
        max_results: Maximum number of policies to return (default 50).
    """
    try:
        return _ok(_client().get_policies_for_insured(insured_id, top=max_results))
    except NowCertsClientError as e:
        return _err(e)


@mcp.tool()
def list_policies(
    max_results: int = 50,
    since: str | None = None,
) -> str:
    """List NowCerts policies, optionally filtered by change date.

    Args:
        max_results: Number of policies to return (default 50).
        since: ISO datetime string — only return records changed after this date.
    """
    try:
        records = _client().fetch_policies(page_size=max_results, since=since, max_pages=1)
        return _ok(records)
    except NowCertsClientError as e:
        return _err(e)


@mcp.tool()
def insert_policy(payload: str) -> str:
    """Create or update a NowCerts policy.

    Args:
        payload: JSON object with policy fields. Common fields:
                 Number, EffectiveDate, ExpirationDate, Premium,
                 AgencyCommissionPercent, InsuredDatabaseId or InsuredName,
                 LineOfBusiness, CarrierName, PolicyType.
    """
    try:
        data = json.loads(payload)
        return _ok(_client().insert_policy(data))
    except (NowCertsClientError, json.JSONDecodeError) as e:
        return _err(e)


@mcp.tool()
def update_policy(database_id: str, payload: str) -> str:
    """Partially update a NowCerts policy. Only the fields provided are changed.

    Args:
        database_id: The NowCerts policy DatabaseId (required).
        payload: JSON object of fields to update (DatabaseId will be injected automatically).
    """
    try:
        data = json.loads(payload)
        data["DatabaseId"] = database_id
        return _ok(_client().update_policy(data))
    except (NowCertsClientError, json.JSONDecodeError) as e:
        return _err(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
