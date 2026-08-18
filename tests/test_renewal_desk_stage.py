"""Creator Renewals Desk stage / window / action rules.

These cases are the Python half of docs/zoho/creator-renewals-desk/deluge/fixtures.md.
"""

from __future__ import annotations

from datetime import date

import pytest

from hermes.renewals import desk


TODAY = date(2026, 8, 18)


def test_advance_one_step_is_allowed():
    ok, reason = desk.stage_change_allowed("Identified", "Outreach Sent")
    assert ok and reason == "advance"


def test_same_stage_is_noop():
    ok, reason = desk.stage_change_allowed("Identified", "Identified")
    assert ok and reason == "unchanged"


def test_cannot_skip_stages():
    ok, reason = desk.stage_change_allowed("Identified", "Quote Requested")
    assert not ok
    assert "cannot skip" in reason
    ok, _ = desk.stage_change_allowed("Identified", "Closed")
    assert not ok


def test_backward_needs_producer():
    ok, reason = desk.stage_change_allowed("Outreach Sent", "Identified")
    assert not ok
    assert "producer" in reason
    ok, reason = desk.stage_change_allowed(
        "Outreach Sent", "Identified", producer_confirmed=True
    )
    assert ok and reason == "backward_with_producer"


def test_blank_current_is_identified():
    ok, reason = desk.stage_change_allowed(None, "Outreach Sent")
    assert ok and reason == "advance"


def test_unknown_stage_refused():
    ok, reason = desk.stage_change_allowed("Identified", "Not A Stage")
    assert not ok
    assert "unknown" in reason


def test_closed_requires_disposition():
    assert desk.disposition_required("Closed")
    assert not desk.disposition_required("Negotiating")
    assert desk.disposition_ok("renewed")
    assert not desk.disposition_ok("")


@pytest.mark.parametrize(
    "exp,lob,bucket",
    [
        ("2026-08-10", "General Liability", "past_due"),
        ("2026-09-01", "General Liability", "30"),
        ("2026-10-01", "General Liability", "60"),
        ("2026-11-10", "General Liability", "90"),
        ("2026-09-01", "Personal Auto", "personal"),
        ("2026-08-10", "Homeowners", "past_due"),
        (None, "General Liability", None),
    ],
)
def test_window_bucket_matches_fixtures(exp, lob, bucket):
    assert desk.window_bucket(exp, lob, today=TODAY) == bucket


def test_executor_actions_are_the_four():
    for action in ("request_terms", "prepare_options", "client_follow_up", "update_ams"):
        assert desk.executor_action_ok(action)
    assert not desk.executor_action_ok("bind")
    assert not desk.executor_action_ok("")
