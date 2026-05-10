"""Tests for Hermes → OpenClaw queue producer."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from hermes.integrations.openclaw_producer import (
    enqueue_openclaw_task,
    validate_openclaw_task_payload,
)
from hermes.integrations.supabase_client import SupabaseClientError


class ValidatePayloadTests(unittest.TestCase):
    def test_crm_manager_requires_client_id(self) -> None:
        with self.assertRaises(ValueError):
            validate_openclaw_task_payload("crm-manager", {})

    def test_retention_requires_client_id(self) -> None:
        with self.assertRaises(ValueError):
            validate_openclaw_task_payload("retention-risk-scout", {"renewal_id": "r1"})

    def test_appetite_requires_state_and_industry_signal(self) -> None:
        with self.assertRaises(ValueError):
            validate_openclaw_task_payload("appetite-analyzer", {"naics_code": "236220"})
        with self.assertRaises(ValueError):
            validate_openclaw_task_payload("appetite-analyzer", {"state": "GA"})

    def test_appetite_ok_with_naics_and_state(self) -> None:
        validate_openclaw_task_payload(
            "appetite-analyzer",
            {"naics_code": "236220", "state": "GA"},
        )


class EnqueueRetryTests(unittest.TestCase):
    @patch("hermes.integrations.openclaw_producer.time.sleep")
    def test_retries_then_succeeds(self, _sleep: MagicMock) -> None:
        supa = MagicMock()
        supa.insert.side_effect = [
            SupabaseClientError("503"),
            SupabaseClientError("503"),
            {"id": "oc-final"},
        ]

        row = enqueue_openclaw_task(
            supa,
            task_type="crm-manager",
            payload={"client_id": "c1"},
            requested_by="analyst",
            priority=1,
        )

        self.assertEqual(row["id"], "oc-final")
        self.assertEqual(supa.insert.call_count, 3)

    @patch("hermes.integrations.openclaw_producer.time.sleep")
    def test_fails_after_four_attempts(self, _sleep: MagicMock) -> None:
        supa = MagicMock()
        supa.insert.side_effect = SupabaseClientError("503")

        with self.assertRaises(SupabaseClientError):
            enqueue_openclaw_task(
                supa,
                task_type="retention-risk-scout",
                payload={"client_id": "c1"},
            )

        self.assertEqual(supa.insert.call_count, 4)


if __name__ == "__main__":
    unittest.main()
