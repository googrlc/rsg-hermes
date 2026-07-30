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


def test_forms_without_a_filler_are_still_selectable_lines():
    # 137 (auto) has no filler yet but is a selectable line of business.
    f137 = acord_registry.get("acord_137")
    assert f137.selectable_line is True and f137.has_filler is False
    assert any(f.form_id == "acord_137" for f in acord_registry.selectable_lines())


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


def test_selectable_line_without_filler_makes_opportunity_but_no_pdf():
    plan = acord_selection.plan_selection(_sub(), ["acord_137"])   # auto, no filler yet
    assert plan.lines == ["commercial_auto"]
    assert len(plan.opportunities) == 1            # opportunity still created
    assert plan.forms_to_fill == []               # ...but nothing to fill
    assert plan.selectable_without_filler == ["acord_137"]


def test_unknown_form_id_is_reported_not_silent():
    plan = acord_selection.plan_selection(_sub(), ["acord_999"])
    assert plan.unknown_form_ids == ["acord_999"]
    assert plan.forms_to_fill == [] and plan.opportunities == []


def test_property_with_no_buildings_still_fills_one_140():
    plan = acord_selection.plan_selection(_sub(buildings=0), ["acord_140"])
    fills = {f.form_id: f.count for f in plan.forms_to_fill}
    assert fills["acord_140"] == 1                 # at least one, never zero
