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


# ---------------------------------------------------------------------------
# Step 3: synthesizer port (agency_intake.synthesize_from_payload + render_hermes_blocks)
# ---------------------------------------------------------------------------


class TestPayloadToRawText:
    def test_flattens_transcript_notes_documents_coaching(self) -> None:
        from hermes.commands.agency_intake import _payload_dict_to_raw_text

        raw = _payload_dict_to_raw_text({
            "transcript": "Sandra called.",
            "notes": "Follow up on umbrella.",
            "documents": [
                {"type": "drivers_license", "extracted_data": {"name": "Sandra", "state": "GA"}},
            ],
            "coaching_snapshot": {
                "covered_topics": ["vehicles", "drivers"],
                "remaining_gaps": ["claims"],
                "flags_detected": [{"flag": "no garaging address", "severity": "med", "context": "..."}],
            },
        })
        assert "=== TRANSCRIPT ===" in raw
        assert "Sandra called." in raw
        assert "=== NOTES ===" in raw
        assert "=== DOCUMENTS ===" in raw
        assert "drivers_license" in raw
        assert "name: Sandra" in raw
        assert "=== COACHING SNAPSHOT ===" in raw
        assert "Covered topics: vehicles, drivers" in raw
        assert "[med] no garaging address" in raw

    def test_skips_empty_sections(self) -> None:
        from hermes.commands.agency_intake import _payload_dict_to_raw_text

        raw = _payload_dict_to_raw_text({"transcript": "hi", "documents": [], "notes": ""})
        assert "TRANSCRIPT" in raw
        assert "DOCUMENTS" not in raw
        assert "NOTES" not in raw

    def test_documents_only_payload_still_produces_text(self) -> None:
        from hermes.commands.agency_intake import _payload_dict_to_raw_text

        raw = _payload_dict_to_raw_text({
            "documents": [{"type": "dec_page", "raw_text": "POLICY: ABC-123"}],
        })
        assert "POLICY: ABC-123" in raw

    def test_empty_payload_returns_empty_string(self) -> None:
        from hermes.commands.agency_intake import _payload_dict_to_raw_text

        assert _payload_dict_to_raw_text({}) == ""
        assert _payload_dict_to_raw_text({"notes": ""}) == ""


class TestSynthesizeFromPayload:
    @patch("hermes.commands.agency_intake._extract_payload")
    def test_happy_path_returns_extracted_plus_warnings(self, mock_extract) -> None:
        from hermes.commands.agency_intake import synthesize_from_payload

        mock_extract.return_value = {
            "action": "crm_intake_upsert",
            "approval_required": True,
            "account": {"account_name": "Acme"},
            "contacts": [],
            "opportunities": [
                {"opportunity_name": "Acme - GL - 06/01", "line_of_business": "General Liability", "stage": "Discovery"},
            ],
            "facts": [],
            "duplicate_search": {},
        }
        result, warnings = synthesize_from_payload({"transcript": "Acme is a contractor."})
        assert result["account"]["account_name"] == "Acme"
        assert isinstance(warnings, list)
        mock_extract.assert_called_once()
        # The flattened text must have been handed to the LLM.
        assert "=== TRANSCRIPT ===" in mock_extract.call_args.args[0]

    def test_empty_payload_raises(self) -> None:
        from hermes.commands.agency_intake import AgencyIntakeError, synthesize_from_payload

        with pytest.raises(AgencyIntakeError, match="nothing to synthesize"):
            synthesize_from_payload({})

    @patch("hermes.commands.agency_intake._extract_payload")
    def test_extractor_error_propagates(self, mock_extract) -> None:
        from hermes.commands.agency_intake import AgencyIntakeError, synthesize_from_payload

        mock_extract.side_effect = AgencyIntakeError("openai timeout")
        with pytest.raises(AgencyIntakeError, match="openai timeout"):
            synthesize_from_payload({"transcript": "..."})


class TestRenderHermesBlocks:
    def test_renders_full_3D_pumps_style_payload(self) -> None:
        from hermes.commands.agency_intake import render_hermes_blocks

        payload = {
            "account": {
                "account_name": "3D Pumps LLC",
                "legal_name": "3D Pumps LLC",
                "entity_type": "LLC",
                "industry": "Water Infrastructure / Specialty Contracting",
                "phone": "(770) 780-8848",
                "email": "jarod.mattison@gmail.com",
                "city": "Atlanta",
                "state": "GA",
                "tags": ["Critical CPL binding gap"],
            },
            "contacts": [
                {"full_name": "Jarod Denero Mattison", "role": "Sole Member / Principal",
                 "phone": "(770) 780-8848", "primary_contact": True},
            ],
            "opportunities": [
                {"opportunity_name": "3D Pumps LLC - GL - 05/21/2026",
                 "line_of_business": "General Liability", "stage": "Discovery",
                 "opportunity_type": "New Business"},
                {"opportunity_name": "3D Pumps LLC - CPL - 05/21/2026",
                 "line_of_business": "Commercial Package", "stage": "Discovery"},
            ],
            "note": {"title": "3D Pumps Summary", "body": "Facts:\n- bypass pumping\n", "note_type": "Quote Summary"},
            "facts": [{"sensitivity": "restricted"}, {"sensitivity": "standard"}],
        }
        rendered = render_hermes_blocks(payload)
        assert rendered.startswith("Hermes:")
        assert "MODULE: account" in rendered
        assert "NAME: 3D Pumps LLC" in rendered
        assert "PHONE: (770) 780-8848" in rendered
        assert "MODULE: contact" in rendered
        assert "NAME: Jarod Denero Mattison" in rendered
        assert "PRIMARY CONTACT: yes" in rendered
        # Both opportunities present.
        assert rendered.count("MODULE: opportunity") == 2
        assert "LINE OF BUSINESS: General Liability" in rendered
        assert "LINE OF BUSINESS: Commercial Package" in rendered
        assert "MODULE: note" in rendered
        assert "TITLE: 3D Pumps Summary" in rendered
        assert "BODY:" in rendered
        # Indented body lines
        assert "    Facts:" in rendered or "Facts:" in rendered
        # Facts summary
        assert "MODULE: facts (2 total, 1 restricted)" in rendered

    def test_skips_empty_modules(self) -> None:
        from hermes.commands.agency_intake import render_hermes_blocks

        rendered = render_hermes_blocks({
            "account": {"account_name": "Acme"},
            "contacts": [{}],  # invalid contact (no name) → skipped
            "opportunities": [],
            "note": {},
        })
        assert "MODULE: account" in rendered
        assert "MODULE: contact" not in rendered
        assert "MODULE: opportunity" not in rendered
        assert "MODULE: note" not in rendered

    def test_empty_payload_returns_header_only(self) -> None:
        from hermes.commands.agency_intake import render_hermes_blocks

        rendered = render_hermes_blocks({})
        assert rendered.strip() == "Hermes:"


class TestWorkerWithRealSynthesizer:
    """End-to-end of the Step 3 wiring: worker -> synthesize_from_payload ->
    render_hermes_blocks -> transitions, with the LLM call itself mocked."""

    @patch("hermes.commands.agency_intake._extract_payload")
    def test_valid_payload_reaches_awaiting_approval(self, mock_extract) -> None:
        mock_extract.return_value = {
            "action": "crm_intake_upsert",
            "approval_required": True,
            "account": {"account_name": "Acme"},
            "contacts": [{"full_name": "Jane Doe"}],
            "opportunities": [{"opportunity_name": "Acme - GL", "line_of_business": "General Liability", "stage": "Discovery"}],
            "facts": [],
            "duplicate_search": {},
            "note": {"title": "t", "body": "b"},
        }

        supa = MagicMock()
        supa.select.side_effect = [
            [{"id": "sub-1", "status_history": []}],  # claim_next_received select
            [{"id": "sub-1", "status": "synthesizing", "status_history": []}],
            [{"id": "sub-1", "status": "synthesized", "status_history": []}],
            [{"id": "sub-1", "status": "drafting", "status_history": []}],
        ]
        supa.update.return_value = {"id": "sub-1"}
        supa.update_where.return_value = [{
            "id": "sub-1", "payload": {"transcript": "Acme is a contractor."},
            "status_history": []
        }]

        # Use the real synthesize_from_payload + render_hermes_blocks
        # (already wired into the worker by Step 3). Slack post stubbed.
        intake_worker.post_draft = MagicMock(return_value=None)

        assert process_one_received(supa) is True

        statuses = [c.args[2]["status"] for c in supa.update.call_args_list]
        assert statuses == ["synthesized", "drafting", "awaiting_approval"]
        synth_update = supa.update.call_args_list[0].args[2]
        assert "Hermes:" in synth_update["hermes_blocks"]
        assert "MODULE: account" in synth_update["hermes_blocks"]
        assert synth_update["draft_summary"]["account"]["account_name"] == "Acme"


# ---------------------------------------------------------------------------
# Step 4: Slack draft post (post_draft_to_slack)
# ---------------------------------------------------------------------------


class TestPostDraftToSlack:
    @patch("hermes.operations.intake_worker.SlackNotifier")
    def test_post_format_includes_summary_and_blocks(self, MockNotifier) -> None:
        from hermes.operations.intake_worker import post_draft_to_slack

        notifier = MockNotifier.return_value
        notifier.post_message.return_value = {"ok": True}

        draft_summary = {
            "account": {"account_name": "3D Pumps LLC", "entity_type": "LLC", "industry": "Construction"},
            "contacts": [{"full_name": "Jarod Mattison"}],
            "opportunities": [
                {"line_of_business": "General Liability", "stage": "Discovery"},
                {"line_of_business": "Commercial Package", "stage": "Discovery"},
            ],
            "facts": [{"sensitivity": "restricted"}, {}],
            "note": {"title": "Quote summary", "note_type": "Quote Summary"},
        }
        post_draft_to_slack("sub-abc-123", draft_summary)

        notifier.post_message.assert_called_once()
        kwargs = notifier.post_message.call_args.kwargs
        text = kwargs["text"]
        blocks = kwargs["blocks"]

        # Text body conveys the key fields.
        assert "Intake draft ready" in text
        assert "submission_id" in text
        assert "sub-abc-123" in text
        assert "3D Pumps LLC" in text
        assert "Jarod Mattison" in text
        assert "General Liability" in text
        assert "Commercial Package" in text
        assert "(1 restricted)" in text

        # Blocks are the 6 approval buttons.
        actions_block = next(b for b in blocks if b.get("type") == "actions")
        button_ids = [el["action_id"] for el in actions_block["elements"]]
        assert button_ids == [
            "agency_intake_approve_all",
            "agency_intake_approve_crm",
            "agency_intake_approve_supabase",
            "agency_intake_approve_tasks",
            "agency_intake_revise",
            "agency_intake_cancel",
        ]

    @patch("hermes.operations.intake_worker.SlackNotifier")
    def test_metadata_carries_submission_id_not_draft_id(self, MockNotifier) -> None:
        from hermes.operations.intake_worker import post_draft_to_slack

        notifier = MockNotifier.return_value
        notifier.post_message.return_value = {"ok": True}

        post_draft_to_slack("submission-uuid-zzz", {"account": {"account_name": "Acme"}})

        blocks = notifier.post_message.call_args.kwargs["blocks"]
        actions_block = next(b for b in blocks if b.get("type") == "actions")

        # block_id encodes submission_id
        assert actions_block["block_id"] == "agency_intake_actions_submission-uuid-zzz"

        # Every button's `value` is the submission_id (the Slack listener
        # reads action.value to recover it).
        for element in actions_block["elements"]:
            assert element["value"] == "submission-uuid-zzz"

    @patch("hermes.operations.intake_worker._post_alert")
    @patch("hermes.operations.intake_worker.SlackNotifier")
    def test_slack_failure_routes_to_alert_channel(
        self, MockNotifier, mock_alert,
    ) -> None:
        """If the draft post fails, the helper must NOT crash the worker —
        it should log and post a warning to the alert channel so the
        operator knows the row is stuck at awaiting_approval."""
        from hermes.integrations.slack_notifier import SlackNotifierError
        from hermes.operations.intake_worker import post_draft_to_slack

        notifier = MockNotifier.return_value
        notifier.post_message.side_effect = SlackNotifierError("rate limited")

        # Should not raise.
        post_draft_to_slack("sub-1", {"account": {"account_name": "X"}})

        # An alert post happened with the submission_id surfaced.
        mock_alert.assert_called_once()
        alert_text = mock_alert.call_args.args[0]
        assert "Slack post failed" in alert_text
        assert "sub-1" in alert_text
        assert "rate limited" in alert_text

    @patch("hermes.operations.intake_worker.SlackNotifier")
    def test_uses_draft_channel_env(self, MockNotifier, monkeypatch) -> None:
        from hermes.operations.intake_worker import post_draft_to_slack

        monkeypatch.setenv("HERMES_INTAKE_DRAFT_CHANNEL", "CCUSTOMDRAFT")
        MockNotifier.return_value.post_message.return_value = {"ok": True}

        post_draft_to_slack("sub-1", {"account": {"account_name": "X"}})

        MockNotifier.assert_called_with(channel="CCUSTOMDRAFT")


class TestPostDraftWiredIntoWorker:
    """Confirms the module-level indirection actually points at the real
    post_draft_to_slack after import (Step 4 wiring)."""

    def test_post_draft_is_real_function_not_stub(self) -> None:
        from hermes.operations.intake_worker import post_draft, post_draft_to_slack

        assert post_draft is post_draft_to_slack


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
