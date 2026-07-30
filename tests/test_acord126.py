"""ACORD 126 (General Liability) filler — pure core + orchestration."""

from __future__ import annotations

from datetime import date

from hermes.deliverables import acord126, acord_pdf
from hermes.command_center.deliverables import build_all
from hermes.command_center.lanes import load_all_lanes
from hermes.command_center.submission import (
    Applicant,
    IntakeMeta,
    Lane,
    LineOfBusiness,
    SourceChannel,
    SubmissionObject,
)

NI = acord126.FIELD_NAMES["named_insured"]
EACH_OCC = acord126.FIELD_NAMES["each_occurrence"]
GEN_AGG = acord126.FIELD_NAMES["general_aggregate"]
GLCODE = acord126.FIELD_NAMES["gl_class_code"]


def _gl(coverage_request: dict | None = None) -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_g1",
        client_name="Bright HVAC LLC",
        lob=LineOfBusiness.COMMERCIAL_GL,
        lane=Lane.COMMERCIAL_ACORD,
        target_effective_date=date(2026, 9, 1),
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Bright HVAC LLC", naics="238220", gl_class_code="91340"),
        coverage_request=coverage_request or {},
    )


# ── from_submission (pure) ────────────────────────────────────────────────────
def test_reads_limits_from_top_level_coverage_request():
    a = acord126.from_submission(_gl({
        "gl_basis": "Occurrence", "each_occurrence": "1,000,000", "general_aggregate": "2,000,000",
    }))
    assert a.named_insured == "Bright HVAC LLC" and a.gl_class_code == "91340"
    assert a.coverage_basis == "Occurrence"
    assert a.each_occurrence == "1,000,000" and a.general_aggregate == "2,000,000"


def test_reads_limits_from_nested_general_liability_block():
    a = acord126.from_submission(_gl({
        "general_liability": {"each_occurrence": "1,000,000", "medical_expense": "5,000"}
    }))
    assert a.each_occurrence == "1,000,000" and a.medical_expense == "5,000"


def test_tolerant_of_alternate_limit_key_spellings():
    a = acord126.from_submission(_gl({"occurrence": "1M", "aggregate": "2M", "med_pay": "5k"}))
    assert a.each_occurrence == "1M" and a.general_aggregate == "2M" and a.medical_expense == "5k"


def test_never_fabricates_missing_limits():
    a = acord126.from_submission(_gl({}))
    assert a.each_occurrence == "" and a.general_aggregate == "" and a.coverage_basis == ""


# ── build_field_map (pure, real names) ────────────────────────────────────────
def test_build_field_map_uses_real_names_and_drops_empty():
    fm = acord126.build_field_map(acord126.from_submission(_gl({"each_occurrence": "1,000,000"})))
    assert fm[NI] == "Bright HVAC LLC"
    assert fm[GLCODE] == "91340"
    assert fm[EACH_OCC] == "1,000,000"
    assert GEN_AGG not in fm       # not supplied -> dropped


# ── occurrence checkbox ───────────────────────────────────────────────────────
def test_occurrence_basis_checks_the_box():
    checks = acord126.build_checkbox_map(acord126.from_submission(_gl({"gl_basis": "Occurrence"})))
    assert checks[acord126.OCCURRENCE_CHECKBOX] == "/1"


def test_claims_made_checks_the_claims_made_box():
    checks = acord126.build_checkbox_map(acord126.from_submission(_gl({"gl_basis": "Claims-Made"})))
    assert checks == {acord126.CLAIMS_MADE_CHECKBOX: "/1"}
    assert acord126.OCCURRENCE_CHECKBOX not in checks


def test_no_basis_checks_nothing():
    checks = acord126.build_checkbox_map(acord126.from_submission(_gl({})))
    assert checks == {}


# ── render_preview (pure) ─────────────────────────────────────────────────────
def test_render_preview_flags_missing_limits():
    md = acord126.render_preview(acord126.from_submission(_gl({})))
    assert "ACORD 126 (General Liability)" in md
    assert "Still needed for a complete ACORD 126" in md and "Each-occurrence limit" in md


# ── orchestration ─────────────────────────────────────────────────────────────
def test_draft_never_auto_sends(monkeypatch, tmp_path):
    monkeypatch.setattr(acord_pdf, "fill_pdf",
                        lambda t, v, o, **k: {"written": o, "placed": sorted(v), "skipped": []})
    posted = []
    summary = acord126.draft_acord126(
        acord126.from_submission(_gl({"each_occurrence": "1,000,000"})),
        template_path="ignored.pdf", output_path=str(tmp_path / "126.pdf"),
        account_name="Bright HVAC LLC", slack_post=posted.append,
    )
    assert summary["auto_sent"] is False and posted and "GL" in posted[0]


# ACORD 126 is generated on demand via acord_selection, not auto-built as a lane
# deliverable — see tests/test_acord_selection.py.
