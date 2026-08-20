"""Phase-1 Desk apply plan — no live Zoho calls."""

from __future__ import annotations

from hermes.desk.fields import SHARED_FIELDS
from hermes.desk.setup import DESK_TYPES, field_payload, plan_phase1
from hermes.desk.spec import DEPARTMENT, TEAMS


def test_field_payload_maps_desk_types_and_marks_sensitive():
    license_field = next(f for f in SHARED_FIELDS if f.api_name == "cf_ams_client_id")
    payload = field_payload(license_field)
    assert payload["displayLabel"] == "AMS Client ID"
    assert payload["type"] == "Text"
    assert payload["showToHelpCenter"] is False

    pick = next(f for f in SHARED_FIELDS if f.api_name == "cf_request_category")
    p = field_payload(pick)
    assert p["type"] == "PickList"
    assert {"value": "Certificate Request"} in p["allowedValues"]
    assert p["isMandatory"] is True

    from hermes.desk.fields import AUTO_DRIVER_FIELDS

    dob = next(f for f in AUTO_DRIVER_FIELDS if f.api_name == "cf_driver_dob")
    d = field_payload(dob)
    assert d["type"] == "Date"
    assert d["isEncryptedField"] is True

    url = next(f for f in SHARED_FIELDS if f.api_name == "cf_document_folder_link")
    assert field_payload(url)["type"] == "Website"
    dt = next(f for f in SHARED_FIELDS if f.api_name == "cf_last_sync_date")
    assert field_payload(dt)["type"] == "Date Time"
    assert set(DESK_TYPES) >= {"Picklist", "URL", "DateTime", "Boolean"}


def test_plan_skips_existing_and_creates_the_rest():
    plan = plan_phase1(
        departments=[{"id": "d1", "name": DEPARTMENT}],
        fields=[{"displayLabel": "AMS Client ID"}],
        teams=[{"name": "Service Intake"}],
        agent_ids=["agent-1"],
    )
    assert plan.existing_department_id == "d1"
    assert plan.create_department is None
    names = {item.name for item in plan.fields}
    assert "AMS Client ID" not in names
    assert "Request Category" in names
    team_names = {item.name for item in plan.teams}
    assert "Service Intake" not in team_names
    assert set(TEAMS) - {"Service Intake"} <= team_names
    assert "department:Agency Service" in plan.skipped
    assert "field:AMS Client ID" in plan.skipped


def test_plan_creates_department_when_missing():
    plan = plan_phase1(
        departments=[],
        fields=[],
        teams=[],
        agent_ids=["a1", "a2"],
    )
    assert plan.create_department is not None
    assert plan.create_department.payload["name"] == DEPARTMENT
    assert plan.create_department.payload["associatedAgentIds"] == ["a1", "a2"]
    assert plan.create_department.payload["isAssignToTeamEnabled"] is True
    assert len(plan.teams) == len(TEAMS)
    assert len(plan.fields) >= 40
