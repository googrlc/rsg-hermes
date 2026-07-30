"""ACORD 130 (Workers Comp) + 131 (Umbrella/Excess) fillers, registry, generation."""

from __future__ import annotations

from datetime import date

from hermes.deliverables import (
    acord130, acord131, acord137, acord_registry, acord_selection, acord125,
    acord_pack_generator as G,
)
from hermes.command_center.submission import (
    Address, Applicant, IntakeMeta, Lane, LineOfBusiness, SourceChannel, SubmissionObject,
)


def _sub() -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_wc",
        client_name="Bright HVAC LLC",
        lob=LineOfBusiness.OTHER,
        lane=Lane.COMMERCIAL_ACORD,
        target_effective_date=date(2026, 9, 1),
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Bright HVAC LLC", fein="12-3456789", naics="238220", sic="1711",
                            mailing_address=Address(street="1 Main St", city="Atlanta", state="GA", zip="30301")),
        coverage_request={"umbrella_each_occurrence": "5,000,000", "self_insured_retention": "10,000",
                          "each_occurrence": "1,000,000", "general_aggregate": "2,000,000"},
    )


# ── 130 (WC) ──────────────────────────────────────────────────────────────────
def test_130_maps_applicant_and_flags_wc_detail():
    a = acord130.from_submission(_sub())
    fm = acord130.build_field_map(a)
    assert fm[acord130.FIELD_NAMES["named_insured"]] == "Bright HVAC LLC"
    assert fm[acord130.FIELD_NAMES["fein"]] == "12-3456789"
    md = acord130.render_preview(a)
    assert "Class codes + payroll per class" in md      # WC rating detail surfaced as needed


def test_130_draft_never_auto_sends(monkeypatch, tmp_path):
    monkeypatch.setattr(G.acord_pdf, "fill_pdf", lambda t, v, o, **k: {"written": o, "placed": sorted(v), "skipped": []})
    posted = []
    s = acord130.draft_acord130(acord130.from_submission(_sub()), template_path="x.pdf",
                                output_path=str(tmp_path / "130.pdf"), account_name="Bright HVAC LLC",
                                slack_post=posted.append)
    assert s["auto_sent"] is False and posted and "Workers Comp" in posted[0]


# ── 131 (Umbrella) ────────────────────────────────────────────────────────────
def test_131_reads_umbrella_and_underlying_limits():
    a = acord131.from_submission(_sub())
    assert a.umbrella_each_occurrence == "5,000,000"
    assert a.self_insured_retention == "10,000"
    assert a.underlying_gl_each_occurrence == "1,000,000"
    fm = acord131.build_field_map(a)
    assert fm[acord131.FIELD_NAMES["umbrella_each_occurrence"]] == "5,000,000"


def test_131_flags_missing_umbrella_limit():
    empty = SubmissionObject(submission_id="e", intake=IntakeMeta(channel=SourceChannel.WEBUI),
                             applicant=Applicant(legal_name="X"))
    md = acord131.render_preview(acord131.from_submission(empty))
    assert "Umbrella limit" in md and "Still needed" in md


# ── registry: promoted from placeholders to fillable ──────────────────────────
def test_137_fills_applicant_and_flags_schedules():
    a = acord137.from_submission(_sub())
    fm = acord137.build_field_map(a)
    assert fm[acord137.FIELD_NAMES["named_insured"]] == "Bright HVAC LLC"
    md = acord137.render_preview(a, vehicle_count=2, driver_count=3)
    assert "Vehicle schedule (2 vehicle(s)" in md and "ACORD 163" in md   # schedules flagged, not faked


def test_registry_130_131_now_have_fillers():
    assert acord_registry.get("acord_130").has_filler is True
    assert acord_registry.get("acord_131").has_filler is True
    # umbrella checks the 125 box; WC does not.
    assert acord_registry.get("acord_131").lob_checkbox_125 == acord125.LOB_CHECKBOX["commercial_umbrella"]
    assert acord_registry.get("acord_130").lob_checkbox_125 is None


# ── generation: selection → filled 130/131 ────────────────────────────────────
def test_generate_umbrella_and_wc(tmp_path):
    calls = []

    def fill(t, v, o, *, checkboxes=None, form_label="ACORD"):
        calls.append(form_label)
        return {"written": o, "placed": sorted(v), "skipped": []}

    templates = {
        G.TEMPLATE_125_126_ENV: "/t/125.pdf",
        "HERMES_ACORD_130_TEMPLATE": "/t/130.pdf",
        "HERMES_ACORD_131_TEMPLATE": "/t/131.pdf",
    }
    m = G.generate_pack(_sub(), ["acord_130", "acord_131"], output_dir=str(tmp_path),
                        templates=templates, fill_fn=fill)
    forms = {a["form"] for a in m["artifacts"]}
    assert "ACORD 130" in forms and "ACORD 131" in forms
    assert len(m["opportunities"]) == 2                 # WC + umbrella lines
