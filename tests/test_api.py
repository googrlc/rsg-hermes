"""Tests for hermes/api.py FastAPI endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app
from hermes.core.dispatcher import DispatchResult


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset lazy singletons between tests."""
    import hermes.api as api_mod
    api_mod._espo = None
    api_mod._dispatcher = None
    yield
    api_mod._espo = None
    api_mod._dispatcher = None


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health(self, client) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestDispatch:
    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_ping(self, mock_espo, mock_dispatcher, client) -> None:
        mock_dispatcher.return_value.dispatch.return_value = DispatchResult(
            True, "Hermes is online and connected to CRM.",
        )
        resp = client.post("/dispatch", json={"command": "ping"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "online" in data["message"]

    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_sync_status(self, mock_espo, mock_dispatcher, client) -> None:
        mock_dispatcher.return_value.dispatch.return_value = DispatchResult(
            True, "No sync runs found yet.",
        )
        resp = client.post("/dispatch", json={"command": "sync status"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_dispatch_error(self, mock_espo, mock_dispatcher, client) -> None:
        mock_dispatcher.return_value.dispatch.return_value = DispatchResult(
            False, "No handler matched.",
        )
        resp = client.post("/dispatch", json={"command": "unknown command"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_empty_command(self, client) -> None:
        resp = client.post("/dispatch", json={"command": ""})
        assert resp.status_code == 400

    def test_missing_command(self, client) -> None:
        resp = client.post("/dispatch", json={})
        assert resp.status_code == 422

    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_server_error(self, mock_espo, mock_dispatcher, client) -> None:
        mock_dispatcher.return_value.dispatch.side_effect = RuntimeError("boom")
        resp = client.post("/dispatch", json={"command": "ping"})
        assert resp.status_code == 500
