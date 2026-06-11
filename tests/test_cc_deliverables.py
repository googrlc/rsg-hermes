"""Deliverables build from the spine; crm_blocks uses the Espo field-map."""
from datetime import date

from hermes.command_center.deliverables import build_all, crm_blocks, quote_worksheet
from hermes.command_center.lanes import load_all_lanes
from hermes.command_center.submission import (
    Applicant,
    IntakeMeta,
    Lane,
    LineOfBusiness,
    SourceChannel,
    SubmissionObject,
)


def _filled() -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_x",
        client_name="Jane Roe",
        lob=LineOfBusiness.PERSONAL_AUTO,
        lane=Lane.PERSONAL_NO_ACORD,
        current_carrier="Progressive",
        current_premium=1284.0,
        current_policy_expiration=date(2025, 10, 16),
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Jane Roe", email="jane@x.com"),
    )


def test_quote_worksheet_has_xdate_and_no_fabrication():
    md = quote_worksheet(_filled())
    assert "X-date" in md and "2025-10-16" in md
    assert "Progressive" in md
    assert "—" in md  # missing fields render as a dash, never invented


def test_crm_blocks_uses_espo_casing_and_xdate_field():
    md = crm_blocks(_filled())
    assert "x_date" in md           # custom snake_case Espo field
    assert "2025-10-16" in md
    assert "Personal Lines" in md   # client_type -> account_type mapping


def test_build_all_for_gretchen_lane():
    lane = load_all_lanes()["gretchen-personal-lines"]
    built = build_all(lane, _filled())
    kinds = {d["kind"] for d in built}
    assert kinds == {"quote_worksheet", "carrier_shortlist", "crm_blocks"}
    assert all(d["content"] for d in built)
