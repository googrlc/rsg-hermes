from __future__ import annotations

import unittest

from hermes.commands.policy_repair import handle, run_policy_account_repair


class FakeClient:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, dict]] = []

    def get(self, entity: str, **kwargs):
        params = kwargs.get("params") or {}
        if entity == "Policy":
            offset = int(params.get("offset", 0))
            rows = [
                {
                    "id": "p1",
                    "name": "Danielle Coates",
                    "insuredMomentumId": "insured-1",
                    "accountId": None,
                    "accountName": "Danielle Coates",
                    "policy_number": None,
                },
                {
                    "id": "p2",
                    "name": "Already Linked",
                    "insuredMomentumId": "insured-2",
                    "accountId": "a2",
                    "accountName": "Already Linked",
                    "policy_number": "P-2",
                },
                {
                    "id": "p3",
                    "name": "No Match",
                    "insuredMomentumId": "insured-3",
                    "accountId": None,
                    "accountName": "No Match",
                    "policy_number": "P-3",
                },
                {
                    "id": "p4",
                    "name": "Duplicate Match",
                    "insuredMomentumId": "insured-dup",
                    "accountId": None,
                    "accountName": "Duplicate Match",
                    "policy_number": "P-4",
                },
            ]
            return {"list": rows[offset: offset + int(params.get("maxSize", 200))], "total": len(rows)}

        if entity == "Account":
            where = params.get("where") or []
            value = where[0].get("value") if where else ""
            matches = {
                "insured-1": [{"id": "a1", "name": "Danielle Coates", "momentum_client_id": "insured-1"}],
                "insured-dup": [
                    {"id": "a4a", "name": "Dup A", "momentum_client_id": "insured-dup"},
                    {"id": "a4b", "name": "Dup B", "momentum_client_id": "insured-dup"},
                ],
            }.get(value, [])
            return {"list": matches, "total": len(matches)}

        return {"list": [], "total": 0}

    def update(self, entity: str, record_id: str, payload: dict):
        self.updates.append((entity, record_id, payload))
        return {"id": record_id, **payload}


class PolicyRepairTests(unittest.TestCase):
    def test_dry_run_finds_policy_account_repairs_without_writing(self) -> None:
        client = FakeClient()

        result = run_policy_account_repair(client, dry_run=True)

        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.unmatched_count, 1)
        self.assertEqual(result.ambiguous_count, 1)
        self.assertEqual(client.updates, [])

    def test_apply_updates_only_exact_momentum_id_matches(self) -> None:
        client = FakeClient()

        result = run_policy_account_repair(client, dry_run=False)

        self.assertEqual(result.updated_count, 1)
        self.assertEqual(
            client.updates,
            [("Policy", "p1", {"accountId": "a1", "accountName": "Danielle Coates"})],
        )

    def test_text_command_defaults_to_dry_run_and_apply_writes(self) -> None:
        dry_client = FakeClient()
        dry_result = handle(dry_client, "repair policy accounts")

        apply_client = FakeClient()
        apply_result = handle(apply_client, "repair policy accounts apply")

        self.assertTrue(dry_result.ok)
        self.assertIn("DRY RUN", dry_result.message)
        self.assertEqual(dry_client.updates, [])
        self.assertTrue(apply_result.ok)
        self.assertIn("Updated: 1", apply_result.message)
        self.assertEqual(len(apply_client.updates), 1)


if __name__ == "__main__":
    unittest.main()
