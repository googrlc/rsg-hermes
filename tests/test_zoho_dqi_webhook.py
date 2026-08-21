"""Tests for Zoho → Cursor DQI webhook relay."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app
from hermes.webhooks.cursor_dqi import _cursor_webhook_config
from hermes.webhooks.zoho_dqi import build_cursor_payload


@pytest.fixture(autouse=True)
def _reset_singletons():
    from hermes_app import deps

    deps.reset_clients()
    yield
    deps.reset_clients()


@pytest.fixture
def client():
    return TestClient(app)


class TestCursorWebhookConfig:
    def test_strips_bearer_prefix(self, monkeypatch):
        monkeypatch.setenv("CURSOR_AUTOMATION_WEBHOOK_URL", "https://api2.cursor.sh/automations/webhook/abc")
        monkeypatch.setenv("CURSOR_AUTOMATION_WEBHOOK_KEY", "Bearer crsr_testkey")
        url, key = _cursor_webhook_config()
        assert url.endswith("/abc")
        assert key == "crsr_testkey"
        assert not key.lower().startswith("bearer")


class TestBuildCursorPayload:
    @patch("hermes.webhooks.zoho_dqi._load_renewal_record")
    def test_resolves_renewal_id(self, mock_load):
        mock_load.return_value = {
            "id": "999",
            "Policy_Number": "990414352",
            "Client_Name": "Steven Prak",
            "Line_of_Business": "Personal Auto",
        }
        payload = build_cursor_payload({"renewal_id": "999"})
        assert payload["policy_number"] == "990414352"
        assert payload["client_name"] == "Steven Prak"
        assert payload["line_of_business"] == "Personal Auto"
        assert payload["zoho_record_id"] == "999"
        assert payload["source"] == "zoho_renewals"

    def test_rejects_unresolved_merge_syntax(self):
        with pytest.raises(ValueError, match="merge field did not resolve"):
            build_cursor_payload({"policy_number": "${Renewals.Policy_Number}"})

    def test_accepts_direct_policy_number(self):
        payload = build_cursor_payload(
            {"policy_number": "990414352", "client_name": "Steven Prak"}
        )
        assert payload["policy_number"] == "990414352"


class TestZohoDqiWebhookEndpoint:
    def test_requires_webhook_secret(self, client, monkeypatch):
        monkeypatch.setenv("SERVICE_WEBHOOK_SECRET", "hook-secret")
        resp = client.post("/api/webhooks/zoho/dqi-investigation", json={"policy_number": "1"})
        assert resp.status_code == 401

    def test_missing_server_secret_is_503(self, client, monkeypatch):
        monkeypatch.delenv("SERVICE_WEBHOOK_SECRET", raising=False)
        resp = client.post(
            "/api/webhooks/zoho/dqi-investigation",
            json={"policy_number": "1"},
            headers={"Authorization": "Bearer anything"},
        )
        assert resp.status_code == 503

    @patch("hermes.webhooks.zoho_dqi.trigger_cursor_dqi_investigation")
    def test_forwards_policy_number(self, mock_trigger, client, monkeypatch):
        monkeypatch.setenv("SERVICE_WEBHOOK_SECRET", "hook-secret")
        mock_trigger.return_value = {"status": "accepted"}

        resp = client.post(
            "/api/webhooks/zoho/dqi-investigation",
            json={"policy_number": "990414352", "client_name": "Steven Prak"},
            headers={"Authorization": "Bearer hook-secret"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["policy_number"] == "990414352"
        mock_trigger.assert_called_once()
        sent = mock_trigger.call_args[0][0]
        assert sent["policy_number"] == "990414352"
        assert sent["client_name"] == "Steven Prak"

    @patch("hermes.webhooks.zoho_dqi.trigger_cursor_dqi_investigation")
    @patch("hermes.webhooks.zoho_dqi._load_renewal_record")
    def test_renewal_id_path(self, mock_load, mock_trigger, client, monkeypatch):
        monkeypatch.setenv("SERVICE_WEBHOOK_SECRET", "hook-secret")
        mock_load.return_value = {
            "Policy_Number": "990414352",
            "Client_Name": "Steven Prak",
            "Line_of_Business": "Auto",
        }
        mock_trigger.return_value = {"status": "accepted"}

        resp = client.post(
            "/api/webhooks/zoho/dqi-investigation",
            json={"renewal_id": "7529682000001234567"},
            headers={"X-Webhook-Secret": "hook-secret"},
        )
        assert resp.status_code == 200
        mock_trigger.assert_called_once()
        assert mock_trigger.call_args[0][0]["policy_number"] == "990414352"
