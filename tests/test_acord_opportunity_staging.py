"""Stage opportunities from the ACORD selection into the gated intake_crm queue."""

from __future__ import annotations

import pytest

from hermes.command_center import router
from hermes.command_center.submission import (
    Applicant, IntakeMeta, Lane, LineOfBusiness, SourceChannel, SubmissionObject,
)
from hermes.deliverables import acord_selection, acord_questions


def _answers_for(form_ids: list[str]) -> dict[str, str]:
    """Answer every in-scope agent question for the selected lines (satisfies the gate)."""
    lines = acord_selection.plan_selection(_sub(), form_ids).lines
    return {q.field: "no" for q in acord_questions.agent_questions(lines)}


class FakeSupa:
    def __init__(self):
        self.rows: list[tuple[str, dict]] = []

    def insert(self, table, payload):
        self.rows.append((table, payload))
        return {**payload, "id": f"row_{len(self.rows)}"}


def _sub() -> SubmissionObject:
    return SubmissionObject(
        submission_id="sub_stage",
        client_name="Bright HVAC LLC",
        lob=LineOfBusiness.COMMERCIAL_GL,
        lane=Lane.COMMERCIAL_ACORD,
        current_carrier="Acme Mutual",
        current_premium=5000.0,
        intake=IntakeMeta(channel=SourceChannel.WEBUI),
        applicant=Applicant(legal_name="Bright HVAC LLC"),
    )


def test_one_gated_intake_crm_row_per_checked_line():
    supa = FakeSupa()
    staged = router.stage_selection_opportunities(
        supa, _sub(), ["acord_126", "acord_131"], approved_by="lamar",
        answers=_answers_for(["acord_126", "acord_131"]))

    assert staged["crm_queued"] == 2 and set(staged["lines"]) == {"commercial_gl", "commercial_umbrella"}
    # Every row is a gated intake_crm opportunity queued for the executor.
    assert len(supa.rows) == 2
    for table, row in supa.rows:
        assert table == router.QUEUE_TABLE
        assert row["object_type"] == router.OBJECT_TYPE_CRM
        assert row["status"] == "queued"                 # gated — nothing writes synchronously
        assert row["approved_by"] == "lamar"
        assert row["payload"]["kind"] == "opportunity"

    lines = {r["payload"]["opportunity"]["line_of_business"] for _, r in supa.rows}
    assert lines == {"commercial_gl", "commercial_umbrella"}


def test_opportunity_carries_incumbent_context():
    supa = FakeSupa()
    router.stage_selection_opportunities(supa, _sub(), ["acord_126"], approved_by="lamar",
                                         answers=_answers_for(["acord_126"]))
    opp = supa.rows[0][1]["payload"]["opportunity"]
    assert opp["insured_name"] == "Bright HVAC LLC"
    assert opp["premium_estimate"] == 5000.0 and opp["carrier"] == "Acme Mutual"
    assert opp["submission_id"] == "sub_stage"
    assert opp["opportunity_type"] == "New Business" and opp["stage"] == "new"


def test_supplemental_stages_no_opportunity():
    supa = FakeSupa()
    staged = router.stage_selection_opportunities(supa, _sub(), ["acord_163"], approved_by="lamar",
                                                  answers=_answers_for(["acord_163"]))
    assert staged["crm_queued"] == 0 and supa.rows == []   # a driver schedule is not a line


def test_row_shape_matches_map_opportunity_row_input():
    # The staged draft must be consumable by the executor's mapper unchanged.
    from hermes.command_center.intake_executor import map_opportunity_row
    supa = FakeSupa()
    router.stage_selection_opportunities(supa, _sub(), ["acord_126"], approved_by="lamar",
                                         answers=_answers_for(["acord_126"]))
    opp = supa.rows[0][1]["payload"]["opportunity"]
    mapped = map_opportunity_row(opp)
    assert mapped["line_of_business"] == "commercial_gl"
    assert mapped["insured_name"] == "Bright HVAC LLC"
    assert mapped["source"] == "intake"

def test_staging_is_blocked_until_questions_answered():
    supa = FakeSupa()
    with pytest.raises(ValueError, match='unanswered'):
        router.stage_selection_opportunities(supa, _sub(), ['acord_126'], approved_by='lamar', answers={})
    assert supa.rows == []   # nothing staged while a required question is open
