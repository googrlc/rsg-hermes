"""Policy corrections — the same override mechanism as clients, one rule tighter.

A policy's identifiers (policy_guid, the insured guid, renewed_policy,
policy_number) come out of NowCerts and are how the row is matched back to the
AMS and tied to the term it renewed. They are shown, and they are read-only:
"correcting" one detaches the record rather than fixing it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes import api

GUID = "6f1c2d84-3b90-4f5e-9a21-0c7ad3e51b44"
INSURED = "bfe42b77-b1a8-4729-aa10-af8494d05a9b"
POLICY = {
    "policy_guid": GUID, "policy_number": "CPP-4471902", "renewed_policy": None,
    "nowcerts_insured_guid": INSURED, "carrier": "Nationwide",
    "lines_of_business": "General Liability", "status": "active",
    "effective_date": "2026-03-01", "expiration_date": "2027-03-01",
    "premium_amount": 4200.0, "annualized_premium": 4200.0,
}


@pytest.fixture
def client_and_supa(monkeypatch):
    supa = MagicMock()
    monkeypatch.setattr(api, "_get_supa", lambda: supa)
    monkeypatch.setattr(api, "_require_users", lambda *a, **k: None)
    monkeypatch.setattr(api.ams_book, "select_policies",
                        lambda *a, **k: [dict(POLICY)])
    return TestClient(api.app), supa


@pytest.mark.parametrize(
    "field", ["policy_guid", "nowcerts_insured_guid", "renewed_policy", "policy_number"]
)
def test_nowcerts_identifiers_are_not_editable(client_and_supa, field):
    c, _ = client_and_supa
    r = c.post(f"/api/policies/{GUID}/override",
               json={"field_name": field, "value": "x",
                     "approved_by": "lamar@risksolutionsgroup.net"})
    assert r.status_code == 400
    assert "not overridable" in r.json()["detail"]


def test_an_unknown_policy_is_a_404(client_and_supa, monkeypatch):
    c, _ = client_and_supa
    monkeypatch.setattr(api.ams_book, "select_policies", lambda *a, **k: [])
    r = c.post(f"/api/policies/{GUID}/override",
               json={"field_name": "carrier", "value": "Travelers",
                     "approved_by": "lamar@risksolutionsgroup.net"})
    assert r.status_code == 404


def test_a_correction_records_the_source_value_it_replaced(client_and_supa):
    c, _ = client_and_supa
    with patch("hermes.overrides.store.set_override", return_value={"id": "o1"}) as so:
        r = c.post(f"/api/policies/{GUID}/override",
                   json={"field_name": "annualized_premium", "value": 4750,
                         "approved_by": "lamar@risksolutionsgroup.net",
                         "reason": "endorsement not synced"})
    assert r.status_code == 200
    kw = so.call_args.kwargs
    assert kw["entity_type"] == api.POLICY_ENTITY_TYPE
    assert kw["entity_key"] == GUID           # the policy guid, not the insured's
    assert kw["override_value"] == 4750
    assert kw["original_value"] == 4200.0


def test_an_override_needs_a_named_approver(client_and_supa):
    c, _ = client_and_supa
    r = c.post(f"/api/policies/{GUID}/override",
               json={"field_name": "carrier", "value": "Travelers"})
    assert r.status_code == 422


def test_corrections_are_applied_when_the_book_is_read(monkeypatch):
    from hermes.overrides.core import Override

    supa = MagicMock()
    ov = Override(entity_type=api.POLICY_ENTITY_TYPE, entity_key=GUID,
                  field_name="carrier", override_value="Travelers",
                  original_value="Nationwide", status="active")
    with patch("hermes.overrides.store.active_overrides",
               return_value={(api.POLICY_ENTITY_TYPE, GUID, "carrier"): ov}):
        rows = api._apply_policy_overrides(supa, [dict(POLICY)])
    assert rows[0]["carrier"] == "Travelers"
    assert rows[0]["_overridden"] == {"carrier": "Nationwide"}


def test_a_failed_override_lookup_still_serves_the_book(monkeypatch):
    supa = MagicMock()
    with patch("hermes.overrides.store.active_overrides", side_effect=RuntimeError("boom")):
        rows = api._apply_policy_overrides(supa, [dict(POLICY)])
    assert rows == [POLICY]


def test_a_corrected_date_is_in_effect_before_the_terms_are_ranked(monkeypatch):
    """Corrections land before the renewal-overlap collapse and the soonest-first
    sort, so a fixed expiration changes what the 360 says is coming up next —
    not just how the row reads."""
    from hermes.overrides.core import Override

    supa = MagicMock()
    supa.select.return_value = []
    other = dict(POLICY, policy_guid="a" * 36, policy_number="CA-88120",
                 lines_of_business="Commercial Auto", expiration_date="2026-06-01")
    monkeypatch.setattr(api, "_get_supa", lambda: supa)
    monkeypatch.setattr(api.ams_book, "select_policies",
                        lambda *a, **k: [dict(POLICY), other])
    ov = Override(entity_type=api.POLICY_ENTITY_TYPE, entity_key=GUID,
                  field_name="expiration_date", override_value="2026-01-15",
                  original_value="2027-03-01", status="active")
    with patch("hermes.overrides.store.active_overrides",
               return_value={(api.POLICY_ENTITY_TYPE, GUID, "expiration_date"): ov}):
        body = TestClient(api.app).get(f"/api/clients/{INSURED}").json()
    # GL was the later of the two before the correction; corrected, it is next up.
    assert [p["policy_guid"] for p in body["policies"]] == [GUID, "a" * 36]
    assert body["policies"][0]["expiration_date"] == "2026-01-15"
