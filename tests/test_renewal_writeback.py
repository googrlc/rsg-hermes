"""Tests for the approval-gated NowCerts writeback: library + NL handlers + routing.

Supabase is mocked; nothing touches NowCerts. Synthetic identifiers only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.commands import renewal_writeback as rw
from hermes.core.dispatcher import DispatchResult, Dispatcher
from hermes.renewals import writeback
from hermes.renewals.executor import (
    ACTION_REQUEST_TERMS,
    ACTION_UPDATE_AMS,
    DESTINATION_NOWCERTS,
    OBJECT_TYPE_RENEWAL,
    QUEUE_QUEUED,
)


def _make_dispatcher() -> Dispatcher:
    d = Dispatcher(use_openai=False)
    d.supa = MagicMock()
    return d


# ============================================================ library

def test_propose_inserts_unapproved_nowcerts_row():
    supa = MagicMock()
    supa.insert.return_value = {"id": "q1"}
    writeback.propose_writeback(
        supa, action=ACTION_REQUEST_TERMS, policy_number="P1", expected_result="terms",
    )
    args, _ = supa.insert.call_args
    table, row = args
    assert table == "outbound_sync_queue"
    assert row["destination_system"] == DESTINATION_NOWCERTS
    assert row["object_type"] == OBJECT_TYPE_RENEWAL
    assert row["object_id"] == "P1"
    assert row["status"] == QUEUE_QUEUED
    assert row["approved_by"] is None and row["approved_at"] is None   # UNAPPROVED


def test_propose_update_ams_requires_fields():
    with pytest.raises(ValueError):
        writeback.propose_writeback(
            MagicMock(), action=ACTION_UPDATE_AMS, policy_number="P1", expected_result="x"
        )


def test_propose_unknown_action_raises():
    with pytest.raises(ValueError):
        writeback.propose_writeback(MagicMock(), action="bogus", policy_number="P1", expected_result="x")


def test_confirm_writeback_is_guarded():
    supa = MagicMock()
    supa.update_where.return_value = [{"id": "q1"}]
    writeback.confirm_writeback(supa, queue_id="q1", approved_by="lamar")
    _, kw = supa.update_where.call_args
    filters = kw["filters"]
    assert filters["id"] == "eq.q1"
    assert filters["status"] == f"eq.{QUEUE_QUEUED}"
    assert filters["approved_at"] == "is.null"           # never re-approve


def test_list_pending_filters_unapproved():
    supa = MagicMock()
    supa.select.return_value = []
    writeback.list_pending(supa, policy_number="P1")
    _, kw = supa.select.call_args
    params = kw["params"]
    assert params["approved_at"] == "is.null"
    assert params["object_id"] == "eq.P1"
    assert params["destination_system"] == f"eq.{DESTINATION_NOWCERTS}"


# ============================================================ handlers

def test_propose_handle_stages():
    supa = MagicMock()
    supa.select.return_value = []          # no project_85 renewal_id
    supa.insert.return_value = {"id": "q-123456"}
    r = rw.propose_handle("propose nowcerts write-back for policy TST-0001: request terms", supa=supa)
    assert r.ok and r.data["approved"] is False
    supa.insert.assert_called_once()


def test_propose_handle_needs_policy():
    r = rw.propose_handle("propose nowcerts write-back: request terms", supa=MagicMock())
    assert not r.ok and r.data.get("need_identifier") is True


def test_propose_handle_update_ams_directed_to_worksheet():
    r = rw.propose_handle("propose nowcerts write-back for policy TST-0001: update ams premium=5000", supa=MagicMock())
    assert not r.ok
    assert "worksheet" in r.message.lower()


def test_show_handle_lists_pending():
    supa = MagicMock()
    supa.select.return_value = [
        {"id": "q1", "object_id": "P1", "payload": {"action": "request_terms", "policy_number": "P1", "note": "call"}}
    ]
    r = rw.show_handle("show me the proposed nowcerts changes for policy P1", supa=supa)
    assert r.ok and r.data["count"] == 1
    assert "request_terms" in r.message


def test_confirm_handle_approves():
    supa = MagicMock()
    supa.update_where.return_value = [{"id": "q1", "payload": {"action": "request_terms"}}]
    r = rw.confirm_handle("approve the proposed nowcerts write-back for policy TST-0001", supa=supa)
    assert r.ok and r.data["approved"] == 1


def test_confirm_handle_needs_policy():
    r = rw.confirm_handle("approve the proposed nowcerts write-back", supa=MagicMock())
    assert not r.ok and r.data.get("need_identifier") is True


def test_confirm_handle_nothing_pending():
    supa = MagicMock()
    supa.update_where.return_value = []
    r = rw.confirm_handle("approve the proposed nowcerts write-back for policy TST-0001", supa=supa)
    assert not r.ok and r.data["approved"] == 0


# ============================================================ routing

def test_propose_routes():
    with patch("hermes.commands.renewal_writeback.propose_handle") as h:
        h.return_value = DispatchResult(True, "proposed")
        _make_dispatcher().dispatch("propose nowcerts write-back for policy TST-0001: request terms")
        h.assert_called_once()


def test_show_routes():
    with patch("hermes.commands.renewal_writeback.show_handle") as h:
        h.return_value = DispatchResult(True, "shown")
        _make_dispatcher().dispatch("show me the proposed nowcerts changes for policy P1")
        h.assert_called_once()


def test_confirm_routes_and_not_swallowed_by_approval_token():
    """The phrase must reach the writeback confirm route, not the generic APPROVE token path."""
    with patch("hermes.commands.renewal_writeback.confirm_handle") as h:
        h.return_value = DispatchResult(True, "approved")
        _make_dispatcher().dispatch("approve the proposed nowcerts write-back for policy P1")
        h.assert_called_once()


def test_bare_approve_all_still_hits_approval_token_not_writeback():
    with patch("hermes.commands.renewal_writeback.confirm_handle") as h:
        result = _make_dispatcher().dispatch("APPROVE ALL")
        h.assert_not_called()
        assert "No pending draft" in result.message
