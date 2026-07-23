from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from hermes.jobs import revenue_integrity


class FakeClient:
    """Espo stand-in retained only for handle_commission_action (Slack button)."""

    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []

    def create(self, entity: str, payload: dict):
        self.created.append((entity, payload))
        return {"id": "task-1", **payload}


# commission_ledger rows for the audit path. Blind spot = commissionable ledger
# row with no expected_commission recorded; chargebacks and rows with a positive
# expected commission are not blind spots.
_LEDGER_ROWS = [
    {
        "policy_number": "p1",
        "client_name": "Exquisite Delites",
        "lob": "Liquor Liab",
        "gross_premium": "4200",
        "expected_commission": None,
        "reconciliation_status": "pending",
    },
    {
        "policy_number": "p2",
        "client_name": "Atlas Protection",
        "lob": "Auto",
        "gross_premium": "12500",
        "expected_commission": "0",
        "reconciliation_status": "pending",
    },
    {
        "policy_number": "p3",
        "client_name": "Healthy Policy",
        "lob": "GL",
        "gross_premium": "7000",
        "expected_commission": "840",
        "reconciliation_status": "reconciled",
    },
    {
        "policy_number": "p5",
        "client_name": "Clawback Co",
        "lob": "Auto",
        "gross_premium": "9000",
        "expected_commission": "-500",
        "reconciliation_status": "chargeback",
    },
]

# canonical_policies rows for the EOM scorecard path (effective_date drives the
# month bucket; agency_commission_amount is agency revenue).
_CANONICAL_POLICIES = [
    {
        "policy_number": "p1",
        "lines_of_business": "Liquor Liab",
        "status": "Active",
        "effective_date": "2026-04-08",
        "premium_amount": "4200",
        "agency_commission_amount": "420",
    },
    {
        "policy_number": "p2",
        "lines_of_business": "Auto",
        "status": "Renewed",
        "effective_date": "2026-04-18",
        "premium_amount": "12500",
        "agency_commission_amount": "1250",
    },
    {
        "policy_number": "p_new",
        "lines_of_business": "Liquor Liab",
        "status": "Active",
        "effective_date": "2026-04-25",
        "premium_amount": "7000",
        "agency_commission_amount": "700",
    },
    {
        "policy_number": "p4",
        "lines_of_business": "Auto",
        "status": "Active",
        "effective_date": "2026-03-15",
        "premium_amount": "10000",
        "agency_commission_amount": "1000",
    },
]


class FakeSupa:
    """Minimal SupabaseClient stand-in exposing .select() for the two read paths."""

    def __init__(self) -> None:
        self.tables = {
            "commission_ledger": list(_LEDGER_ROWS),
            "canonical_policies": list(_CANONICAL_POLICIES),
        }

    def select(self, table: str, *, columns: str = "*", params=None, limit: int = 100):
        _ = (columns, params, limit)
        return list(self.tables.get(table, []))


class FakeNotifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post_message(self, *, text: str, blocks=None):
        self.calls.append({"text": text, "blocks": blocks})
        return {"ok": True}


class RevenueIntegrityTests(unittest.TestCase):
    def test_commission_audit_dry_run_flags_missing_commission_rows(self) -> None:
        result = revenue_integrity.run_commission_audit(supa=FakeSupa(), dry_run=True)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.rows), 2)
        self.assertIn("REVENUE BLIND SPOT", result.message)
        self.assertIn("Exquisite Delites", result.message)
        self.assertIn("Atlas Protection", result.message)
        self.assertNotIn("Healthy Policy", result.message)

    def test_commission_audit_skips_duplicate_post_same_day(self) -> None:
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state.json")
            with patch.dict(
                os.environ,
                {"HERMES_COMMISSION_AUDIT_STATE_FILE": state},
                clear=False,
            ):
                first = revenue_integrity.run_commission_audit(supa=FakeSupa(), notifier=notifier, force=True)
                second = revenue_integrity.run_commission_audit(supa=FakeSupa(), notifier=notifier)
        self.assertTrue(first.ok)
        self.assertTrue(first.posted)
        self.assertTrue(second.ok)
        self.assertTrue(second.skipped)

    def test_commission_action_creates_update_task(self) -> None:
        client = FakeClient()
        action_value = revenue_integrity.build_commission_action_value(
            policy_id="p1",
            policy_name="Exquisite Delites",
        )
        result = revenue_integrity.handle_commission_action(
            client=client,
            action="commission_update_pct",
            action_value=action_value,
        )
        self.assertIn("task created", result.lower())
        self.assertEqual(client.created[0][0], "Task")
        self.assertIn("Update Commission %", client.created[0][1]["name"])

    def test_eom_scorecard_dry_run_aggregates_previous_month(self) -> None:
        now = datetime(2026, 5, 15, 10, 0, 0)
        with patch.dict(os.environ, {"HERMES_NORTH_STAR_PREMIUM_GOAL": "1000000"}, clear=False):
            result = revenue_integrity.run_eom_scorecard(supa=FakeSupa(), now=now, dry_run=True)

        self.assertTrue(result.ok)
        self.assertIn("APRIL 2026 REVENUE REPORT", result.message)
        self.assertIn("Total Premium: $23,700", result.message)
        self.assertIn("Renewals: $12,500", result.message)
        self.assertIn("New Business: $11,200", result.message)

    def test_eom_scorecard_skips_duplicate_month(self) -> None:
        notifier = FakeNotifier()
        now = datetime(2026, 5, 15, 10, 0, 0)
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "eom_state.json")
            with patch.dict(
                os.environ,
                {"HERMES_EOM_SCORECARD_STATE_FILE": state},
                clear=False,
            ):
                first = revenue_integrity.run_eom_scorecard(supa=FakeSupa(), notifier=notifier, now=now, force=True)
                second = revenue_integrity.run_eom_scorecard(supa=FakeSupa(), notifier=notifier, now=now)
        self.assertTrue(first.ok)
        self.assertTrue(first.posted)
        self.assertTrue(second.skipped)


if __name__ == "__main__":
    unittest.main()

