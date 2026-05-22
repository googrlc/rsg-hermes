from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from hermes.core.client import EspoClientError
from hermes.operations.crm_queue_worker import MAX_ATTEMPTS, _apply_to_espo, enqueue_crm_write, process_queue


class EnqueueCrmWriteTests(unittest.TestCase):
    def test_enqueues_espocrm_payload_with_priority(self) -> None:
        supa = MagicMock()
        supa.insert.return_value = {"id": "q-1"}

        enqueue_crm_write(
            supa,
            entity_type="Renewal",
            entity_id="ren-123",
            created_by_role="service",
            priority=1,
            payload={
                "action_type": "request_docs",
                "context": {
                    "client_name": "Smith Auto",
                    "requested_by": "service",
                    "urgency": "30_day_window",
                },
            },
        )

        supa.insert.assert_called_once()
        table_name, row = supa.insert.call_args.args
        self.assertEqual(table_name, "crm_write_queue")
        self.assertEqual(row["target_system"], "EspoCRM")
        self.assertEqual(row["priority"], 1)
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["payload"]["action_type"], "request_docs")

    def test_rejects_non_espocrm_target(self) -> None:
        supa = MagicMock()

        with self.assertRaises(ValueError):
            enqueue_crm_write(
                supa,
                entity_type="Task",
                created_by_role="service",
                target_system="HubSpot",
                payload={"action_type": "update_status", "context": {"status": "Completed"}},
            )

    def test_rejects_bad_payload_shape(self) -> None:
        supa = MagicMock()

        with self.assertRaises(ValueError):
            enqueue_crm_write(
                supa,
                entity_type="Task",
                created_by_role="service",
                payload={"action_type": "update_status", "context": "not-an-object"},
            )


class ApplyToEspoTests(unittest.TestCase):
    def test_action_payload_updates_when_entity_id_present(self) -> None:
        espo = MagicMock()
        espo.update.return_value = {"id": "task-1"}

        _apply_to_espo(
            espo,
            entity_type="Task",
            entity_id="task-1",
            payload={
                "action_type": "update_status",
                "context": {"status": "Completed"},
            },
        )

        espo.update.assert_called_once_with("Task", "task-1", {"status": "Completed"})
        espo.create.assert_not_called()

    def test_action_payload_creates_when_entity_missing(self) -> None:
        espo = MagicMock()
        espo.create.return_value = {"id": "ren-1"}

        _apply_to_espo(
            espo,
            entity_type="Renewal",
            entity_id=None,
            payload={
                "action_type": "create_renewal",
                "context": {"client_name": "Smith Auto"},
            },
        )

        espo.create.assert_called_once_with("Renewal", {"client_name": "Smith Auto"})
        espo.update.assert_not_called()


class ProcessQueueRetryTests(unittest.TestCase):
    @patch("hermes.operations.crm_queue_worker.log_guardrail_event")
    @patch("hermes.operations.crm_queue_worker._alert_slack_on_terminal_failure")
    def test_terminal_failures_log_guardrail_and_alert_slack(
        self, alert_mock: MagicMock, guardrail_mock: MagicMock
    ) -> None:
        supa = MagicMock()
        supa.select.return_value = [
            {
                "id": "q-1",
                "entity_type": "Task",
                "entity_id": "task-1",
                "payload": {"action_type": "update_status", "context": {"status": "Completed"}},
                "attempt_count": MAX_ATTEMPTS - 1,
                "created_by_role": "dashboard",
            }
        ]
        espo = MagicMock()
        espo.update.side_effect = EspoClientError("429 upstream throttle")

        result = process_queue(supa, espo, batch_size=1, dry_run=False)

        self.assertFalse(result.ok)
        self.assertEqual(result.failed, 1)
        self.assertTrue(any("429" in err for err in result.errors))
        guardrail_mock.assert_called_once()
        alert_mock.assert_called_once()


class NonRetryableErrorTests(unittest.TestCase):
    """Tests for the 4xx validation-failure short-circuit.

    EspoCRM validation failures (400 with messageTranslation) won't succeed
    on retry. The worker bumps attempt_count straight to MAX_ATTEMPTS so the
    row drops out of the dequeue filter on the first failure.
    """

    def test_non_retryable_error_detector(self) -> None:
        from hermes.operations.crm_queue_worker import _is_non_retryable_espo_error

        # Exact shape EspoCRM returned for the 3D Pumps Opportunity failure.
        msg = (
            "400 POST Opportunity: EspoCRM request failed. "
            "Body: {'messageTranslation': {'label': 'validationFailure', "
            "'scope': None, 'data': {'field': 'lineOfBusiness', 'type': 'valid'}}}"
        )
        self.assertTrue(_is_non_retryable_espo_error(msg))

        self.assertTrue(_is_non_retryable_espo_error("404 GET Account: not found"))
        self.assertTrue(_is_non_retryable_espo_error("422 POST Contact: unprocessable"))

        self.assertFalse(_is_non_retryable_espo_error("429 upstream throttle"))
        self.assertFalse(_is_non_retryable_espo_error("500 internal server error"))
        self.assertFalse(_is_non_retryable_espo_error("503 upstream unavailable"))
        self.assertFalse(_is_non_retryable_espo_error("Connection reset by peer"))
        self.assertFalse(_is_non_retryable_espo_error(""))

    @patch("hermes.operations.crm_queue_worker.log_guardrail_event")
    @patch("hermes.operations.crm_queue_worker._alert_slack_on_terminal_failure")
    def test_400_validation_failure_short_circuits_to_max_attempts(
        self, alert_mock: MagicMock, guardrail_mock: MagicMock
    ) -> None:
        """A 400 validation error on attempt 1 should mark the row as if it had
        exhausted all retries — no more dequeue, terminal-failure alert fires."""
        supa = MagicMock()
        supa.select.return_value = [
            {
                "id": "q-validation",
                "entity_type": "Opportunity",
                "entity_id": None,
                "payload": {"name": "X", "lineOfBusiness": "Unknown LOB"},
                "attempt_count": 0,
                "created_by_role": "agency-intake:U_TEST",
            }
        ]
        espo = MagicMock()
        espo.create.side_effect = EspoClientError(
            "400 POST Opportunity: EspoCRM request failed. "
            "Body: {'messageTranslation': {'label': 'validationFailure'}}"
        )

        result = process_queue(supa, espo, batch_size=1, dry_run=False)

        self.assertEqual(result.failed, 1)
        # Status update should record attempt_count = MAX_ATTEMPTS so the row
        # is excluded from future dequeues. Inspect the FAILED update call.
        update_calls = [
            call for call in supa.update.call_args_list
            if call.args and call.args[0] == "crm_write_queue" and call.args[1] == "q-validation"
        ]
        terminal_update = next(
            (c for c in update_calls if c.args[2].get("status") == "FAILED"),
            None,
        )
        self.assertIsNotNone(terminal_update, "expected a FAILED status update")
        self.assertEqual(terminal_update.args[2]["attempt_count"], MAX_ATTEMPTS)
        alert_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
