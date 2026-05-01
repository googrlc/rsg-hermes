from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from hermes.jobs import revenue_integrity


class FakeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []

    def get(self, entity: str, **kwargs):
        _ = kwargs
        if entity == "Policy":
            return {
                "list": [
                    {
                        "id": "p1",
                        "accountName": "Exquisite Delites",
                        "lineOfBusiness": "Liquor Liab",
                        "status": "Bound",
                        "boundDate": "2026-04-08",
                        "premiumAmount": "4200",
                        "commissionRate": "",
                    },
                    {
                        "id": "p2",
                        "accountName": "Atlas Protection",
                        "lineOfBusiness": "Auto",
                        "status": "Active",
                        "boundDate": "2026-04-18",
                        "renewedFrom": ["legacy-policy"],
                        "premiumAmount": "12500",
                        "commissionRate": "0",
                    },
                    {
                        "id": "p3",
                        "accountName": "Healthy Policy",
                        "lineOfBusiness": "GL",
                        "status": "Active",
                        "boundDate": "2026-04-25",
                        "premiumAmount": "7000",
                        "commissionRate": "12",
                    },
                    {
                        "id": "p4",
                        "accountName": "Outside Month",
                        "lineOfBusiness": "Auto",
                        "status": "Active",
                        "boundDate": "2026-03-15",
                        "premiumAmount": "10000",
                        "commissionRate": "10",
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


class RevenueIntegrityTests(unittest.TestCase):
    def test_commission_audit_dry_run_flags_missing_commission_rows(self) -> None:
        result = revenue_integrity.run_commission_audit(FakeClient(), dry_run=True)

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
                first = revenue_integrity.run_commission_audit(FakeClient(), notifier=notifier, force=True)
                second = revenue_integrity.run_commission_audit(FakeClient(), notifier=notifier)
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
            result = revenue_integrity.run_eom_scorecard(FakeClient(), now=now, dry_run=True)

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
                first = revenue_integrity.run_eom_scorecard(FakeClient(), notifier=notifier, now=now, force=True)
                second = revenue_integrity.run_eom_scorecard(FakeClient(), notifier=notifier, now=now)
        self.assertTrue(first.ok)
        self.assertTrue(first.posted)
        self.assertTrue(second.skipped)


if __name__ == "__main__":
    unittest.main()

