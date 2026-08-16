"""SharePoint Graph client unit tests (mocked HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes_integrations.ms365_client import MS365ClientError
from hermes_integrations.sharepoint_client import SharePointClient


def test_parse_site_url() -> None:
    host, path = SharePointClient.parse_site_url(
        "https://contoso.sharepoint.com/sites/RSG-Knowledge"
    )
    assert host == "contoso.sharepoint.com"
    assert path == "/sites/RSG-Knowledge"


def test_parse_site_url_rejects_bad_url() -> None:
    with pytest.raises(MS365ClientError):
        SharePointClient.parse_site_url("not-a-url")


@patch.dict(
    "os.environ",
    {
        "MS365_TENANT_ID": "t",
        "MS365_CLIENT_ID": "c",
        "MS365_CLIENT_SECRET": "s",
    },
)
def test_list_sites_calls_graph() -> None:
    client = SharePointClient()
    with patch.object(
        client,
        "_request",
        return_value={"value": [{"webUrl": "https://x/sites/A", "displayName": "A"}]},
    ) as req:
        sites = client.list_sites("RSG", limit=10)
    assert len(sites) == 1
    req.assert_called_once()
    assert req.call_args[0][1] == "/sites"


@patch.dict(
    "os.environ",
    {
        "MS365_TENANT_ID": "t",
        "MS365_CLIENT_ID": "c",
        "MS365_CLIENT_SECRET": "s",
        "SHAREPOINT_SITE_URL": "https://contoso.sharepoint.com/sites/RSG",
    },
)
def test_get_site_calls_graph() -> None:
    client = SharePointClient()
    with patch.object(client, "_request", return_value={"id": "site-1", "webUrl": "https://x"}) as req:
        site = client.get_site()
    assert site["id"] == "site-1"
    req.assert_called_once_with(
        "GET",
        "/sites/contoso.sharepoint.com:/sites/RSG",
    )


@patch.dict(
    "os.environ",
    {
        "MS365_TENANT_ID": "t",
        "MS365_CLIENT_ID": "c",
        "MS365_CLIENT_SECRET": "s",
        "SHAREPOINT_SITE_URL": "https://contoso.sharepoint.com/sites/RSG",
    },
)
def test_list_drives_uses_default_site() -> None:
    client = SharePointClient()
    client._default_site = {"id": "site-1"}  # noqa: SLF001
    with patch.object(
        client,
        "_request",
        return_value={"value": [{"id": "d1", "name": "Documents"}]},
    ) as req:
        drives = client.list_drives()
    assert drives[0]["name"] == "Documents"
    req.assert_called_once_with("GET", "/sites/site-1/drives")


@patch.dict(
    "os.environ",
    {
        "MS365_TENANT_ID": "t",
        "MS365_CLIENT_ID": "c",
        "MS365_CLIENT_SECRET": "s",
    },
)
def test_ping_without_site_url() -> None:
    client = SharePointClient()
    with patch.object(client, "_authenticate"):
        msg = client.ping()
    assert "SHAREPOINT_SITE_URL not set" in msg
