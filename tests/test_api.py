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

    def test_hermes_ping_compatibility_route(self, client) -> None:
        resp = client.get("/hermes/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "Pong" in data["message"]


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
    assert "/api/intake" in schema["paths"]


# ---------------------------------------------------------------------------
# POST /api/intake (Phase 2 — rsg-intake pipeline)
# ---------------------------------------------------------------------------

_INTAKE_API_KEY = "test-intake-key-abc123"


@pytest.fixture
def intake_api_key(monkeypatch):
    monkeypatch.setenv("RSG_INTAKE_API_KEY", _INTAKE_API_KEY)
    yield _INTAKE_API_KEY


def _valid_intake_payload(**overrides):
    body = {
        "idempotency_key": "phase2-test-key-001",
        "source": "cowork",
        "agent": "gretchen",
        "captured_at": "2026-05-22T14:32:00-04:00",
        "client_identifier": "Sandra Centeno",
        "lob_code": "personal_auto",
        "transcript": "Sandra called about adding a vehicle to her auto policy.",
        "notes": "follow up about umbrella",
    }
    body.update(overrides)
    return body


class TestIntakeSubmit:
    def test_missing_api_key_returns_401(self, client, intake_api_key) -> None:
        resp = client.post("/api/intake", json=_valid_intake_payload())
        assert resp.status_code == 401

    def test_wrong_api_key_returns_401(self, client, intake_api_key) -> None:
        resp = client.post(
            "/api/intake",
            json=_valid_intake_payload(),
            headers={"X-RSG-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_unconfigured_server_returns_503(self, client, monkeypatch) -> None:
        monkeypatch.delenv("RSG_INTAKE_API_KEY", raising=False)
        resp = client.post(
            "/api/intake",
            json=_valid_intake_payload(),
            headers={"X-RSG-API-Key": "anything"},
        )
        assert resp.status_code == 503

    def test_missing_required_field_returns_422(self, client, intake_api_key) -> None:
        body = _valid_intake_payload()
        body.pop("source")
        resp = client.post(
            "/api/intake", json=body, headers={"X-RSG-API-Key": _INTAKE_API_KEY},
        )
        assert resp.status_code == 422

    def test_invalid_source_returns_422(self, client, intake_api_key) -> None:
        resp = client.post(
            "/api/intake",
            json=_valid_intake_payload(source="bogus"),
            headers={"X-RSG-API-Key": _INTAKE_API_KEY},
        )
        assert resp.status_code == 422

    def test_neither_transcript_nor_documents_returns_422(self, client, intake_api_key) -> None:
        body = _valid_intake_payload()
        body.pop("transcript")
        resp = client.post(
            "/api/intake", json=body, headers={"X-RSG-API-Key": _INTAKE_API_KEY},
        )
        assert resp.status_code == 422

    def test_documents_only_is_accepted(self, client, intake_api_key) -> None:
        with patch("hermes.api._get_supa") as mock_get_supa, patch(
            "hermes.integrations.intake_submissions.insert_submission"
        ) as mock_insert:
            mock_get_supa.return_value = MagicMock()
            mock_insert.return_value = (
                {"id": "sub-1", "status": "received", "created_at": "2026-05-22T18:32:01Z"},
                True,
            )
            body = _valid_intake_payload()
            body.pop("transcript")
            body["documents"] = [{"type": "drivers_license", "extracted_data": {"name": "X"}}]
            resp = client.post(
                "/api/intake", json=body, headers={"X-RSG-API-Key": _INTAKE_API_KEY},
            )
        assert resp.status_code == 202

    @patch("hermes.integrations.intake_submissions.insert_submission")
    @patch("hermes.api._get_supa")
    def test_valid_payload_returns_202_with_submission_id(
        self, mock_get_supa, mock_insert, client, intake_api_key,
    ) -> None:
        mock_get_supa.return_value = MagicMock()
        mock_insert.return_value = (
            {
                "id": "11111111-2222-3333-4444-555555555555",
                "status": "received",
                "created_at": "2026-05-22T18:32:01+00:00",
            },
            True,
        )
        resp = client.post(
            "/api/intake",
            json=_valid_intake_payload(),
            headers={"X-RSG-API-Key": _INTAKE_API_KEY},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["submission_id"] == "11111111-2222-3333-4444-555555555555"
        assert data["status"] == "received"
        assert data["idempotent_replay"] is False
        assert data["status_url"].endswith(
            "/api/intake/11111111-2222-3333-4444-555555555555/status"
        )

        # Confirm the projection: structured columns extracted, payload holds content
        args = mock_insert.call_args.kwargs
        assert args["idempotency_key"] == "phase2-test-key-001"
        assert args["source"] == "cowork"
        assert args["agent"] == "gretchen"
        assert args["intake_kind"] == "full_intake"
        assert args["client_identifier"] == "Sandra Centeno"
        assert args["lob_code"] == "personal_auto"
        assert "transcript" in args["payload"]
        assert "documents" in args["payload"]

    @patch("hermes.integrations.intake_submissions.insert_submission")
    @patch("hermes.api._get_supa")
    def test_idempotent_replay_returns_200(
        self, mock_get_supa, mock_insert, client, intake_api_key,
    ) -> None:
        mock_get_supa.return_value = MagicMock()
        mock_insert.return_value = (
            {
                "id": "existing-id-001",
                "status": "synthesizing",
                "created_at": "2026-05-22T18:32:01+00:00",
            },
            False,
        )
        resp = client.post(
            "/api/intake",
            json=_valid_intake_payload(),
            headers={"X-RSG-API-Key": _INTAKE_API_KEY},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["submission_id"] == "existing-id-001"
        assert data["status"] == "synthesizing"
        assert data["idempotent_replay"] is True


class TestIntakeSubmissionsHelper:
    def test_insert_returns_is_new_true_on_fresh_insert(self) -> None:
        from datetime import datetime, timezone
        from hermes.integrations.intake_submissions import insert_submission

        supa = MagicMock()
        supa.insert.return_value = {"id": "row-1", "status": "received"}
        row, is_new = insert_submission(
            supa,
            idempotency_key="k1",
            source="cowork",
            agent="gretchen",
            intake_kind="full_intake",
            client_identifier=None,
            lob_code=None,
            captured_at=datetime(2026, 5, 22, 18, 32, tzinfo=timezone.utc),
            payload={"transcript": "hi"},
        )
        assert is_new is True
        assert row["id"] == "row-1"
        supa.insert.assert_called_once()
        sent = supa.insert.call_args.args[1]
        assert sent["idempotency_key"] == "k1"
        assert sent["captured_at"].endswith("+00:00")
        assert sent["payload"] == {"transcript": "hi"}

    def test_insert_replay_on_unique_violation(self) -> None:
        from datetime import datetime, timezone
        from hermes.integrations.intake_submissions import insert_submission
        from hermes.integrations.supabase_client import SupabaseClientError

        supa = MagicMock()
        supa.insert.side_effect = SupabaseClientError(
            "409 INSERT intake_submissions: duplicate key value violates unique constraint (23505)"
        )
        supa.select.return_value = [{"id": "existing", "status": "drafting"}]

        row, is_new = insert_submission(
            supa,
            idempotency_key="k1",
            source="cowork",
            agent="gretchen",
            intake_kind="full_intake",
            client_identifier=None,
            lob_code=None,
            captured_at=datetime(2026, 5, 22, 18, 32, tzinfo=timezone.utc),
            payload={"transcript": "hi"},
        )
        assert is_new is False
        assert row["id"] == "existing"
        supa.select.assert_called_once()

    def test_naive_captured_at_raises(self) -> None:
        from datetime import datetime
        from hermes.integrations.intake_submissions import IntakeError, insert_submission

        supa = MagicMock()
        with pytest.raises(IntakeError):
            insert_submission(
                supa,
                idempotency_key="k1",
                source="cowork",
                agent="gretchen",
                intake_kind="full_intake",
                client_identifier=None,
                lob_code=None,
                captured_at=datetime(2026, 5, 22, 18, 32),  # no tz
                payload={"transcript": "hi"},
            )
