"""Tests for hermes.sync.pipeline — NowCerts → EspoCRM sync orchestration."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from hermes.operations.crm_queue_worker import ItemResult, ProcessResult
from hermes.sync.pipeline import (
    SyncRunResult,
    _derive_queue_action_type,
    _resolve_mapping,
    _stage_record,
    run_insured_to_account_sync,
)


def _process_result(*items: ItemResult) -> ProcessResult:
    """Build a ProcessResult mirroring the worker's per-row contract."""
    failed = sum(1 for it in items if not it.ok)
    return ProcessResult(
        total=len(items),
        succeeded=len(items) - failed,
        failed=failed,
        blocked=0,
        errors=[it.message for it in items if it.message],
        dry_run=False,
        items=list(items),
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
    espo.find_one_by_field.return_value = None
    return espo


def _mock_nc(insureds: list | None = None) -> MagicMock:
    nc = MagicMock()
    nc.fetch_insureds.return_value = insureds or []
    return nc


def _sample_insured(**overrides) -> dict:
    base = {
        "id": "NC-001",
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
            records_processed=10,
            records_created=5,
            records_updated=3,
            records_skipped=1,
            records_failed=1,
        )
        msg = r.message
        self.assertIn("processed=10", msg)
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
        self.assertEqual(result.records_processed, 0)
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
        self.assertEqual(result.records_processed, 1)
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
    @patch("hermes.sync.pipeline.process_queue")
    @patch("hermes.sync.pipeline.enqueue_crm_write")
    def test_creates_new_account_via_queue(self, enqueue_mock: MagicMock, process_mock: MagicMock) -> None:
        nc = _mock_nc(insureds=[_sample_insured()])
        espo = _mock_espo()
        supa = _mock_supa()
        enqueue_mock.return_value = {"id": "crm-q-1"}
        process_mock.return_value = _process_result(
            ItemResult(queue_id="crm-q-1", entity_type="Account", entity_id=None, ok=True)
        )

        # select calls: mapping lookup returns empty, queue lookup returns our item
        queue_item = {
            "id": "q-1",
            "object_type": "Account",
            "object_id": None,
            "action": "create",
            "payload": {"name": "Acme Corp", "account_type": "Commercial Lines", "momentum_client_id": "NC-001"},
            "mapping_id": "map-new",
        }
        supa.select.side_effect = [
            [],           # mapping lookup (no existing mapping)
            [],           # staging lookup for status update
            [queue_item], # outbound queue items
        ]

        result = run_insured_to_account_sync(nc, espo, supa, dry_run=False)
        self.assertEqual(result.records_processed, 1)
        self.assertEqual(result.records_created, 1)
        espo.create.assert_not_called()
        enqueue_mock.assert_called_once()
        process_mock.assert_called_once()

    def test_skips_record_without_id(self) -> None:
        nc = _mock_nc(insureds=[{"commercialName": "No ID Corp"}])
        espo = _mock_espo()
        supa = _mock_supa()

        result = run_insured_to_account_sync(nc, espo, supa, dry_run=True)
        self.assertEqual(result.records_skipped, 1)
        self.assertEqual(result.records_created, 0)



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
        espo.find_one_by_field.return_value = None
        supa.select.return_value = []
        supa.upsert.return_value = {"id": "map-new", "espocrm_id": None, "match_method": "none"}

        result = _resolve_mapping(
            supa, espo, source_id="NC-999",
            nc_record=_sample_insured(id="NC-999"), run_id="run-1",
        )
        self.assertIsNotNone(result)
        self.assertIsNone(result.get("espocrm_id"))

    def test_matches_by_dedup_key(self) -> None:
        supa = _mock_supa()
        espo = _mock_espo()
        supa.select.return_value = []  # no existing mapping

        # find_one_by_field returns a match on dedup key
        espo.find_one_by_field.return_value = {"id": "espo-dedup-match", "name": "Acme"}
        supa.upsert.return_value = {"id": "map-dedup", "espocrm_id": "espo-dedup-match", "match_method": "dedup_key"}

        result = _resolve_mapping(
            supa, espo, source_id="NC-001",
            nc_record=_sample_insured(), run_id="run-1",
        )
        self.assertEqual(result["espocrm_id"], "espo-dedup-match")


def _ctx(**overrides) -> dict:
    base = {
        "outbound_queue_id": "ob-1",
        "run_id": "run-1",
        "object_type": "Account",
        "object_id": None,
        "action": "create",
        "payload": {"name": "Dup Co", "momentum_client_id": "NC-9"},
        "mapping_id": "map-1",
        "attempt": 1,
    }
    base.update(overrides)
    return base


class ReconcileConflict409Tests(unittest.TestCase):
    def test_found_match_reenqueues_as_update(self) -> None:
        from hermes.sync.pipeline import _reconcile_conflict_409

        supa = _mock_supa()
        espo = _mock_espo()
        espo.search.return_value = [{"id": "acc-existing", "name": "Dup Co, LLC"}]
        result = SyncRunResult()

        _reconcile_conflict_409(
            supa, espo, _ctx(),
            ItemResult(queue_id="crm-1", entity_type="Account", entity_id=None,
                       ok=False, error_type="conflict_409", status_code=409),
            result=result,
        )

        self.assertEqual(result.records_failed, 0)
        # Mapping repointed to the existing account and row re-queued as update.
        mapping_update = next(
            c for c in supa.update.call_args_list
            if c.args[0] == "sync_mappings"
        )
        self.assertEqual(mapping_update.args[2]["espocrm_id"], "acc-existing")
        queue_update = next(
            c for c in supa.update.call_args_list
            if c.args[0] == "outbound_sync_queue"
        )
        self.assertEqual(queue_update.args[2]["status"], "queued")
        self.assertEqual(queue_update.args[2]["action"], "update")
        self.assertEqual(queue_update.args[2]["object_id"], "acc-existing")
        self.assertTrue(queue_update.args[2]["payload"]["__recon_409"])

    def test_no_match_fails_row_without_blind_create(self) -> None:
        from hermes.sync.pipeline import _reconcile_conflict_409

        supa = _mock_supa()
        espo = _mock_espo()
        espo.search.return_value = []  # nothing matches
        result = SyncRunResult()

        _reconcile_conflict_409(
            supa, espo, _ctx(),
            ItemResult(queue_id="crm-1", entity_type="Account", entity_id=None,
                       ok=False, error_type="conflict_409"),
            result=result,
        )

        self.assertEqual(result.records_failed, 1)
        espo.create.assert_not_called()
        status_update = next(
            c for c in supa.update.call_args_list
            if c.args[0] == "outbound_sync_queue"
        )
        self.assertEqual(status_update.args[2]["status"], "failed")

    def test_already_reconciled_does_not_loop(self) -> None:
        from hermes.sync.pipeline import _reconcile_conflict_409

        supa = _mock_supa()
        espo = _mock_espo()
        result = SyncRunResult()

        _reconcile_conflict_409(
            supa, espo, _ctx(payload={"name": "Dup Co", "__recon_409": True}),
            ItemResult(queue_id="crm-1", entity_type="Account", entity_id=None,
                       ok=False, error_type="conflict_409"),
            result=result,
        )

        # Guard: no second normalized-name search, row just fails.
        espo.search.assert_not_called()
        self.assertEqual(result.records_failed, 1)


class ReconcileMissing404Tests(unittest.TestCase):
    def test_purged_target_marked_dead(self) -> None:
        from hermes.sync.pipeline import _reconcile_missing_404

        supa = _mock_supa()
        espo = _mock_espo()
        espo.search.return_value = []  # no live account by name
        result = SyncRunResult()

        _reconcile_missing_404(
            supa, espo, _ctx(action="update", object_id="acc-gone"),
            ItemResult(queue_id="crm-1", entity_type="Account", entity_id="acc-gone",
                       ok=False, error_type="missing_404", status_code=404),
            result=result,
        )

        # Stale mapping deactivated, row is terminal 'dead'.
        supa.update_where.assert_called_once()
        self.assertEqual(
            supa.update_where.call_args.kwargs["filters"]["espocrm_id"], "eq.acc-gone",
        )
        status_update = next(
            c for c in supa.update.call_args_list
            if c.args[0] == "outbound_sync_queue"
        )
        self.assertEqual(status_update.args[2]["status"], "dead")
        self.assertIn("target_purged", status_update.args[2]["last_error"])

    def test_reresolved_target_reenqueued(self) -> None:
        from hermes.sync.pipeline import _reconcile_missing_404

        supa = _mock_supa()
        espo = _mock_espo()
        espo.search.return_value = [{"id": "acc-live", "name": "Dup Co"}]
        result = SyncRunResult()

        _reconcile_missing_404(
            supa, espo, _ctx(action="update", object_id="acc-gone"),
            ItemResult(queue_id="crm-1", entity_type="Account", entity_id="acc-gone",
                       ok=False, error_type="missing_404"),
            result=result,
        )

        self.assertEqual(result.records_failed, 0)
        queue_update = next(
            c for c in supa.update.call_args_list
            if c.args[0] == "outbound_sync_queue"
        )
        self.assertEqual(queue_update.args[2]["object_id"], "acc-live")
        self.assertEqual(queue_update.args[2]["status"], "queued")


class IdempotentEnqueueTests(unittest.TestCase):
    def test_skips_when_open_queued_row_exists(self) -> None:
        from hermes.sync.pipeline import _enqueue_outbound

        supa = _mock_supa()
        supa.select.return_value = [{"id": "existing-ob"}]

        row = _enqueue_outbound(
            supa, run_id="run-1", mapping_id="m-1",
            object_type="Account", object_id="acc-1", action="update",
            payload={"name": "Acme"},
        )

        self.assertEqual(row, {"id": "existing-ob"})
        supa.insert.assert_not_called()

    def test_inserts_when_none_open(self) -> None:
        from hermes.sync.pipeline import _enqueue_outbound

        supa = _mock_supa()
        supa.select.return_value = []
        supa.insert.return_value = {"id": "new-ob"}

        row = _enqueue_outbound(
            supa, run_id="run-1", mapping_id="m-1",
            object_type="Account", object_id="acc-1", action="update",
            payload={"name": "Acme"},
        )

        self.assertEqual(row, {"id": "new-ob"})
        supa.insert.assert_called_once()

    def test_create_with_null_object_id_bypasses_precheck(self) -> None:
        from hermes.sync.pipeline import _enqueue_outbound

        supa = _mock_supa()
        supa.insert.return_value = {"id": "new-ob"}

        _enqueue_outbound(
            supa, run_id="run-1", mapping_id="m-1",
            object_type="Account", object_id=None, action="create",
            payload={"name": "Acme"},
        )

        # No dedupe SELECT for NULL object_id; goes straight to insert.
        supa.select.assert_not_called()
        supa.insert.assert_called_once()

    def test_swallows_unique_violation(self) -> None:
        from hermes.sync.pipeline import _enqueue_outbound
        from hermes.integrations.supabase_client import SupabaseClientError

        supa = _mock_supa()
        supa.select.return_value = []
        supa.insert.side_effect = SupabaseClientError(
            "409 INSERT outbound_sync_queue: duplicate key value violates unique "
            "constraint \"uq_outbound_queue_open_work\" (23505)"
        )

        row = _enqueue_outbound(
            supa, run_id="run-1", mapping_id="m-1",
            object_type="Account", object_id="acc-1", action="update",
            payload={"name": "Acme"},
        )
        self.assertIsNone(row)


class QueueActionTypeDerivationTests(unittest.TestCase):
    def test_create_renewal_maps_to_first_class_action(self) -> None:
        action_type = _derive_queue_action_type(
            object_type="Renewal",
            action="create",
            payload={"client_name": "Smith Auto"},
        )
        self.assertEqual(action_type, "create_renewal")

    def test_update_status_maps_when_status_key_present(self) -> None:
        action_type = _derive_queue_action_type(
            object_type="Task",
            action="update",
            payload={"status": "Completed"},
        )
        self.assertEqual(action_type, "update_status")

    def test_update_note_maps_when_note_key_present(self) -> None:
        action_type = _derive_queue_action_type(
            object_type="Account",
            action="update",
            payload={"notes": "Client requested callback"},
        )
        self.assertEqual(action_type, "create_note")

    def test_request_docs_maps_when_doc_request_keys_present(self) -> None:
        action_type = _derive_queue_action_type(
            object_type="Renewal",
            action="update",
            payload={"requested_documents": ["loss_runs"], "urgency": "30_day_window"},
        )
        self.assertEqual(action_type, "request_docs")

    def test_unknown_patterns_fall_back_to_legacy_write(self) -> None:
        action_type = _derive_queue_action_type(
            object_type="Account",
            action="update",
            payload={"phoneNumber": "4045551212"},
        )
        self.assertEqual(action_type, "legacy_write")


if __name__ == "__main__":
    unittest.main()
