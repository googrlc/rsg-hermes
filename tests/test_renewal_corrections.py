"""Correcting a renewal, and taking one off the worklist.

The renewal desk could type a premium onto a row and watch the 2:30am refresh
put the old one back, because project_85_renewals is re-projected from
renewal_candidates every night. Nothing could be removed from the list at all.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hermes import api
from hermes.renewals import corrections as corr

REN_ID = "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f"
CAND_ID = "aa11bb22-cc33-4d44-8e55-f66677778888"
LAMAR = "lamar@risksolutionsgroup.net"

RENEWAL = {"id": REN_ID, "policy_number": "CPP4471902", "client_name": "1Asfg LLC",
           "expiration_date": "2027-03-01", "premium_current": 4200,
           "premium_renewal": None, "risk_status": "AT_RISK",
           "ai_strategy_notes": "Auto-generated note", "last_contact_date": None}

CANDIDATE = {"id": CAND_ID, "insured_id": "guid-1", "policy_lineage_id": "guid-1:gl:CPP4471902",
             "renewal_event_date": "2027-03-01", "policy_number": "CPP4471902",
             "eligibility_state": "eligible", "premium_current": 4200}


@pytest.fixture
def c_supa(monkeypatch):
    supa = MagicMock()
    monkeypatch.setattr(api, "_get_supa", lambda: supa)
    monkeypatch.setattr(api, "_require_users", lambda *a, **k: None)
    return TestClient(api.app), supa


def _table_select(mapping):
    """supa.select stub keyed by table name."""
    return lambda table, **k: list(mapping.get(table, []))


# ── Correcting a field ───────────────────────────────────────────────────────

def test_a_correction_is_recorded_and_written_through(c_supa):
    """Both halves matter: the override is what survives the nightly refresh, the
    row write is what makes the number right before then."""
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "premium_current", "value": 4500,
                     "approved_by": LAMAR, "reason": "came over wrong from the AMS"})
    assert r.status_code == 200, r.text

    ovr = next(ca for ca in supa.insert.call_args_list if ca.args[0] == "portal_overrides").args[1]
    assert ovr["entity_type"] == "project_85_renewals"
    assert ovr["entity_key"] == "CPP4471902"        # natural key — survives a re-projection
    assert ovr["field_name"] == "premium_current"
    assert ovr["override_value"] == 4500
    assert ovr["original_value"] == 4200            # what the source said, for reconcile
    assert ovr["approved_by"] == LAMAR

    table, rec_id, payload = supa.update.call_args.args
    assert (table, rec_id) == ("project_85_renewals", REN_ID)
    assert payload["premium_current"] == 4500


def test_the_change_percentage_cannot_be_corrected(c_supa):
    """It is generated in Postgres from the two premiums. Fix those."""
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "increase_percentage", "value": 12, "approved_by": LAMAR})
    assert r.status_code == 400
    assert "not correctable" in r.json()["detail"]


def test_the_policy_number_cannot_be_corrected(c_supa):
    """It is how the row is matched back to the AMS — and the correction's own key."""
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "policy_number", "value": "OTHER-1", "approved_by": LAMAR})
    assert r.status_code == 400


def test_risk_status_is_normalised_before_it_reaches_the_enum(c_supa):
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "risk_status", "value": "critical", "approved_by": LAMAR})
    assert r.status_code == 200
    assert supa.update.call_args.args[2]["risk_status"] == "CRITICAL"


def test_an_unknown_risk_status_names_the_valid_ones(c_supa):
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "risk_status", "value": "SPICY", "approved_by": LAMAR})
    assert r.status_code == 400
    assert "CRITICAL" in r.json()["detail"]


def test_a_premium_that_is_not_a_number_is_refused(c_supa):
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "premium_current", "value": "four thousand",
                     "approved_by": LAMAR})
    assert r.status_code == 400
    supa.insert.assert_not_called()


def test_clearing_a_field_is_a_correction(c_supa):
    """A renewal premium the carrier never quoted should be removable."""
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "premium_renewal", "value": "", "approved_by": LAMAR})
    assert r.status_code == 200
    assert supa.update.call_args.args[2]["premium_renewal"] is None


def test_correcting_an_unknown_renewal_is_a_404(c_supa):
    c, supa = c_supa
    supa.select.side_effect = _table_select({})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "premium_current", "value": 1, "approved_by": LAMAR})
    assert r.status_code == 404


def test_a_correction_needs_a_named_approver(c_supa):
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    r = c.post(f"/api/renewals/{REN_ID}/override",
               json={"field_name": "premium_current", "value": 4500})
    assert r.status_code == 422


# ── Removing a renewal ───────────────────────────────────────────────────────

def test_removing_a_renewal_excludes_the_event_underneath_it(c_supa):
    """Dismissing only the projection row lasts until 2:30am — the refresh
    re-projects the same renewal from renewal_candidates."""
    c, supa = c_supa
    supa.select.side_effect = _table_select({
        "project_85_renewals": [dict(RENEWAL)],
        "renewal_candidates": [dict(CANDIDATE)],
    })
    r = c.request("DELETE", f"/api/renewals/{REN_ID}",
                  json={"deleted_by": LAMAR, "reason": "already renewed under a new number"})
    assert r.status_code == 200, r.text
    assert r.json()["events_excluded"] == 1

    written = [ca.args[1] for ca in supa.insert.call_args_list if ca.args[0] == "portal_overrides"]
    by_type = {o["entity_type"]: o for o in written}
    assert by_type["project_85_renewals"]["field_name"] == "dismissed"
    assert by_type["project_85_renewals"]["override_value"] is True
    assert by_type["renewal_candidates"]["field_name"] == "eligibility_state"
    assert by_type["renewal_candidates"]["override_value"] == "excluded"
    assert by_type["renewal_candidates"]["entity_key"] == "guid-1|guid-1:gl:CPP4471902|2027-03-01"


def test_removing_a_renewal_never_deletes_the_row(c_supa):
    """renewal_actions cascades off project_85_renewals — a DELETE would erase the
    record of the work already done on the renewal."""
    c, supa = c_supa
    supa.select.side_effect = _table_select({
        "project_85_renewals": [dict(RENEWAL)], "renewal_candidates": [],
    })
    c.request("DELETE", f"/api/renewals/{REN_ID}", json={"deleted_by": LAMAR})
    supa.delete.assert_not_called()
    supa.delete_where.assert_not_called()


def test_removing_an_unknown_renewal_is_a_404(c_supa):
    c, supa = c_supa
    supa.select.side_effect = _table_select({})
    r = c.request("DELETE", f"/api/renewals/{REN_ID}", json={"deleted_by": LAMAR})
    assert r.status_code == 404
    supa.insert.assert_not_called()


def test_a_removal_needs_a_named_actor(c_supa):
    c, supa = c_supa
    supa.select.side_effect = _table_select({"project_85_renewals": [dict(RENEWAL)]})
    assert c.request("DELETE", f"/api/renewals/{REN_ID}", json={}).status_code == 422


# ── Undoing ──────────────────────────────────────────────────────────────────

def test_undoing_a_correction_restores_what_the_source_said(c_supa, monkeypatch):
    c, supa = c_supa
    monkeypatch.setattr(
        "hermes.overrides.store.withdraw",
        lambda supa, oid, actor: {"id": oid, "entity_type": "project_85_renewals",
                                  "entity_key": "CPP4471902", "field_name": "premium_current",
                                  "original_value": 4200, "status": "retired"},
    )
    supa.select.side_effect = _table_select({"project_85_renewals": [{"id": REN_ID}]})
    r = c.delete(f"/api/renewals/overrides/abc?approved_by={LAMAR}")
    assert r.status_code == 200
    table, rec_id, payload = supa.update.call_args.args
    assert (table, rec_id) == ("project_85_renewals", REN_ID)
    assert payload["premium_current"] == 4200


def test_undoing_a_removal_touches_no_column(c_supa, monkeypatch):
    """'dismissed' is not a column — restoring it would 400 the whole undo."""
    c, supa = c_supa
    monkeypatch.setattr(
        "hermes.overrides.store.withdraw",
        lambda supa, oid, actor: {"id": oid, "entity_type": "project_85_renewals",
                                  "entity_key": "CPP4471902", "field_name": "dismissed",
                                  "original_value": None, "status": "retired"},
    )
    r = c.delete(f"/api/renewals/overrides/abc?approved_by={LAMAR}")
    assert r.status_code == 200
    supa.update.assert_not_called()


# ── The list the desk reads ──────────────────────────────────────────────────

def test_the_cockpit_list_shows_corrections_and_hides_removals(c_supa, monkeypatch):
    c, supa = c_supa
    rows = [dict(RENEWAL), dict(RENEWAL, id="other", policy_number="GONE-1",
                                client_name="Removed Co")]
    supa.select.side_effect = _table_select({"project_85_renewals": rows})
    monkeypatch.setattr(
        "hermes.overrides.store.active_overrides",
        lambda supa, entity_type, **k: {
            ("project_85_renewals", "CPP4471902", "premium_current"):
                corr_override("CPP4471902", "premium_current", 4500),
            ("project_85_renewals", "GONE-1", "dismissed"):
                corr_override("GONE-1", "dismissed", True),
        },
    )
    monkeypatch.setattr(api, "_get_supa", lambda: supa)
    body = c.get("/api/command-center/renewals").json()
    listed = body.get("upcoming") or body.get("renewals") or []
    numbers = {r.get("policy_number") for r in listed}
    assert "GONE-1" not in numbers
    assert body["total"] == 1


def corr_override(key, field, value):
    from hermes.overrides.core import Override

    return Override.from_row({
        "id": f"o-{key}-{field}", "entity_type": "project_85_renewals", "entity_key": key,
        "field_name": field, "override_value": value, "original_value": None,
        "status": "active", "approved_by": LAMAR,
    })


# ── The nightly rebuild ──────────────────────────────────────────────────────

def test_the_refresh_does_not_overwrite_a_corrected_premium(monkeypatch):
    """The whole point: the projection rebuilds every night from the candidate,
    and used to put the AMS's wrong number straight back."""
    from hermes.renewals import candidate_refresh as cr

    supa = MagicMock()
    supa.select.return_value = []          # no renewal_actions to protect
    monkeypatch.setattr(
        "hermes.overrides.store.active_overrides",
        lambda s, entity_type, **k: (
            {("project_85_renewals", "CPP4471902", "premium_current"):
                corr_override("CPP4471902", "premium_current", 4500)}
            if entity_type == "project_85_renewals" else {}),
    )
    cr._project_eligible(supa, [{"policy_number": "CPP4471902", "client_name": "1Asfg LLC",
                                 "renewal_event_date": "2027-03-01", "premium_current": 4200,
                                 "risk_status": "AT_RISK", "normalized_status": "Active"}])
    payload = supa.upsert.call_args.args[1]
    assert payload["premium_current"] == 4500
    assert "dismissed" not in payload and "_overridden" not in payload


def test_the_refresh_does_not_hand_back_a_removed_renewal(monkeypatch):
    from hermes.renewals import candidate_refresh as cr

    supa = MagicMock()
    supa.select.return_value = []
    monkeypatch.setattr(
        "hermes.overrides.store.active_overrides",
        lambda s, entity_type, **k: (
            {("project_85_renewals", "CPP4471902", "dismissed"):
                corr_override("CPP4471902", "dismissed", True)}
            if entity_type == "project_85_renewals" else {}),
    )
    counts = cr._project_eligible(supa, [{"policy_number": "CPP4471902",
                                          "renewal_event_date": "2027-03-01"}])
    supa.upsert.assert_not_called()
    assert counts["projected"] == 0


# ── Identity ─────────────────────────────────────────────────────────────────

def test_a_candidate_key_splits_back_to_its_identity():
    """The lineage id in the middle is itself colon-composite, so the key has to
    split from both ends rather than by counting separators."""
    key = corr.candidate_key(CANDIDATE)
    assert key == "guid-1|guid-1:gl:CPP4471902|2027-03-01"
    assert corr.split_key(key) == ("guid-1", "guid-1:gl:CPP4471902", "2027-03-01")
    assert corr.key_filters(key)["renewal_event_date"] == "eq.2027-03-01"


def test_a_malformed_key_is_refused():
    with pytest.raises(ValueError):
        corr.split_key("guid-1|2027-03-01")
