"""Zoho Desk REST client construction (no live API calls)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from hermes_integrations.zoho_desk_client import ZohoDeskClient, ZohoDeskClientError, reset_client


def test_raises_without_credentials():
    reset_client()
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ZohoDeskClientError, match="ZOHO_CLIENT_ID"):
            ZohoDeskClient()


def test_oauth_without_org_id_is_allowed_for_org_discovery():
    reset_client()
    env = {
        "ZOHO_CLIENT_ID": "cid",
        "ZOHO_CLIENT_SECRET": "secret",
        "ZOHO_REFRESH_TOKEN": "refresh",
    }
    with patch.dict(os.environ, env, clear=True):
        client = ZohoDeskClient()
        assert client.org_id == ""
        assert client.client_id == "cid"


def test_accepts_desk_overrides_and_shared_zoho_oauth():
    reset_client()
    env = {
        "ZOHO_DESK_ORG_ID": "123",
        "ZOHO_CLIENT_ID": "cid",
        "ZOHO_CLIENT_SECRET": "secret",
        "ZOHO_REFRESH_TOKEN": "refresh",
        "ZOHO_DATA_CENTER": "com",
    }
    with patch.dict(os.environ, env, clear=True):
        client = ZohoDeskClient()
        assert client.org_id == "123"
        assert client.api_base == "https://desk.zoho.com/api/v1"
        assert client.client_id == "cid"


def test_rows_reads_teams_list_payload():
    rows = ZohoDeskClient._rows({"teams": [{"id": "1", "name": "Certificates"}]})
    assert rows == [{"id": "1", "name": "Certificates"}]
