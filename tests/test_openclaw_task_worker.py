from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hermes.operations.openclaw_task_worker import enqueue_openclaw_task, process_openclaw_queue


class OpenClawTaskWorkerTests(unittest.TestCase):
    def test_enqueue_openclaw_task(self) -> None:
        supa = MagicMock()
        supa.insert.return_value = {"id": "oc-1"}

        row = enqueue_openclaw_task(
            supa,
            task_type="risk_scoring",
            payload={"account_id": "acc-1"},
            requested_by="dashboard",
            priority=2,
            notify_slack=True,
        )

        self.assertEqual(row["id"], "oc-1")
        table, payload = supa.insert.call_args.args
        self.assertEqual(table, "openclaw_task_queue")
        self.assertEqual(payload["status"], "PENDING")
        self.assertEqual(payload["task_type"], "risk_scoring")

    def test_process_openclaw_queue(self) -> None:
        supa = MagicMock()
        supa.select.return_value = [
            {
                "id": "oc-1",
                "task_type": "carrier_matching",
                "payload": {"account_id": "acc-1"},
                "attempt_count": 0,
                "notify_slack": False,
                "requested_by": "dashboard",
            }
        ]

        result = process_openclaw_queue(supa, batch_size=10)

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertGreaterEqual(supa.update.call_count, 2)


if __name__ == "__main__":
    unittest.main()
