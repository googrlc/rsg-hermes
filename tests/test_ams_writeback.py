"""Pushing a CRM correction on to NowCerts, keyed on the record's AMS GUID.

A portal override fixes what the CRM shows and nothing else — for a wrong phone
number the AMS never catches up on its own. This is the other half, and the
thing it must not do is invent records: Insured/Insert upserts on DatabaseId OR
CommercialName, so a push against a GUID the AMS cannot confirm is how you mint
another duplicate insured into a book that already has them.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hermes import api
from hermes.ams import writeback
from hermes_app import deps

GUID = "bfe42b77-b1a8-4729-aa10-af8494d05a9b"
POLICY = "6f1c2d84-3b90-4f5e-9a21-0c7ad3e51b44"
LAMAR = "lamar@risksolutionsgroup.net"


@pytest.fixture
def supa():
    s = MagicMock()
    s.insert.return_value = {"id": "q1"}
    return s


def _nowcerts(read_rows, *, fail_write=None):
    """A NowCerts double whose _get answers the read-by-GUID probes."""
    nc = MagicMock()
    nc._get.side_effect = lambda path, params=None: list(read_rows)
    if fail_write:
        nc.create_insured.side_effect = fail_write
        nc.update_policy.side_effect = fail_write
    return nc


# ── Field mapping ────────────────────────────────────────────────────────────

def test_crm_names_map_to_the_nowcerts_ones():
    out = writeback.map_fields("client", {"phone": "404-555-0101", "zip": "30067"})
    assert out == {"PhoneNumber": "404-555-0101", "Zip": "30067"}


def test_a_premium_is_pushed_as_a_number():
    """An <input> hands back a string; NowCerts silently ignores a premium of '4200'."""
    assert writeback.map_fields("policy", {"premium_amount": "4,200"})["Premium"] == 4200.0


def test_a_premium_that_is_not_a_number_is_refused_by_name():
    with pytest.raises(ValueError, match="premium_amount must be a number"):
        writeback.map_fields("policy", {"premium_amount": "about four grand"})


def test_an_unpushable_field_is_named_not_dropped():
    """A Save that reports success while quietly discarding half the change is
    worse than one that fails."""
    with pytest.raises(ValueError, match="policy_guid"):
        writeback.map_fields("policy", {"policy_guid": "x", "carrier": "Travelers"})


def test_identifiers_are_not_in_either_map():
    for ident in ("policy_guid", "nowcerts_insured_guid", "renewed_policy", "policy_number"):
        assert ident not in writeback.CLIENT_FIELD_MAP
        assert ident not in writeback.POLICY_FIELD_MAP


# ── The push ─────────────────────────────────────────────────────────────────

def test_a_client_push_writes_the_guid_and_the_mapped_fields(supa):
    nc = _nowcerts([{"id": GUID, "phoneNumber": "404-555-0101"}])
    out = writeback.push(supa, nc, object_type="client", object_id=GUID,
                         fields={"phone": "404-555-0101"}, actor=LAMAR)
    assert out["pushed"] and out["verified"]
    sent = nc.create_insured.call_args.args[0]
    assert sent["DatabaseId"] == GUID          # this is what makes it an update
    assert sent["PhoneNumber"] == "404-555-0101"


def test_a_policy_push_goes_through_partial_update(supa):
    nc = _nowcerts([{"databaseId": POLICY, "carrierName": "Travelers"}])
    out = writeback.push(supa, nc, object_type="policy", object_id=POLICY,
                         fields={"carrier": "Travelers"}, actor=LAMAR)
    assert out["pushed"] and out["verified"]
    assert nc.update_policy.call_args.args[0]["DatabaseId"] == POLICY
    nc.create_insured.assert_not_called()


def test_a_record_the_ams_cannot_confirm_is_never_written(supa):
    """The duplicate guard. Insured/Insert upserts on CommercialName too, so an
    unconfirmed GUID plus a name would create a second insured."""
    nc = _nowcerts([])                      # every read probe comes back empty
    out = writeback.push(supa, nc, object_type="client", object_id=GUID,
                         fields={"insured_name": "1Asfg LLC"}, actor=LAMAR)
    assert out["pushed"] is False
    assert "could not confirm" in out["error"]
    nc.create_insured.assert_not_called()


def test_every_guid_spelling_is_tried_before_giving_up(supa):
    """$filter on the insured id is not reliably supported; one empty answer is
    not proof the record is absent."""
    nc = MagicMock()
    nc._get.side_effect = [[], [{"databaseId": GUID}], [{"databaseId": GUID}]]
    out = writeback.push(supa, nc, object_type="client", object_id=GUID,
                         fields={"city": "Marietta"}, actor=LAMAR)
    assert out["pushed"] is True
    assert nc._get.call_count >= 2


def test_a_failed_push_closes_the_queue_row_and_says_why(supa):
    nc = _nowcerts([{"id": GUID}], fail_write=RuntimeError("NowCerts 503"))
    out = writeback.push(supa, nc, object_type="client", object_id=GUID,
                         fields={"city": "Marietta"}, actor=LAMAR)
    assert out["pushed"] is False and "503" in out["error"]
    closed = [c for c in supa.update.call_args_list if c.args[0] == "outbound_sync_queue"]
    assert closed and closed[-1].args[2]["status"] == "failed"


def test_the_queue_row_is_written_before_the_ams_call(supa):
    """It is the durable part: a push that dies mid-flight must leave a row that
    says so, because nothing else drains this queue — the renewal executor's
    cron is not enabled."""
    nc = _nowcerts([{"id": GUID}])
    writeback.push(supa, nc, object_type="client", object_id=GUID,
                   fields={"city": "Marietta"}, actor=LAMAR)
    staged = [c for c in supa.insert.call_args_list if c.args[0] == "outbound_sync_queue"]
    assert staged, "no queue row"
    row = staged[0].args[1]
    assert row["object_id"] == GUID and row["destination_system"] == "nowcerts"
    assert row["approved_by"] == LAMAR       # the portal's confirm IS the approval


def test_a_write_the_ams_did_not_take_reports_unverified(supa):
    """Read-back is the point: NowCerts accepting the call is not the same as
    NowCerts having changed."""
    nc = MagicMock()
    nc._get.side_effect = [[{"id": GUID, "city": "Marietta"}],
                           [{"id": GUID, "city": "Marietta"}]]   # unchanged
    out = writeback.push(supa, nc, object_type="client", object_id=GUID,
                         fields={"city": "Atlanta"}, actor=LAMAR)
    assert out["pushed"] is True and out["verified"] is False
    assert out["unverified_fields"] == ["City"]


def test_the_push_is_audited(supa):
    nc = _nowcerts([{"id": GUID, "city": "Atlanta"}])
    writeback.push(supa, nc, object_type="client", object_id=GUID,
                   fields={"city": "Atlanta"}, actor=LAMAR)
    logged = [c for c in supa.insert.call_args_list if c.args[0] == "portal_write_log"]
    assert logged and logged[0].args[1]["action"] == "ams_push"
    assert logged[0].args[1]["entity_key"] == GUID


# ── The endpoints ────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, supa):
    monkeypatch.setattr(deps, "get_supa", lambda: supa)
    monkeypatch.setattr(deps, "require_users", lambda *a, **k: None)
    monkeypatch.setattr(deps, "get_nowcerts", lambda: _nowcerts([{"id": GUID, "city": "Atlanta"}]))
    return TestClient(api.app)


def test_the_client_endpoint_pushes(client):
    r = client.post(f"/api/clients/{GUID}/push-to-ams",
                    json={"fields": {"city": "Atlanta"}, "approved_by": LAMAR})
    assert r.status_code == 200, r.text
    assert r.json()["pushed"] is True


def test_the_endpoint_refuses_an_unpushable_field(client):
    r = client.post(f"/api/policies/{POLICY}/push-to-ams",
                    json={"fields": {"policy_number": "CPP-1"}, "approved_by": LAMAR})
    assert r.status_code == 400
    assert "policy_number" in r.json()["detail"]


def test_a_push_needs_a_named_approver(client):
    r = client.post(f"/api/clients/{GUID}/push-to-ams", json={"fields": {"city": "x"}})
    assert r.status_code == 422


# ── Adding a policy ──────────────────────────────────────────────────────────

def test_a_policy_is_created_against_a_confirmed_insured(client, monkeypatch):
    nc = _nowcerts([{"id": GUID}])
    monkeypatch.setattr(deps, "get_nowcerts", lambda: nc)
    r = client.post("/api/policies", json={
        "insured_id": GUID, "policy_number": "CPP-9001", "carrier": "Travelers",
        "premium_amount": 4200, "approved_by": LAMAR})
    assert r.status_code == 200, r.text
    sent = nc.insert_policy.call_args.args[0]
    assert sent["InsuredDatabaseId"] == GUID and sent["Number"] == "CPP-9001"
    assert sent["IsQuote"] is False and sent["Premium"] == 4200.0


def test_a_policy_against_an_unknown_insured_is_refused(client, monkeypatch):
    """A typo'd insured id would otherwise spawn an orphan policy."""
    nc = _nowcerts([])
    monkeypatch.setattr(deps, "get_nowcerts", lambda: nc)
    r = client.post("/api/policies", json={
        "insured_id": GUID, "policy_number": "CPP-9001", "approved_by": LAMAR})
    assert r.status_code == 404
    nc.insert_policy.assert_not_called()


def test_the_kanban_gets_its_columns_from_the_backend(client):
    """Hardcoding stages in the browser is how the board drifts from what a
    stage move will actually be allowed to set."""
    body = client.get("/api/pipeline/stages").json()
    assert body["new_business"][0] == "Not Assigned"
    assert "Bound / Won" in body["new_business"] and "Lost" in body["new_business"]
    assert "Renewal in 90 days" in body["renewal"]


# ── The active flag is derived, never set ────────────────────────────────────

def test_active_cannot_be_pushed_to_the_ams():
    """canonical_clients.active is recomputed by a trigger from the client's
    policies, and a policy's active follows its status. Offering either as an
    editable field would paint over a value the database owns."""
    with pytest.raises(ValueError, match="active"):
        writeback.map_fields("client", {"active": False})
    with pytest.raises(ValueError, match="active"):
        writeback.map_fields("policy", {"active": False})


def test_active_is_not_an_overridable_client_field():
    assert "active" not in api.CLIENT_OVERRIDABLE_FIELDS
    assert "active" not in api.POLICY_OVERRIDABLE_FIELDS


# ── Retrying a failed push ───────────────────────────────────────────────────

FAILED_ROW = {
    "id": "q-failed", "object_type": "client", "object_id": GUID, "status": "failed",
    "payload": {"crm_fields": {"city": "Atlanta"}}, "last_error": "NowCerts 503",
}


def test_a_failed_push_is_replayed_from_its_own_row(supa):
    """The row remembers the fields, so a retry does not need the person to."""
    supa.select.return_value = [dict(FAILED_ROW)]
    nc = _nowcerts([{"id": GUID, "city": "Atlanta"}])
    out = writeback.retry(supa, nc, queue_id="q-failed", actor=LAMAR)
    assert out["pushed"] and out["verified"]
    assert nc.create_insured.call_args.args[0]["City"] == "Atlanta"


def test_only_a_failed_push_can_be_retried(supa):
    supa.select.return_value = [dict(FAILED_ROW, status="completed")]
    with pytest.raises(ValueError, match="only failed pushes"):
        writeback.retry(supa, MagicMock(), queue_id="q-failed", actor=LAMAR)


def test_a_renewal_job_is_not_retried_by_this_path(supa):
    """The renewal executor owns those, with its own approval contract."""
    supa.select.return_value = [dict(FAILED_ROW, object_type="renewal")]
    with pytest.raises(ValueError, match="only client and policy"):
        writeback.retry(supa, MagicMock(), queue_id="q-failed", actor=LAMAR)


def test_failed_pushes_are_listed_for_the_portal(supa):
    supa.select.return_value = [dict(FAILED_ROW)]
    rows = writeback.list_failed(supa)
    assert rows and rows[0]["object_id"] == GUID
    params = supa.select.call_args.kwargs["params"]
    assert params["status"] == "eq.failed"
    assert "client" in params["object_type"] and "policy" in params["object_type"]


def test_the_banner_survives_a_broken_lookup(client, supa):
    """A banner about failures must not itself take the portal down."""
    supa.select.side_effect = RuntimeError("boom")
    body = client.get("/api/ams/failed-pushes").json()
    assert body == {"failed": [], "count": 0, "unavailable": True}


def test_the_board_is_told_which_types_are_renewals(client):
    """Not re-derived in the browser: a substring test on the type name would
    sweep in 'Remarket', which is a different pipeline with a different ladder."""
    body = client.get("/api/pipeline/stages").json()
    assert body["renewal_types"] == ["Renewals"]
    assert "Remarket" in body["types"] and "Remarket" not in body["renewal_types"]
    # and the two ladders really are different work
    assert "Renewal in 90 days" in body["renewal"]
    assert "Renewal in 90 days" not in body["new_business"]
