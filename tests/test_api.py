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
    api_mod._supa = None
    yield
    api_mod._espo = None
    api_mod._dispatcher = None
    api_mod._supa = None


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
        # DispatchRequest allows optional fields; empty body is rejected as empty command.
        assert resp.status_code == 400

    @patch("hermes.api._get_dispatcher")
    @patch("hermes.api._get_espo")
    def test_server_error(self, mock_espo, mock_dispatcher, client) -> None:
        mock_dispatcher.return_value.dispatch.side_effect = RuntimeError("boom")
        resp = client.post("/dispatch", json={"command": "ping"})
        assert resp.status_code == 500


class TestDashboardDispatch:
    @patch("hermes.api._get_supa")
    def test_dashboard_dispatch_queues_crm_write(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        mock_get_supa.return_value = supa
        supa.insert.return_value = {"id": "crm-q-1"}

        resp = client.post(
            "/api/hermes/dispatch",
            json={
                "crm_write": {
                    "entity_type": "Task",
                    "entity_id": "task-1",
                    "created_by_role": "dashboard",
                    "priority": 1,
                    "payload": {
                        "action_type": "update_status",
                        "context": {"status": "Completed"},
                    },
                }
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["queue_name"] == "crm_write_queue"
        assert data["task_id"] == "crm-q-1"

    @patch("hermes.api._get_supa")
    def test_dashboard_dispatch_queues_openclaw_task(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        mock_get_supa.return_value = supa
        supa.insert.return_value = {"id": "oc-q-1"}

        resp = client.post(
            "/api/hermes/dispatch",
            json={
                "ai_enrichment": {
                    "task_type": "crm-manager",
                    "payload": {
                        "client_id": "test-client-003",
                        "renewal_id": "test-renewal-003",
                        "naics_code": "236220",
                        "sic_code": "1542",
                        "industry": "Commercial Construction",
                        "state": "GA",
                    },
                    "requested_by": "dashboard",
                    "priority": 2,
                    "notify_slack": True,
                }
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["queue_name"] == "openclaw_task_queue"
        assert data["task_id"] == "oc-q-1"

    @patch("hermes.api._get_supa")
    def test_sync_health_payload(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        mock_get_supa.return_value = supa
        supa.select.side_effect = [
            [{"id": "1"}],  # crm pending
            [],  # crm processing
            [{"id": "2"}, {"id": "3"}],  # crm failed
            [],  # openclaw pending
            [{"id": "4"}],  # openclaw processing
            [],  # openclaw failed
            [{"id": "run-1", "status": "success", "workflow_name": "insured_to_account", "finished_at": "2026-01-01T00:00:00Z"}],
        ]

        resp = client.get("/api/hermes/sync-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["crm_write_queue"]["pending"] == 1
        assert data["crm_write_queue"]["failed"] == 2
        assert data["openclaw_task_queue"]["processing"] == 1
        assert data["latest_sync_run"]["id"] == "run-1"

    @patch("hermes.api._get_supa")
    def test_openclaw_enqueue_route(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        mock_get_supa.return_value = supa
        supa.insert.return_value = {"id": "oc-direct-1"}

        resp = client.post(
            "/api/hermes/openclaw/enqueue",
            json={
                "task_type": "appetite-analyzer",
                "payload": {
                    "naics_code": "236220",
                    "sic_code": "1542",
                    "industry": "Commercial Construction",
                    "state": "GA",
                },
                "priority": 1,
            },
        )
        assert resp.status_code == 202
        assert resp.json()["task_id"] == "oc-direct-1"

    @patch("hermes.api._get_supa")
    def test_openclaw_enqueue_rejects_invalid_payload(self, mock_get_supa, client) -> None:
        mock_get_supa.return_value = MagicMock()

        resp = client.post(
            "/api/hermes/openclaw/enqueue",
            json={
                "task_type": "crm-manager",
                "payload": {},
                "priority": 1,
            },
        )
        assert resp.status_code == 400


def test_requires_confirmation_for_write_like_commands() -> None:
    from hermes.api import requires_confirmation

    assert requires_confirmation('create Task name="Call client" status=Inbox')
    assert requires_confirmation("add Lead firstName=Jane lastName=Doe")
    assert requires_confirmation('move opportunity opp-1 to "Quoted"')
    assert requires_confirmation("intake met Jane at chamber lunch")
    assert requires_confirmation("merge contact abc into def")


def test_read_commands_do_not_require_confirmation() -> None:
    from hermes.api import requires_confirmation

    assert not requires_confirmation("find Acme")
    assert not requires_confirmation("renewal audit")
    assert not requires_confirmation("stale leads")


def test_openapi_schema_advertises_command_endpoint() -> None:
    from hermes.api import openapi_schema

    schema = openapi_schema()
    assert schema["openapi"].startswith("3.")
    assert "/command" in schema["paths"]
    assert "/api/hermes/openclaw/enqueue" in schema["paths"]
