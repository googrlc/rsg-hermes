"""Intake router — the three-way split (CRM sales / AMS rest / PDF remainder) + staging."""
from __future__ import annotations

from datetime import date

from hermes.command_center import router as R
from hermes.command_center.submission import (
    Address,
    Applicant,
    IntakeMeta,
    LineOfBusiness,
    PriorCarrier,
    SourceChannel,
    SubmissionObject,
)


class FakeSupa:
    def __init__(self):
        self.rows = []

    def insert(self, table, payload):
        row = {"id": f"q{len(self.rows) + 1}", "table": table, **payload}
        self.rows.append(row)
        return dict(row)


def _sub(**o):
    base = dict(submission_id="s1", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    base.update(o)
    return SubmissionObject(**base)


# --- plan_routing ---
def test_one_opportunity_per_lob():
    sub = _sub(client_name="Acme LLC", lob=LineOfBusiness.list()[0] if hasattr(LineOfBusiness, "list") else None,
               coverage_request={"General Liability": {}, "Commercial Auto": {}, "Workers Comp": {}},
               current_premium=12000.0, current_carrier="Travelers")
    plan = R.plan_routing(sub)
    lobs = [o["line_of_business"] for o in plan["crm"]]
    assert "General Liability" in lobs and "Commercial Auto" in lobs and "Workers Comp" in lobs
    # sales fields carried onto each opportunity
    assert all(o["opportunity_type"] == "New Business" and o["source"] == "intake" for o in plan["crm"])
    assert plan["crm"][0]["premium_estimate"] == 12000.0
    assert plan["crm"][0]["carrier"] == "Travelers"


def test_ams_insured_and_incumbent_policy():
    sub = _sub(
        client_name="Acme LLC",
        applicant=Applicant(fein="12-3456789", naics="484110", phone="555-1212",
                            email="ops@acme.com", mailing_address=Address(street="1 Main", city="Atlanta", state="GA", zip="30301")),
        current_carrier="Travelers", current_premium=12000.0,
        current_policy_expiration=date(2027, 3, 1),
    )
    plan = R.plan_routing(sub)
    ins = plan["ams"]["insured"]
    assert ins["name"] == "Acme LLC" and ins["fein"] == "12-3456789" and ins["city"] == "Atlanta"
    pol = plan["ams"]["incumbent_policy"]
    assert pol["carrier"] == "Travelers" and pol["premium"] == 12000.0 and pol["expiration_date"] == "2027-03-01"


def test_pdf_remainder_detects_schedules_and_narrative():
    sub = _sub(client_name="Acme LLC", prior_carriers=[PriorCarrier(carrier="Old Co")],
               coverage_request={"GL": {"limit": "1M"}})
    sub.intake.note = "Owner wants higher limits."
    plan = R.plan_routing(sub)
    kept = plan["pdf_kept_on_document"]
    assert "prior_carriers" in kept and "coverage_detail" in kept and "intake_note" in kept


def test_client_name_falls_back_to_applicant_legal_name():
    sub = _sub(applicant=Applicant(legal_name="Beta Corp"))
    plan = R.plan_routing(sub)
    assert plan["client_name"] == "Beta Corp"


def test_empty_submission_routes_nothing():
    plan = R.plan_routing(_sub())
    assert plan["crm"] == [] and plan["ams"] == {} and plan["pdf_kept_on_document"] == []


# --- routing_summary ---
def test_routing_summary_is_readable():
    sub = _sub(client_name="Acme LLC", coverage_request={"GL": {}}, current_carrier="Travelers",
               applicant=Applicant(fein="12-3456789"))
    text = R.routing_summary(R.plan_routing(sub))
    assert "Acme LLC" in text and "CRM (sales): 1" in text and "insured" in text


# --- stage_routing ---
def test_stage_routing_enqueues_gated_intents():
    sub = _sub(client_name="Acme LLC",
               coverage_request={"GL": {}, "Auto": {}},
               applicant=Applicant(fein="12-3456789"), current_carrier="Travelers")
    plan = R.plan_routing(sub)
    supa = FakeSupa()
    staged = R.stage_routing(supa, plan, approved_by="lamar@rsg")
    assert staged["crm_queued"] == 2 and staged["ams_queued"] == 1
    kinds = [(r["object_type"], r["status"], r["approved_by"]) for r in supa.rows]
    assert ("intake_crm", "queued", "lamar@rsg") in kinds
    assert ("intake_ams", "queued", "lamar@rsg") in kinds
    # every row lands on the shared gated queue
    assert all(r["table"] == "outbound_sync_queue" for r in supa.rows)


def test_stage_routing_nothing_to_stage():
    supa = FakeSupa()
    staged = R.stage_routing(supa, R.plan_routing(_sub()), approved_by="x")
    assert staged["crm_queued"] == 0 and staged["ams_queued"] == 0 and supa.rows == []


# --- process_intake (full pipeline) ---
def test_process_intake_dry_run_and_commit():
    # empty text → synthesis no-ops; routing runs on the fields already on the submission
    sub = _sub(client_name="Acme LLC", coverage_request={"GL": {}},
               applicant=Applicant(fein="12-3456789"))
    supa = FakeSupa()

    dry = R.process_intake(supa, sub, "", approved_by=None)
    assert dry["plan"]["crm"] and "Acme LLC" in dry["summary"]
    assert "staged" not in dry and supa.rows == []          # dry run writes nothing

    committed = R.process_intake(supa, sub, "", approved_by="lamar@rsg")
    assert committed["staged"]["crm_queued"] == 1 and committed["staged"]["ams_queued"] == 1
    assert len(supa.rows) == 2                              # gated intents enqueued
