from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from hermes.jobs import revenue_sentinel


class FakeSupa:
    """Stands in for SupabaseClient — serves the tables the sentinel reads."""

    OPPORTUNITIES = [
        {
            "id": "opp-1",
            "insured_name": "Atlas Protection Group",
            "status": "open",
            "line_of_business": "Auto",
            "premium_estimate": "35000",
            "updated_at": "2026-04-10T10:00:00Z",
        },
        {
            "id": "opp-2",
            "insured_name": "Raymond Harrison",
            "status": "open",
            "line_of_business": "Auto",
            "premium_estimate": "5000",
            "updated_at": "2026-04-08T10:00:00Z",
        },
        {
            "id": "opp-3",
            "insured_name": "Fresh Record",
            "status": "open",
            "line_of_business": "GL",
            "premium_estimate": "1000",
            "updated_at": "2026-04-28T10:00:00Z",   # inside the 14-day window
        },
        {
            "id": "opp-4",
            "insured_name": "Jerome Green",
            "status": "lost",
            "line_of_business": "Builders Risk",
            "premium_estimate": "4000",
            "carrier": "Travelers",
            "expiration_date": "2026-06-30",
            "updated_at": "2026-04-01T10:00:00Z",
        },
    ]

    RENEWAL_CANDIDATES = [
        {
            "id": "rc-1",
            "policy_number": "POL-1",
            "client_name": "Atlas Protection Group",
            "line_of_business": "Auto",
            "renewal_event_date": "2026-07-30",     # 90d tier
            "premium_current": "35000",
            "policy_active": True,
            "eligibility_state": "eligible",
            "in_working_queue": True,
        },
        {
            "id": "rc-2",
            "policy_number": "POL-2",
            "client_name": "Raymond Harrison",
            "line_of_business": "Auto",
            "renewal_event_date": "2026-06-30",     # 60d tier
            "premium_current": "5000",
            "policy_active": True,
            "eligibility_state": "eligible",
            "in_working_queue": False,
        },
        {
            "id": "rc-3",
            "policy_number": "POL-3",
            "client_name": "Late Co",
            "line_of_business": "GL",
            "renewal_event_date": "2026-05-25",     # 30d tier
            "premium_current": "2000",
            "policy_active": True,
            "eligibility_state": "eligible",
            "in_working_queue": False,
        },
    ]

    def select(self, table: str, *, params=None, limit=None, columns=None):
        _ = (limit, columns)
        params = params or {}
        if table == "opportunities":
            status = params.get("status", "")
            wanted = status.split(".", 1)[1] if "." in status else None
            return [r for r in self.OPPORTUNITIES if wanted is None or r["status"] == wanted]
        if table == "renewal_candidates":
            return list(self.RENEWAL_CANDIDATES)
        return []


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
                result = revenue_sentinel.run(supa=FakeSupa(), dry_run=True, now=now)

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
        # Every renewal in the window surfaces, grouped by urgency tier.
        self.assertIn("≤30d", result.message)
        self.assertIn("31-60d", result.message)
        self.assertIn("61-90d", result.message)

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
                result = revenue_sentinel.run(supa=FakeSupa(), notifier=notifier, now=now)

        self.assertTrue(result.ok)
        self.assertTrue(result.skipped)
        self.assertEqual(len(notifier.calls), 0)

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
                    "HERMES_TALK_ROOM_BOSS": "room-test",
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
                    "HERMES_TALK_ROOM_BOSS": "room-test",
                    "HERMES_SENTINEL_SLACK_CHANNEL": "#the-boss",
                },
                clear=False,
            ):
                status = revenue_sentinel.health_status(now=now)

        self.assertTrue(status.ok)
        self.assertTrue(status.details["is_fresh"])


if __name__ == "__main__":
    unittest.main()

