"""Intake → NowCerts insured write + read-back verify (P4)."""

from __future__ import annotations

from datetime import date

from hermes.intake import ams_intake_mapper as M
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
        submission_id="sub_ams",
        client_name="Bright HVAC LLC",
        lob=LineOfBusiness.COMMERCIAL_GL,
        lane=Lane.COMMERCIAL_ACORD,
        target_effective_date=date(2026, 9, 1),
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(
            legal_name="Bright HVAC LLC",
            dbas=["Bright Air"],
            entity_type=EntityType.llc,
            fein="12-3456789",
            phone="404-555-0100",
            email="ops@brighthvac.com",
            naics="238220",
            sic="1711",
            website="brighthvac.com",
            mailing_address=Address(street="1 Main St", city="Atlanta", state="ga", zip="30301"),
        ),
    )


# ── payload: verified fields only ─────────────────────────────────────────────
def test_payload_carries_only_verified_fields():
    payload, unsupported = M.build_insured_payload(_sub())
    # Proven insured fields land under their real NowCerts keys.
    assert payload["CommercialName"] == "Bright HVAC LLC"
    assert payload["FEIN"] == "12-3456789"
    assert payload["AddressLine1"] == "1 Main St"
    assert payload["City"] == "Atlanta"
    assert payload["State"] == "GA"          # state_2letter normalizer upper-cased
    assert payload["Zip"] == "30301"
    assert payload["EMail"] == "ops@brighthvac.com"
    assert payload["PhoneNumber"] == "404-555-0100"
    assert payload["typeOfBusiness"] == "LLC"        # EntityType.llc → NowCerts picklist label
    assert payload["insuredType"] == "0" and payload["type"] == 1


def test_unconfirmed_fields_are_flagged_not_written():
    payload, unsupported = M.build_insured_payload(_sub())
    unsupported_paths = {u["path"] for u in unsupported}
    # DBA / NAICS / SIC / website are present but their keys aren't confirmed -> flagged.
    assert {"applicant.dbas", "applicant.naics", "applicant.sic",
            "applicant.website"} <= unsupported_paths
    # entity_type is now confirmed, so it is NOT flagged.
    assert "applicant.entity_type" not in unsupported_paths
    # ...and none of the unconfirmed values leaked into the AMS payload.
    assert "238220" not in payload.values()
    assert all("Bright Air" != v for v in payload.values())


def test_business_type_maps_to_nowcerts_picklist():
    from hermes.command_center.submission import EntityType
    assert M._business_type(EntityType.llc) == "LLC"
    assert M._business_type(EntityType.s_corp) == "Subchapter Corp"
    assert M._business_type(EntityType.not_for_profit) == "Not For Profit Org"
    assert M._business_type("individual") == "Individual"
    assert M._business_type("something_else") == "Other"    # unknown → Other, never invented


def test_blank_fields_are_dropped():
    empty = SubmissionObject(submission_id="e", intake=IntakeMeta(channel=SourceChannel.WEBUI),
                             applicant=Applicant())
    payload, _ = M.build_insured_payload(empty)
    # Only the derived connector codes remain; no blank strings sent.
    assert payload == {"insuredType": "0", "type": 1}


# ── classification (plan preview) ─────────────────────────────────────────────
def test_classify_groups_by_home():
    homes = M.classify_fields(_sub())
    assert "ams_insured" in homes
    assert any(f["path"] == "general_liability.each_occurrence" for f in homes.get("acord_only", []))
    assert "opportunity" in homes


# ── verify_readback ───────────────────────────────────────────────────────────
def test_verify_matches_case_insensitively():
    sent = {"CommercialName": "Bright HVAC LLC", "State": "GA", "type": 1}
    after = {"commercialName": "bright hvac llc", "state": "ga"}   # NowCerts casing differs
    ok, mismatched = M.verify_readback(sent, after)
    assert ok and mismatched == []          # codes (type) excluded, content matches


def test_verify_flags_a_mismatch():
    sent = {"CommercialName": "Bright HVAC LLC", "Zip": "30301"}
    after = {"CommercialName": "Bright HVAC LLC", "Zip": "30303"}
    ok, mismatched = M.verify_readback(sent, after)
    assert not ok and mismatched == ["Zip"]


def test_verify_no_readback_is_unverified():
    ok, mismatched = M.verify_readback({"CommercialName": "X"}, None)
    assert not ok and mismatched == ["CommercialName"]


# ── create_and_verify (I/O injected) ──────────────────────────────────────────
def test_create_verified_when_readback_matches():
    def create_fn(payload):
        return {"insuredDatabaseId": "guid-123"}

    def read_fn(guid):
        assert guid == "guid-123"
        return {"CommercialName": "Bright HVAC LLC", "FEIN": "12-3456789",
                "AddressLine1": "1 Main St", "City": "Atlanta", "State": "GA",
                "Zip": "30301", "EMail": "ops@brighthvac.com", "PhoneNumber": "404-555-0100",
                "typeOfBusiness": "LLC"}

    receipt = M.create_and_verify(_sub(), create_fn=create_fn, read_fn=read_fn)
    assert receipt["status"] == M.STATUS_VERIFIED
    assert receipt["nowcerts_guid"] == "guid-123"
    assert receipt["mismatched"] == []
    assert {u["path"] for u in receipt["unsupported_fields"]}      # still reported


def test_create_committed_unverified_on_mismatch():
    receipt = M.create_and_verify(
        _sub(),
        create_fn=lambda p: {"id": "g1"},
        read_fn=lambda g: {"CommercialName": "WRONG NAME"},   # doesn't match
    )
    assert receipt["status"] == M.STATUS_UNVERIFIED
    assert "CommercialName" in receipt["mismatched"]


def test_create_unverified_when_no_guid_or_readback():
    receipt = M.create_and_verify(
        _sub(),
        create_fn=lambda p: {"ok": True},   # no id in response
        read_fn=lambda g: None,
    )
    assert receipt["status"] == M.STATUS_UNVERIFIED
    assert receipt["nowcerts_guid"] is None


def test_duplicate_search_blocks_create():
    calls = {"created": 0}

    def create_fn(p):
        calls["created"] += 1
        return {"id": "g"}

    receipt = M.create_and_verify(
        _sub(), create_fn=create_fn, read_fn=lambda g: {},
        dup_search_fn=lambda payload: [{"guid": "existing-1", "name": "Bright HVAC LLC"}],
    )
    assert receipt["status"] == M.STATUS_DUPLICATE
    assert receipt["duplicates"] and calls["created"] == 0   # never created a duplicate
