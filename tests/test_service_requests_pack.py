"""Service_Requests CRM pack — CSV, apply plan, Deluge, Catalyst mapping.

No live Zoho network in these tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes.zoho.apply import apply_plan, build_plan, inspect_live, module_create_payload
from hermes.zoho.crm_buttons import (
    ACCOUNT_BUTTON,
    CONNECTION_NAME,
    EMAIL_BUTTON,
    POLICY_BUTTON,
    all_deluge,
    button_catalog,
    render_account_button,
    render_email_button,
    render_policy_button,
    render_workflow_on_complete,
    render_workflow_on_create,
)
from hermes.zoho.service_requests import (
    CASES_INSURANCE_FIELDS,
    CSV_HEADERS,
    FIELDS_CSV,
    MODULE_API_NAME,
    REQUEST_TYPES,
    STATUSES,
    catalyst_row,
    existing_field,
    field_create_payload,
    load_catalyst_map,
    load_field_pack,
    matches_query,
    matches_view,
    names_match,
    plan_actions,
    suggest_request_type,
    status_to_desk_stage,
    vocab_values,
)

DOCS = Path("docs/zoho")
ENDORSEMENT_EXTRAS = {
    "Replace Driver",
    "Replace Vehicle",
    "Address Change",
    "Coverage Change",
    "Certificate of Insurance",
}


def test_csv_headers_match_the_other_field_packs():
    pack = load_field_pack()
    assert list(pack[0].keys()) == list(CSV_HEADERS)
    assert None not in pack[0]
    policies = (DOCS / "fields_policies.csv").read_text(encoding="utf-8").splitlines()[0]
    assert policies == ",".join(CSV_HEADERS)
    assert all(row["Module"] == MODULE_API_NAME for row in pack)
    assert all(row["Sync_Direction"] in {"Z-only", "System"} for row in pack)


def test_modules_custom_and_checklist_name_the_module():
    modules = (DOCS / "modules_custom.csv").read_text(encoding="utf-8")
    assert "Service_Requests" in modules
    assert "Claims" not in modules.splitlines()[0] and "Service_Requests" in modules
    checklist = (DOCS / "FIELD_CREATE_CHECKLIST.md").read_text(encoding="utf-8")
    assert "fields_service_requests.csv" in checklist
    assert "Service_Requests → Accounts" in checklist
    assert "do not create claims" in checklist.lower()
    readme = (DOCS / "README.md").read_text(encoding="utf-8")
    assert "fields_service_requests.csv" in readme
    assert (DOCS / "service_requests.md").is_file()
    assert (DOCS / "catalyst_field_map.csv").is_file()


def test_request_types_are_exact_and_do_not_import_endorsement_extras():
    assert REQUEST_TYPES == (
        "Certificate Request",
        "Policy Change",
        "Add Vehicle",
        "Remove Vehicle",
        "Add Driver",
        "Remove Driver",
        "Billing Question",
        "Claims Question",
        "Coverage Question",
        "Renewal Service",
        "Cancellation",
        "Reinstatement",
        "ID Card Request",
        "Mortgagee Change",
        "Document Request",
        "Other",
    )
    vocab = vocab_values("service_request_type")
    assert vocab == list(REQUEST_TYPES)
    assert ENDORSEMENT_EXTRAS.isdisjoint(set(REQUEST_TYPES))
    assert vocab_values("service_request_status") == list(STATUSES)
    assert vocab_values("service_request_priority") == ["Low", "Standard", "High"]


def test_account_lookup_is_required_and_system_fields_are_not_posted():
    pack = load_field_pack()
    by_api = {row["API_Name"]: row for row in pack}
    assert by_api["Account_Name"]["Mandatory"] == "Y"
    assert by_api["Account_Name"]["Data_Type"] == "Lookup (Accounts)"
    assert by_api["Contact_Name"]["Mandatory"] == "N"
    assert by_api["Policy"]["Data_Type"] == "Lookup (Policies)"
    assert by_api["Renewal"]["Data_Type"] == "Lookup (Renewals)"
    assert field_create_payload(by_api["Owner"]) is None
    assert field_create_payload(by_api["Created_Time"]) is None
    payload = field_create_payload(by_api["Account_Name"])
    assert payload["data_type"] == "lookup"
    assert payload["lookup"]["module"]["api_name"] == "Accounts"
    assert payload["required"] is True


def test_picklist_and_formula_payloads():
    pack = {row["API_Name"]: row for row in load_field_pack()}
    rt = field_create_payload(pack["Request_Type"])
    assert rt["data_type"] == "picklist"
    values = [item["display_value"] for item in rt["pick_list_values"]]
    assert values == list(REQUEST_TYPES)
    auto = field_create_payload(pack["Request_Number"])
    assert auto["data_type"] == "autonumber"
    assert auto["auto_number"]["prefix"] == "SR-"
    assert auto["auto_number"]["start_number"] == 1001
    formula = field_create_payload(pack["Service_Time"])
    assert formula["data_type"] == "formula"
    assert formula["formula"]["return_type"] == "double"
    assert "Datecomp" in formula["formula"]["expression"]
    overdue = field_create_payload(pack["Overdue"])
    assert overdue["formula"]["return_type"] == "boolean"


def test_idempotent_plan_skips_existing_fields_and_missing_lookup_targets():
    pack = load_field_pack()
    live_modules = [
        {"api_name": "Accounts"},
        {"api_name": "Contacts"},
        {"api_name": "Service_Requests", "id": "1"},
        # Policies / Renewals absent → those lookups skipped
    ]
    live_fields = [
        {"api_name": "Subject", "field_label": "Subject"},
        {"api_name": "Request_Type__c", "field_label": "Request Type"},
    ]
    actions = plan_actions(pack, live_modules=live_modules, live_fields=live_fields)
    by_name = {(a["kind"], a["api_name"]): a for a in actions if "api_name" in a}
    assert by_name[("module", "Service_Requests")]["action"] == "skip"
    assert by_name[("field", "Subject")]["action"] == "skip"
    assert by_name[("field", "Request_Type")]["action"] == "skip"
    assert by_name[("field", "Policy")]["action"] == "skip"
    assert "not present live" in by_name[("field", "Policy")]["reason"]
    assert by_name[("field", "Description")]["action"] == "create"
    assert by_name[("field", "Account_Name")]["action"] == "create"


def test_names_match_strips_zoho_suffixes():
    assert names_match("Request_Type__c", "Request_Type")
    assert names_match("Service_Requests", "Service_Requests")
    assert existing_field(
        [{"api_name": "Due_Date__s", "field_label": "Due Date"}],
        "Due_Date",
        "Due Date",
    )


def test_catalyst_row_projects_cases_shaped_fields():
    row = catalyst_row(
        {
            "id": "99",
            "Subject": "COI for jobsite",
            "Status": "In Progress",
            "Request_Type": "Certificate Request",
            "Policy_Number": "P-1",
            "Account_Name": {"id": "a1", "name": "ABC Trucking"},
            "Owner": {"id": "u1", "name": "Gretchen Coates"},
            "Overdue": "false",
            "Due_Date": "2026-09-01T00:00:00-04:00",
        }
    )
    assert row["module"] == MODULE_API_NAME
    assert row["Desk_Stage"] == "In progress"
    assert row["Client_Name"] == "ABC Trucking"
    assert row["Owner_Name"] == "Gretchen Coates"
    assert row["Request_Type_Label"] == "Certificate Request"
    assert row["Overdue"] is False
    assert status_to_desk_stage("In Progress") == "In progress"
    assert matches_view(row, view="desk")
    assert not matches_view(row, view="waiting")
    assert matches_query(row, q="abc", type_filter="Certificate Request")
    assert not matches_query(row, type_filter="Add Vehicle")


def test_suggest_request_type_uses_the_locked_labels():
    assert suggest_request_type("Need a COI", "") == "Certificate Request"
    assert suggest_request_type("please add vehicle", "") == "Add Vehicle"
    assert suggest_request_type("hello", "how are you") == "Other"


def test_deluge_creates_service_requests_and_never_desk():
    blob = all_deluge()
    assert "zoho.crm.create" in blob
    assert MODULE_API_NAME in blob
    assert "zoho.desk.create" not in blob.lower()
    assert "zoho.desk.search" not in blob.lower()
    assert "desk.zoho.com" not in blob.lower()
    assert "cf_crm_account_id" not in blob
    assert "desk.zoho.com" not in blob
    assert ACCOUNT_BUTTON  # label used on Accounts
    account = render_account_button()
    assert "getRecordById(\"Accounts\"" in account
    assert "Owner" in account
    policy = render_policy_button()
    assert "getRecordById(\"Policies\"" in policy
    assert "Policy_Number" in policy
    assert "Carrier" in policy
    email = render_email_button()
    assert "Certificate Request" in email
    assert CONNECTION_NAME == "zoho_crm"
    create_wf = render_workflow_on_create()
    assert "Status\", \"New\"" in create_wf or '"Status", "New"' in create_wf
    complete_wf = render_workflow_on_complete()
    assert "Closed_Date" in complete_wf
    catalog = button_catalog()
    assert {item["module"] for item in catalog} == {"Accounts", "Contacts", "Policies", "Emails"}
    assert any(item["name"] == EMAIL_BUTTON for item in catalog)
    assert any(item["name"] == POLICY_BUTTON for item in catalog)


def test_apply_dry_run_does_not_call_client():
    client = MagicMock()
    actions = build_plan(None)
    result = apply_plan(client, actions, apply=False)
    assert result["apply"] is False
    assert result["module_api_name"] == MODULE_API_NAME
    assert result["pending"]
    client._post.assert_not_called()
    client._put.assert_not_called()
    payload = module_create_payload()
    assert payload["modules"][0]["plural_label"] == "Service Requests"


def test_apply_writes_module_then_fields():
    client = MagicMock()
    client._post.return_value = {
        "modules": [{"details": {"id": "m1", "api_name": "Service_Requests"}}]
    }
    actions = [
        {
            "kind": "module",
            "action": "create",
            "api_name": MODULE_API_NAME,
        },
        {
            "kind": "field",
            "action": "create",
            "api_name": "Subject",
            "payload": {"field_label": "Subject", "data_type": "text"},
        },
        {"kind": "field", "action": "skip", "api_name": "Owner", "reason": "system"},
    ]
    result = apply_plan(client, actions, apply=True)
    assert result["apply"] is True
    assert client._post.call_count == 2
    assert result["errors"] == []
    assert len(result["created"]) == 2


def test_inspect_live_captures_auth_failure():
    client = MagicMock()
    client._get.side_effect = RuntimeError("invalid_code")
    inv = inspect_live(client)
    assert inv.auth_ok is False
    assert "invalid_code" in (inv.auth_error or "")


def test_inspect_live_flags_cases_insurance_fields_but_still_plans_service_requests():
    client = MagicMock()

    def _get(path, params=None):
        if path.endswith("/settings/modules"):
            return {
                "modules": [
                    {"api_name": "Cases"},
                    {"api_name": "Accounts"},
                    {"api_name": "Policies"},
                ]
            }
        if params and params.get("module") == "Cases":
            return {
                "fields": [
                    {"api_name": name, "field_label": name}
                    for name in CASES_INSURANCE_FIELDS
                ]
            }
        return {"fields": []}

    client._get.side_effect = _get
    inv = inspect_live(client)
    assert inv.auth_ok is True
    assert inv.has("Cases")
    assert not inv.has(MODULE_API_NAME)
    assert any("insurance-shaped" in n for n in inv.notes)
    assert any("retarget" in n.lower() for n in inv.notes)
    assert any("Policies exists live" in n for n in inv.notes)
    assert any("Claims" in n and "do not create" in n.lower() for n in inv.notes)


def test_catalyst_map_retargets_cases_to_service_requests():
    rows = load_catalyst_map()
    by_cat = {r["Catalyst_Field"]: r for r in rows}
    assert by_cat["Desk_Stage"]["Service_Requests_Logical_API"] == "Status"
    assert by_cat["module"]["Transform"] == "constant"
    assert "Service_Requests" in by_cat["module"]["Notes"] or by_cat["module"]["Service_Requests_Logical_API"]


def test_operator_doc_says_desk_is_not_sot():
    text = (DOCS / "service_requests.md").read_text(encoding="utf-8")
    assert "Desk is not the system of record" in text or "not the system of record" in text.lower()
    assert "cf_crm_account_id" in text
    assert "do not dual-write" in text.lower() or "Do not dual-write" in text
    assert "--apply" in text


def test_no_service_desk_supabase_table_in_this_pack():
    pack = load_field_pack()
    for row in pack:
        blob = " ".join(str(v) for v in row.values() if v)
        assert "agency_crm" not in blob.lower()
        assert row["Sync_Direction"] in {"Z-only", "System"}
    migrations = list(Path("supabase/migrations").glob("*service_request*"))
    assert migrations == []


# ── HTTP surface ──────────────────────────────────────────────────────────


@pytest.fixture
def api_client():
    from hermes.api import app

    return TestClient(app)


def test_desk_queue_reads_service_requests_not_desk(api_client, monkeypatch):
    records = [
        {
            "id": "sr-1",
            "Subject": "Add driver",
            "Status": "New",
            "Request_Type": "Add Driver",
            "Account_Name": {"name": "Patel Household"},
            "Policy_Number": "PA-22",
        },
        {
            "id": "sr-2",
            "Subject": "Waiting on carrier",
            "Status": "Waiting",
            "Request_Type": "Policy Change",
        },
    ]
    client = MagicMock()
    client._get.return_value = {"data": records}

    monkeypatch.setattr("hermes.routers.desk._zoho", lambda: client)
    resp = api_client.get("/api/desk", params={"view": "desk"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["module"] == MODULE_API_NAME
    assert body["not_desk"] is True
    assert body["source"] == "zoho_crm"
    ids = [row["id"] for row in body["rows"]]
    assert ids == ["sr-1"]
    assert "Add Driver" in body["request_types"]
    waiting = api_client.get("/api/desk", params={"view": "waiting"}).json()
    assert [row["id"] for row in waiting["rows"]] == ["sr-2"]


def test_desk_detail_and_email_do_not_open_desk(api_client, monkeypatch):
    client = MagicMock()
    client._get.return_value = {
        "data": [{"id": "sr-9", "Subject": "COI", "Status": "New", "Request_Type": "Certificate Request"}]
    }
    monkeypatch.setattr("hermes.routers.desk._zoho", lambda: client)
    detail = api_client.get("/api/desk/cases/sr-9")
    assert detail.status_code == 200
    assert detail.json()["case"]["Request_Type"] == "Certificate Request"
    assert detail.json()["module"] == MODULE_API_NAME
    email = api_client.post("/api/desk/cases/sr-9/email")
    assert email.status_code == 501
    assert "Desk" in email.json()["detail"]


def test_desk_close_writes_service_requests(api_client, monkeypatch):
    monkeypatch.setenv("HERMES_API_TOKEN", "secret")
    stored = {
        "id": "sr-9",
        "Subject": "COI",
        "Status": "Completed",
        "Request_Type": "Certificate Request",
        "Closed_Date": "2026-08-21T12:00:00+00:00",
    }
    client = MagicMock()
    client._put.return_value = {"data": [{"id": "sr-9"}]}
    client._get.return_value = {"data": [stored]}
    monkeypatch.setattr("hermes.routers.desk._zoho", lambda: client)
    resp = api_client.post(
        "/api/desk/cases/sr-9/close",
        json={"disposition": "done"},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
    posted = client._put.call_args.args[1]["data"][0]
    assert posted["id"] == "sr-9"
    assert posted["Status"] == "Completed"
    blob = json.dumps(client._put.call_args.args[1])
    assert "desk" not in blob.lower()
    assert "cf_crm" not in blob
