"""Tests for hermes/api.py FastAPI endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app
from hermes_core.dispatch import DispatchResult


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset lazy singletons between tests."""
    from hermes.routers import deps
    deps.reset_clients()
    yield
    deps.reset_clients()


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_root_points_at_the_portal_now_that_no_ui_is_served(self, client, monkeypatch) -> None:
        """The root used to redirect into the cockpit. The cockpit is gone, so it
        answers with where the CRM actually is — an old bookmark should read as a
        forwarding address, not a 404."""
        monkeypatch.setenv("HERMES_PORTAL_URL", "https://ws.ts.net:8447")
        body = client.get("/").json()
        assert body["portal"] == "https://ws.ts.net:8447"
        assert body["ui"].startswith("none")

    def test_root_admits_when_no_portal_is_configured(self, client, monkeypatch) -> None:
        monkeypatch.delenv("HERMES_PORTAL_URL", raising=False)
        assert "unset" in client.get("/").json()["portal"]

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
    @patch("hermes.routers.deps.get_dispatcher")
    def test_ping(self, mock_dispatcher, client) -> None:
        mock_dispatcher.return_value.dispatch.return_value = DispatchResult(
            True, "Hermes is online and connected to CRM.",
        )
        resp = client.post("/dispatch", json={"command": "ping"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "online" in data["message"]

    @patch("hermes.routers.deps.get_dispatcher")
    def test_sync_status(self, mock_dispatcher, client) -> None:
        mock_dispatcher.return_value.dispatch.return_value = DispatchResult(
            True, "No sync runs found yet.",
        )
        resp = client.post("/dispatch", json={"command": "sync status"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("hermes.routers.deps.get_dispatcher")
    def test_dispatch_error(self, mock_dispatcher, client) -> None:
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

    @patch("hermes.routers.deps.get_dispatcher")
    def test_server_error(self, mock_dispatcher, client) -> None:
        mock_dispatcher.return_value.dispatch.side_effect = RuntimeError("boom")
        resp = client.post("/dispatch", json={"command": "ping"})
        assert resp.status_code == 500


class TestDashboardDispatch:
    def test_dashboard_dispatch_requires_a_command(self, client) -> None:
        resp = client.post("/api/hermes/dispatch", json={})
        assert resp.status_code == 400

    @patch("hermes.routers.deps.get_supa")
    def test_sync_health_payload(self, mock_get_supa, client) -> None:
        supa = MagicMock()
        mock_get_supa.return_value = supa
        supa.select.side_effect = [
            [{"id": "1"}],  # queued
            [{"id": "2"}, {"id": "3"}],  # failed
            [],  # dead
            [{"id": "job-1", "object_type": "renewal",
              "destination_system": "nowcerts", "updated_at": "2026-01-01T00:00:00Z"}],
        ]

        resp = client.get("/api/hermes/sync-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["outbound_sync_queue"]["queued"] == 1
        assert data["outbound_sync_queue"]["failed"] == 2
        assert data["latest_completed"]["id"] == "job-1"


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
        with patch("hermes.routers.deps.get_supa") as mock_get_supa, patch(
            "hermes.intake.submissions.insert_submission"
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

    @patch("hermes.intake.submissions.insert_submission")
    @patch("hermes.routers.deps.get_supa")
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

    @patch("hermes.intake.submissions.insert_submission")
    @patch("hermes.routers.deps.get_supa")
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
        from hermes.intake.submissions import insert_submission

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
        from hermes.intake.submissions import insert_submission
        from hermes_integrations.supabase_client import SupabaseClientError

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
        from hermes.intake.submissions import IntakeError, insert_submission

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


# ---------------------------------------------------------------------------
# Phase 3 — state transition helper + claim pattern
# ---------------------------------------------------------------------------


class TestTransitionHelper:
    def _supa_with_row(self, row):
        supa = MagicMock()
        supa.select.return_value = [row]
        supa.update.return_value = row
        return supa

    def test_happy_path_appends_status_history(self) -> None:
        from hermes.intake.submissions import transition

        existing = {
            "id": "sub-1",
            "status": "received",
            "status_history": [],
            "error_log": [],
        }
        supa = self._supa_with_row(existing)
        transition(supa, "sub-1", "synthesizing", note="worker claim")

        supa.update.assert_called_once()
        sent = supa.update.call_args.args[2]
        assert sent["status"] == "synthesizing"
        assert len(sent["status_history"]) == 1
        entry = sent["status_history"][0]
        assert entry["from"] == "received"
        assert entry["to"] == "synthesizing"
        assert entry["note"] == "worker claim"
        assert "error_log" not in sent  # no error on a happy path

    def test_complete_sets_completed_at(self) -> None:
        from hermes.intake.submissions import transition

        existing = {
            "id": "sub-1",
            "status": "written",
            "status_history": [{"from": "writing", "to": "written", "at": "2026-05-22T00:00:00Z"}],
        }
        supa = self._supa_with_row(existing)
        transition(supa, "sub-1", "complete", note="done")

        sent = supa.update.call_args.args[2]
        assert sent["status"] == "complete"
        assert "completed_at" in sent
        assert sent["completed_at"].endswith("+00:00")

    def test_failed_appends_to_error_log(self) -> None:
        from hermes.intake.submissions import transition

        existing = {
            "id": "sub-1",
            "status": "writing",
            "status_history": [],
            "error_log": [{"at": "earlier", "message": "prev"}],
        }
        supa = self._supa_with_row(existing)
        transition(
            supa, "sub-1", "failed",
            error={"message": "ams 400", "field": "phoneNumber"},
        )

        sent = supa.update.call_args.args[2]
        assert sent["status"] == "failed"
        assert len(sent["error_log"]) == 2
        last = sent["error_log"][-1]
        assert last["message"] == "ams 400"
        assert last["field"] == "phoneNumber"
        assert last["status_at_failure"] == "writing"

    def test_failed_with_string_error_records_message(self) -> None:
        from hermes.intake.submissions import transition

        existing = {"id": "sub-1", "status": "synthesizing", "status_history": [], "error_log": []}
        supa = self._supa_with_row(existing)
        transition(supa, "sub-1", "failed", error="openai timeout")

        sent = supa.update.call_args.args[2]
        assert sent["error_log"][-1]["message"] == "openai timeout"

    def test_invalid_transition_raises(self) -> None:
        from hermes.intake.submissions import IntakeError, transition

        existing = {"id": "sub-1", "status": "received", "status_history": [], "error_log": []}
        supa = self._supa_with_row(existing)
        with pytest.raises(IntakeError, match="invalid transition"):
            transition(supa, "sub-1", "complete")  # can't skip the pipeline
        supa.update.assert_not_called()

    def test_failed_reachable_from_any_state(self) -> None:
        from hermes.intake.submissions import transition

        for src in ("received", "synthesizing", "drafting", "awaiting_approval", "writing", "written"):
            existing = {"id": "x", "status": src, "status_history": [], "error_log": []}
            supa = self._supa_with_row(existing)
            transition(supa, "x", "failed", error=f"failure from {src}")
            assert supa.update.call_args.args[2]["status"] == "failed"

    def test_unknown_status_rejected(self) -> None:
        from hermes.intake.submissions import IntakeError, transition

        existing = {"id": "sub-1", "status": "received", "status_history": [], "error_log": []}
        supa = self._supa_with_row(existing)
        with pytest.raises(IntakeError, match="unknown status"):
            transition(supa, "sub-1", "thinking_real_hard")

    def test_no_op_when_already_in_target_status(self) -> None:
        from hermes.intake.submissions import transition

        existing = {"id": "sub-1", "status": "synthesizing", "status_history": [], "error_log": []}
        supa = self._supa_with_row(existing)
        result = transition(supa, "sub-1", "synthesizing")

        supa.update.assert_not_called()
        assert result is existing

    def test_extra_fields_merge_atomically(self) -> None:
        from hermes.intake.submissions import transition

        existing = {"id": "sub-1", "status": "synthesizing", "status_history": [], "error_log": []}
        supa = self._supa_with_row(existing)
        transition(
            supa, "sub-1", "synthesized",
            extra_fields={"hermes_blocks": "block-text", "draft_summary": {"account": {"name": "X"}}},
        )

        sent = supa.update.call_args.args[2]
        assert sent["hermes_blocks"] == "block-text"
        assert sent["draft_summary"]["account"]["name"] == "X"

    def test_extra_fields_cannot_override_protected(self) -> None:
        from hermes.intake.submissions import IntakeError, transition

        existing = {"id": "sub-1", "status": "received", "status_history": [], "error_log": []}
        supa = self._supa_with_row(existing)
        with pytest.raises(IntakeError, match="protected"):
            transition(supa, "sub-1", "synthesizing", extra_fields={"payload": {"hacked": True}})

    def test_missing_row_raises(self) -> None:
        from hermes.intake.submissions import IntakeError, transition

        supa = MagicMock()
        supa.select.return_value = []
        with pytest.raises(IntakeError, match="not found"):
            transition(supa, "missing", "synthesizing")


class TestClaimNextReceived:
    def test_returns_none_when_queue_empty(self) -> None:
        from hermes.intake.submissions import claim_next_received

        supa = MagicMock()
        supa.select.return_value = []
        assert claim_next_received(supa) is None
        supa.update_where.assert_not_called()

    def test_claims_oldest_received_row(self) -> None:
        from hermes.intake.submissions import claim_next_received

        supa = MagicMock()
        supa.select.return_value = [{"id": "sub-7", "status_history": []}]
        supa.update_where.return_value = [{
            "id": "sub-7",
            "status": "synthesizing",
            "status_history": [{"from": "received", "to": "synthesizing", "at": "now", "note": "claimed by worker"}],
        }]

        row = claim_next_received(supa)
        assert row is not None
        assert row["status"] == "synthesizing"

        # The SELECT must filter by status=received and order by created_at asc
        sel_kwargs = supa.select.call_args.kwargs
        assert sel_kwargs["params"]["status"] == "eq.received"
        assert "created_at.asc" in sel_kwargs["params"]["order"]

        # The UPDATE must be conditional on status=received (race protection)
        upd_kwargs = supa.update_where.call_args.kwargs
        assert upd_kwargs["filters"]["status"] == "eq.received"
        assert upd_kwargs["filters"]["id"] == "eq.sub-7"

    def test_returns_none_when_race_lost(self) -> None:
        from hermes.intake.submissions import claim_next_received

        supa = MagicMock()
        supa.select.return_value = [{"id": "sub-7", "status_history": []}]
        supa.update_where.return_value = []  # another worker won

        assert claim_next_received(supa) is None
