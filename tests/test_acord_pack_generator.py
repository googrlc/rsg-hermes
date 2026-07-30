"""On-demand ACORD pack generator (P2) — fill from selection, stage opportunities."""

from __future__ import annotations

from hermes.deliverables import acord_pack_generator as G, acord_pdf
from hermes.command_center.submission import (
    Address,
    Applicant,
    EntityType,
    IntakeMeta,
    Lane,
    LineOfBusiness,
    PropertyLocation,
    SourceChannel,
    SubmissionObject,
)

TEMPLATES = {
    G.TEMPLATE_125_126_ENV: "/tpl/125_126.pdf",
    G.TEMPLATE_140_ENV: "/tpl/140.pdf",
}


def _sub(buildings: int = 0) -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_gen",
        client_name="Bright HVAC LLC",
        lob=LineOfBusiness.COMMERCIAL_GL,
        lane=Lane.COMMERCIAL_ACORD,
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Bright HVAC LLC", entity_type=EntityType.llc,
                            naics="238220", gl_class_code="91340",
                            mailing_address=Address(street="1 Main St", city="Atlanta", state="GA", zip="30301")),
        coverage_request={"gl_basis": "Occurrence", "each_occurrence": "1,000,000"},
        property_locations=[PropertyLocation(address=Address(street=f"{i} Main St"))
                            for i in range(buildings)],
    )


def _fake_fill():
    calls = []

    def fill(t, v, o, *, checkboxes=None, form_label="ACORD"):
        calls.append({"template": t, "output": o, "label": form_label,
                      "values": dict(v), "checks": dict(checkboxes or {})})
        return {"written": o, "placed": sorted({**v, **(checkboxes or {})}), "skipped": []}

    return fill, calls


def test_generate_gl_produces_125_126_and_one_opportunity(tmp_path):
    fill, calls = _fake_fill()
    m = G.generate_pack(_sub(), ["acord_126"], output_dir=str(tmp_path),
                        templates=TEMPLATES, fill_fn=fill)
    assert [a["form"] for a in m["artifacts"]] == ["ACORD 125/126"]
    assert len(calls) == 1 and calls[0]["label"] == "ACORD 125/126"
    # GL box + GL limit landed on the one combined fill.
    from hermes.deliverables import acord125, acord126
    assert calls[0]["checks"][acord125.LOB_CHECKBOX["commercial_gl"]] == "/1"
    assert acord126.FIELD_NAMES["each_occurrence"] in calls[0]["values"]
    assert len(m["opportunities"]) == 1 and m["opportunities"][0]["line_of_business"] == "commercial_gl"
    assert m["auto_sent"] is False


def test_generate_property_makes_one_140_per_building(tmp_path):
    fill, calls = _fake_fill()
    m = G.generate_pack(_sub(buildings=2), ["acord_126", "acord_140"],
                        output_dir=str(tmp_path), templates=TEMPLATES, fill_fn=fill)
    forms = [a["form"] for a in m["artifacts"]]
    assert forms.count("ACORD 140") == 2 and "ACORD 125/126" in forms
    assert len(m["opportunities"]) == 2                 # GL + property
    # 140 filenames are per-location.
    locs = sorted(a["location"] for a in m["artifacts"] if a["form"] == "ACORD 140")
    assert locs == [1, 2]


def test_no_140_when_property_not_selected(tmp_path):
    fill, _ = _fake_fill()
    m = G.generate_pack(_sub(buildings=3), ["acord_126"], output_dir=str(tmp_path),
                        templates=TEMPLATES, fill_fn=fill)
    assert all(a["form"] != "ACORD 140" for a in m["artifacts"])   # buildings exist but not selected


def test_schedule_csv_rides_along_as_attachment(tmp_path):
    fill, _ = _fake_fill()
    csv_path = str(tmp_path / "driver_schedule.csv")
    m = G.generate_pack(_sub(), ["acord_137"], output_dir=str(tmp_path),
                        templates={**TEMPLATES, "HERMES_ACORD_137_TEMPLATE": "/t/137.pdf"},
                        fill_fn=fill, schedule_attachments=[csv_path])
    assert m["attachments"] == [{"path": csv_path, "name": "driver_schedule.csv"}]
    # the ACORD 137 still fills its applicant block alongside the attached schedule.
    assert any(a["form"] == "ACORD 137" for a in m["artifacts"])


def test_no_attachments_by_default(tmp_path):
    fill, _ = _fake_fill()
    m = G.generate_pack(_sub(), ["acord_126"], output_dir=str(tmp_path),
                        templates=TEMPLATES, fill_fn=fill)
    assert m["attachments"] == []


def test_missing_template_reported_not_fatal(tmp_path):
    fill, calls = _fake_fill()
    m = G.generate_pack(_sub(), ["acord_126"], output_dir=str(tmp_path),
                        templates={}, fill_fn=fill)     # no templates configured
    assert m["artifacts"] == [] and calls == []
    assert G.TEMPLATE_125_126_ENV in m["missing_templates"]
    assert len(m["opportunities"]) == 1                 # opportunity still planned


def test_supplemental_without_filler_is_reported(tmp_path):
    fill, _ = _fake_fill()
    # 163 driver schedule is selectable/attachable but has no filler yet (row QA pending).
    m = G.generate_pack(_sub(), ["acord_163"], output_dir=str(tmp_path),
                        templates=TEMPLATES, fill_fn=fill)
    assert m["needs_filler"] == ["acord_163"]
    assert all(a["form"] != "ACORD 163" for a in m["artifacts"])
    assert m["opportunities"] == []              # a supplemental is not a line → no opportunity


def test_default_fill_fn_is_the_real_filler():
    assert G.generate_pack.__defaults__ is None    # keyword-only; guard signature
    # fill_fn defaults to acord_pdf.fill_pdf (wired for real use).
    import inspect
    assert inspect.signature(G.generate_pack).parameters["fill_fn"].default is acord_pdf.fill_pdf
