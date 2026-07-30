"""ACORD 140 (Property) filler — pure core + orchestration + lane wiring."""

from __future__ import annotations

from datetime import date

from hermes.deliverables import acord140, acord_pdf
from hermes.command_center.deliverables import build_all
from hermes.command_center.lanes import load_all_lanes
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

NI = acord140.FIELD_NAMES["named_insured"]
AREA = acord140.FIELD_NAMES["building_area"]
LIMIT = acord140.FIELD_NAMES["building_limit"]


def _property() -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_p1",
        client_name="Bright HVAC LLC",
        lob=LineOfBusiness.COMMERCIAL_PROP,
        lane=Lane.COMMERCIAL_ACORD,
        target_effective_date=date(2026, 9, 1),
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Bright HVAC LLC"),
        property_locations=[
            PropertyLocation(
                address=Address(street="1 Main St", city="Atlanta", state="GA", zip="30301"),
                year_built=1998, square_footage=12000, construction_type="Masonry",
                protection_class="4", distance_to_hydrant_ft=200,
                distance_to_fire_station_mi=1.5, building_limit=750000.0,
            ),
            PropertyLocation(address=Address(street="9 Depot Rd", city="Marietta")),
        ],
    )


# ── from_submission (pure) ────────────────────────────────────────────────────
def test_from_submission_maps_first_location():
    a = acord140.from_submission(_property())
    assert a.named_insured == "Bright HVAC LLC"
    assert a.premises_address == "1 Main St, Atlanta GA 30301"
    assert a.year_built == "1998" and a.building_area == "12000"
    assert a.construction_code == "Masonry" and a.protection_class == "4"
    assert a.building_limit == "750000.0"


def test_from_submission_second_location_by_index():
    a = acord140.from_submission(_property(), location_index=1)
    assert a.premises_address == "9 Depot Rd, Marietta"
    assert a.year_built == ""          # not supplied -> blank, never fabricated


def test_no_locations_stays_blank():
    empty = SubmissionObject(submission_id="e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    a = acord140.from_submission(empty)
    assert a.premises_address == "" and a.building_limit == ""


# ── build_field_map (pure, real names) ────────────────────────────────────────
def test_build_field_map_uses_real_names():
    fm = acord140.build_field_map(acord140.from_submission(_property()))
    assert fm[NI] == "Bright HVAC LLC"
    assert fm[AREA] == "12000"
    assert fm[LIMIT] == "750000.0"


# ── render_preview (pure) ─────────────────────────────────────────────────────
def test_render_preview_flags_missing():
    empty = SubmissionObject(submission_id="e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    md = acord140.render_preview(acord140.from_submission(empty))
    assert "ACORD 140 (Property)" in md
    assert "Still needed for a complete ACORD 140" in md and "Year built" in md


# ── orchestration ─────────────────────────────────────────────────────────────
def test_draft_never_auto_sends(monkeypatch, tmp_path):
    monkeypatch.setattr(acord_pdf, "fill_pdf",
                        lambda t, v, o, **k: {"written": o, "placed": sorted(v), "skipped": []})
    posted = []
    summary = acord140.draft_acord140(
        acord140.from_submission(_property()),
        template_path="ignored.pdf", output_path=str(tmp_path / "140.pdf"),
        account_name="Bright HVAC LLC", slack_post=posted.append,
    )
    assert summary["auto_sent"] is False and posted and "Property" in posted[0]


# ── command-center wiring ─────────────────────────────────────────────────────
def test_commercial_lane_builds_acord_140_deliverable():
    lane = load_all_lanes()["lamar-commercial"]
    by_kind = {d["kind"]: d for d in build_all(lane, _property())}
    assert "acord_140" in by_kind and "ACORD 140" in by_kind["acord_140"]["content"]
