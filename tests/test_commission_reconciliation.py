from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from hermes.jobs import commission_reconciliation


class FakeSupa:
    """Stands in for SupabaseClient — serves the commission_ledger rows."""

    LEDGER = [
        {
            "id": "led-1",
            "policy_number": "AB-12345",
            "carrier_name": "Progressive",
            "expected_commission": "504",   # 4200 premium @ 12%
            "gross_premium": "4200",
        },
        {
            "id": "led-2",
            "policy_number": "88888",
            "carrier_name": "Travelers",
            "expected_commission": "300",
            "gross_premium": "2500",
        },
    ]

    def select(self, table: str, **kwargs):
        _ = kwargs
        return list(self.LEDGER) if table == "commission_ledger" else []


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
            statement.write_text(
                "policy_number,carrier,commission_paid\nAB12345,Progressive,420\n88888,Travelers,290\n40404,Unknown,100\n"
            )
            result = commission_reconciliation.run_reconciliation(
                FakeSupa(),
                statement_path=str(statement),
                dry_run=True,
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.discrepancies), 2)
        self.assertIn("COMMISSION DISCREPANCY", result.message)
        self.assertEqual(result.matched_count, 2)
        self.assertEqual(result.unmatched_count, 1)
        self.assertIn("40404", result.unmatched_policy_numbers)
        self.assertIn("AB-12345", result.message)

    def test_percent_threshold_rule_filters_small_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            statement = Path(tmp) / "carrier.csv"
            statement.write_text("policy_number,carrier,commission_paid\nAB12345,Progressive,503\n")
            with patch.dict(
                os.environ,
                {
                    "HERMES_COMMISSION_RECON_RULE": "percent_over_1",
                    "HERMES_COMMISSION_RECON_PERCENT_THRESHOLD": "1",
                },
                clear=False,
            ):
                result = commission_reconciliation.run_reconciliation(
                    FakeSupa(),
                    statement_path=str(statement),
                    dry_run=True,
                )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.discrepancies), 0)
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.unmatched_count, 0)

    def test_reconcile_posts_to_slack(self) -> None:
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            statement = Path(tmp) / "carrier.csv"
            statement.write_text("policy_number,carrier,commission_paid\nAB12345,Progressive,420\n")
            result = commission_reconciliation.run_reconciliation(
                FakeSupa(),
                statement_path=str(statement),
                notifier=notifier,
                dry_run=False,
            )
        self.assertTrue(result.ok)
        self.assertTrue(result.posted)
        self.assertEqual(len(notifier.calls), 1)
        self.assertEqual(len(result.discrepancies), 1)
        self.assertGreater(result.discrepancies[0]["delta"], Decimal("0"))
        self.assertIn("matched 1", result.message.lower())


if __name__ == "__main__":
    unittest.main()

