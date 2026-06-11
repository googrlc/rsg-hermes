"""Phase 3 — the remaining lanes are config-only and coherent."""
from datetime import date

from hermes.command_center.deliverables import GENERATORS, build_all
from hermes.command_center.lanes import load_all_lanes
from hermes.command_center.submission import (
    Applicant,
    IntakeMeta,
    SourceChannel,
    SubmissionObject,
)

EXPECTED = {
    "gretchen-personal-lines", "lamar-commercial", "lamar-peo",
    "gretchen-medicare", "lamar-benefits",
}


def _sub() -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_x", client_name="Acme LLC",
        current_carrier="Travelers", current_premium=4200.0,
        current_policy_expiration=date(2026, 9, 1),
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Acme LLC", fein="12-3456789"),
    )


def test_all_five_lanes_load_and_validate():
    lanes = load_all_lanes()
    assert set(lanes) == EXPECTED


def test_every_lane_keeps_the_xdate_first_invariant():
    for lane in load_all_lanes().values():
        assert "xdate" in lane.extraction_fields


def test_no_lane_references_a_missing_generator():
    # The "config-only" guarantee: a lane can't declare a deliverable that has
    # no generator (which would silently produce nothing).
    for lane in load_all_lanes().values():
        for d in lane.deliverables:
            assert d.kind in GENERATORS, f"{lane.key}: no generator for '{d.kind}'"


def test_each_lane_produces_nonempty_deliverables():
    lanes = load_all_lanes()
    for key in EXPECTED:
        built = build_all(lanes[key], _sub())
        assert built, f"{key} produced no deliverables"
        assert all(d["content"].strip() for d in built), f"{key} has an empty deliverable"
