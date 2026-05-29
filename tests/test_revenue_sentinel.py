from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from hermes.jobs import revenue_sentinel


class FakeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []

    def get(self, entity: str, **kwargs):
        _ = kwargs
        if entity == "Opportunity":
            return {
                "list": [
                    {
                        "id": "opp-1",
                        "name": "Atlas Protection Group",
                        "stage": "Prospecting",
                        "lineOfBusiness": "Auto",
                        "amount": "35000",
                        "modifiedAt": "2026-04-10 10:00:00",
                    },
                    {
                        "id": "opp-2",
                        "name": "Raymond Harrison",
                        "status": "Quoting",
                        "lineOfBusiness": "Auto",
                        "amount": "5000",
                        "modifiedAt": "2026-04-08 10:00:00",
                    },
                    {
                        "id": "opp-3",
                        "name": "Fresh Record",
                        "status": "Closed Won",
                        "modifiedAt": "2026-04-28 10:00:00",
                    },
                    {
                        "id": "opp-4",
                        "name": "Jerome Green",
                        "stage": "Closed Lost",
                        "lineOfBusiness": "Builders Risk",
                        "xDate": "2026-06-30",
                        "carrier": "Travelers",
                        "amount": "4000",
                    },
                ]
            }
        if entity == "Policy":
            return {
                "list": [
                    {
                        "id": "pol-1",
                        "name": "Atlas Auto",
                        "accountId": "acct-1",
                        "accountName": "Atlas Protection Group",
                        "lineOfBusiness": "Auto",
                        "premium": "35000",
                        "expirationDate": "2026-07-30",
                        "status": "Active",
                    },
                    {
                        "id": "pol-2",
                        "name": "Ray Renewal",
                        "accountId": "acct-2",
                        "accountName": "Raymond Harrison",
                        "lineOfBusiness": "Auto",
                        "premium": "5000",
                        "expirationDate": "2026-06-30",
                        "status": "Active",
                    },
                    {
                        "id": "pol-3",
                        "name": "Late Renewal",
                        "accountId": "acct-3",
                        "accountName": "Late Co",
                        "lineOfBusiness": "GL",
                        "premium": "2000",
                        "expirationDate": "2026-05-31",
                        "status": "Active",
                    }
                ]
            }
        if entity == "Account":
            return {
                "list": [
                    {"id": "acct-1", "renewalOutreachStage": "Review Started"},
                    {"id": "acct-2", "renewalOutreachStage": ""},
                    {"id": "acct-3", "renewalOutreachStage": "Quoted"},
                ]
            }
        if entity == "Lead":
            return {
                "list": [
                    {
                        "id": "lead-1",
                        "name": "Jerome Green",
                        "line_of_business": "Builders Risk",
                        "x_date": "2026-06-30",
                        "carrier": "Travelers",
                        "amount": "3500",
                    }
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


class RevenueSentinelTests(unittest.TestCase):
    def test_run_dry_run_groups_sections_and_prioritizes_whale(self) -> None:
        now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with patch.dict(
                os.environ,
                {
                    "HERMES_SENTINEL_STATE_FILE": state_file,
                    "HERMES_SENTINEL_TIMEZONE": "America/New_York",
                    "HERMES_SENTINEL_WHALE_PREMIUM": "20000",
                },
                clear=False,
            ):
                result = revenue_sentinel.run(FakeClient(), dry_run=True, now=now)

        self.assertTrue(result.ok)
        self.assertFalse(result.posted)
        self.assertIn("GOOD MORNING, CAPTAIN", result.message)
        stale = result.sections["stale_leads"]
        self.assertEqual(stale[0]["name"], "Atlas Protection Group")
        self.assertTrue(stale[0]["_is_whale"])
        renewals = result.sections["upcoming_renewals"]
        self.assertEqual(renewals[0]["name"], "Atlas Protection Group")
        self.assertTrue(renewals[0]["_is_whale"])
        self.assertEqual(renewals[0]["checkpoint_days"], 90)
        self.assertTrue(renewals[0]["in_pipeline"])
        self.assertEqual(renewals[1]["checkpoint_days"], 60)
        self.assertFalse(renewals[1]["in_pipeline"])
        self.assertIn("Add to Renewal Pipeline", result.message)
        self.assertIn("90d checkpoint", result.message)
        self.assertIn("60d checkpoint", result.message)
        self.assertIn("30d checkpoint", result.message)

    def test_run_skips_duplicate_post_for_same_day(self) -> None:
        now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"last_sent_date": "2026-05-01"}))
            with patch.dict(
                os.environ,
                {
                    "HERMES_SENTINEL_STATE_FILE": state_file,
                    "HERMES_SENTINEL_TIMEZONE": "America/New_York",
                },
                clear=False,
            ):
                result = revenue_sentinel.run(FakeClient(), notifier=notifier, now=now)

        self.assertTrue(result.ok)
        self.assertTrue(result.skipped)
        self.assertEqual(len(notifier.calls), 0)

    def test_handle_slack_action_creates_task_for_reminder(self) -> None:
        client = FakeClient()
        value = revenue_sentinel.build_action_value(
            entity="Opportunity",
            record_id="opp-1",
            name="Atlas Protection Group",
            category="STALE LEADS",
        )
        message = revenue_sentinel.handle_slack_action(
            client=client,
            action="sentinel_remind",
            action_value=value,
        )

        self.assertIn("Reminder created", message)
        self.assertEqual(client.created[0][0], "Task")
        self.assertIn("Follow up", client.created[0][1]["name"])

    def test_parse_action_value_rejects_invalid_json(self) -> None:
        with self.assertRaises(ValueError):
            revenue_sentinel.parse_action_value("not-json")

    def test_health_status_reports_stale_when_last_sent_before_expected_business_day(self) -> None:
        now = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)  # Tuesday
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"last_sent_date": "2026-05-02"}))  # Friday
            with patch.dict(
                os.environ,
                {
                    "HERMES_SENTINEL_STATE_FILE": state_file,
                    "HERMES_SENTINEL_TIMEZONE": "America/New_York",
                    "SLACK_BOT_TOKEN": "xoxb-test",
                    "HERMES_SENTINEL_SLACK_CHANNEL": "#the-boss",
                },
                clear=False,
            ):
                status = revenue_sentinel.health_status(now=now)

        self.assertFalse(status.ok)
        self.assertIn("stale", status.summary.lower())
        self.assertEqual(status.details["last_sent_date"], "2026-05-02")

    def test_health_status_reports_ok_when_latest_business_day_sent(self) -> None:
        now = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)  # Tuesday
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"last_sent_date": "2026-05-05"}))
            with patch.dict(
                os.environ,
                {
                    "HERMES_SENTINEL_STATE_FILE": state_file,
                    "HERMES_SENTINEL_TIMEZONE": "America/New_York",
                    "SLACK_BOT_TOKEN": "xoxb-test",
                    "HERMES_SENTINEL_SLACK_CHANNEL": "#the-boss",
                },
                clear=False,
            ):
                status = revenue_sentinel.health_status(now=now)

        self.assertTrue(status.ok)
        self.assertTrue(status.details["is_fresh"])


if __name__ == "__main__":
    unittest.main()

