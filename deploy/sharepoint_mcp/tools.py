"""SharePoint MCP tool handlers — shared by stdio and HTTP transports."""

from __future__ import annotations

import json
import os
from typing import Any

from hermes_integrations.ms365_client import MS365ClientError
from hermes_integrations.sharepoint_client import SharePointClient

_client: SharePointClient | None = None


def _get_client() -> SharePointClient:
    global _client
    if _client is None:
        _client = SharePointClient()
    return _client


def _text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, indent=2, default=str)


def run_ping(_args: dict[str, Any]) -> str:
    return _get_client().ping()


def run_list_sites(args: dict[str, Any]) -> str:
    query = str(args.get("query") or "*")
    limit = int(args.get("limit") or 50)
    sites = _get_client().list_sites(query, limit=limit)
    rows = [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "displayName": s.get("displayName"),
            "webUrl": s.get("webUrl"),
        }
        for s in sites
    ]
    return _text({"query": query, "count": len(rows), "sites": rows})


def run_get_site(args: dict[str, Any]) -> str:
    url = (args.get("site_url") or "").strip() or None
    site = _get_client().get_site(url)
    return _text(
        {
            "id": site.get("id"),
            "name": site.get("name"),
            "displayName": site.get("displayName"),
            "webUrl": site.get("webUrl"),
        }
    )


def run_list_libraries(_args: dict[str, Any]) -> str:
    drives = _get_client().list_drives()
    rows = [
        {
            "id": d.get("id"),
            "name": d.get("name"),
            "webUrl": d.get("webUrl"),
            "driveType": d.get("driveType"),
        }
        for d in drives
    ]
    return _text(rows)


def run_list_folder(args: dict[str, Any]) -> str:
    path = str(args.get("path") or "/")
    drive_id = (args.get("drive_id") or "").strip() or None
    items = _get_client().list_folder(path, drive_id=drive_id)
    rows = []
    for item in items:
        rows.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "webUrl": item.get("webUrl"),
                "folder": bool(item.get("folder")),
                "size": item.get("size"),
                "lastModifiedDateTime": item.get("lastModifiedDateTime"),
            }
        )
    return _text(rows)


def run_search_knowledge(args: dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    limit = int(args.get("limit") or 25)
    hits = _get_client().search_files(query, limit=limit)
    rows = []
    for item in hits:
        rows.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "webUrl": item.get("webUrl"),
                "path": (item.get("parentReference") or {}).get("path"),
                "lastModifiedDateTime": item.get("lastModifiedDateTime"),
            }
        )
    return _text({"query": query, "count": len(rows), "results": rows})


def run_read_document(args: dict[str, Any]) -> str:
    item_id = str(args.get("item_id") or "").strip()
    drive_id = (args.get("drive_id") or "").strip() or None
    text = _get_client().read_item_text(item_id, drive_id=drive_id)
    return text


HANDLERS: dict[str, Any] = {
    "ping": run_ping,
    "list_sites": run_list_sites,
    "get_site_info": run_get_site,
    "list_libraries": run_list_libraries,
    "list_folder": run_list_folder,
    "search_knowledge": run_search_knowledge,
    "read_document": run_read_document,
}

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "ping",
        "description": "Verify SharePoint Graph auth and default site connectivity.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_sites",
        "description": (
            "Search SharePoint sites in the tenant — use during consolidation to inventory "
            "sites before merging into RSG-Knowledge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search text (use * for broad inventory)",
                    "default": "*",
                },
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "get_site_info",
        "description": (
            "Resolve a SharePoint site URL to its Graph id and web URL. "
            "Uses SHAREPOINT_SITE_URL when site_url is omitted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "site_url": {
                    "type": "string",
                    "description": "Full site URL, e.g. https://tenant.sharepoint.com/sites/RSG-Knowledge",
                }
            },
        },
    },
    {
        "name": "list_libraries",
        "description": "List document libraries (drives) on the configured SharePoint site.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_folder",
        "description": "List files and folders under a path in the default document library.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Folder path from library root, e.g. /SOPs/Personal-Lines",
                    "default": "/",
                },
                "drive_id": {
                    "type": "string",
                    "description": "Optional drive id from list_libraries",
                },
            },
        },
    },
    {
        "name": "search_knowledge",
        "description": (
            "Search agency knowledge files (SOPs, carrier guides, training) on the "
            "configured SharePoint site. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "limit": {"type": "integer", "description": "Max results (default 25)", "default": 25},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Read a plain-text or markdown file from SharePoint by drive item id "
            "(from list_folder or search_knowledge). Max size controlled by "
            "SHAREPOINT_MAX_READ_BYTES (default 512KB)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "Drive item id"},
                "drive_id": {"type": "string", "description": "Optional drive id"},
            },
            "required": ["item_id"],
        },
    },
]


def call_tool(name: str, args: dict[str, Any]) -> str:
    handler = HANDLERS.get(name)
    if handler is None:
        raise KeyError(name)
    try:
        return handler(args or {})
    except MS365ClientError as exc:
        return f"SharePoint error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"
