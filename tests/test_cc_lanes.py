"""Lane configs validate against the spine at load time."""
import pytest
from pydantic import ValidationError

from hermes.command_center.lanes import LaneConfig, load_all_lanes


def test_gretchen_lane_loads_and_validates():
    lanes = load_all_lanes()
    assert "gretchen-personal-lines" in lanes
    g = lanes["gretchen-personal-lines"]
    assert g.owner == "gretchen"
    assert g.theme == "teal"
    assert "xdate" in g.extraction_fields          # XDATE-first rule satisfied
    assert {d.kind for d in g.deliverables} == {
        "quote_worksheet", "carrier_shortlist"
    }


def test_unknown_extraction_field_rejected():
    with pytest.raises(ValidationError):
        LaneConfig(key="x", owner="lamar", label="X",
                   extraction_fields=["xdate", "not_a_real_field"])


def test_missing_xdate_rejected():
    with pytest.raises(ValidationError):
        LaneConfig(key="x", owner="lamar", label="X",
                   extraction_fields=["insured_name", "address"])


def test_unknown_validator_rejected():
    with pytest.raises(ValidationError):
        LaneConfig(key="x", owner="lamar", label="X",
                   extraction_fields=["xdate"], validators=["bogus_validator"])
