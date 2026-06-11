"""Extraction is deterministic and XDATE-first."""
from datetime import date

from hermes.command_center.extract import (
    apply_extraction,
    classify_doc,
    extract_fields,
    extract_xdate,
)
from hermes.command_center.submission import (
    IntakeMeta,
    SourceChannel,
    SubmissionObject,
)

SAMPLE_DEC = """
DECLARATIONS PAGE
Named Insured: Jane Roe
Carrier: Progressive Insurance Company
Policy Number: 980043890
Effective Date: 04/16/2025    Expiration Date: 10/16/2025
Total Premium: $1,284.00
"""


def _sub() -> SubmissionObject:
    return SubmissionObject(submission_id="sub_x", intake=IntakeMeta(channel=SourceChannel.WEBUI))


def test_classify_doc():
    assert classify_doc("client_dec_page.pdf") == "dec_page"
    assert classify_doc("JaneRoe_DL.jpg") == "drivers_license"
    assert classify_doc("mystery.bin") == "other"


def test_xdate_is_the_expiration_not_the_effective():
    assert extract_xdate(SAMPLE_DEC) == date(2025, 10, 16)


def test_extract_fields_from_dec():
    f = extract_fields(SAMPLE_DEC, "dec_page")
    assert f["current_policy_expiration"] == date(2025, 10, 16)
    assert "progressive" in f["current_carrier"].lower()
    assert f["current_premium"] == 1284.0
    assert f["client_name"].startswith("Jane Roe")


def test_apply_is_gapfill_with_provenance():
    sub = _sub()
    apply_extraction(sub, extract_fields(SAMPLE_DEC), "dec_page")
    assert sub.current_policy_expiration == date(2025, 10, 16)
    assert sub.enrichment.sources["current_policy_expiration"] == "dec_page"
    # gap-fill: a value already present is not overwritten
    sub2 = _sub()
    sub2.current_carrier = "Existing Carrier"
    apply_extraction(sub2, {"current_carrier": "Progressive"}, "dec_page")
    assert sub2.current_carrier == "Existing Carrier"
    assert "current_carrier" not in sub2.enrichment.sources
