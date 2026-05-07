"""Tests for hermes.sync.pipeline — NowCerts → EspoCRM sync orchestration."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from hermes.sync.pipeline import (
    SyncRunResult,
    _resolve_mapping,
    _stage_record,
    run_insured_to_account_sync,
)


def _mock_supa() -> MagicMock:
    """Return a mock SupabaseClient with sensible defaults."""
    supa = MagicMock()
    supa.insert.return_value = {"id": "test-run-id"}
    supa.upsert.return_value = {"id": "test-staging-id"}
    supa.select.return_value = []
    supa.update.return_value = {}
    return supa


def _mock_espo() -> MagicMock:
    espo = MagicMock()
    espo.create.return_value = {"id": "espo-new-123"}
    espo.update.return_value = {"id": "espo-existing-456"}
    espo._find_one_by_field.return_value = None
    return espo


def _mock_nc(insureds: list | None = None) -> MagicMock:
    nc = MagicMock()
    nc.fetch_insureds.return_value = insureds or []
    return nc


def _sample_insured(**overrides) -> dict:
    base = {
        "database_id": "NC-001",
        "commercialName": "Acme Corp",
        "firstName": "John",
        "lastName": "Doe",
        "insuredType": "Commercial",
        "typeOfBusiness": "LLC",
        "fein": "12-3456789",
        "changeDate": "2026-05-01T10:00:00",
    }
    base.update(overrides)
    return base


class SyncRunResultTests(unittest.TestCase):
    def test_ok_when_no_failures(self) -> None:
        r = SyncRunResult(records_created=5, records_updated=3)
        self.assertTrue(r.ok)

    def test_not_ok_with_failures(self) -> None:
        r = SyncRunResult(records_failed=1)
        self.assertFalse(r.ok)

    def test_message_includes_counts(self) -> None:
        r = SyncRunResult(
            run_id="abc",
            records_pulled=10,
            records_created=5,
            records_updated=3,
            records_skipped=1,
            records_failed=1,
        )
        msg = r.message
        self.assertIn("pulled=10", msg)
        self.assertIn("created=5", msg)
        self.assertIn("failed=1", msg)

    def test_dry_run_prefix(self) -> None:
        r = SyncRunResult(dry_run=True)
        self.assertTrue(r.message.startswith("DRY RUN:"))


class DryRunPipelineTests(unittest.TestCase):
    def test_dry_run_no_records(self) -> None:
        nc = _mock_nc(insureds=[])
        espo = _mock_espo()
        supa = _mock_supa()

        result = run_insured_to_account_sync(nc, espo, supa, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.records_pulled, 0)
        espo.create.assert_not_called()
        espo.update.assert_not_called()

    def test_dry_run_with_new_record(self) -> None:
        nc = _mock_nc(insureds=[_sample_insured()])
        espo = _mock_espo()
        supa = _mock_supa()
        # No existing mapping → will be a create
        supa.select.return_value = []

        result = run_insured_to_account_sync(nc, espo, supa, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.records_pulled, 1)
        self.assertEqual(result.records_created, 1)
        # Verify no actual EspoCRM writes in dry run
        espo.create.assert_not_called()
        espo.update.assert_not_called()

    def test_dry_run_with_existing_mapping(self) -> None:
        nc = _mock_nc(insureds=[_sample_insured()])
        espo = _mock_espo()
        supa = _mock_supa()
        # Return existing mapping on first select call (for sync_mappings lookup)
        supa.select.side_effect = [
            [{"id": "map-1", "espocrm_id": "espo-456", "nowcerts_id": "NC-001"}],
            [],  # staging lookup
        ]
        # Simulate fetching existing Account
        espo.get.return_value = {"id": "espo-456", "name": "Acme Corp", "fein": "12-3456789"}

        result = run_insured_to_account_sync(nc, espo, supa, dry_run=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.records_updated, 1)
        espo.create.assert_not_called()
        espo.update.assert_not_called()


class QueuedPipelineTests(unittest.TestCase):
    def test_creates_new_account_via_queue(self) -> None:
        nc = _mock_nc(insureds=[_sample_insured()])
        espo = _mock_espo()
        supa = _mock_supa()

        # select calls: mapping lookup returns empty, queue lookup returns our item
        queue_item = {
            "id": "q-1",
            "object_type": "Account",
            "object_id": None,
            "action": "create",
            "payload": {"name": "Acme Corp", "accountType": "Commercial Lines", "momentumClientId": "NC-001"},
            "mapping_id": "map-new",
        }
        supa.select.side_effect = [
            [],           # mapping lookup (no existing mapping)
            [],           # staging lookup for status update
            [queue_item], # outbound queue items
        ]

        result = run_insured_to_account_sync(nc, espo, supa, dry_run=False)
        self.assertEqual(result.records_pulled, 1)
        self.assertEqual(result.records_created, 1)
        espo.create.assert_called_once()

    def test_skips_record_without_database_id(self) -> None:
        nc = _mock_nc(insureds=[{"commercialName": "No ID Corp"}])
        espo = _mock_espo()
        supa = _mock_supa()

        result = run_insured_to_account_sync(nc, espo, supa, dry_run=True)
        self.assertEqual(result.records_skipped, 1)
        self.assertEqual(result.records_created, 0)

    def test_handles_espo_create_failure(self) -> None:
        from hermes.core.client import EspoClientError

        nc = _mock_nc(insureds=[_sample_insured()])
        espo = _mock_espo()
        espo.create.side_effect = EspoClientError("422 rejected")
        supa = _mock_supa()

        queue_item = {
            "id": "q-1",
            "object_type": "Account",
            "object_id": None,
            "action": "create",
            "payload": {"name": "Acme Corp", "accountType": "Commercial Lines", "momentumClientId": "NC-001"},
            "mapping_id": None,
        }
        supa.select.side_effect = [
            [],           # mapping lookup
            [],           # staging lookup
            [queue_item], # queue
        ]

        result = run_insured_to_account_sync(nc, espo, supa, dry_run=False)
        self.assertEqual(result.records_failed, 1)
        self.assertFalse(result.ok)
        self.assertTrue(any("422" in e for e in result.errors))


class ResolveMappingTests(unittest.TestCase):
    def test_returns_existing_mapping(self) -> None:
        supa = _mock_supa()
        espo = _mock_espo()
        existing = {"id": "map-1", "espocrm_id": "espo-123"}
        supa.select.return_value = [existing]

        result = _resolve_mapping(
            supa, espo, source_id="NC-001",
            nc_record=_sample_insured(), run_id="run-1",
        )
        self.assertEqual(result, existing)

    def test_creates_new_mapping_when_no_match(self) -> None:
        supa = _mock_supa()
        espo = _mock_espo()
        espo._find_one_by_field.return_value = None
        supa.select.return_value = []
        supa.upsert.return_value = {"id": "map-new", "espocrm_id": None, "match_method": "none"}

        result = _resolve_mapping(
            supa, espo, source_id="NC-999",
            nc_record=_sample_insured(database_id="NC-999"), run_id="run-1",
        )
        self.assertIsNotNone(result)
        self.assertIsNone(result.get("espocrm_id"))

    def test_matches_by_dedup_key(self) -> None:
        supa = _mock_supa()
        espo = _mock_espo()
        supa.select.return_value = []  # no existing mapping

        # _find_one_by_field returns a match on dedup key
        espo._find_one_by_field.return_value = {"id": "espo-dedup-match", "name": "Acme"}
        supa.upsert.return_value = {"id": "map-dedup", "espocrm_id": "espo-dedup-match", "match_method": "dedup_key"}

        result = _resolve_mapping(
            supa, espo, source_id="NC-001",
            nc_record=_sample_insured(), run_id="run-1",
        )
        self.assertEqual(result["espocrm_id"], "espo-dedup-match")


if __name__ == "__main__":
    unittest.main()
