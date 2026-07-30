"""ACORD 125 (Commercial Application) filler — pure core + orchestration.

Field-name assertions reference ``acord125.FIELD_NAMES`` / the checkbox maps
rather than hard-coding the AcroForm strings, so a template-driven rename updates
in one place. The names themselves are verified against the real licensed
template out of band (see the PR notes), not in unit tests (the copyrighted
template is not committed).
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

NI = acord125.FIELD_NAMES["named_insured"]
FEIN = acord125.FIELD_NAMES["fein"]
CITY = acord125.FIELD_NAMES["mail_city"]


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
            naics="238220",
            sic="1711",
            gl_class_code="91340",
            mailing_address=Address(street="1 Main St", city="Atlanta", state="GA", zip="30301"),
        ),
        property_locations=[
            PropertyLocation(address=Address(street="1 Main St", city="Atlanta", state="GA", zip="30301")),
        ],
        prior_carriers=[PriorCarrier(carrier="Acme Mutual", policy_no="GL-9", premium=5000.0,
                                     expiration=date(2026, 9, 1))],
    )


# ── from_submission (pure) ────────────────────────────────────────────────────
def test_from_submission_maps_and_splits_address():
    a = acord125.from_submission(_commercial())
    assert a.named_insured == "Bright HVAC LLC"
    assert a.dba == "Bright Air"
    assert (a.mail_line_one, a.mail_city, a.mail_state, a.mail_postal) == \
        ("1 Main St", "Atlanta", "GA", "30301")
    assert a.fein == "12-3456789"
    assert a.gl_class_code == "91340"
    assert a.entity_key == "llc" and a.lob_key == "commercial_gl"
    assert a.prior_carrier == "Acme Mutual"


def test_from_submission_never_fabricates():
    empty = SubmissionObject(submission_id="sub_e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    a = acord125.from_submission(empty)
    assert a.named_insured == "" and a.fein == "" and a.entity_key == ""
    assert a.mail_line_one == "" and a.prior_carrier == ""


# ── build_field_map (pure, real names) ────────────────────────────────────────
def test_build_field_map_uses_real_names_and_drops_empty():
    fm = acord125.build_field_map(acord125.from_submission(_commercial()))
    assert fm[NI] == "Bright HVAC LLC"
    assert fm[FEIN] == "12-3456789"
    assert fm[CITY] == "Atlanta"


def test_build_field_map_empty_is_only_producer():
    empty = SubmissionObject(submission_id="sub_e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    fm = acord125.build_field_map(acord125.from_submission(empty))
    assert fm == {acord125.FIELD_NAMES["producer_name"]: "Risk Solutions Group"}


def test_field_name_override_wins():
    a = acord125.from_submission(_commercial())
    fm = acord125.build_field_map(a, field_names={"named_insured": "Custom_NI"})
    assert fm["Custom_NI"] == "Bright HVAC LLC"
    assert NI not in fm


# ── checkbox map (entity / LOB / status) ──────────────────────────────────────
def test_checkbox_map_selects_entity_lob_and_quote():
    checks = acord125.build_checkbox_map(acord125.from_submission(_commercial()))
    assert checks[acord125.ENTITY_CHECKBOX["llc"]] == "/1"
    assert checks[acord125.LOB_CHECKBOX["commercial_gl"]] == "/1"
    assert checks[acord125.STATUS_QUOTE_CHECKBOX] == "/1"
    # A different entity would not check the LLC box.
    assert acord125.ENTITY_CHECKBOX["corporation"] not in checks


def test_checkbox_map_unknown_entity_is_only_quote():
    empty = SubmissionObject(submission_id="sub_e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    checks = acord125.build_checkbox_map(acord125.from_submission(empty))
    assert checks == {acord125.STATUS_QUOTE_CHECKBOX: "/1"}  # no entity/LOB fabricated


# ── render_preview (pure) ─────────────────────────────────────────────────────
def test_render_preview_shows_values_and_flags_missing():
    md = acord125.render_preview(acord125.from_submission(_commercial()))
    assert "Bright HVAC LLC" in md and "1 Main St, Atlanta GA 30301" in md
    assert "Still needed" not in md

    empty = SubmissionObject(submission_id="sub_e", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    md2 = acord125.render_preview(acord125.from_submission(empty))
    assert "Still needed for a complete ACORD 125" in md2 and "—" in md2


# ── orchestration: never auto-send + logs (fill_pdf faked) ────────────────────
def test_draft_never_auto_sends_and_passes_checkboxes(monkeypatch, tmp_path):
    seen = {}

    def fake_fill(t, v, o, *, checkboxes=None, form_label="ACORD"):
        seen["values"], seen["checks"] = v, checkboxes
        return {"written": o, "placed": sorted({**v, **(checkboxes or {})}), "skipped": []}

    monkeypatch.setattr(acord_pdf, "fill_pdf", fake_fill)
    posted, logged = [], []
    summary = acord125.draft_acord125(
        acord125.from_submission(_commercial()),
        template_path="ignored.pdf", output_path=str(tmp_path / "125.pdf"),
        account_name="Bright HVAC LLC", slack_post=posted.append, supa_log=logged.append,
    )
    assert summary["auto_sent"] is False
    assert seen["checks"][acord125.ENTITY_CHECKBOX["llc"]] == "/1"   # checkboxes reached fill_pdf
    assert posted and logged and logged[0]["account"] == "Bright HVAC LLC"


def test_supabase_logger_stamps_agent_and_form(monkeypatch):
    monkeypatch.setenv("HERMES_AGENT_ID", "hermes-test")
    rows = []

    class FakeSupa:
        def insert(self, table, payload):
            rows.append((table, payload)); return payload

    acord125.supabase_logger(FakeSupa())({"account": "Bright HVAC LLC", "output_path": "/tmp/x.pdf"})
    table, row = rows[0]
    assert table == "acord_drafts" and row["agent_id"] == "hermes-test"
    assert row["form"] == "ACORD 125" and row["auto_sent"] is False


# ACORD 125/126/140 are generated on demand via acord_selection, not auto-built
# as lane deliverables — see tests/test_acord_selection.py.
