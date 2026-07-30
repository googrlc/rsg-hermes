"""ACORD form registry + selection model (P1).

Selecting lines on the 125 drives the 125 checkboxes, the supplemental forms
(one 140 per building), and one opportunity per checked line.
"""

from __future__ import annotations

from hermes.deliverables import acord_registry, acord_selection, acord125
from hermes.command_center.submission import (
    Address,
    Applicant,
    IntakeMeta,
    Lane,
    LineOfBusiness,
    PropertyLocation,
    SourceChannel,
    SubmissionObject,
)


def _sub(buildings: int = 0) -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_sel",
        client_name="Bright HVAC LLC",
        lob=LineOfBusiness.COMMERCIAL_GL,
        lane=Lane.COMMERCIAL_ACORD,
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Bright HVAC LLC"),
        property_locations=[PropertyLocation(address=Address(street=f"{i} Main St"))
                            for i in range(buildings)],
    )


# ── registry ──────────────────────────────────────────────────────────────────
def test_registry_loads_catalog():
    reg = acord_registry.load_registry()
    assert {"acord_125", "acord_126", "acord_140"} <= set(reg)
    assert reg["acord_125"].role == "base"
    assert reg["acord_126"].line_of_business == "commercial_gl"
    assert reg["acord_140"].per_building is True


def test_lob_checkbox_resolves_to_the_125_box():
    assert acord_registry.get("acord_126").lob_checkbox_125 == acord125.LOB_CHECKBOX["commercial_gl"]
    assert acord_registry.get("acord_140").lob_checkbox_125 == acord125.LOB_CHECKBOX["commercial_property"]


def test_selectable_line_and_filler_are_independent():
    # A line can be selectable (checks the 125 box + makes an opportunity) with or
    # without a PDF filler — the two are independent properties.
    from hermes.deliverables.acord_registry import AcordForm
    no_filler = AcordForm(form_id="x", title="X", role="lob",
                          line_of_business="commercial_gl", template_env=None, filler=None)
    assert no_filler.selectable_line is True and no_filler.has_filler is False
    assert acord_registry.get("acord_126").has_filler is True    # a real one does have a filler


# ── selection ─────────────────────────────────────────────────────────────────
def test_select_gl_checks_box_fills_form_and_makes_opportunity():
    plan = acord_selection.plan_selection(_sub(), ["acord_126"])
    assert plan.lines == ["commercial_gl"]
    assert plan.lob_checkboxes_125 == {acord125.LOB_CHECKBOX["commercial_gl"]: "/1"}
    assert [f.form_id for f in plan.forms_to_fill] == ["acord_126"]
    assert len(plan.opportunities) == 1
    opp = plan.opportunities[0]
    assert opp["line_of_business"] == "commercial_gl"
    assert opp["insured_name"] == "Bright HVAC LLC"
    assert opp["opportunity_type"] == "New Business"


def test_select_property_expands_140_one_per_building():
    plan = acord_selection.plan_selection(_sub(buildings=2), ["acord_126", "acord_140"])
    assert set(plan.lines) == {"commercial_gl", "commercial_property"}
    fills = {f.form_id: f.count for f in plan.forms_to_fill}
    assert fills["acord_126"] == 1
    assert fills["acord_140"] == 2                 # one 140 per property location
    assert len(plan.opportunities) == 2            # one opportunity per checked line
    assert len(plan.lob_checkboxes_125) == 2       # both LOB boxes checked on the 125


def test_selected_line_makes_opportunity_and_is_fillable():
    plan = acord_selection.plan_selection(_sub(), ["acord_137"])   # auto, now fillable
    assert plan.lines == ["commercial_auto"]
    assert len(plan.opportunities) == 1                            # opportunity created
    assert [f.form_id for f in plan.forms_to_fill] == ["acord_137"]
    assert plan.selectable_without_filler == []


def test_supplemental_without_filler_is_reported_not_an_opportunity():
    plan = acord_selection.plan_selection(_sub(), ["acord_163"])   # driver schedule, no filler yet
    assert plan.selectable_without_filler == ["acord_163"]
    assert plan.forms_to_fill == [] and plan.opportunities == []   # supplemental → no line, no opp


def test_unknown_form_id_is_reported_not_silent():
    plan = acord_selection.plan_selection(_sub(), ["acord_999"])
    assert plan.unknown_form_ids == ["acord_999"]
    assert plan.forms_to_fill == [] and plan.opportunities == []


def test_property_with_no_buildings_still_fills_one_140():
    plan = acord_selection.plan_selection(_sub(buildings=0), ["acord_140"])
    fills = {f.form_id: f.count for f in plan.forms_to_fill}
    assert fills["acord_140"] == 1                 # at least one, never zero
