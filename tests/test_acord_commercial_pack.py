"""Combined 125/126 pack — one submission fills both sections in one pass."""

from __future__ import annotations

from datetime import date

from hermes.deliverables import acord_commercial_pack as pack, acord125, acord126, acord_pdf
from hermes.command_center.submission import (
    Address,
    Applicant,
    EntityType,
    IntakeMeta,
    Lane,
    LineOfBusiness,
    SourceChannel,
    SubmissionObject,
)


def _sub() -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_pack",
        client_name="Bright HVAC LLC",
        lob=LineOfBusiness.COMMERCIAL_GL,
        lane=Lane.COMMERCIAL_ACORD,
        target_effective_date=date(2026, 9, 1),
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Bright HVAC LLC", entity_type=EntityType.llc,
                            fein="12-3456789", naics="238220", gl_class_code="91340",
                            mailing_address=Address(street="1 Main St", city="Atlanta", state="GA", zip="30301")),
        coverage_request={"gl_basis": "Occurrence", "each_occurrence": "1,000,000",
                          "general_aggregate": "2,000,000"},
    )


def test_combined_map_merges_125_and_126():
    text, checks = pack.combined_field_map(_sub())
    # 125 identity + 126 GL limits share the one template.
    assert text[acord125.FIELD_NAMES["named_insured"]] == "Bright HVAC LLC"
    assert text[acord126.FIELD_NAMES["each_occurrence"]] == "1,000,000"
    # 125 entity/LOB checkboxes + 126 occurrence checkbox all present.
    assert checks[acord125.ENTITY_CHECKBOX["llc"]] == "/1"
    assert checks[acord126.OCCURRENCE_CHECKBOX] == "/1"


def test_125_and_126_headers_are_separate_pages():
    text, _ = pack.combined_field_map(_sub())
    # The 126 GL-section header is on its own page (P5), distinct from the 125 P1
    # header — filling both, not overwriting one with the other.
    assert acord125.FIELD_NAMES["named_insured"] != acord126.FIELD_NAMES["named_insured"]
    assert text[acord125.FIELD_NAMES["named_insured"]] == "Bright HVAC LLC"   # 125 P1 header
    assert text[acord126.FIELD_NAMES["named_insured"]] == "Bright HVAC LLC"   # 126 P5 header


def test_draft_pack_fills_once_and_never_auto_sends(monkeypatch, tmp_path):
    calls = []

    def fake_fill(t, v, o, *, checkboxes=None, form_label="ACORD"):
        calls.append((t, form_label, dict(v), dict(checkboxes or {})))
        return {"written": o, "placed": sorted({**v, **(checkboxes or {})}), "skipped": []}

    monkeypatch.setattr(acord_pdf, "fill_pdf", fake_fill)
    posted = []
    result = pack.draft_pack(
        _sub(), template_125_126="combined.pdf", output_path=str(tmp_path / "125126.pdf"),
        slack_post=posted.append,
    )
    assert len(calls) == 1                                   # one fill for the combined template
    assert calls[0][1] == "ACORD 125/126"
    assert result["acord_125_126"]["auto_sent"] is False
    assert result["acord_140"] is None                        # no 140 template supplied
    assert posted                                             # review note posted


def test_draft_pack_also_drafts_140_when_template_supplied(monkeypatch, tmp_path):
    monkeypatch.setattr(acord_pdf, "fill_pdf",
                        lambda t, v, o, **k: {"written": o, "placed": sorted(v), "skipped": []})
    from hermes.command_center.submission import PropertyLocation
    sub = _sub()
    sub.property_locations = [PropertyLocation(address=Address(street="1 Main St"), building_limit=750000.0)]
    result = pack.draft_pack(
        sub, template_125_126="combined.pdf", output_path=str(tmp_path / "a.pdf"),
        template_140="prop.pdf", output_path_140=str(tmp_path / "b.pdf"),
    )
    assert result["acord_140"] is not None
    assert result["acord_140"]["auto_sent"] is False
