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


# ---------------------------------------------------------------------------
# Driver / Vehicle approve path (scoped dedup + Lead create-then-link)
# ---------------------------------------------------------------------------

from hermes.operations.crm_proposals import approve_proposal as _approve, ProposalError


def _mock_supa_with_proposal(proposal: dict):
    """Supabase mock whose select() returns the proposal row and records update() calls."""
    supa = MagicMock()
    supa.select.return_value = [proposal]
    return supa


def _driver_proposal(after: dict, op: str = "create", espocrm_id: str | None = None) -> dict:
    return {"id": "prop-d1", "status": "pending", "entity": "OpportunityDriver",
            "op": op, "espocrm_id": espocrm_id, "after": after}


def test_approve_driver_create_no_duplicate():
    supa = _mock_supa_with_proposal(_driver_proposal(
        {"driverName": "New Drv", "driverLicenseNumber": "DL-NEW", "opportunityId": "opp-1"}))
    espo = MagicMock()
    espo.get.return_value = {"list": []}              # scoped dedup: no match
    espo.create.return_value = {"id": "drv-new-1"}

    res = _approve(supa, "prop-d1", reviewer="lamar", espo=espo)

    assert res["status"] == "committed" and res["action"] == "create" and res["espocrm_id"] == "drv-new-1"
    espo.create.assert_called_once_with("OpportunityDriver",
        {"driverName": "New Drv", "driverLicenseNumber": "DL-NEW", "opportunityId": "opp-1"})
    espo.post.assert_not_called()                    # no lead link
    upd = supa.update.call_args.args[2]
    assert upd["status"] == "committed" and upd["result"]["action"] == "create"


def test_approve_driver_lead_only_creates_then_links():
    supa = _mock_supa_with_proposal(_driver_proposal(
        {"driverName": "Lead Drv", "driverLicenseNumber": "DL-LEAD", "leadId": "lead-9"}))
    espo = MagicMock()
    espo.get.return_value = {"list": []}              # dedup scoped to lead: no match
    espo.create.return_value = {"id": "drv-lead-1"}

    res = _approve(supa, "prop-d1", reviewer="lamar", espo=espo)

    assert res["action"] == "create+link" and res["espocrm_id"] == "drv-lead-1"
    # create must NOT include leadId (inline leadId is ACL-blocked)
    sent = espo.create.call_args.args[1]
    assert "leadId" not in sent and sent["driverName"] == "Lead Drv"
    espo.post.assert_called_once_with("Lead/lead-9/opportunityDrivers", {"id": "drv-lead-1"})


def test_approve_driver_create_dedup_updates_existing_on_same_opportunity():
    supa = _mock_supa_with_proposal(_driver_proposal(
        {"driverName": "Dup Drv", "driverLicenseNumber": "DL-DUP", "opportunityId": "opp-1"}))
    espo = MagicMock()
    espo.get.return_value = {"list": [{"id": "drv-existing-1", "name": "Dup Drv"}]}  # scoped match

    res = _approve(supa, "prop-d1", reviewer="lamar", espo=espo)

    assert res["action"] == "update+dedup" and res["espocrm_id"] == "drv-existing-1"
    espo.create.assert_not_called()                 # did not duplicate
    espo.update.assert_called_once_with("OpportunityDriver", "drv-existing-1",
        {"driverName": "Dup Drv", "driverLicenseNumber": "DL-DUP", "opportunityId": "opp-1"})
    # dedup query was scoped to the same opportunity
    where = espo.get.call_args.kwargs["params"]["where"]
    assert {"type": "equals", "attribute": "opportunityId", "value": "opp-1"} in where


def test_approve_vehicle_dedup_on_vin():
    supa = _mock_supa_with_proposal({"id": "prop-v1", "status": "pending",
        "entity": "OpportunityVehicle", "op": "create", "espocrm_id": None,
        "after": {"vin": "VIN123", "make": "Honda", "accountId": "acct-1"}})
    espo = MagicMock()
    espo.get.return_value = {"list": [{"id": "veh-existing-1"}]}   # dup on same account

    res = _approve(supa, "prop-v1", reviewer="lamar", espo=espo)

    assert res["action"] == "update+dedup" and res["espocrm_id"] == "veh-existing-1"
    espo.update.assert_called_once_with("OpportunityVehicle", "veh-existing-1",
        {"vin": "VIN123", "make": "Honda", "accountId": "acct-1"})


def test_approve_driver_update_uses_espocrm_id():
    supa = _mock_supa_with_proposal(_driver_proposal(
        {"driverName": "Fix", "driverLicenseNumber": "DL-X"}, op="update", espocrm_id="drv-77"))
    espo = MagicMock()

    res = _approve(supa, "prop-d1", reviewer="lamar", espo=espo)

    assert res["action"] == "update" and res["espocrm_id"] == "drv-77"
    espo.update.assert_called_once_with("OpportunityDriver", "drv-77",
        {"driverName": "Fix", "driverLicenseNumber": "DL-X"})
    espo.create.assert_not_called()


def test_approve_driver_without_espo_is_503():
    supa = _mock_supa_with_proposal(_driver_proposal({"driverName": "X", "opportunityId": "opp-1"}))
    with pytest.raises(ProposalError) as exc:
        _approve(supa, "prop-d1", reviewer="lamar", espo=None)
    assert exc.value.status_code == 503


def test_approve_driver_commit_failure_marks_failed():
    supa = _mock_supa_with_proposal(_driver_proposal(
        {"driverName": "Boom", "driverLicenseNumber": "DL-BOOM", "opportunityId": "opp-1"}))
    espo = MagicMock()
    espo.get.return_value = {"list": []}
    espo.create.side_effect = RuntimeError("espo down")

    with pytest.raises(ProposalError) as exc:
        _approve(supa, "prop-d1", reviewer="lamar", espo=espo)
    assert exc.value.status_code == 502
    upd = supa.update.call_args.args[2]
    assert upd["status"] == "failed" and "espo down" in upd["error"]
