"""Editing, completing and deleting the work surfaces: tasks, cases, renewals.

Tasks could be created and patched; cases could only be created and closed —
and closing was broken. Nothing could be deleted, so a case opened by mistake
stayed on the board forever. Renewals were read-only, which meant a premium that
came over wrong stayed wrong and every number derived from it inherited that.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hermes import api

CASE_ID = "3f2b1a90-11c2-4d3e-9f0a-5b6c7d8e9f01"
TASK_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
REN_ID = "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"
LAMAR = "lamar@risksolutionsgroup.net"

CASE = {"id": CASE_ID, "case_number": "REN-CPP4471902-20270301", "title": "Renewal — 1Asfg LLC",
        "status": "open", "priority": "medium", "owner_email": "gretchen@risksolutionsgroup.net"}
TASK = {"id": TASK_ID, "title": "Request renewal terms", "status": "not_started",
        "case_id": CASE_ID, "is_required": True}


@pytest.fixture
def c_supa(monkeypatch):
    supa = MagicMock()
    monkeypatch.setattr(api, "_get_supa", lambda: supa)
    monkeypatch.setattr(api, "_require_users", lambda *a, **k: None)
    return TestClient(api.app), supa


# ── Cases: edit ──────────────────────────────────────────────────────────────

def test_a_case_can_be_edited(c_supa):
    c, supa = c_supa
    supa.select.return_value = [dict(CASE)]
    supa.update.return_value = {**CASE, "title": "Renewal — 1Asfg LLC (2027)"}
    r = c.patch(f"/api/cases/{CASE_ID}", json={"title": "Renewal — 1Asfg LLC (2027)"})
    assert r.status_code == 200
    table, rec_id, payload = supa.update.call_args.args
    assert (table, rec_id) == ("agency_crm_cases", CASE_ID)
    assert payload["title"] == "Renewal — 1Asfg LLC (2027)"
    assert "updated_at" in payload


def test_editing_a_case_cannot_close_it(c_supa):
    """Closing runs the required-task checks, records a resolution and pushes a
    summary to the AMS. A bare status write would skip all three."""
    c, supa = c_supa
    supa.select.return_value = [dict(CASE)]
    r = c.patch(f"/api/cases/{CASE_ID}", json={"status": "closed"})
    assert r.status_code == 422       # status is not a field on the model


def test_a_case_edit_with_no_fields_is_refused(c_supa):
    c, supa = c_supa
    supa.select.return_value = [dict(CASE)]
    assert c.patch(f"/api/cases/{CASE_ID}", json={}).status_code == 400


def test_an_unknown_case_is_a_404(c_supa):
    c, supa = c_supa
    supa.select.return_value = []
    assert c.patch(f"/api/cases/{CASE_ID}", json={"title": "x"}).status_code == 404


def test_a_bad_priority_names_the_valid_ones(c_supa):
    c, supa = c_supa
    supa.select.return_value = [dict(CASE)]
    r = c.patch(f"/api/cases/{CASE_ID}", json={"priority": "screaming"})
    assert r.status_code == 400
    assert "medium" in r.json()["detail"]


# ── Cases: close ─────────────────────────────────────────────────────────────

def test_closing_a_case_writes_the_row_by_id(c_supa):
    """Regression: close passed the payload where the record id belongs, with a
    params= kwarg supa.update does not take — every close raised TypeError and
    came back a 502, so closing a case had never once worked."""
    c, supa = c_supa
    supa.select.side_effect = lambda table, **k: [dict(CASE)] if table == "agency_crm_cases" else []
    supa.update.return_value = {**CASE, "status": "closed"}
    r = c.post(f"/api/cases/{CASE_ID}/close",
               json={"resolution": "Renewed with incumbent", "resolved_by_email": LAMAR,
                     "push_to_ams": False})
    assert r.status_code == 200, r.text
    table, rec_id, payload = supa.update.call_args.args
    assert (table, rec_id) == ("agency_crm_cases", CASE_ID)
    assert payload["status"] == "closed"
    assert r.json()["case"]["status"] == "closed"


def test_a_case_with_required_tasks_open_still_refuses_to_close(c_supa):
    c, supa = c_supa
    supa.select.side_effect = lambda table, **k: (
        [dict(CASE)] if table == "agency_crm_cases" else [dict(TASK)])
    r = c.post(f"/api/cases/{CASE_ID}/close",
               json={"resolution": "done", "resolved_by_email": LAMAR})
    assert r.status_code == 409
    assert r.json()["detail"]["blocking"][0]["title"] == "Request renewal terms"


# ── Tasks: delete ────────────────────────────────────────────────────────────

def test_deleting_a_task_removes_it_and_leaves_a_trace(c_supa):
    c, supa = c_supa
    supa.select.return_value = [dict(TASK)]
    r = c.request("DELETE", f"/api/tasks/{TASK_ID}",
                  json={"deleted_by": LAMAR, "reason": "duplicate"})
    assert r.status_code == 200
    supa.delete.assert_called_once_with("agency_crm_tasks", TASK_ID)
    logged = [ca for ca in supa.insert.call_args_list if ca.args[0] == "portal_write_log"]
    assert logged, "a deletion nobody can trace is how a shared queue stops being trusted"
    entry = logged[0].args[1]
    assert entry["action"] == "deleted" and entry["actor"] == LAMAR
    assert entry["before_value"]["title"] == "Request renewal terms"
    # and the case it hung off says why its checklist got shorter
    events = [ca for ca in supa.insert.call_args_list if ca.args[0] == "agency_crm_case_events"]
    assert events and events[0].args[1]["event_type"] == "task_deleted"


def test_deleting_an_unknown_task_is_a_404(c_supa):
    c, supa = c_supa
    supa.select.return_value = []
    r = c.request("DELETE", f"/api/tasks/{TASK_ID}", json={"deleted_by": LAMAR})
    assert r.status_code == 404
    supa.delete.assert_not_called()


def test_a_deletion_needs_a_named_actor(c_supa):
    c, supa = c_supa
    supa.select.return_value = [dict(TASK)]
    assert c.request("DELETE", f"/api/tasks/{TASK_ID}", json={}).status_code == 422


# ── Cases: delete ────────────────────────────────────────────────────────────

def test_deleting_a_case_takes_its_children_first(c_supa):
    c, supa = c_supa
    supa.select.return_value = [dict(CASE)]
    r = c.request("DELETE", f"/api/cases/{CASE_ID}", json={"deleted_by": LAMAR})
    assert r.status_code == 200, r.text
    cleared = [ca.args[0] for ca in supa.delete_where.call_args_list]
    assert cleared == ["agency_crm_document_links", "agency_crm_case_events",
                       "renewal_case_details", "agency_crm_tasks"]
    for ca in supa.delete_where.call_args_list:
        assert ca.kwargs["filters"] == {"case_id": f"eq.{CASE_ID}"}
    supa.delete.assert_called_once_with("agency_crm_cases", CASE_ID)


def test_deleting_an_unknown_case_is_a_404(c_supa):
    c, supa = c_supa
    supa.select.return_value = []
    r = c.request("DELETE", f"/api/cases/{CASE_ID}", json={"deleted_by": LAMAR})
    assert r.status_code == 404
    supa.delete.assert_not_called()


def test_delete_where_refuses_an_empty_filter():
    """The one way delete_where could empty a table is the one thing it refuses."""
    from hermes.integrations.supabase_client import SupabaseClient

    supa = SupabaseClient.__new__(SupabaseClient)
    with pytest.raises(ValueError):
        SupabaseClient.delete_where(supa, "agency_crm_tasks", filters={})


# ── Renewals ─────────────────────────────────────────────────────────────────

def test_both_premiums_are_editable(c_supa):
    c, supa = c_supa
    supa.select.return_value = [{"id": REN_ID, "premium_current": 4200}]
    supa.update.return_value = {"id": REN_ID, "premium_current": 4500, "premium_renewal": 5100}
    r = c.patch(f"/api/renewals/{REN_ID}",
                json={"premium_current": 4500, "premium_renewal": 5100})
    assert r.status_code == 200
    table, rec_id, payload = supa.update.call_args.args
    assert (table, rec_id) == ("project_85_renewals", REN_ID)
    assert payload["premium_current"] == 4500 and payload["premium_renewal"] == 5100


def test_the_change_percentage_is_never_written(c_supa):
    """increase_percentage is a generated column — it follows the premiums."""
    c, supa = c_supa
    supa.select.return_value = [{"id": REN_ID}]
    supa.update.return_value = {"id": REN_ID}
    c.patch(f"/api/renewals/{REN_ID}", json={"premium_renewal": 5100, "increase_percentage": 2})
    assert "increase_percentage" not in supa.update.call_args.args[2]


def test_risk_status_is_checked_against_the_enum(c_supa):
    c, supa = c_supa
    supa.select.return_value = [{"id": REN_ID}]
    r = c.patch(f"/api/renewals/{REN_ID}", json={"risk_status": "SPICY"})
    assert r.status_code == 400
    assert "CRITICAL" in r.json()["detail"]


def test_risk_status_is_normalised_before_it_reaches_the_enum(c_supa):
    """The column is a Postgres enum in caps; a dropdown value should not have to
    be typed in caps to be accepted."""
    c, supa = c_supa
    supa.select.return_value = [{"id": REN_ID}]
    supa.update.return_value = {"id": REN_ID}
    r = c.patch(f"/api/renewals/{REN_ID}", json={"risk_status": "at_risk"})
    assert r.status_code == 200
    assert supa.update.call_args.args[2]["risk_status"] == "AT_RISK"
