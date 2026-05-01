from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from hermes.jobs import commission_reconciliation


class FakeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []

    def get(self, entity: str, **kwargs):
        _ = kwargs
        if entity == "Policy":
            return {
                "list": [
                    {
                        "id": "pol-1",
                        "policyNumber": "12345",
                        "carrier": "Progressive",
                        "premiumAmount": "4200",
                        "commissionRate": "12",
                    },
                    {
                        "id": "pol-2",
                        "policyNumber": "88888",
                        "carrier": "Travelers",
                        "commissionAmount": "300",
                    },
                ]
            }
        return {"list": []}

    def create(self, entity: str, payload: dict):
        self.created.append((entity, payload))
        return {"id": "task-1", **payload}


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post_message(self, *, text: str, blocks=None):
        self.calls.append({"text": text, "blocks": blocks})
        return {"ok": True}


class CommissionReconciliationTests(unittest.TestCase):
    def test_csv_reconciliation_flags_any_difference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            statement = Path(tmp) / "carrier.csv"
            statement.write_text("policy_number,carrier,commission_paid\n12345,Progressive,420\n88888,Travelers,290\n")
            result = commission_reconciliation.run_reconciliation(
                FakeClient(),
                statement_path=str(statement),
                dry_run=True,
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.discrepancies), 2)
        self.assertIn("COMMISSION DISCREPANCY", result.message)

    def test_percent_threshold_rule_filters_small_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            statement = Path(tmp) / "carrier.csv"
            statement.write_text("policy_number,carrier,commission_paid\n12345,Progressive,503\n")
            with patch.dict(
                os.environ,
                {
                    "HERMES_COMMISSION_RECON_RULE": "percent_over_1",
                    "HERMES_COMMISSION_RECON_PERCENT_THRESHOLD": "1",
                },
                clear=False,
            ):
                result = commission_reconciliation.run_reconciliation(
                    FakeClient(),
                    statement_path=str(statement),
                    dry_run=True,
                )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.discrepancies), 0)

    def test_dispute_action_creates_task(self) -> None:
        client = FakeClient()
        value = commission_reconciliation.build_dispute_action_value(
            policy_id="pol-1",
            policy_number="12345",
            carrier="Progressive",
        )
        result = commission_reconciliation.handle_dispute_action(
            client=client,
            action="commission_create_dispute",
            action_value=value,
        )
        self.assertIn("created", result.lower())
        self.assertEqual(client.created[0][0], "Task")
        self.assertIn("12345", client.created[0][1]["name"])

    def test_reconcile_posts_to_slack(self) -> None:
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            statement = Path(tmp) / "carrier.csv"
            statement.write_text("policy_number,carrier,commission_paid\n12345,Progressive,420\n")
            result = commission_reconciliation.run_reconciliation(
                FakeClient(),
                statement_path=str(statement),
                notifier=notifier,
                dry_run=False,
            )
        self.assertTrue(result.ok)
        self.assertTrue(result.posted)
        self.assertEqual(len(notifier.calls), 1)
        self.assertGreater(result.discrepancies[0]["delta"], Decimal("0"))


if __name__ == "__main__":
    unittest.main()

