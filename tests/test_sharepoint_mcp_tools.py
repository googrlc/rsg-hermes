"""SharePoint MCP tool wiring tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from deploy.sharepoint_mcp.tools import HANDLERS, MCP_TOOLS, call_tool


def test_every_declared_tool_has_handler() -> None:
    names = {t["name"] for t in MCP_TOOLS}
    assert names == set(HANDLERS.keys())


@pytest.mark.parametrize("tool", [t["name"] for t in MCP_TOOLS])
def test_handlers_are_callable(tool: str) -> None:
    with patch("deploy.sharepoint_mcp.tools._get_client") as mock_client:
        mock_client.return_value.ping.return_value = "ok"
        mock_client.return_value.list_sites.return_value = []
        mock_client.return_value.get_site.return_value = {"id": "1"}
        mock_client.return_value.list_drives.return_value = []
        mock_client.return_value.list_folder.return_value = []
        mock_client.return_value.search_files.return_value = []
        mock_client.return_value.read_item_text.return_value = "body"
        if tool == "ping":
            call_tool(tool, {})
        elif tool == "list_sites":
            call_tool(tool, {"query": "RSG"})
        elif tool == "get_site_info":
            call_tool(tool, {})
        elif tool == "list_libraries":
            call_tool(tool, {})
        elif tool == "list_folder":
            call_tool(tool, {"path": "/"})
        elif tool == "search_knowledge":
            call_tool(tool, {"query": "COI"})
        elif tool == "read_document":
            call_tool(tool, {"item_id": "item-1"})


def test_unknown_tool_raises() -> None:
    with pytest.raises(KeyError):
        call_tool("missing", {})
