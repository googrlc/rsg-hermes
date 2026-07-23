"""The review gate is the most important code — these prove its hard rules."""
import pytest

from hermes.command_center.review import (
    Flag,
    ReviewError,
    ReviewState,
    Severity,
    assert_can_approve,
    assert_can_download,
    assert_transition,
    has_blocking_flags,
    review_event,
)


def test_download_blocked_while_in_review():
    with pytest.raises(ReviewError) as ei:
        assert_can_download(ReviewState.IN_REVIEW)
    assert ei.value.status_code == 403


def test_download_ok_once_approved():
    assert_can_download(ReviewState.APPROVED)   # no raise
    assert_can_download("delivered")            # accepts raw status strings too


def test_approve_with_blocking_flag_is_422():
    flags = [Flag("xdate", "missing X-date").to_dict()]
    with pytest.raises(ReviewError) as ei:
        assert_can_approve(ReviewState.IN_REVIEW, flags)
    assert ei.value.status_code == 422


def test_approve_clean_passes():
    flags = [Flag("premium", "not a number", Severity.WARNING)]  # warning != blocking
    assert has_blocking_flags(flags) is False
    assert_can_approve(ReviewState.IN_REVIEW, flags)             # no raise


def test_approve_only_from_in_review():
    with pytest.raises(ReviewError) as ei:
        assert_can_approve(ReviewState.DRAFT, [])
    assert ei.value.status_code == 409




def test_state_cannot_skip():
    with pytest.raises(ReviewError) as ei:
        assert_transition(ReviewState.DRAFT, ReviewState.APPROVED)
    assert ei.value.status_code == 409


def test_full_happy_path_with_audit_trail():
    state = ReviewState.DRAFT
    events = []
    for nxt in (ReviewState.EXTRACTING, ReviewState.IN_REVIEW,
                ReviewState.APPROVED, ReviewState.DELIVERED):
        if nxt is ReviewState.APPROVED:
            assert_can_approve(state, [])           # clean -> allowed
        assert_transition(state, nxt)
        state = nxt
        events.append(review_event("sub_1", "gretchen", f"->{nxt.value}"))
    assert state is ReviewState.DELIVERED
    assert [e["action"] for e in events] == [
        "->extracting", "->in_review", "->approved", "->delivered"
    ]
    assert all(e["at"] and e["submission_id"] == "sub_1" for e in events)
