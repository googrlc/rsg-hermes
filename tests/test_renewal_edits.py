"""Editing a renewal: premiums, the derived percentage, and the risk enum.

Renewals were read-only, which meant a premium that came over wrong stayed wrong
and every number derived from it inherited that.

This file used to cover cases and tasks as well, and was kept here on the
grounds that exercising two apps made it an integration test. It was not one:
the case half and the renewal half shared a fixture and nothing else. When cases
left for googrlc/rsg-hermes-cases the case half did not fail loudly — the three
"unknown thing is a 404" tests kept passing, because a route that no longer
exists returns 404 too. A test that cannot tell a correct rejection from a
missing feature is worse than no test, so those moved to the repo that now
serves them (`tests/test_case_task_edits.py` there).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hermes import api
from hermes_app import deps

REN_ID = "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"


@pytest.fixture
def c_supa(monkeypatch):
    supa = MagicMock()
    monkeypatch.setattr(deps, "get_supa", lambda: supa)
    monkeypatch.setattr(deps, "require_users", lambda *a, **k: None)
    return TestClient(api.app), supa


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


# ── The shared client's delete guard ─────────────────────────────────────────
#
# Stays here rather than moving with the case-deletion tests that motivated it:
# it exercises hermes_integrations.supabase_client directly, and that client
# ships to every app repo from this one.

def test_delete_where_refuses_an_empty_filter():
    """The one way delete_where could empty a table is the one thing it refuses."""
    from hermes_integrations.supabase_client import SupabaseClient

    supa = SupabaseClient.__new__(SupabaseClient)
    with pytest.raises(ValueError):
        SupabaseClient.delete_where(supa, "agency_crm_tasks", filters={})
