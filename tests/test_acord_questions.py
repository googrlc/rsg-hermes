"""ACORD checkbox questions — classification, scope, and the staging gate (P3)."""

from __future__ import annotations

from hermes.deliverables import acord_questions as Q


# ── classification ────────────────────────────────────────────────────────────
def test_structural_boxes_are_not_prompted():
    assert Q.classify("F[0].P1[0].Policy_LineOfBusiness_CommercialGeneralLiability_A[0]") == Q.CLASS_STRUCTURAL
    assert Q.classify("F[0].P1[0].NamedInsured_LegalEntity_CorporationIndicator_A[0]") == Q.CLASS_STRUCTURAL
    assert Q.classify("F[0].P1[0].CommercialPolicy_Attachment_StatementOfValuesIndicator_A[0]") == Q.CLASS_STRUCTURAL


def test_derived_boxes_are_not_prompted():
    assert Q.classify("F[0].P5[0].GeneralLiability_OccurrenceIndicator_A[0]") == Q.CLASS_DERIVED
    assert Q.classify("F[0].P1[0].BusinessInformation_BusinessType_ContractorIndicator_A[0]") == Q.CLASS_DERIVED
    assert Q.classify("F[0].P2[0].NamedInsured_Contact_PrimaryCellPhoneIndicator_A[0]") == Q.CLASS_DERIVED


def test_underwriting_questions_are_agent_prompted():
    assert Q.classify("F[0].P4[0].LossHistory_NoPriorLossesIndicator_A[0]") == Q.CLASS_AGENT
    assert Q.classify("F[0].P1[0].CommercialPolicy_FormalSafetyProgram_OSHAIndicator_A[0]") == Q.CLASS_AGENT
    assert Q.classify("F[0].P5[0].GeneralLiability_MedicalPayments_CoverageAvailableIndicator_A[0]") == Q.CLASS_AGENT


def test_unknown_box_defaults_to_agent_prompted():
    # The safety bias: an unrecognized box is asked, never silently skipped.
    assert Q.classify("F[0].P9[0].SomeBrandNew_UnderwritingIndicator_A[0]") == Q.CLASS_AGENT


# ── scope ─────────────────────────────────────────────────────────────────────
def test_section_scoping():
    assert Q.section_of("F[0].P5[0].GeneralLiability_MedicalPayments_A[0]") == "commercial_gl"
    assert Q.section_of("F[0].P9[0].SwimmingPool_DivingBoardIndicator_A[0]") == "commercial_property"
    assert Q.section_of("F[0].P4[0].LossHistory_NoPriorLossesIndicator_A[0]") == "base"


def test_gl_questions_only_in_scope_when_gl_selected():
    gl_q = [q for q in Q.agent_questions(["commercial_gl"]) if q.section == "commercial_gl"]
    assert gl_q                                             # GL selected → GL questions present
    none_q = [q for q in Q.agent_questions(["commercial_property"]) if q.section == "commercial_gl"]
    assert none_q == []                                    # GL not selected → GL questions absent


def test_base_questions_always_in_scope():
    q_names = {q.field for q in Q.agent_questions([])}      # nothing selected
    assert any("LossHistory_NoPriorLosses" in f for f in q_names)   # base question still asked


# ── the gate ──────────────────────────────────────────────────────────────────
def test_catalog_loads_all_208():
    assert len(Q.load_questions()) == 208


def test_gated_until_every_in_scope_question_answered():
    lines = ["commercial_gl"]
    required = Q.unanswered_required(lines, answers={})
    assert required and Q.is_gated(lines, answers={})      # unanswered → gated

    # Answer them all → gate opens.
    answers = {q.field: "no" for q in Q.agent_questions(lines)}
    assert Q.unanswered_required(lines, answers) == []
    assert not Q.is_gated(lines, answers)


def test_a_single_blank_answer_keeps_it_gated():
    lines = ["commercial_gl"]
    answers = {q.field: "no" for q in Q.agent_questions(lines)}
    one = Q.agent_questions(lines)[0].field
    answers[one] = ""                                       # blanked
    assert Q.is_gated(lines, answers)
    assert [q.field for q in Q.unanswered_required(lines, answers)] == [one]


def test_out_of_scope_questions_do_not_gate():
    # Property questions are irrelevant when only GL is marketed.
    lines = ["commercial_gl"]
    answers = {q.field: "no" for q in Q.agent_questions(lines)}
    assert not Q.is_gated(lines, answers)                   # property boxes don't block a GL-only deal
