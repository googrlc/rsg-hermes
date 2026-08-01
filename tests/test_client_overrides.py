"""Client corrections — the CRM's edit surface.

The book is a read-only mirror of NowCerts, so a wrong phone number used to mean
opening the AMS. An override is a human correction that outranks the synced
value until the source catches up; it is deliberately NOT a write to NowCerts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes import api
from hermes.routers import deps

GUID = "bfe42b77-b1a8-4729-aa10-af8494d05a9b"
CLIENT = {
    "nowcerts_insured_guid": GUID, "insured_name": "1Asfg LLC",
    "client_type": "Commercial", "city": "Marietta", "state": "Georgia",
    "email": "1afieldsg@gmail.com", "phone": None,
}


@pytest.fixture
def client_and_supa(monkeypatch):
    supa = MagicMock()
    supa.select.return_value = [dict(CLIENT)]
    monkeypatch.setattr(deps, "get_supa", lambda: supa)
    monkeypatch.setattr(deps, "require_users", lambda *a, **k: None)
    return TestClient(api.app), supa


def test_a_field_outside_the_allowlist_is_refused(client_and_supa):
    """An allowlist, not an open door — this writes to the book's display layer."""
    c, _ = client_and_supa
    r = c.post(f"/api/clients/{GUID}/override",
               json={"field_name": "nowcerts_insured_guid", "value": "x",
                     "approved_by": "lamar@risksolutionsgroup.net"})
    assert r.status_code == 400
    assert "not overridable" in r.json()["detail"]


def test_an_unknown_client_is_a_404(client_and_supa):
    c, supa = client_and_supa
    supa.select.return_value = []
    r = c.post(f"/api/clients/{GUID}/override",
               json={"field_name": "phone", "value": "404-555-0101",
                     "approved_by": "lamar@risksolutionsgroup.net"})
    assert r.status_code == 404


def test_a_correction_records_the_source_value_it_replaced(client_and_supa):
    """original_value must be what the SOURCE reports, not the previous override —
    reconciliation compares against it to decide whether the AMS caught up."""
    c, _ = client_and_supa
    with patch("hermes.overrides.store.set_override", return_value={"id": "o1"}) as so:
        r = c.post(f"/api/clients/{GUID}/override",
                   json={"field_name": "email", "value": "new@example.com",
                         "approved_by": "lamar@risksolutionsgroup.net", "reason": "bounced"})
    assert r.status_code == 200
    kw = so.call_args.kwargs
    assert kw["entity_key"] == GUID           # keyed on the guid: survives a re-seed
    assert kw["field_name"] == "email"
    assert kw["override_value"] == "new@example.com"
    assert kw["original_value"] == "1afieldsg@gmail.com"
    assert kw["reason"] == "bounced"


def test_an_override_needs_a_named_approver(client_and_supa):
    c, _ = client_and_supa
    r = c.post(f"/api/clients/{GUID}/override", json={"field_name": "phone", "value": "x"})
    assert r.status_code == 422       # approved_by is required by the model


def test_corrections_are_applied_when_the_book_is_read(monkeypatch):
    from hermes.overrides.core import Override

    supa = MagicMock()
    supa.select.return_value = [dict(CLIENT)]
    monkeypatch.setattr(deps, "get_supa", lambda: supa)
    ov = Override(entity_type=api.CLIENT_ENTITY_TYPE, entity_key=GUID,
                  field_name="phone", override_value="404-555-0101",
                  original_value=None, status="active")
    with patch("hermes.overrides.store.active_overrides",
               return_value={(api.CLIENT_ENTITY_TYPE, GUID, "phone"): ov}):
        rows = api._apply_client_overrides(supa, [dict(CLIENT)])
    assert rows[0]["phone"] == "404-555-0101"
    # and the surface says what was changed, rather than passing a human's value
    # off as the AMS's
    assert rows[0]["_overridden"] == {"phone": None}


def test_a_failed_override_lookup_still_serves_the_book(monkeypatch):
    """A correction is an enrichment; losing it must not take the client list down."""
    supa = MagicMock()
    with patch("hermes.overrides.store.active_overrides", side_effect=RuntimeError("boom")):
        rows = api._apply_client_overrides(supa, [dict(CLIENT)])
    assert rows == [CLIENT]
