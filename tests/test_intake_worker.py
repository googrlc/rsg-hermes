"""Tests for the Phase 3 intake_submissions worker."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.operations import intake_worker
from hermes.operations.intake_worker import process_one_received, tick


@pytest.fixture(autouse=True)
def _reset_module_stubs():
    """Restore the module-level synthesize/render/post indirections after each test."""
    orig = (intake_worker.synthesize_payload, intake_worker.render_blocks, intake_worker.post_draft)
    yield
    (intake_worker.synthesize_payload, intake_worker.render_blocks, intake_worker.post_draft) = orig


@pytest.fixture
def supa_with_received_row():
    """A MagicMock SupabaseClient where claim_next_received returns one row."""
    supa = MagicMock()
    # The select called by claim_next_received → return one candidate.
    supa.select.return_value = [{"id": "sub-1", "status_history": []}]
    # The update_where called by claim_next_received → atomic claim win.
    supa.update_where.return_value = [{
        "id": "sub-1",
        "status": "synthesizing",
        "status_history": [{"from": "received", "to": "synthesizing", "at": "t0", "note": "claimed by worker"}],
        "payload": {"transcript": "hi"},
    }]
    return supa


@pytest.fixture
def supa_with_empty_queue():
    """A MagicMock SupabaseClient where the received queue is empty."""
    supa = MagicMock()
    supa.select.return_value = []
    return supa


# ---------------------------------------------------------------------------
# Claim arc
# ---------------------------------------------------------------------------


class TestProcessOneReceived:
    def test_empty_queue_returns_false(self, supa_with_empty_queue) -> None:
        assert process_one_received(supa_with_empty_queue) is False
        # No transitions attempted on an empty queue
        supa_with_empty_queue.update.assert_not_called()
        supa_with_empty_queue.update_where.assert_not_called()

    def test_claim_race_lost_returns_false(self) -> None:
        """If the UPDATE-WHERE conditional returns [] (another worker won),
        the function reports no work done."""
        supa = MagicMock()
        supa.select.return_value = [{"id": "sub-1", "status_history": []}]
        supa.update_where.return_value = []  # race lost
        assert process_one_received(supa) is False

    def test_happy_path_reaches_awaiting_approval(self, supa_with_received_row) -> None:
        supa = supa_with_received_row
        # Re-fetch row inside transition() returns the row in its latest state.
        # First call: synthesized (after claim). Subsequent: drafting, awaiting_approval.
        supa.select.side_effect = [
            [{"id": "sub-1", "status_history": []}],  # claim_next_received select
            [{"id": "sub-1", "status": "synthesizing", "status_history": []}],  # transition: synthesizing -> synthesized
            [{"id": "sub-1", "status": "synthesized", "status_history": []}],  # synthesized -> drafting
            [{"id": "sub-1", "status": "drafting", "status_history": []}],  # drafting -> awaiting_approval
        ]
        supa.update.return_value = {"id": "sub-1"}

        intake_worker.synthesize_payload = lambda payload: ({"account": {"account_name": "Acme"}}, [])
        intake_worker.render_blocks = lambda payload: "rendered text"
        intake_worker.post_draft = MagicMock()

        assert process_one_received(supa) is True

        # Each forward transition issues an update with a new status.
        updates = [call.args[2] for call in supa.update.call_args_list]
        statuses = [u["status"] for u in updates]
        assert statuses == ["synthesized", "drafting", "awaiting_approval"]

        # Synthesized update carries hermes_blocks + draft_summary.
        synthesized = updates[0]
        assert synthesized["hermes_blocks"] == "rendered text"
        assert synthesized["draft_summary"] == {"account": {"account_name": "Acme"}}

        # Slack post called once with submission_id + draft_summary.
        intake_worker.post_draft.assert_called_once_with("sub-1", {"account": {"account_name": "Acme"}})

    def test_synthesizer_exception_transitions_to_failed(self, supa_with_received_row) -> None:
        supa = supa_with_received_row
        # Used: claim_next_received select, then transition() select for the failed transition.
        supa.select.side_effect = [
            [{"id": "sub-1", "status_history": []}],  # claim select
            [{"id": "sub-1", "status": "synthesizing", "status_history": [], "error_log": []}],  # failed transition select
        ]
        supa.update.return_value = {"id": "sub-1"}

        def boom(_):
            raise RuntimeError("openai timeout")
        intake_worker.synthesize_payload = boom

        with patch.object(intake_worker, "_post_alert") as mock_alert:
            assert process_one_received(supa) is True
            mock_alert.assert_called_once()
            assert "submission failed" in mock_alert.call_args.args[0]
            assert "openai timeout" in mock_alert.call_args.args[0]

        # The transition to 'failed' was attempted.
        update = supa.update.call_args.args[2]
        assert update["status"] == "failed"
        assert update["error_log"][-1]["stage"] == "synthesize"
        assert update["error_log"][-1]["exception_type"] == "RuntimeError"

    def test_stub_synthesizer_routes_to_failed(self, supa_with_received_row) -> None:
        """The default Step-2 stub raises NotImplementedError. The worker
        should catch that, mark the row failed, and NOT crash the loop."""
        supa = supa_with_received_row
        supa.select.side_effect = [
            [{"id": "sub-1", "status_history": []}],
            [{"id": "sub-1", "status": "synthesizing", "status_history": [], "error_log": []}],
        ]
        supa.update.return_value = {"id": "sub-1"}

        # Don't override — use the module's default stub.
        with patch.object(intake_worker, "_post_alert"):
            assert process_one_received(supa) is True

        update = supa.update.call_args.args[2]
        assert update["status"] == "failed"
        assert "NotImplementedError" in update["error_log"][-1]["exception_type"]

    def test_slack_post_failure_does_not_break_pipeline_when_post_swallows(
        self, supa_with_received_row,
    ) -> None:
        """post_draft is expected to be non-fatal (Slack post helper swallows
        errors). If it returns normally even after a Slack hiccup, the row
        should still settle at awaiting_approval."""
        supa = supa_with_received_row
        supa.select.side_effect = [
            [{"id": "sub-1", "status_history": []}],
            [{"id": "sub-1", "status": "synthesizing", "status_history": []}],
            [{"id": "sub-1", "status": "synthesized", "status_history": []}],
            [{"id": "sub-1", "status": "drafting", "status_history": []}],
        ]
        supa.update.return_value = {"id": "sub-1"}

        intake_worker.synthesize_payload = lambda payload: ({}, [])
        intake_worker.render_blocks = lambda payload: ""
        # The real post_draft swallows Slack errors internally. Simulate that.
        intake_worker.post_draft = MagicMock(return_value=None)

        assert process_one_received(supa) is True
        statuses = [c.args[2]["status"] for c in supa.update.call_args_list]
        assert statuses[-1] == "awaiting_approval"


# ---------------------------------------------------------------------------
# tick() smoke
# ---------------------------------------------------------------------------


class TestTick:
    def test_tick_reports_zero_when_empty(self) -> None:
        supa = MagicMock()
        supa.select.return_value = []
        result = tick(supa)
        assert result == {"received_processed": 0}

    def test_tick_reports_one_when_row_processed(self) -> None:
        supa = MagicMock()
        supa.select.side_effect = [
            [{"id": "sub-1", "status_history": []}],
            [{"id": "sub-1", "status": "synthesizing", "status_history": [], "error_log": []}],
        ]
        supa.update.return_value = {"id": "sub-1"}
        supa.update_where.return_value = [{"id": "sub-1", "payload": {"transcript": "x"}}]

        with patch.object(intake_worker, "_post_alert"):
            result = tick(supa)
        assert result == {"received_processed": 1}


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------


class TestChannelResolution:
    def test_alert_channel_default(self, monkeypatch) -> None:
        monkeypatch.delenv("HERMES_INTAKE_ALERT_CHANNEL", raising=False)
        monkeypatch.delenv("HERMES_SYSTEMS_CHECK_CHANNEL", raising=False)
        assert intake_worker._intake_alert_channel() == "C0ANSEP6SSD"

    def test_alert_channel_override(self, monkeypatch) -> None:
        monkeypatch.setenv("HERMES_INTAKE_ALERT_CHANNEL", "C123ABCDEF")
        assert intake_worker._intake_alert_channel() == "C123ABCDEF"

    def test_draft_channel_falls_back_to_sentinel(self, monkeypatch) -> None:
        monkeypatch.delenv("HERMES_INTAKE_DRAFT_CHANNEL", raising=False)
        monkeypatch.setenv("HERMES_SENTINEL_SLACK_CHANNEL", "DSENTINEL")
        assert intake_worker._intake_draft_channel() == "DSENTINEL"
