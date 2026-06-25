"""Tests for the /api/crm/proposals approval flow (hermes/api.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.api import app


@pytest.fixture(autouse=True)
def _reset_singletons():
    import hermes.api as api_mod
    api_mod._supa = None
    yield
    api_mod._supa = None


@pytest.fixture
def client():
    return TestClient(app)


# ---------- create ----------

def test_create_proposal_stages_pending(client):
    with patch("hermes.api._get_supa") as mock_supa:
        supa = MagicMock()
        mock_supa.return_value = supa
        supa.insert.return_value = {"id": "p-1", "status": "pending", "entity": "Account"}

        resp = client.post("/api/crm/proposals", json={
            "entity": "Account",
            "espocrm_id": "6a16fc451922fe66a",
            "op": "update",
            "after": {"accountStatus": "Active"},
            "rationale": "status fix",
            "proposed_by": "claude",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "pending"
    inserted = supa.insert.call_args.args[1]
    assert inserted["entity"] == "Account"
    assert inserted["after"] == {"accountStatus": "Active"}
    assert inserted["status"] == "pending"


def test_create_proposal_rejects_update_without_espocrm_id(client):
    with patch("hermes.api._get_supa"):
        resp = client.post("/api/crm/proposals", json={
            "entity": "Contact", "op": "update", "after": {"name": "X"},
        })
    assert resp.status_code == 400
    assert "espocrm_id" in resp.json()["detail"]


def test_create_proposal_rejects_create_with_espocrm_id(client):
    with patch("hermes.api._get_supa"):
        resp = client.post("/api/crm/proposals", json={
            "entity": "Account", "op": "create",
            "espocrm_id": "abc", "after": {"name": "X"},
        })
    assert resp.status_code == 400


def test_create_proposal_rejects_empty_after(client):
    with patch("hermes.api._get_supa"):
        resp = client.post("/api/crm/proposals", json={
            "entity": "Account", "espocrm_id": "x", "op": "update", "after": {},
        })
    assert resp.status_code == 400


# ---------- list ----------

def test_list_proposals_passes_status_filter(client):
    with patch("hermes.api._get_supa") as mock_supa:
        supa = MagicMock()
        mock_supa.return_value = supa
        supa.select.return_value = [{"id": "p-1", "status": "pending"}]
        resp = client.get("/api/crm/proposals?status=pending&limit=10")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    params = supa.select.call_args.kwargs["params"]
    assert params["status"] == "eq.pending"


# ---------- approve ----------

def test_approve_enqueues_crm_write_and_marks_approved(client):
    with patch("hermes.api._get_supa") as mock_supa:
        supa = MagicMock()
        mock_supa.return_value = supa
        # select (get_proposal) -> returns pending row; insert (enqueue) -> queue row; update -> ok
        supa.select.return_value = [{
            "id": "p-1", "status": "pending", "entity": "Account",
            "espocrm_id": "6a16fc451922fe66a", "op": "update",
            "after": {"accountStatus": "Active"},
        }]
        supa.insert.return_value = {"id": "queue-9"}

        resp = client.post("/api/crm/proposals/p-1/approve", json={"reviewer": "lamar"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "approved"
    assert data["queue_id"] == "queue-9"
    assert data["espocrm_id"] == "6a16fc451922fe66a"
    # enqueued into crm_write_queue with reviewer role
    enq = supa.insert.call_args.args[1]
    assert enq["entity_type"] == "Account"
    assert enq["entity_id"] == "6a16fc451922fe66a"
    # _normalize_queue_payload wraps a flat field dict as {action_type: legacy_write, context: <after>}
    assert enq["payload"] == {"action_type": "legacy_write", "context": {"accountStatus": "Active"}}
    assert enq["created_by_role"] == "reviewer"
    assert enq["status"] == "PENDING"
    # proposal row updated to approved with queue_id in result
    upd = supa.update.call_args.args[2]
    assert upd["status"] == "approved"
    assert upd["reviewed_by"] == "lamar"
    assert upd["result"]["queue_id"] == "queue-9"


def test_approve_404_for_missing_proposal(client):
    with patch("hermes.api._get_supa") as mock_supa:
        supa = MagicMock()
        mock_supa.return_value = supa
        supa.select.return_value = []  # not found
        resp = client.post("/api/crm/proposals/ghost/approve", json={"reviewer": "lamar"})
    assert resp.status_code == 404


def test_approve_409_for_non_pending(client):
    with patch("hermes.api._get_supa") as mock_supa:
        supa = MagicMock()
        mock_supa.return_value = supa
        supa.select.return_value = [{"id": "p-2", "status": "approved", "entity": "Account",
                                     "espocrm_id": "x", "op": "update", "after": {"a": 1}}]
        resp = client.post("/api/crm/proposals/p-2/approve", json={"reviewer": "lamar"})
    assert resp.status_code == 409


def test_approve_marks_failed_when_enqueue_raises(client):
    with patch("hermes.api._get_supa") as mock_supa:
        supa = MagicMock()
        mock_supa.return_value = supa
        supa.select.return_value = [{"id": "p-3", "status": "pending", "entity": "Account",
                                     "espocrm_id": "x", "op": "update", "after": {"a": 1}}]
        supa.insert.side_effect = RuntimeError("supabase down")
        resp = client.post("/api/crm/proposals/p-3/approve", json={"reviewer": "lamar"})
    assert resp.status_code == 502
    # the proposal should have been marked failed
    upd = supa.update.call_args.args[2]
    assert upd["status"] == "failed"


# ---------- reject ----------

def test_reject_marks_rejected(client):
    with patch("hermes.api._get_supa") as mock_supa:
        supa = MagicMock()
        mock_supa.return_value = supa
        supa.select.return_value = [{"id": "p-4", "status": "pending"}]
        resp = client.post("/api/crm/proposals/p-4/reject", json={"reviewer": "gretchen", "reason": "wrong field"})
    assert resp.status_code == 200
    upd = supa.update.call_args.args[2]
    assert upd["status"] == "rejected"
    assert upd["reviewed_by"] == "gretchen"
    assert upd["error"] == "wrong field"


# ---------- openapi ----------

def test_openapi_advertises_proposal_endpoints():
    from hermes.api import openapi_schema
    schema = openapi_schema()
    paths = schema["paths"]
    assert "/api/crm/proposals" in paths
    assert "/api/crm/proposals/{proposal_id}/approve" in paths
    assert "/api/crm/proposals/{proposal_id}/reject" in paths
