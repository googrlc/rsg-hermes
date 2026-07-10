"""Tests for hermes.jobs.espo_to_nowcerts_writeback — Cases -> NowCerts ledger."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hermes.jobs.espo_to_nowcerts_writeback import run_writeback


def fake_espo(cases, account_guid_map, tasks=None):
    """MagicMock EspoClient: Case/Task list endpoints + Account-by-id GUID lookup."""
    tasks = tasks or []
    espo = MagicMock()

    def _get(path, params=None):
        if path == "Case":
            return {"list": list(cases), "total": len(cases)}
        if path == "Task":
            return {"list": list(tasks), "total": len(tasks)}
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


class WritebackTaskChannelTests(unittest.TestCase):
    def test_client_linked_task_creates(self) -> None:
        task = {"id": "T1", "name": "Call client re: COI", "status": "Inbox",
                "taskType": "Client Service", "urgency": "High", "accountId": "A1",
                "dateEnd": "2026-07-12 00:00:00", "syncSource": "Manual"}
        espo = fake_espo([], {"A1": "GUID-1"}, tasks=[task])
        nc = MagicMock()
        nc.insert_task.return_value = {"data": {"database_id": "NT-1"}}

        r = run_writeback(espo, nc)
        self.assertEqual(r.created, 1)
        p = nc.insert_task.call_args.args[0]
        self.assertEqual(p["category_name"], "Client Service")
        self.assertEqual(p["insured_database_id"], "GUID-1")
        self.assertEqual(p["priority"], "High")
        self.assertEqual(espo.patch.call_args.args[0], "Task/T1")  # stamped on Task

    def test_internal_hermes_task_skipped(self) -> None:
        task = {"id": "T2", "name": "Renewal prep", "status": "Inbox",
                "taskType": "Renewal", "accountId": "A1", "syncSource": "Hermes"}
        espo = fake_espo([], {"A1": "GUID-1"}, tasks=[task])
        nc = MagicMock()

        r = run_writeback(espo, nc)
        self.assertEqual(r.skipped_internal, 1)
        self.assertEqual(r.created, 0)
        nc.insert_task.assert_not_called()

    def test_task_linked_via_parent_account(self) -> None:
        task = {"id": "T3", "name": "Endorsement", "status": "In Progress",
                "taskType": "Policy Change", "parentType": "Account", "parentId": "A1",
                "syncSource": "Email"}
        espo = fake_espo([], {"A1": "GUID-1"}, tasks=[task])
        nc = MagicMock()
        nc.insert_task.return_value = {"data": {"database_id": "NT-3"}}

        r = run_writeback(espo, nc)
        self.assertEqual(r.created, 1)
        self.assertEqual(nc.insert_task.call_args.args[0]["status"], "Open")

    def test_completed_task_maps_to_closed_and_updates(self) -> None:
        task = {"id": "T4", "name": "Done thing", "status": "Completed",
                "taskType": "Client Service", "accountId": "A1",
                "momentumTaskId": "NT-OLD", "syncSource": "Manual"}
        espo = fake_espo([], {"A1": "GUID-1"}, tasks=[task])
        nc = MagicMock()
        nc.update_task.return_value = {}

        r = run_writeback(espo, nc)
        self.assertEqual(r.updated, 1)
        nc.insert_task.assert_not_called()
        p = nc.update_task.call_args.args[0]
        self.assertEqual(p["status"], "Closed")
        self.assertEqual(p["database_id"], "NT-OLD")

    def test_unlinked_task_skipped(self) -> None:
        task = {"id": "T5", "name": "internal note", "status": "Inbox",
                "taskType": "Admin", "syncSource": "Manual"}  # no account/parent
        espo = fake_espo([], {}, tasks=[task])
        nc = MagicMock()

        r = run_writeback(espo, nc)
        self.assertEqual(r.skipped_no_client, 1)
        nc.insert_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
