"""Renewal OS: scorecard + checkpoint complete must not skip remaining work."""

from __future__ import annotations

from hermes.renewals.operating import (
    ACTOR_HERMES,
    CHECKPOINTS,
    PIPELINE_STAGE_IDENTIFIED,
    complete_checkpoint,
    operating_label,
    remaining_required,
    scorecard,
    states_from_tasks,
    stored_desk_stage,
)


def _done(keys):
    return {key: {"key": key, "status": "Complete"} for key in keys}


def test_stored_stage_reads_live_stage_alias():
    assert stored_desk_stage({"Stage": "Outreach Sent"}) == "Outreach Sent"
    assert stored_desk_stage({"Desk_Stage": "Identified", "Stage": "Closed"}) == "Identified"
    assert stored_desk_stage({}) == "Identified"


def test_operating_labels_map_six_stored_stages():
    assert operating_label("Identified") == "Review Account"
    assert operating_label("Outreach Sent") == "Pre-Renewal Outreach"
    assert operating_label("Quote Requested") == "Market Renewal"
    assert operating_label("Proposal Sent") == "Build Renewal Options"
    assert operating_label("Negotiating") == "Present Renewal"
    assert operating_label("Closed") == "Close Renewal"


def test_scorecard_empty_identified_is_mostly_empty():
    card = scorecard("Identified", {})
    assert card["health"] < 30
    by_key = {r["key"]: r["state"] for r in card["rails"]}
    assert by_key["account_reviewed"] == "active"
    assert by_key["closed"] == "empty"
    assert by_key["account_reviewed"] != "done"
    assert "verify_policy_info" in card["remaining"]


def test_scorecard_uses_live_task_subjects():
    states = states_from_tasks([
        {"Subject": "Pull the expiring declaration and review exposures", "Status": "Completed"},
        {"Subject": "Request renewal terms from the carrier", "Status": "Not Started"},
    ])
    assert states["verify_policy_info"]["status"] == "Complete"
    card = scorecard("Identified", states)
    assert card["label"] == "Review Account"
    assert "verify_policy_info" not in card["remaining"]
    assert "verify_customer_info" in card["remaining"]


def test_completing_one_checkpoint_does_not_advance_while_required_remain():
    result = complete_checkpoint(
        "Identified",
        {},
        "verify_customer_info",
        actor="user",
    )
    assert result.ok
    assert result.advanced is False
    assert result.desk_stage == "Identified"
    assert "verify_policy_info" in result.remaining
    assert result.scorecard["stage"] == "Identified"


def test_hermes_never_advances_even_when_all_required_are_done():
    identified = [c.key for c in CHECKPOINTS if c.stage == PIPELINE_STAGE_IDENTIFIED]
    states = _done(identified)
    result = complete_checkpoint(
        "Identified",
        states,
        "review_renewal_timeline",
        actor=ACTOR_HERMES,
    )
    assert result.ok
    assert result.advanced is False
    assert result.desk_stage == "Identified"


def test_last_required_checkpoint_advances_one_stage_only():
    required = [
        c.key for c in CHECKPOINTS
        if c.stage == "Identified" and c.required and c.key != "review_renewal_timeline"
    ]
    result = complete_checkpoint(
        "Identified",
        _done(required),
        "review_renewal_timeline",
        actor="user",
    )
    assert result.ok
    assert result.advanced is True
    assert result.desk_stage == "Outreach Sent"
    assert result.scorecard["label"] == "Pre-Renewal Outreach"


def test_cannot_jump_by_completing_a_future_checkpoint():
    result = complete_checkpoint(
        "Identified",
        {},
        "record_customer_selection",
        actor="user",
    )
    assert result.ok
    assert result.advanced is False
    assert result.desk_stage == "Identified"


def test_outreach_complete_rule_is_customer_response_only():
    result = complete_checkpoint(
        "Outreach Sent",
        {},
        "record_customer_response",
        actor="user",
    )
    assert result.advanced is True
    assert result.desk_stage == "Quote Requested"


def test_close_without_disposition_does_not_advance():
    result = complete_checkpoint(
        "Negotiating",
        {},
        "record_customer_selection",
        actor="user",
    )
    assert result.advanced is False
    assert result.desk_stage == "Negotiating"
    close = complete_checkpoint(
        "Negotiating",
        {"record_customer_selection": {"status": "Complete"}},
        "record_disposition",
        actor="user",
        disposition="renewed",
    )
    assert close.advanced is True
    assert close.desk_stage == "Closed"
    assert close.scorecard["health"] == 100
    assert all(r["state"] == "done" for r in close.scorecard["rails"])


def test_live_close_labels_and_alias_mapping():
    from hermes.renewals.operating import OS_DISPOSITIONS, normalize_disposition

    labels = [label for _code, label in OS_DISPOSITIONS]
    assert labels == [
        "Renewed",
        "Rewritten",
        "Lost — Price",
        "Lost — Coverage",
        "Lost — No response",
        "Do not renew",
    ]
    assert "Marketed" not in labels
    assert "Lost to Competitor" not in labels
    assert normalize_disposition("Marketed") == "rewritten"
    assert normalize_disposition("Cancelled") == "do_not_renew"
    assert normalize_disposition("Lost to Competitor") == "lost_price"
    assert normalize_disposition("Lost — Coverage") == "lost_coverage"


def test_remaining_required_lists_only_current_stage():
    left = remaining_required("Quote Requested", {})
    assert left == ["record_carrier_responses"]
