"""Validators produce the flags the review gate enforces."""
from datetime import date

from hermes.command_center.lanes import load_all_lanes
from hermes.command_center.submission import (
    IntakeMeta,
    SourceChannel,
    SubmissionObject,
)
from hermes.command_center.validators import run_validators


def _sub(**kw) -> SubmissionObject:
    base = dict(submission_id="sub_x", intake=IntakeMeta(channel=SourceChannel.WEBUI))
    base.update(kw)
    return SubmissionObject(**base)


def test_xdate_missing_is_blocking():
    flags = run_validators(_sub(), ["xdate_present"])
    assert len(flags) == 1
    assert flags[0]["field"] == "xdate"
    assert flags[0]["severity"] == "blocking"


def test_xdate_present_no_flag():
    sub = _sub(current_policy_expiration=date(2026, 7, 1))
    assert run_validators(sub, ["xdate_present"]) == []


def test_insured_name_satisfied_by_client_name():
    assert run_validators(_sub(client_name="Jane Roe"), ["insured_name_present"]) == []


def test_premium_number_ok_but_garbage_warns():
    assert run_validators(_sub(current_premium=1200.0), ["premium_is_number"]) == []
    sub = _sub(current_premium=1200.0)
    sub.current_premium = "lots"   # simulate a bad extraction slipping the model
    flags = run_validators(sub, ["premium_is_number"])
    assert flags and flags[0]["severity"] == "warning"


def test_gretchen_lane_blocks_until_filled():
    g = load_all_lanes()["gretchen-personal-lines"]
    empty = run_validators(_sub(), g.validators)
    assert {f["field"] for f in empty} >= {"xdate", "insured_name"}
    good = _sub(client_name="Jane", current_policy_expiration=date(2026, 7, 1))
    blocking = [f for f in run_validators(good, g.validators) if f["severity"] == "blocking"]
    assert blocking == []
