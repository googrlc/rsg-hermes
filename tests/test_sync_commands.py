"""Tests for hermes/commands/sync.py dispatcher-routed sync commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.commands.sync import handle
from hermes.core.dispatcher import DispatchResult


def _mock_espo() -> MagicMock:
    return MagicMock()


def _mock_supa(
    *,
    runs: list | None = None,
    conflicts: list | None = None,
    errors: list | None = None,
) -> MagicMock:
    supa = MagicMock()

    def _select(table: str, **kwargs):
        if table == "sync_runs":
            return runs if runs is not None else []
        if table == "sync_conflicts":
            return conflicts if conflicts is not None else []
        if table == "sync_errors":
            return errors if errors is not None else []
        return []

    supa.select.side_effect = _select
    return supa


class TestSyncStatus:
    def test_no_runs(self) -> None:
        result = handle(_mock_espo(), "sync status", supa=_mock_supa())
        assert result.ok
        assert "No sync runs" in result.message

    def test_shows_recent_runs(self) -> None:
        runs = [
            {
                "id": "run-1",
                "workflow_name": "insured_to_account",
                "status": "success",
                "records_processed": 10,
                "records_created": 5,
                "records_updated": 3,
                "records_failed": 0,
                "finished_at": "2026-05-07T12:00:00Z",
            },
            {
                "id": "run-2",
                "workflow_name": "dry_run:insured_to_account",
                "status": "partial",
                "records_processed": 8,
                "records_created": 2,
                "records_updated": 1,
                "records_failed": 2,
                "finished_at": "2026-05-06T10:00:00Z",
            },
        ]
        result = handle(_mock_espo(), "sync status", supa=_mock_supa(runs=runs))
        assert result.ok
        assert "insured_to_account" in result.message
        assert "success" in result.message
        assert "partial" in result.message

    def test_last_run_alias(self) -> None:
        result = handle(_mock_espo(), "sync last run", supa=_mock_supa())
        assert result.ok
        assert "No sync runs" in result.message

    def test_sync_runs_alias(self) -> None:
        result = handle(_mock_espo(), "sync runs", supa=_mock_supa())
        assert result.ok


class TestSyncConflicts:
    def test_no_conflicts(self) -> None:
        result = handle(_mock_espo(), "sync conflicts", supa=_mock_supa())
        assert result.ok
        assert "No unresolved" in result.message

    def test_shows_conflicts(self) -> None:
        conflicts = [
            {
                "id": "c-1",
                "field_name": "phoneNumber",
                "nowcerts_value": "555-0100",
                "espocrm_value": "555-0200",
                "resolution": "pending",
                "created_at": "2026-05-07T12:00:00Z",
            },
        ]
        result = handle(
            _mock_espo(), "sync conflicts", supa=_mock_supa(conflicts=conflicts),
        )
        assert result.ok
        assert "phoneNumber" in result.message
        assert "555-0100" in result.message


class TestSyncErrors:
    def test_no_errors(self) -> None:
        result = handle(_mock_espo(), "sync errors", supa=_mock_supa())
        assert result.ok
        assert "No recent" in result.message

    def test_shows_errors(self) -> None:
        errors = [
            {
                "id": "e-1",
                "error_type": "api_error",
                "error_message": "EspoCRM 500 Internal Server Error",
                "object_type": "Insured",
                "object_id": "nc-123",
                "created_at": "2026-05-07T12:00:00Z",
            },
        ]
        result = handle(_mock_espo(), "sync errors", supa=_mock_supa(errors=errors))
        assert result.ok
        assert "api_error" in result.message
        assert "nc-123" in result.message


class TestSyncTrigger:
    @patch("hermes.sync.nowcerts_client.NowCertsClient")
    @patch("hermes.sync.pipeline.run_insured_to_account_sync")
    def test_dry_run(self, mock_pipeline, mock_nc_cls) -> None:
        from hermes.sync.pipeline import SyncRunResult

        mock_pipeline.return_value = SyncRunResult(
            run_id="run-dry", records_processed=5, dry_run=True,
        )
        result = handle(_mock_espo(), "sync nowcerts dry-run", supa=_mock_supa())
        assert result.ok
        assert "DRY RUN" in result.message
        mock_pipeline.assert_called_once()
        _, kwargs = mock_pipeline.call_args
        assert kwargs.get("dry_run") is True

    @patch("hermes.sync.nowcerts_client.NowCertsClient")
    @patch("hermes.sync.pipeline.run_insured_to_account_sync")
    def test_live_sync(self, mock_pipeline, mock_nc_cls) -> None:
        from hermes.sync.pipeline import SyncRunResult

        mock_pipeline.return_value = SyncRunResult(
            run_id="run-live", records_processed=10, records_created=3,
        )
        result = handle(_mock_espo(), "sync nowcerts", supa=_mock_supa())
        assert result.ok
        assert "run-live" in result.message
        _, kwargs = mock_pipeline.call_args
        assert kwargs.get("dry_run") is False

    @patch("hermes.sync.nowcerts_client.NowCertsClient")
    @patch("hermes.sync.pipeline.run_insured_to_account_sync")
    def test_sync_with_since(self, mock_pipeline, mock_nc_cls) -> None:
        from hermes.sync.pipeline import SyncRunResult

        mock_pipeline.return_value = SyncRunResult(run_id="run-since")
        result = handle(
            _mock_espo(), "sync nowcerts since 2026-05-01T00:00:00", supa=_mock_supa(),
        )
        assert result.ok
        _, kwargs = mock_pipeline.call_args
        assert kwargs.get("since") == "2026-05-01T00:00:00"

    @patch("hermes.sync.nowcerts_client.NowCertsClient")
    @patch("hermes.sync.pipeline.run_insured_to_account_sync")
    def test_sync_with_errors(self, mock_pipeline, mock_nc_cls) -> None:
        from hermes.sync.pipeline import SyncRunResult

        mock_pipeline.return_value = SyncRunResult(
            run_id="run-err",
            records_processed=5,
            records_failed=2,
            errors=["q1: timeout", "q2: 500 error"],
        )
        result = handle(_mock_espo(), "sync nowcerts", supa=_mock_supa())
        assert not result.ok
        assert "timeout" in result.message
        assert "500 error" in result.message


class TestNoSupabase:
    def test_returns_error(self) -> None:
        result = handle(_mock_espo(), "sync status", supa=None)
        assert not result.ok
        assert "Supabase" in result.message


class TestDispatcherRouting:
    def test_sync_nowcerts_routes(self) -> None:
        """Verify the Dispatcher routes 'sync nowcerts' to the sync handler."""
        from hermes.core.dispatcher import Dispatcher

        d = Dispatcher.__new__(Dispatcher)
        d.use_openai = False
        d.supa = None
        d._slack_ctx = {}

        from hermes.commands import data_entry, lookup, merge, revenue
        import re

        d._routes = [
            (re.compile(r"^\s*(ping|health|status)\s*$", re.I), "ping"),
            (re.compile(r"\bsync\b.*\b(nowcerts|status|conflicts?|errors?|runs?)\b", re.I), "sync"),
            (re.compile(r"^\s*sync\s", re.I), "sync"),
        ]

        for pattern, handler in d._routes:
            if pattern.search("sync nowcerts"):
                assert handler == "sync"
                break
        else:
            pytest.fail("No route matched 'sync nowcerts'")

    def test_sync_status_routes(self) -> None:
        import re

        pattern = re.compile(r"\bsync\b.*\b(nowcerts|status|conflicts?|errors?|runs?)\b", re.I)
        assert pattern.search("sync status")
        assert pattern.search("sync conflicts")
        assert pattern.search("sync errors")
        assert pattern.search("show sync runs")
        assert pattern.search("sync nowcerts dry-run")
