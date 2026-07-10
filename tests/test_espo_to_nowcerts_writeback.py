"""Tests for hermes.jobs.espo_to_nowcerts_writeback — Cases -> NowCerts ledger."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hermes.jobs.espo_to_nowcerts_writeback import run_writeback


def fake_espo(cases, account_guid_map):
    """MagicMock EspoClient: Case list endpoint + Account-by-id GUID lookup."""
    espo = MagicMock()

    def _get(path, params=None):
        if path == "Case":
            return {"list": list(cases), "total": len(cases)}
        if path.startswith("Account/"):
            aid = path.split("/", 1)[1]
            return {"id": aid, "momentum_client_id": account_guid_map.get(aid)}
        return {}

    espo.get.side_effect = _get
    return espo


class WritebackCreateTests(unittest.TestCase):
    def test_creates_task_and_stamps_case(self) -> None:
        case = {
            "id": "C1", "name": "COI for Acme", "status": "New",
            "type": "Certificate of Insurance (COI)", "priority": "Normal",
            "accountId": "A1", "createdAt": "2026-07-10 12:00:00",
        }
        espo = fake_espo([case], {"A1": "GUID-1"})
        nc = MagicMock()
        # Real NowCerts InsertTask shape: id nested under "data".
        nc.insert_task.return_value = {
            "status": 1,
            "data": {"database_id": "TASK-NEW", "status": "Open"},
            "message": "Task ... created",
        }

        result = run_writeback(espo, nc, dry_run=False)

        self.assertEqual(result.created, 1)
        self.assertEqual(result.failed, 0)
        payload = nc.insert_task.call_args.args[0]
        self.assertEqual(payload["category_name"], "Certificate of Insurance (COI)")
        self.assertEqual(payload["insured_database_id"], "GUID-1")
        self.assertEqual(payload["status"], "Open")
        self.assertEqual(payload["priority"], "Medium")
        self.assertNotIn("database_id", payload)  # create, not update
        # Case stamped with the returned task id
        self.assertEqual(espo.patch.call_args.args[0], "Case/C1")
        self.assertEqual(espo.patch.call_args.kwargs["json"]["momentumTaskId"], "TASK-NEW")

    def test_insert_without_returned_id_fails_loudly(self) -> None:
        case = {"id": "C1", "name": "x", "status": "New", "type": "Other",
                "accountId": "A1", "createdAt": "2026-07-10 12:00:00"}
        espo = fake_espo([case], {"A1": "GUID-1"})
        nc = MagicMock()
        nc.insert_task.return_value = {"status": 1, "message": "ok"}  # no id

        result = run_writeback(espo, nc)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.failed, 1)
        espo.patch.assert_not_called()


class WritebackUpdateTests(unittest.TestCase):
    def test_updates_when_already_linked(self) -> None:
        case = {
            "id": "C2", "name": "Add unit", "status": "Closed", "type": "Add Vehicle",
            "priority": "High", "accountId": "A1", "momentumTaskId": "TASK-9",
            "createdAt": "2026-07-01 00:00:00",
        }
        espo = fake_espo([case], {"A1": "GUID-1"})
        nc = MagicMock()
        nc.update_task.return_value = {}

        result = run_writeback(espo, nc)

        self.assertEqual(result.updated, 1)
        nc.insert_task.assert_not_called()
        payload = nc.update_task.call_args.args[0]
        self.assertEqual(payload["database_id"], "TASK-9")
        self.assertEqual(payload["status"], "Closed")
        self.assertEqual(payload["priority"], "High")


class WritebackClientLinkedFilterTests(unittest.TestCase):
    def test_skips_account_without_guid(self) -> None:
        case = {"id": "C3", "name": "x", "status": "New", "type": "Other",
                "accountId": "A2", "createdAt": "2026-07-10 00:00:00"}
        espo = fake_espo([case], {"A2": None})
        nc = MagicMock()

        result = run_writeback(espo, nc)
        self.assertEqual(result.skipped_no_client, 1)
        nc.insert_task.assert_not_called()

    def test_skips_case_without_account(self) -> None:
        case = {"id": "C4", "name": "x", "status": "New", "type": "Other",
                "createdAt": "2026-07-10 00:00:00"}
        espo = fake_espo([case], {})
        nc = MagicMock()

        result = run_writeback(espo, nc)
        self.assertEqual(result.skipped_no_client, 1)
        nc.insert_task.assert_not_called()


class WritebackDryRunTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        case = {"id": "C5", "name": "x", "status": "New", "type": "Other",
                "accountId": "A1", "createdAt": "2026-07-10 00:00:00"}
        espo = fake_espo([case], {"A1": "GUID-1"})
        nc = MagicMock()

        result = run_writeback(espo, nc, dry_run=True)
        self.assertEqual(result.created, 1)   # counted...
        nc.insert_task.assert_not_called()     # ...but not written
        nc.update_task.assert_not_called()
        espo.patch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
