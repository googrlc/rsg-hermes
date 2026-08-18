"""Zoho Renewal_Events / Renewals mapping + Creator override capture."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from hermes.sync.zoho_renewals import (
    creator_override_diffs,
    map_candidate_to_renewal_event,
    map_projection_to_renewal,
    run_zoho_renewals_sync,
)

TODAY = date(2026, 8, 18)

CANDIDATE = {
    "id": "cand-1",
    "insured_id": "guid-ins",
    "policy_lineage_id": "guid-ins:gl:CPP1",
    "renewal_event_date": "2026-11-10",
    "nowcerts_policy_guid": "guid-pol",
    "policy_number": "CPP1",
    "insured_active": True,
    "policy_active": True,
    "normalized_status": "Active",
    "branch": "current_term",
    "effective_date": "2025-11-10",
    "expiration_date": "2026-11-10",
    "eligibility_state": "eligible",
    "eligibility_reason": "current term in window",
    "segment": "commercial_mid",
    "line_of_business": "General Liability",
    "client_name": "Acme LLC",
    "in_working_queue": True,
    "risk_status": "SAFE",
    "premium_current": 4200,
    "premium_renewal": None,
}

P85 = {
    "id": "ren-1",
    "policy_number": "CPP1",
    "client_name": "Acme LLC",
    "expiration_date": "2026-11-10",
    "premium_current": 4200,
    "premium_renewal": None,
    "risk_status": "SAFE",
    "ai_strategy_notes": "keep",
    "last_contact_date": None,
}


def test_map_candidate_sets_external_id_and_key():
    payload = map_candidate_to_renewal_event(CANDIDATE, account_id="A1", policy_id="P1")
    assert payload["Hermes_Candidate_ID"] == "cand-1"
    assert payload["Renewal_Key"] == "guid-ins|guid-ins:gl:CPP1|2026-11-10"
    assert payload["Eligibility"] == "eligible"
    assert payload["Account_Name"] == {"id": "A1"}
    assert payload["Policy"] == {"id": "P1"}
    assert payload["Name"].startswith("CPP1")


def test_map_projection_create_sets_identified_and_window():
    payload = map_projection_to_renewal(
        P85,
        policy={"carrier": "Travelers", "lines_of_business": "General Liability"},
        creating=True,
        today=TODAY,
    )
    assert payload["Desk_Stage"] == "Identified"
    assert payload["Window_Bucket"] == "90"
    assert payload["Carrier"] == "Travelers"
    assert payload["Hermes_Renewal_ID"] == "ren-1"


def test_map_projection_update_omits_desk_owned_fields():
    payload = map_projection_to_renewal(P85, creating=False, today=TODAY)
    assert "Desk_Stage" not in payload
    assert "Disposition" not in payload
    assert "Recommended_Action" not in payload
    assert "Touch_Early" not in payload
    assert payload["Policy_Number"] == "CPP1"
    assert payload["Window_Bucket"] == "90"


def test_creator_premium_edit_becomes_an_override():
    diffs = creator_override_diffs(
        P85,
        {"Policy_Number": "CPP1", "Premium_Current": 4500, "Dismissed": False},
    )
    assert len(diffs) == 1
    assert diffs[0]["field_name"] == "premium_current"
    assert diffs[0]["override_value"] == 4500
    assert diffs[0]["original_value"] == 4200
    assert diffs[0]["entity_key"] == "CPP1"
    assert diffs[0]["approved_by"] == "zoho_creator"


def test_matching_active_override_is_not_recaptured():
    diffs = creator_override_diffs(
        P85,
        {"Policy_Number": "CPP1", "Premium_Current": 4500},
        active_overrides={("CPP1", "premium_current"): 4500},
    )
    assert diffs == []


def test_dismissed_on_zoho_is_captured():
    diffs = creator_override_diffs(
        P85,
        {"Policy_Number": "CPP1", "Dismissed": True},
    )
    assert any(d["field_name"] == "dismissed" and d["override_value"] is True for d in diffs)


def test_empty_zoho_field_is_not_a_creator_edit():
    diffs = creator_override_diffs(
        P85,
        {"Policy_Number": "CPP1", "Strategy_Notes": None},
    )
    assert diffs == []


class FakeZoho:
    def __init__(self):
        self.upserts = []
        self.existing = {}

    def search_records(self, module, criteria):
        return []

    def _find_first(self, module, criteria):
        key = criteria.split("equals:")[-1].rstrip(")")
        return self.existing.get((module, key))

    def upsert_by_field(self, module, record, *, match_field, match_value=None):
        value = match_value if match_value is not None else record.get(match_field)
        self.upserts.append((module, match_field, value, record))
        existing = self.existing.get((module, value))
        action = "updated" if existing else "created"
        rid = (existing or {}).get("id") or f"{module}-{value}"
        self.existing[(module, value)] = {"id": rid, **record}
        return {"id": rid, "action": action}


def test_run_sync_upserts_event_then_renewal_and_captures_override():
    supa = MagicMock()
    p85 = dict(P85)
    supa.select.side_effect = lambda table, **k: {
        "renewal_candidates": [dict(CANDIDATE)],
        "project_85_renewals": [p85],
        "canonical_policies": [{
            "policy_number": "CPP1",
            "policy_guid": "guid-pol",
            "nowcerts_insured_guid": "guid-ins",
            "carrier": "Travelers",
            "lines_of_business": "General Liability",
            "effective_date": "2025-11-10",
        }],
        "portal_overrides": [],
    }[table]
    zoho = FakeZoho()
    zoho.existing[("Renewals", "ren-1")] = {
        "id": "z-ren",
        "Hermes_Renewal_ID": "ren-1",
        "Policy_Number": "CPP1",
        "Premium_Current": 4500,
        "Dismissed": False,
    }
    result = run_zoho_renewals_sync(supa=supa, zoho=zoho, today=TODAY, dry_run=False)
    assert result.ok
    assert result.events_created == 1
    assert result.renewals_updated == 1
    assert result.overrides_captured == 1
    assert any(c.args[0] == "portal_overrides" for c in supa.insert.call_args_list)
    event_upsert = next(u for u in zoho.upserts if u[0] == "Renewal_Events")
    assert event_upsert[3]["Hermes_Candidate_ID"] == "cand-1"
    renewal_upsert = next(u for u in zoho.upserts if u[0] == "Renewals")
    assert "Desk_Stage" not in renewal_upsert[3]
    assert renewal_upsert[3]["Premium_Current"] == 4500
