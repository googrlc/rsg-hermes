"""Build and run the SharePoint MCP server (stdio or streamable HTTP)."""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from deploy.sharepoint_mcp.tools import call_tool

_server: MCPServer | None = None


def build_server() -> MCPServer:
    global _server
    if _server is not None:
        return _server

    mcp = MCPServer(
        "rsg-sharepoint",
        instructions=(
            "Read-only SharePoint knowledge tools for RSG agency SOPs, carrier guides, "
            "and training docs. Uses Microsoft Graph with MS365_* credentials and "
            "SHAREPOINT_SITE_URL for the default site."
        ),
    )

    @mcp.tool()
    def ping() -> str:
        """Verify SharePoint Graph auth and default site connectivity."""
        return call_tool("ping", {})

    @mcp.tool()
    def get_site_info(site_url: str = "") -> str:
        """Resolve a SharePoint site URL to its Graph id and web URL."""
        args = {"site_url": site_url} if site_url else {}
        return call_tool("get_site_info", args)

    @mcp.tool()
    def list_libraries() -> str:
        """List document libraries on the configured SharePoint site."""
        return call_tool("list_libraries", {})

    @mcp.tool()
    def list_folder(path: str = "/", drive_id: str = "") -> str:
        """List files and folders under a path in the default document library."""
        args: dict[str, str] = {"path": path}
        if drive_id:
            args["drive_id"] = drive_id
        return call_tool("list_folder", args)

    @mcp.tool()
    def search_knowledge(query: str, limit: int = 25) -> str:
        """Search agency knowledge files on SharePoint. Read-only."""
        return call_tool("search_knowledge", {"query": query, "limit": limit})

    @mcp.tool()
    def read_document(item_id: str, drive_id: str = "") -> str:
        """Read a plain-text or markdown SharePoint file by drive item id."""
        args: dict[str, str] = {"item_id": item_id}
        if drive_id:
            args["drive_id"] = drive_id
        return call_tool("read_document", args)

    _server = mcp
    return mcp


def run_stdio() -> None:
    build_server().run(transport="stdio")


def run_http() -> None:
    host = os.environ.get("SHAREPOINT_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("SHAREPOINT_MCP_PORT", "8082"))
    build_server().run(
        transport="streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
    )
