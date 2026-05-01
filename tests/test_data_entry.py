from __future__ import annotations

import unittest

from hermes.commands.data_entry import handle
from hermes.core.dispatcher import Dispatcher


class WriteClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, str, dict]] = []
        self.find_results: dict[tuple[str, str, str], dict] = {}
        self.get_calls: list[tuple[str, dict]] = []
        self.user_id = "user-1"

    def get_metadata(self):
        return {
            "entityDefs": {
                "Account": {
                    "fields": {
                        "name": {"type": "varchar"},
                        "account_type": {"type": "enum"},
                        "phoneNumber": {"type": "phone"},
                        "createdAt": {"type": "datetime", "readOnly": True},
                    }
                },
                "Task": {
                    "fields": {
                        "name": {"type": "varchar"},
                        "status": {"type": "enum"},
                        "dateEnd": {"type": "date"},
                        "assignedUser": {"type": "link"},
                    },
                    "links": {"assignedUser": {"type": "belongsTo"}},
                },
                "Lead": {
                    "fields": {
                        "firstName": {"type": "varchar"},
                        "lastName": {"type": "varchar"},
                        "name": {"type": "varchar"},
                        "emailAddress": {"type": "email"},
                        "phoneNumber": {"type": "phone"},
                        "status": {"type": "enum"},
                        "assignedUser": {"type": "link"},
                    },
                    "links": {"assignedUser": {"type": "belongsTo"}},
                },
                "Opportunity": {
                    "fields": {
                        "name": {"type": "varchar"},
                        "stage": {"type": "enum"},
                        "account": {"type": "link"},
                        "lineOfBusiness": {"type": "enum"},
                        "amount": {"type": "currency"},
                        "assignedUser": {"type": "link"},
                    },
                    "links": {"account": {"type": "belongsTo"}, "assignedUser": {"type": "belongsTo"}},
                },
            }
        }

    def ping(self):
        return {"user": {"id": self.user_id, "userName": "herm_assistant", "name": "herm_assistant"}}

    def get(self, entity: str, **kwargs):
        params = kwargs.get("params") or {}
        self.get_calls.append((entity, params))
        where = params.get("where") or []
        if where:
            rule = where[0]
            found = self.find_results.get((entity, rule.get("attribute"), rule.get("value")))
            if found:
                return {"list": [found], "total": 1}
        return {"list": [], "total": 0}

    def create(self, entity: str, payload: dict):
        self.created.append((entity, payload))
        return {"id": "new-id", **payload}

    def update(self, entity: str, record_id: str, payload: dict):
        self.updated.append((entity, record_id, payload))
        return {"id": record_id, **payload}


class DataEntryGenericWriteTests(unittest.TestCase):
    def test_create_any_entity_with_key_value_fields(self) -> None:
        client = WriteClient()

        result = handle(client, 'create Account name="Acme Co" account_type=Prospect phoneNumber=4045551212')

        self.assertTrue(result.ok)
        self.assertEqual(
            client.created,
            [("Account", {"name": "Acme Co", "account_type": "Prospect", "phoneNumber": "4045551212"})],
        )
        self.assertIn("Created Account new-id", result.message)

    def test_create_task_defaults_assigned_user_to_hermes_api_user(self) -> None:
        client = WriteClient()

        result = handle(client, 'create Task name="Hermes smoke" status=Inbox')

        self.assertTrue(result.ok)
        self.assertEqual(
            client.created,
            [("Task", {"name": "Hermes smoke", "status": "Inbox", "assignedUserId": "user-1"})],
        )

    def test_add_lead_dedupes_by_email_and_updates_existing(self) -> None:
        client = WriteClient()
        client.find_results[("Lead", "emailAddress", "jane@example.com")] = {"id": "lead-1", "name": "Jane Doe"}

        result = handle(client, "add Lead firstName=Jane lastName=Doe emailAddress=jane@example.com status=New")

        self.assertTrue(result.ok)
        self.assertEqual(client.created, [])
        self.assertEqual(
            client.updated,
            [("Lead", "lead-1", {"firstName": "Jane", "lastName": "Doe", "emailAddress": "jane@example.com", "status": "New"})],
        )
        self.assertIn("Updated existing Lead lead-1", result.message)
        self.assertFalse(any(entity == "Opportunity" for entity, _ in client.created))
        self.assertFalse(any(entity == "Opportunity" for entity, _, _ in client.updated))

    def test_add_opportunity_dedupes_by_name_and_updates_pipeline_stage(self) -> None:
        client = WriteClient()
        client.find_results[("Opportunity", "name", "Jane Ukoh - BOP")] = {"id": "opp-1", "name": "Jane Ukoh - BOP"}

        result = handle(client, 'add Opportunity name="Jane Ukoh - BOP" stage=Proposal lineOfBusiness=GL/BOP')

        self.assertTrue(result.ok)
        self.assertEqual(client.created, [])
        self.assertEqual(
            client.updated,
            [("Opportunity", "opp-1", {"name": "Jane Ukoh - BOP", "stage": "Proposal", "lineOfBusiness": "GL/BOP"})],
        )

    def test_move_opportunity_to_pipeline_stage(self) -> None:
        client = WriteClient()

        result = handle(client, 'move opportunity opp-1 to "Closed Won"')

        self.assertTrue(result.ok)
        self.assertEqual(client.updated, [("Opportunity", "opp-1", {"stage": "Closed Won"})])

    def test_dispatcher_routes_move_opportunity_to_data_entry(self) -> None:
        client = WriteClient()

        result = Dispatcher().dispatch(client, 'move opportunity opp-1 to "Quoted"')

        self.assertTrue(result.ok)
        self.assertEqual(client.updated, [("Opportunity", "opp-1", {"stage": "Quoted"})])

    def test_update_any_entity_by_id(self) -> None:
        client = WriteClient()

        result = handle(client, "update Task abc123 status=Completed dateEnd=2026-05-01")

        self.assertTrue(result.ok)
        self.assertEqual(client.updated, [("Task", "abc123", {"status": "Completed", "dateEnd": "2026-05-01"})])

    def test_rejects_unknown_fields_before_writing(self) -> None:
        client = WriteClient()

        result = handle(client, "create Account name=Acme madeUpField=yes")

        self.assertFalse(result.ok)
        self.assertEqual(client.created, [])
        self.assertIn("Unknown field", result.message)

    def test_rejects_read_only_fields_before_writing(self) -> None:
        client = WriteClient()

        result = handle(client, 'create Account name=Acme createdAt="2026-05-01 00:00:00"')

        self.assertFalse(result.ok)
        self.assertEqual(client.created, [])
        self.assertIn("read-only", result.message)


if __name__ == "__main__":
    unittest.main()
