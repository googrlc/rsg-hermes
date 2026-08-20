"""Zoho Desk case-management spec and rules engine."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hermes.desk.blueprints import CERTIFICATE, can_transition
from hermes.desk.classify import classify_request
from hermes.desk.closure import closure_blockers, may_close
from hermes.desk.duplicates import possible_duplicates
from hermes.desk.fields import (
    ALL_FIELDS,
    CSV_COLUMNS,
    LAYOUT_FIELDS,
    render_fields_csv,
    render_picklists_csv,
    render_views_csv,
)
from hermes.desk.matching import AccountMatch, resolve_account
from hermes.desk.priority import HIGH, LOW, NORMAL, URGENT, keyword_alone_is_not_urgent, recommend_priority
from hermes.desk.renewals import renewal_identity, renewal_stage_for_days_out
from hermes.desk.routing import TicketSnapshot, apply_event, auto_driver_required_fields
from hermes.desk.spec import (
    AUTOMATION_IDS,
    AUTOMATIONS,
    CUSTOM_FUNCTIONS,
    DEPARTMENT,
    DEPARTMENT_ALIASES,
    EMAIL_TEMPLATE_REQUIRED_TOKENS,
    EMAIL_TEMPLATES,
    KNOWLEDGE_BASE_INTERNAL,
    LAUNCH_WORKFLOWS,
    OPERATIONAL_VIEWS,
    PHASES,
    STATUSES,
    SYSTEMS_OF_RECORD,
    TEAMS,
)
from hermes.desk.live import (
    DEPARTMENT_ID,
    LAYOUT_CLASSIFICATION_DEFAULTS,
    LAYOUT_IDS,
    NATIVE_BRIDGES,
    ORG_ID,
    TEAM_IDS,
)
from hermes.desk.titles import case_title

DOCS = Path("docs/zoho-desk")


def test_operating_model_keeps_systems_separate():
    assert DEPARTMENT == "Agency Service"
    assert "RSG" in DEPARTMENT_ALIASES
    assert "desk" in SYSTEMS_OF_RECORD
    assert "ams" in SYSTEMS_OF_RECORD
    assert "crm" in SYSTEMS_OF_RECORD
    assert LAUNCH_WORKFLOWS == (
        "Certificate Requests",
        "Vehicle and Driver Changes",
        "Billing, Cancellation, and Reinstatement",
        "General Policy Changes",
    )
    assert len(TEAMS) == 11
    assert "New" in STATUSES and "Monitoring" in STATUSES
    assert len(AUTOMATIONS) == 14
    assert AUTOMATION_IDS == tuple(item.automation_id for item in AUTOMATIONS)
    assert len(PHASES) == 4
    assert "CF-06 Renewal case generation" in CUSTOM_FUNCTIONS
    assert "ticket_number" in EMAIL_TEMPLATE_REQUIRED_TOKENS
    assert "Agency codes" in KNOWLEDGE_BASE_INTERNAL
    assert len(OPERATIONAL_VIEWS) >= 18
    assert len(EMAIL_TEMPLATES) >= 18


def test_case_title_format():
    assert (
        case_title("Certificate Request", "ABC Trucking LLC", "CA123456", "Holder request")
        == "Certificate | ABC Trucking LLC | CA123456 | Holder request"
    )
    assert "Unknown client" in case_title(None, None, None, None)


def test_asap_does_not_make_a_ticket_urgent():
    assert keyword_alone_is_not_urgent("Need this ASAP please")
    assert recommend_priority() == NORMAL
    assert recommend_priority(urgency_reason="Not urgent") == NORMAL
    assert (
        recommend_priority(urgency_reason="Cancellation or lapse imminent") == URGENT
    )
    assert recommend_priority(urgency_reason="Time-sensitive certificate") == HIGH
    assert recommend_priority(business_impact="Informational") == LOW
    assert (
        recommend_priority(
            cancellation_warning=True,
            cancellation_date=date(2026, 8, 25),
            today=date(2026, 8, 20),
        )
        == HIGH
    )


def test_classify_certificate_and_vehicle_change():
    cert = classify_request("COI needed", "Please send a certificate of insurance to the holder.")
    assert cert.category == "Certificate Request"
    assert not cert.uncertain

    auto = classify_request("Add a vehicle", "Please add VIN 1HGCM82633A004352 effective Monday.")
    assert auto.category == "Policy Change"
    assert auto.subtype == "Add vehicle"

    unknown = classify_request("Hello", "Can you help?")
    assert unknown.uncertain
    assert unknown.category is None


def test_aut_01_uncertain_goes_to_intake():
    action = apply_event(TicketSnapshot(subject="help"), "ticket_created")
    assert "AUT-01" in action.automation_ids
    assert action.status == "New"
    assert action.team == "Service Intake"
    assert "no_account_match" in action.notifications


def test_aut_02_certificate_routing():
    ticket = TicketSnapshot(
        category="Certificate Request",
        ams_client_id="guid",
        account_id="acc",
        required_by=date(2026, 8, 22),
        fields={"cf_holder_name": "Acme", "cf_holder_address": "1 Main", "cf_required_by_date": "2026-08-22", "cf_ams_client_id": "guid", "cf_policy_number": "P1"},
    )
    action = apply_event(ticket, "ticket_created")
    assert "AUT-02" in action.automation_ids
    assert action.layout == "certificate"
    assert action.team == "Certificates"
    assert "Certificate request received" in action.emails
    assert action.require_fields == ()


def test_aut_03_blocks_ready_until_vin_present():
    ticket = TicketSnapshot(
        category="Policy Change",
        subtype="Add vehicle",
        line_of_business="Commercial Auto",
        account_id="acc",
        ams_client_id="guid",
        fields={"cf_effective_date_requested": "2026-09-01"},
    )
    action = apply_event(ticket, "ticket_created")
    assert "AUT-03" in action.automation_ids
    assert action.layout == "auto_driver"
    assert action.team == "Commercial Auto Service"
    assert action.block_status == "Ready for Processing"
    assert "cf_vin" in action.require_fields
    assert "cf_vin" in auto_driver_required_fields("Add vehicle")


def test_aut_03_personal_lines_team():
    ticket = TicketSnapshot(
        category="Policy Change",
        subtype="Add driver",
        line_of_business="Personal Auto",
        account_id="a",
        ams_client_id="g",
        fields={"cf_effective_date_requested": "2026-09-01", "cf_driver_name": "Pat", "cf_license_state": "GA"},
    )
    action = apply_event(ticket, "ticket_created")
    assert action.team == "Personal Lines Service"
    assert action.block_status is None


def test_aut_04_cancellation_warning_is_high_or_urgent():
    ticket = TicketSnapshot(
        category="Billing and Payments",
        cancellation_warning=True,
        cancellation_date=date(2026, 8, 21),
        account_id="a",
        ams_client_id="g",
    )
    action = apply_event(ticket, "ticket_created", today=date(2026, 8, 20))
    assert "AUT-04" in action.automation_ids
    assert action.team == "Billing and Retention"
    assert action.priority in {HIGH, URGENT}
    assert action.flags.get("disposition_required") is True


def test_aut_05_and_07_waiting_on_client():
    waiting = TicketSnapshot(status="Waiting on Client")
    entered = apply_event(waiting, "status_changed")
    assert "AUT-05" in entered.automation_ids
    assert "cf_missing_information" in entered.require_fields
    assert "cf_next_follow_up_date" in entered.require_fields
    assert "Missing information" in entered.emails

    reply = apply_event(waiting, "customer_reply")
    assert "AUT-07" in reply.automation_ids
    assert reply.status == "Ready for Processing"
    assert reply.waiting_on == "None"


def test_aut_06_and_08_waiting_on_carrier():
    waiting = TicketSnapshot(status="Waiting on Carrier")
    entered = apply_event(waiting, "status_changed")
    assert "AUT-06" in entered.automation_ids
    assert "cf_carrier" in entered.require_fields
    assert "cf_next_follow_up_date" in entered.require_fields

    reply = apply_event(waiting, "carrier_reply")
    assert "AUT-08" in reply.automation_ids
    assert reply.status == "In Progress"


def test_aut_09_required_by_reminder():
    ticket = TicketSnapshot(
        status="In Progress",
        required_by=date(2026, 8, 20),
        priority=NORMAL,
    )
    action = apply_event(ticket, "required_by_approaching", today=date(2026, 8, 20))
    assert "AUT-09" in action.automation_ids
    assert "escalate_service_lead" in action.notifications
    assert action.priority in {HIGH, URGENT}


def test_aut_10_rolls_back_incomplete_closure():
    ticket = TicketSnapshot(status="Resolved")
    action = apply_event(ticket, "status_changed", outstanding_tasks=True)
    assert "AUT-10" in action.automation_ids
    assert action.rollback_to == "In Progress"
    assert action.status == "In Progress"
    assert "outstanding_task" in action.require_fields
    assert not may_close(ticket, outstanding_tasks=True)


def test_aut_10_allows_complete_closure():
    ticket = TicketSnapshot(
        status="Closed",
        resolution_type="Completed",
        ams_activity_posted=True,
        customer_confirmation_sent=True,
        final_documents_stored=True,
        fields={"cf_resolution_type": "Completed", "cf_customer_confirmation_sent": True, "cf_final_documents_stored": True},
    )
    assert closure_blockers(ticket) == ()
    action = apply_event(ticket, "status_changed")
    assert action.rollback_to is None
    assert "AUT-10" in action.automation_ids


def test_aut_11_reopen_increments_count():
    ticket = TicketSnapshot(status="Closed", previous_owner="gretchen@x", reopen_count=1)
    action = apply_event(ticket, "client_reply_after_resolution")
    assert "AUT-11" in action.automation_ids
    assert action.status == "In Progress"
    assert action.owner == "gretchen@x"
    assert action.flags["reopen_count"] == 2
    assert "cf_reopened_reason" in action.require_fields


def test_aut_12_flags_duplicate_and_does_not_delete():
    candidate = TicketSnapshot(
        contact_id="c1",
        policy_number="P1",
        category="Certificate Request",
        subject="Certificate for ABC holder",
    )
    other = TicketSnapshot(
        contact_id="c1",
        policy_number="P1",
        category="Certificate Request",
        subject="Certificate request ABC holder",
        fields={"created_on": date(2026, 8, 18)},
    )
    flags = possible_duplicates(candidate, [("T-99", other)], today=date(2026, 8, 20))
    assert flags and flags[0].other_ticket_id == "T-99"
    action = apply_event(candidate, "ticket_created", possible_duplicate=True)
    assert "AUT-12" in action.automation_ids
    assert action.possible_duplicate is True
    assert any("not auto-delete" in note.lower() or "Do not auto-delete" in note for note in action.notes)


def test_aut_13_sales_handoff_keeps_service_ticket_open():
    ticket = TicketSnapshot(
        category="General Service",
        account_id="a",
        ams_client_id="g",
        subject="Also want a quote for a new location",
    )
    action = apply_event(ticket, "ticket_created")
    assert "AUT-13" in action.automation_ids
    assert action.flags["sales_opportunity"] is True
    assert action.flags["keep_service_ticket_open"] is True


def test_aut_14_ams_posting_marks_success_only_after_response():
    ticket = TicketSnapshot(status="Resolved", resolution_type="Completed")
    pending = apply_event(ticket, "status_changed")
    assert "AUT-14" in pending.automation_ids
    failed = apply_event(ticket, "status_changed", ams_post_succeeded=False)
    assert failed.flags["integration_status"] == "Failed"
    assert failed.flags["ams_activity_posted"] is False
    ok = apply_event(ticket, "status_changed", ams_post_succeeded=True)
    assert ok.flags["ams_activity_posted"] is True
    assert ok.flags["integration_status"] == "Synced"


def test_certificate_blueprint_requires_identified_wording_not_yes():
    present = {
        "cf_ams_client_id": "g",
        "cf_policy_number": "P1",
        "cf_holder_name": "Acme",
        "cf_holder_address": "1 Main",
        "cf_required_by_date": "2026-08-22",
        "cf_special_wording_requested": False,
    }
    ok, missing = can_transition(CERTIFICATE, "Validate Request", present)
    assert ok and missing == ()

    ok, missing = can_transition(CERTIFICATE, "Prepare Certificate", present)
    assert not ok
    assert "cf_coverage_verified" in missing


def test_cf01_multiple_matches_flag_manual_selection():
    unmatched = resolve_account([])
    assert unmatched.status == "unmatched"
    one = resolve_account([AccountMatch(ams_client_id="a", score=10)])
    assert one.status == "matched"
    tied = resolve_account(
        [
            AccountMatch(ams_client_id="a", score=5),
            AccountMatch(ams_client_id="b", score=5),
        ]
    )
    assert tied.status == "manual_selection"
    assert len(tied.candidates) == 2


def test_cf06_one_renewal_case_not_three_tickets():
    early = renewal_identity("CA123", "2026-11-01", days_out=90)
    mid = renewal_identity("CA123", "2026-11-01", days_out=60)
    late = renewal_identity("CA123", "2026-11-01", days_out=30)
    assert early.case_key == mid.case_key == late.case_key == "REN|CA123|2026-11-01"
    assert early.stage == "90-day"
    assert mid.stage == "60-day"
    assert late.stage == "30-day"
    assert renewal_stage_for_days_out(0) == "Completion"


def test_field_types_are_locked_and_sensitive_driver_data_is_marked():
    types = {field.data_type for field in ALL_FIELDS}
    assert "Text" in types and "Picklist" in types and "Boolean" in types
    license_field = next(f for f in ALL_FIELDS if f.api_name == "cf_license_number")
    dob = next(f for f in ALL_FIELDS if f.api_name == "cf_driver_dob")
    assert license_field.sensitive and dob.sensitive
    assert set(LAYOUT_FIELDS) == {
        "shared",
        "certificate",
        "auto_driver",
        "policy_change",
        "claims",
        "billing",
        "cancellation",
        "renewal",
    }


def test_operator_field_csvs_match_the_spec():
    for layout in LAYOUT_FIELDS:
        path = DOCS / f"fields_{layout}.csv"
        assert path.is_file(), f"missing {path}"
        assert path.read_text(encoding="utf-8") == render_fields_csv(layout)
        header = path.read_text(encoding="utf-8").splitlines()[0]
        assert header == ",".join(CSV_COLUMNS)
    assert (DOCS / "picklists.csv").read_text(encoding="utf-8") == render_picklists_csv()
    assert (DOCS / "views.csv").read_text(encoding="utf-8") == render_views_csv()


def test_docs_pack_covers_the_build_sequence():
    readme = (DOCS / "README.md").read_text(encoding="utf-8")
    assert "Desk owns the work" in readme
    assert "Agency Service" in readme
    for name in LAUNCH_WORKFLOWS:
        assert name in readme
    assert (DOCS / "SETUP_CHECKLIST.md").is_file()
    assert (DOCS / "LIVE.md").is_file()
    live = (DOCS / "LIVE.md").read_text(encoding="utf-8")
    assert ORG_ID in live
    assert DEPARTMENT_ID in live
    for layout_name, layout_id in LAYOUT_IDS.items():
        assert layout_name in live
        assert layout_id in live
    assert NATIVE_BRIDGES["cf_request_category"] == "classification"
    assert LAYOUT_CLASSIFICATION_DEFAULTS["Certificate Request"] == "Certificate Request"
    assert LAYOUT_CLASSIFICATION_DEFAULTS["Billing and Cancellation"] == "Billing and Payments"
    assert len(TEAM_IDS) == 11
    assert TEAM_IDS["Certificates"]
    assert (DOCS / "automations.md").is_file()
    assert (DOCS / "blueprints.md").is_file()
    assert (DOCS / "email_templates.md").is_file()
    assert (DOCS / "custom_functions.md").is_file()
    views = (DOCS / "views.csv").read_text(encoding="utf-8")
    for name in OPERATIONAL_VIEWS:
        assert name in views
