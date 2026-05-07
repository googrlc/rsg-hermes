"""Tests for hermes/jobs/nightly_changelog.py."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from hermes.jobs.nightly_changelog import (
    ChangelogRunResult,
    EntityChange,
    _build_slack_payload,
    _classify_action,
    _query_entity_changes,
    run,
    run_on_demand,
)


def _mock_espo(entity_data: dict | None = None) -> MagicMock:
    """Return a mock EspoClient.

    entity_data maps entity type → list of rows returned by GET.
    """
    client = MagicMock()
    entity_data = entity_data or {}

    def _get(entity, **kwargs):
        rows = entity_data.get(entity, [])
        return {"total": len(rows), "list": rows}

    client.get.side_effect = _get
    client.create.return_value = {"id": "task-1"}
    return client


def _mock_notifier() -> MagicMock:
    notifier = MagicMock()
    notifier.post_message.return_value = {"ok": True}
    return notifier


def _sample_account(
    name: str = "Acme Corp",
    record_id: str = "acc-1",
    modified_at: str = "2026-05-07 10:00:00",
    created_at: str = "2026-05-07 10:00:00",
    modified_by: str = "Lamar Coates",
) -> dict:
    return {
        "id": record_id,
        "name": name,
        "modifiedAt": modified_at,
        "createdAt": created_at,
        "modifiedByName": modified_by,
        "createdByName": modified_by,
    }


class ClassifyActionTests(unittest.TestCase):
    def test_created_when_in_window(self) -> None:
        cutoff = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
        result = _classify_action("2026-05-07 10:00:00", "2026-05-07 10:00:00", cutoff)
        self.assertEqual(result, "created")

    def test_updated_when_created_before_window(self) -> None:
        cutoff = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
        result = _classify_action("2026-05-01 10:00:00", "2026-05-07 10:00:00", cutoff)
        self.assertEqual(result, "updated")

    def test_updated_when_invalid_created(self) -> None:
        cutoff = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
        result = _classify_action("", "2026-05-07 10:00:00", cutoff)
        self.assertEqual(result, "updated")


class QueryEntityChangesTests(unittest.TestCase):
    def test_returns_changes(self) -> None:
        client = _mock_espo({
            "Account": [
                _sample_account("Acme Corp", "acc-1"),
                _sample_account("Beta Inc", "acc-2", created_at="2026-05-01 10:00:00"),
            ],
        })
        cutoff = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
        changes, warning = _query_entity_changes(client, "Account", cutoff)
        self.assertIsNone(warning)
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0].action, "created")
        self.assertEqual(changes[1].action, "updated")

    def test_handles_api_error(self) -> None:
        from hermes.core.client import EspoClientError

        client = MagicMock()
        client.get.side_effect = EspoClientError("connection failed")
        cutoff = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
        changes, warning = _query_entity_changes(client, "Account", cutoff)
        self.assertEqual(changes, [])
        self.assertIn("Account", warning)

    def test_empty_results(self) -> None:
        client = _mock_espo({"Account": []})
        cutoff = datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc)
        changes, warning = _query_entity_changes(client, "Account", cutoff)
        self.assertEqual(changes, [])
        self.assertIsNone(warning)


class BuildSlackPayloadTests(unittest.TestCase):
    def test_empty_changes(self) -> None:
        text, blocks = _build_slack_payload(
            day=date(2026, 5, 7),
            changes={},
            totals={},
            created_count=0,
            updated_count=0,
            warnings=[],
            lookback_hours=24,
        )
        self.assertIn("0 changes", text)
        self.assertTrue(any("No CRM changes" in str(b) for b in blocks))

    def test_with_changes(self) -> None:
        changes = {
            "Account": [
                EntityChange("Account", "acc-1", "Acme Corp", "created", "Lamar", "2026-05-07 10:00:00", "2026-05-07 10:00:00"),
                EntityChange("Account", "acc-2", "Beta Inc", "updated", "Gretchen", "2026-05-07 11:00:00", "2026-05-01 10:00:00"),
            ],
        }
        text, blocks = _build_slack_payload(
            day=date(2026, 5, 7),
            changes=changes,
            totals={"Account": 2},
            created_count=1,
            updated_count=1,
            warnings=[],
            lookback_hours=24,
        )
        self.assertIn("2 changes", text)
        block_text = json.dumps(blocks)
        self.assertIn("Acme Corp", block_text)
        self.assertIn("Beta Inc", block_text)

    def test_warnings_included(self) -> None:
        text, blocks = _build_slack_payload(
            day=date(2026, 5, 7),
            changes={},
            totals={},
            created_count=0,
            updated_count=0,
            warnings=["Lead: API error"],
            lookback_hours=24,
        )
        self.assertTrue(any("API error" in str(b) for b in blocks))


class RunOnDemandTests(unittest.TestCase):
    def test_no_changes(self) -> None:
        client = _mock_espo()
        result = run_on_demand(client, lookback_hours=24)
        self.assertTrue(result.ok)
        self.assertIn("No CRM changes", result.message)

    def test_with_changes(self) -> None:
        client = _mock_espo({
            "Account": [_sample_account()],
        })
        result = run_on_demand(client, lookback_hours=24)
        self.assertTrue(result.ok)
        self.assertIn("Acme Corp", result.message)
        self.assertIn("Account", result.changes)

    def test_multiple_entity_types(self) -> None:
        client = _mock_espo({
            "Account": [_sample_account("Acme Corp", "acc-1")],
            "Contact": [_sample_account("John Doe", "con-1")],
        })
        result = run_on_demand(client, lookback_hours=24)
        self.assertTrue(result.ok)
        self.assertIn("Account", result.changes)
        self.assertIn("Contact", result.changes)


class RunFullTests(unittest.TestCase):
    @patch("hermes.jobs.nightly_changelog._already_sent_today", return_value=False)
    @patch("hermes.jobs.nightly_changelog._write_state")
    @patch("hermes.jobs.nightly_changelog._log_to_crm")
    def test_dry_run(self, mock_log, mock_state, mock_sent) -> None:
        client = _mock_espo({"Account": [_sample_account()]})
        result = run(client, dry_run=True)
        self.assertTrue(result.ok)
        self.assertFalse(result.posted)
        mock_log.assert_not_called()
        mock_state.assert_not_called()

    @patch("hermes.jobs.nightly_changelog._already_sent_today", return_value=True)
    def test_skips_if_already_sent(self, mock_sent) -> None:
        client = _mock_espo()
        result = run(client)
        self.assertTrue(result.ok)
        self.assertTrue(result.skipped)

    @patch("hermes.jobs.nightly_changelog._already_sent_today", return_value=True)
    @patch("hermes.jobs.nightly_changelog._write_state")
    @patch("hermes.jobs.nightly_changelog._log_to_crm")
    def test_force_overrides_skip(self, mock_log, mock_state, mock_sent) -> None:
        client = _mock_espo({"Account": [_sample_account()]})
        notifier = _mock_notifier()
        result = run(client, notifier=notifier, force=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.posted)
        notifier.post_message.assert_called_once()
        mock_log.assert_called_once()

    @patch("hermes.jobs.nightly_changelog._already_sent_today", return_value=False)
    @patch("hermes.jobs.nightly_changelog._write_state")
    @patch("hermes.jobs.nightly_changelog._log_to_crm")
    def test_posts_to_slack_and_logs(self, mock_log, mock_state, mock_sent) -> None:
        client = _mock_espo({"Account": [_sample_account()]})
        notifier = _mock_notifier()
        result = run(client, notifier=notifier)
        self.assertTrue(result.ok)
        self.assertTrue(result.posted)
        notifier.post_message.assert_called_once()
        mock_log.assert_called_once()
        mock_state.assert_called_once()


class ChangelogCommandTests(unittest.TestCase):
    def test_changelog_routes(self) -> None:
        """Verify changelog command routes through the dispatcher."""
        from hermes.commands.changelog import handle

        client = _mock_espo()
        result = handle(client, "changelog")
        self.assertTrue(result.ok)
        self.assertIn("No CRM changes", result.message)

    def test_changelog_with_hours(self) -> None:
        from hermes.commands.changelog import handle

        client = _mock_espo({"Account": [_sample_account()]})
        result = handle(client, "changelog 48 hours")
        self.assertTrue(result.ok)
        self.assertIn("Account", result.message)

    def test_what_changed(self) -> None:
        from hermes.commands.changelog import handle

        client = _mock_espo()
        result = handle(client, "what changed today")
        self.assertTrue(result.ok)
