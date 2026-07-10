"""Tests for hermes.renewals.cadence — segment classifier + touch scheduler.

Covers the BRIEF's Acceptance Criteria #1 (classifier) and #2 (idempotency /
grace window). Pure logic, no network, no LLM.
"""
from datetime import date

import pytest

from hermes.renewals import cadence
from hermes.renewals import cadence_config as cc


# ------------------------------------------------------------ helpers
def _policy(lob, *, effective=None, expiration="2026-12-31"):
    p = {"line_of_business": lob, "expiration_date": expiration}
    if effective is not None:
        p["effective_date"] = effective
    return p


def _six_month_auto():
    # 181-day term (< 270) => 6-month term
    return _policy("Personal Auto", effective="2026-01-01", expiration="2026-07-01")


def _twelve_month_auto():
    return _policy("Personal Auto", effective="2026-01-01", expiration="2026-12-31")


# ============================================================ Acceptance #1
# ------------------------------------------------------------ classifier: rules

def test_medicare_is_excluded():
    for lob in ("Medicare Advantage", "Medicare Supplement", "PDP"):
        assert cadence.classify_segment(_policy(lob), account_active_premium=50_000) is None


def test_benefits_override_beats_premium():
    # group benefits win regardless of (account) premium size
    seg = cadence.classify_segment(_policy("Group Health"), account_active_premium=200)
    assert seg == cc.SEGMENT_BENEFITS
    seg_big = cadence.classify_segment(_policy("Group Dental"), account_active_premium=999_999)
    assert seg_big == cc.SEGMENT_BENEFITS


def test_six_month_auto_detection():
    seg = cadence.classify_segment(_six_month_auto(), account_active_premium=1500)
    assert seg == cc.SEGMENT_AUTO_6MO


def test_twelve_month_auto_is_personal_not_auto_6mo():
    seg = cadence.classify_segment(_twelve_month_auto(), account_active_premium=1500)
    assert seg == cc.SEGMENT_PERSONAL_12MO


def test_personal_lines_twelve_month():
    for lob in ("Homeowners", "Personal Umbrella", "Renters", "Dwelling Fire"):
        seg = cadence.classify_segment(_policy(lob), account_active_premium=3000)
        assert seg == cc.SEGMENT_PERSONAL_12MO, lob


def test_commercial_auto_is_not_personal_auto():
    # "Commercial Auto" contains "auto" but the commercial marker disqualifies it
    seg = cadence.classify_segment(
        _policy("Commercial Auto", effective="2026-01-01", expiration="2026-07-01"),
        account_active_premium=3000,
    )
    assert seg == cc.SEGMENT_COMMERCIAL_SMALL


# ------------------------------------------------------------ classifier: small/mid boundary

def test_small_mid_boundary_just_under_cutoff():
    seg = cadence.classify_segment(_policy("General Liability"), account_active_premium=4999)
    assert seg == cc.SEGMENT_COMMERCIAL_SMALL


def test_small_mid_boundary_at_cutoff_is_small():
    # <= cutoff is small (inclusive)
    seg = cadence.classify_segment(
        _policy("General Liability"),
        account_active_premium=cc.SMALL_COMMERCIAL_MAX_ACCOUNT_PREMIUM,
    )
    assert seg == cc.SEGMENT_COMMERCIAL_SMALL


def test_small_mid_boundary_just_over_cutoff():
    seg = cadence.classify_segment(_policy("General Liability"), account_active_premium=5001)
    assert seg == cc.SEGMENT_COMMERCIAL_MID


def test_multi_policy_account_promoted_to_mid():
    # four $3K policies => $12K relationship => richer (mid) cadence for each
    account_premium = 3000 * 4
    seg = cadence.classify_segment(_policy("BOP"), account_active_premium=account_premium)
    assert seg == cc.SEGMENT_COMMERCIAL_MID


def test_missing_account_premium_defaults_to_small():
    seg = cadence.classify_segment(_policy("Workers Comp"), account_active_premium=None)
    assert seg == cc.SEGMENT_COMMERCIAL_SMALL


# ------------------------------------------------------------ full-review vs light rotation

def test_auto_full_review_when_never_reviewed():
    assert cadence.auto_6mo_cycle(None, today=date(2026, 6, 1)) == "full_review"


def test_auto_full_review_when_last_review_is_old():
    # > 300 days ago => this cycle is the full review
    assert cadence.auto_6mo_cycle("2025-06-01", today=date(2026, 6, 1)) == "full_review"


def test_auto_light_confirm_when_recently_reviewed():
    # 90 days ago => off-cycle => light confirmation
    assert cadence.auto_6mo_cycle("2026-03-01", today=date(2026, 6, 1)) == "light_confirm"


def test_auto_cycle_selects_template_in_due_touches():
    exp = "2026-07-30"
    today = date(2026, 7, 1)  # 29 days out, within the T-30 window
    full = cadence.due_touches(
        cc.SEGMENT_AUTO_6MO, expiration_date=exp, today=today, auto_cycle="full_review")
    light = cadence.due_touches(
        cc.SEGMENT_AUTO_6MO, expiration_date=exp, today=today, auto_cycle="light_confirm")
    assert full[0]["template"] == cc.TPL_T30_OPTIONS
    assert light[0]["template"] == cc.TPL_LIGHT_CONFIRM


# ============================================================ Acceptance #2
# ------------------------------------------------------------ scheduler: due window

def test_touch_fires_at_threshold():
    # mid commercial T-90: 90 days out exactly
    due = cadence.due_touches(
        cc.SEGMENT_COMMERCIAL_MID, expiration_date="2026-10-01", today=date(2026, 7, 3))
    days = {t["days"] for t in due}
    assert 90 in days


def test_touch_not_due_before_threshold():
    # 120 days out: no touch yet (earliest mid touch is T-90)
    due = cadence.due_touches(
        cc.SEGMENT_COMMERCIAL_MID, expiration_date="2026-11-01", today=date(2026, 7, 4))
    assert due == []


def test_touch_maps_to_correct_field_and_template():
    due = cadence.due_touches(
        cc.SEGMENT_COMMERCIAL_MID, expiration_date="2026-10-01", today=date(2026, 7, 3))
    t90 = next(t for t in due if t["days"] == 90)
    assert t90["field"] == cc.FIELD_TOUCH_EARLY
    assert t90["template"] == cc.TPL_T90_KICKOFF


# ------------------------------------------------------------ scheduler: idempotency

def test_touch_does_not_fire_twice():
    # field already stamped => skip, even though we're in the window
    sent = {cc.FIELD_TOUCH_EARLY: "2026-07-03T10:00:00"}
    due = cadence.due_touches(
        cc.SEGMENT_COMMERCIAL_MID,
        expiration_date="2026-10-01",
        today=date(2026, 7, 3),
        sent_fields=sent,
    )
    days = {t["days"] for t in due}
    assert 90 not in days


def test_full_term_no_touch_fires_twice_across_the_cycle():
    # Walk a mid-commercial renewal day by day; each field stamps once and never
    # produces a second touch for the same slot.
    exp = date(2026, 10, 1)
    sent: dict = {}
    fired_fields: list[str] = []
    for offset in range(0, 120):
        today = exp.fromordinal(exp.toordinal() - (120 - offset))
        due = cadence.due_touches(
            cc.SEGMENT_COMMERCIAL_MID,
            expiration_date=exp.isoformat(),
            today=today,
            sent_fields=sent,
        )
        for t in due:
            fired_fields.append(t["field"])
            sent[t["field"]] = today.isoformat()  # stamp on post, as the engine does
    # each of the three mid slots fired exactly once
    assert fired_fields.count(cc.FIELD_TOUCH_EARLY) == 1
    assert fired_fields.count(cc.FIELD_TOUCH_MID) == 1
    assert fired_fields.count(cc.FIELD_TOUCH_DECISION) == 1


# ------------------------------------------------------------ scheduler: grace window

def test_late_touch_is_skipped_not_sent():
    # T-90 that is 40 days late (50 days out): outside the 10-day grace => skipped
    due = cadence.due_touches(
        cc.SEGMENT_COMMERCIAL_MID, expiration_date="2026-08-22", today=date(2026, 7, 3))
    days = {t["days"] for t in due}
    assert 90 not in days  # the stale 90-day heads-up does not fire
    assert 60 in days      # but the next touch, whose window we're in, does


def test_touch_fires_within_grace_window():
    # T-90 that is 8 days late (82 days out): inside the 10-day grace => still fires
    due = cadence.due_touches(
        cc.SEGMENT_COMMERCIAL_MID, expiration_date="2026-09-23", today=date(2026, 7, 3))
    days = {t["days"] for t in due}
    assert 90 in days


def test_touch_skipped_just_past_grace_edge():
    # exactly 11 days past threshold (79 days out, grace 10) => skipped
    due = cadence.due_touches(
        cc.SEGMENT_COMMERCIAL_MID, expiration_date="2026-09-20", today=date(2026, 7, 3))
    days = {t["days"] for t in due}
    assert 90 not in days


# ------------------------------------------------------------ scheduler: guards

def test_excluded_segment_yields_no_touches():
    assert cadence.due_touches(None, expiration_date="2026-10-01", today=date(2026, 7, 3)) == []


def test_missing_expiration_yields_no_touches():
    due = cadence.due_touches(
        cc.SEGMENT_PERSONAL_12MO, expiration_date=None, today=date(2026, 7, 3))
    assert due == []


# ------------------------------------------------------------ config integrity

def test_cadence_and_touch_spec_stay_in_sync():
    # the import-time drift guard must pass for the shipped config
    cadence._assert_spec_matches_cadence()


def test_template_set_is_exactly_eight():
    # BRIEF: 8 templates total, one per touch-map row
    assert len(cc.TEMPLATES) == 8
    assert len(set(cc.TEMPLATES)) == 8


def test_every_scheduled_template_is_declared():
    declared = set(cc.TEMPLATES)
    for seg, touches in cc.TOUCH_SPEC.items():
        for t in touches:
            tpl = t["template"]
            names = tpl.values() if isinstance(tpl, dict) else [tpl]
            for name in names:
                assert name in declared, f"{seg} references undeclared template {name!r}"


def test_every_touch_field_is_a_known_slot():
    for seg, touches in cc.TOUCH_SPEC.items():
        for t in touches:
            assert t["field"] in cc.TOUCH_FIELDS, f"{seg} uses unknown field {t['field']!r}"
