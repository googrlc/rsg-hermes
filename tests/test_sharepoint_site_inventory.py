"""Tests for sharepoint_site_inventory.py"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# Import after path setup inside script — test helpers directly
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "rsg-hermes-core"))
sys.path.insert(0, str(REPO))

from scripts.sharepoint_site_inventory import _gather, _render_markdown  # noqa: E402


@patch.dict(
    "os.environ",
    {
        "MS365_TENANT_ID": "t",
        "MS365_CLIENT_ID": "c",
        "MS365_CLIENT_SECRET": "s",
    },
)
def test_gather_shallow() -> None:
    with patch("hermes_integrations.sharepoint_client.SharePointClient.list_sites") as ls:
        ls.return_value = [
            {
                "displayName": "Old Training",
                "webUrl": "https://x.sharepoint.com/sites/Old-Training",
                "id": "site-1",
            }
        ]
        rows = _gather("*", deep=False, limit=10)
    assert len(rows) == 1
    assert rows[0]["displayName"] == "Old Training"
    assert rows[0]["libraries"] == []


def test_render_markdown_contains_table() -> None:
    md = _render_markdown(
        [{"displayName": "A", "webUrl": "https://a", "id": "1", "libraries": [], "root_items_sample": []}],
        query="*",
        deep=False,
    )
    assert "SharePoint site inventory" in md
    assert "Do not create RSG-Knowledge" in md
    assert "| Display name |" in md
