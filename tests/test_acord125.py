"""ACORD 125 (Commercial Application) filler — pure core + orchestration.

Mirrors the acord25 test posture: from_submission / build_field_map / render_preview
are pure and asserted directly; draft_acord125 is exercised with fill_pdf faked so
the "never auto-send + logs" contract is verified without a licensed template.
"""

from __future__ import annotations

from datetime import date

from hermes.deliverables import acord125, acord_pdf
from hermes.command_center.deliverables import build_all
from hermes.command_center.lanes import load_all_lanes
from hermes.command_center.submission import (
    Address,
    Applicant,
    EntityType,
    IntakeMeta,
    Lane,
    LineOfBusiness,
    PriorCarrier,
    PropertyLocation,
    SourceChannel,
    SubmissionObject,
)


def _commercial() -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_c1",
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
            website="brighthvac.com",
            naics="238220",
            sic="1711",
            mailing_address=Address(street="1 Main St", city="Atlanta", state="GA", zip="30301"),
        ),
        property_locations=[
            PropertyLocation(address=Address(street="1 Main St", city="Atlanta", state="GA", zip="30301")),
            PropertyLocation(address=Address(street="9 Depot Rd", city="Marietta", state="GA", zip="30060")),
        ],
        prior_carriers=[PriorCarrier(carrier="Acme Mutual", policy_no="GL-9", premium=5000.0,
                                     expiration=date(2026, 9, 1))],
    )


# ── from_submission (pure) ────────────────────────────────────────────────────
def test_from_submission_maps_applicant_and_premises():
    a = acord125.from_submission(_commercial())
    assert a.named_insured == "Bright HVAC LLC"
    assert a.dba == "Bright Air"
    assert a.entity_type == "LLC"
    assert a.fein == "12-3456789"
    assert a.naics == "238220" and a.sic == "1711"
    assert a.mailing_address == "1 Main St, Atlanta GA 30301"
    assert a.proposed_eff_date == "2026-09-01"
    assert a.premises == ["1 Main St, Atlanta GA 30301", "9 Depot Rd, Marietta GA 30060"]
    assert a.prior_carrier == "Acme Mutual" and a.prior_policy_number == "GL-9"


def test_from_submission_never_fabricates():
    empty = SubmissionObject(submission_id="sub_e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    a = acord125.from_submission(empty)
    assert a.named_insured == "" and a.fein == "" and a.mailing_address == ""
    assert a.premises == [] and a.prior_carrier == ""


# ── build_field_map (pure) ────────────────────────────────────────────────────
def test_build_field_map_places_known_and_drops_empty():
    fm = acord125.build_field_map(acord125.from_submission(_commercial()))
    assert fm["NAMED INSURED"] == "Bright HVAC LLC"
    assert fm["FEIN"] == "12-3456789"
    assert fm["PREMISES 1"] == "1 Main St, Atlanta GA 30301"
    assert fm["PREMISES 2"] == "9 Depot Rd, Marietta GA 30060"
    assert "PREMISES 3" not in fm            # only two premises -> third slot dropped


def test_build_field_map_empty_submission_is_only_producer():
    empty = SubmissionObject(submission_id="sub_e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    fm = acord125.build_field_map(acord125.from_submission(empty))
    assert fm == {"PRODUCER": "Risk Solutions Group"}  # agency constant, not fabrication


def test_build_field_map_respects_field_name_override():
    a = acord125.from_submission(_commercial())
    fm = acord125.build_field_map(a, field_names={"named_insured": "Form_NamedInsured_A"})
    assert fm["Form_NamedInsured_A"] == "Bright HVAC LLC"
    assert "NAMED INSURED" not in fm


# ── render_preview (pure) ─────────────────────────────────────────────────────
def test_render_preview_shows_values_and_flags_missing():
    md = acord125.render_preview(acord125.from_submission(_commercial()))
    assert "Bright HVAC LLC" in md
    assert "9 Depot Rd, Marietta GA 30060" in md
    assert "Still needed" not in md          # this submission has the required fields

    empty = SubmissionObject(submission_id="sub_e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    md2 = acord125.render_preview(acord125.from_submission(empty))
    assert "Still needed for a complete ACORD 125" in md2
    assert "FEIN" in md2 and "—" in md2       # missing renders as a dash, never invented


# ── orchestration: never auto-send + logs (fill_pdf faked) ────────────────────
def test_draft_never_auto_sends_and_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(
        acord_pdf, "fill_pdf",
        lambda t, v, o, **k: {"written": o, "placed": sorted(v), "skipped": []},
    )
    posted, logged = [], []
    a = acord125.from_submission(_commercial())
    summary = acord125.draft_acord125(
        a, template_path="ignored.pdf", output_path=str(tmp_path / "125.pdf"),
        account_name="Bright HVAC LLC",
        slack_post=posted.append, supa_log=logged.append,
    )
    assert summary["auto_sent"] is False
    assert posted and "review before it goes to the underwriter" in posted[0]
    assert logged and logged[0]["account"] == "Bright HVAC LLC"


def test_supabase_logger_stamps_agent_and_form(monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_ID", "hermes-test")
    rows = []

    class FakeSupa:
        def insert(self, table, payload):
            rows.append((table, payload))
            return payload

    acord125.supabase_logger(FakeSupa())({"account": "Bright HVAC LLC", "output_path": "/tmp/x.pdf"})
    table, row = rows[0]
    assert table == "acord_drafts"
    assert row["agent_id"] == "hermes-test"
    assert row["form"] == "ACORD 125"
    assert row["auto_sent"] is False


# ── command-center wiring (behind the review gate) ────────────────────────────
def test_commercial_lane_builds_acord_125_deliverable():
    lane = load_all_lanes()["lamar-commercial"]
    built = build_all(lane, _commercial())
    by_kind = {d["kind"]: d for d in built}
    assert "acord_125" in by_kind
    assert by_kind["acord_125"]["content_type"] == "text/markdown"
    assert "ACORD 125" in by_kind["acord_125"]["content"]
    assert "Bright HVAC LLC" in by_kind["acord_125"]["content"]
