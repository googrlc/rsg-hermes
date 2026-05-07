"""NowCerts → EspoCRM sync pipeline orchestrator.

One-line rule: NowCerts data enters inbound_sync_staging → matching updates
sync_mappings → outbound_sync_queue or direct API step → every step logs to
sync_audit_log (and sync_errors / sync_conflicts when needed) under a single
sync_runs id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.sync.field_mapper import (
    INSURED_DEDUP_SOURCE,
    INSURED_DEDUP_TARGET,
    detect_conflicts,
    map_insured_to_account,
    payload_hash,
)
from hermes.sync.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)


@dataclass
class SyncRunResult:
    """Summary of a complete sync pipeline execution."""

    run_id: str = ""
    records_pulled: int = 0
    records_created: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.records_failed == 0

    @property
    def message(self) -> str:
        prefix = "DRY RUN: " if self.dry_run else ""
        return (
            f"{prefix}NowCerts→EspoCRM sync complete: "
            f"pulled={self.records_pulled} created={self.records_created} "
            f"updated={self.records_updated} skipped={self.records_skipped} "
            f"failed={self.records_failed} run_id={self.run_id}"
        )


def run_insured_to_account_sync(
    nc: NowCertsClient,
    espo: EspoClient,
    supa: SupabaseClient,
    *,
    dry_run: bool = False,
    since: str | None = None,
    use_outbound_queue: bool = True,
) -> SyncRunResult:
    """Execute the full NowCerts Insured → EspoCRM Account pipeline.

    Steps:
        A. Start a sync run
        B. Pull from NowCerts → inbound_sync_staging
        C. Normalize + match → sync_mappings
        D. Build outbound payloads → outbound_sync_queue (or direct API)
        E. Dequeue and apply to EspoCRM
        F. Audit everything → sync_audit_log, sync_errors, sync_conflicts
        G. Finish the run
    """
    result = SyncRunResult(dry_run=dry_run)

    # ── A. Start a sync run ──────────────────────────────────────────────
    run_row = _start_run(supa, workflow_name="insured_to_account", dry_run=dry_run)
    run_id = run_row.get("id", "")
    result.run_id = run_id
    log.info("Sync run started: %s", run_id)

    try:
        # ── B. Pull from NowCerts ────────────────────────────────────────
        raw_insureds = nc.fetch_insureds(since=since)
        result.records_pulled = len(raw_insureds)
        _update_run(supa, run_id, {"records_pulled": len(raw_insureds)})

        if not raw_insureds:
            log.info("No insureds to sync")
            _finish_run(supa, run_id, result, status="success")
            return result

        # ── B (cont). Stage raw payloads ─────────────────────────────────
        for record in raw_insureds:
            source_id = str(record.get("database_id") or record.get("databaseId") or "")
            if not source_id:
                log.warning("Insured record missing database_id, skipping")
                result.records_skipped += 1
                continue

            _stage_record(supa, run_id=run_id, source_id=source_id, record=record)

        # ── C. Normalize + match ─────────────────────────────────────────
        for record in raw_insureds:
            source_id = str(record.get("database_id") or record.get("databaseId") or "")
            if not source_id:
                continue

            mapping = _resolve_mapping(
                supa, espo, source_id=source_id, nc_record=record, run_id=run_id,
            )

            espo_id = mapping.get("espocrm_id") if mapping else None
            is_update = bool(espo_id)
            is_first_sync = not is_update

            # Fetch existing Espo data for conflict detection and append transforms
            existing_espo: dict[str, Any] | None = None
            if is_update and espo_id:
                try:
                    resp = espo.get(f"Account/{espo_id}")
                    existing_espo = resp if isinstance(resp, dict) else None
                except EspoClientError:
                    log.warning("Could not fetch existing Account %s for conflict check", espo_id)

            # ── D. Build outbound payload ────────────────────────────────
            espo_payload = map_insured_to_account(
                record,
                existing_espo=existing_espo,
                is_first_sync=is_first_sync,
            )

            # Check for conflicts on updates
            if existing_espo:
                conflicts = detect_conflicts(espo_payload, existing_espo)
                for conflict in conflicts:
                    _log_conflict(
                        supa,
                        run_id=run_id,
                        mapping_id=mapping.get("id") if mapping else None,
                        source_id=source_id,
                        dest_id=espo_id or "",
                        conflict=conflict,
                    )

            action = "update" if is_update else "create"
            p_hash = payload_hash(espo_payload)

            if use_outbound_queue and not dry_run:
                _enqueue_outbound(
                    supa,
                    run_id=run_id,
                    mapping_id=mapping.get("id") if mapping else None,
                    object_type="Account",
                    object_id=espo_id,
                    action=action,
                    payload=espo_payload,
                )
                _update_staging_status(supa, run_id, source_id, "queued")
            elif dry_run:
                log.info(
                    "DRY RUN: would %s Account for NC insured %s → %s",
                    action, source_id, espo_payload.get("name", "?"),
                )
                if is_update:
                    result.records_updated += 1
                else:
                    result.records_created += 1
                _log_audit(
                    supa,
                    run_id=run_id,
                    object_type="Account",
                    source_id=source_id,
                    dest_id=espo_id,
                    action=action,
                    status="dry_run",
                    payload_hash=p_hash,
                )

        # ── E. Dequeue and apply to EspoCRM ──────────────────────────────
        if use_outbound_queue and not dry_run:
            _process_outbound_queue(supa, espo, run_id=run_id, result=result)

        # ── G. Finish the run ────────────────────────────────────────────
        status = "success" if result.ok else "partial"
        _finish_run(supa, run_id, result, status=status)

    except Exception as exc:
        log.exception("Sync run %s failed: %s", run_id, exc)
        result.errors.append(str(exc))
        _finish_run(supa, run_id, result, status="failed", error_summary=str(exc))

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Supabase helpers (sync_runs, staging, mappings, queue, audit)
# ─────────────────────────────────────────────────────────────────────────────

def _start_run(
    supa: SupabaseClient, *, workflow_name: str, dry_run: bool,
) -> dict[str, Any]:
    wf_name = f"dry_run:{workflow_name}" if dry_run else workflow_name
    return supa.insert(
        "sync_runs",
        {
            "workflow_name": wf_name,
            "source_system": "nowcerts",
            "destination_system": "espocrm",
            "direction": "nowcerts_to_espocrm",
            "status": "running",
        },
    )


def _update_run(supa: SupabaseClient, run_id: str, data: dict[str, Any]) -> None:
    try:
        supa.update("sync_runs", run_id, data)
    except SupabaseClientError:
        log.exception("Failed to update sync_runs %s", run_id)


def _finish_run(
    supa: SupabaseClient,
    run_id: str,
    result: SyncRunResult,
    *,
    status: str,
    error_summary: str | None = None,
) -> None:
    data: dict[str, Any] = {
        "status": status,
        "records_pulled": result.records_pulled,
        "records_created": result.records_created,
        "records_updated": result.records_updated,
        "records_skipped": result.records_skipped,
        "records_failed": result.records_failed,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_summary:
        data["error_summary"] = error_summary[:2000]
    _update_run(supa, run_id, data)
    log.info("Sync run %s finished: status=%s", run_id, status)


def _stage_record(
    supa: SupabaseClient,
    *,
    run_id: str,
    source_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    p_hash = payload_hash(record)
    return supa.upsert(
        "inbound_sync_staging",
        {
            "run_id": run_id,
            "source_system": "nowcerts",
            "source_object_type": "Insured",
            "source_object_id": source_id,
            "raw_payload": record,
            "payload_hash": p_hash,
            "processing_status": "pending",
        },
        on_conflict="run_id,source_system,source_object_type,source_object_id",
    )


def _resolve_mapping(
    supa: SupabaseClient,
    espo: EspoClient,
    *,
    source_id: str,
    nc_record: dict[str, Any],
    run_id: str,
) -> dict[str, Any] | None:
    """Look up or create a sync_mappings row for a NowCerts Insured.

    Resolution order:
    1. Existing mapping by nowcerts_id
    2. EspoCRM lookup by momentumClientId (dedup key)
    3. EspoCRM lookup by FEIN
    4. EspoCRM fuzzy match by name
    5. No match → new mapping with espocrm_id=NULL (will be created)
    """
    # 1. Check existing mapping
    existing = supa.select(
        "sync_mappings",
        params={
            "nowcerts_entity_type": "eq.Insured",
            "nowcerts_id": f"eq.{source_id}",
        },
        limit=1,
    )
    if existing:
        return existing[0]

    # 2. Search Espo by dedup key (momentumClientId)
    espo_match = _find_espo_account(espo, INSURED_DEDUP_TARGET, source_id)
    if espo_match:
        return _upsert_mapping(
            supa,
            source_id=source_id,
            espo_id=espo_match["id"],
            method="dedup_key",
            confidence=1.0,
        )

    # 3. Search by FEIN
    fein = nc_record.get("fein")
    if fein:
        espo_match = _find_espo_account(espo, "fein", str(fein))
        if espo_match:
            return _upsert_mapping(
                supa,
                source_id=source_id,
                espo_id=espo_match["id"],
                method="fein",
                confidence=0.95,
            )

    # 4. Search by email
    email = nc_record.get("email")
    if email:
        espo_match = _find_espo_account(espo, "emailAddress", str(email))
        if espo_match:
            return _upsert_mapping(
                supa,
                source_id=source_id,
                espo_id=espo_match["id"],
                method="email",
                confidence=0.90,
            )

    # 5. Name match (lower confidence)
    insured_type = nc_record.get("insuredType", "")
    if insured_type == "Commercial":
        name = nc_record.get("commercialName", "")
    else:
        first = nc_record.get("firstName", "")
        last = nc_record.get("lastName", "")
        name = f"{first} {last}".strip()

    if name:
        espo_match = _find_espo_account(espo, "name", name)
        if espo_match:
            return _upsert_mapping(
                supa,
                source_id=source_id,
                espo_id=espo_match["id"],
                method="name_match",
                confidence=0.70,
            )

    # 6. No match — will create new Account
    return _upsert_mapping(
        supa,
        source_id=source_id,
        espo_id=None,
        method="none",
        confidence=0.0,
    )


def _find_espo_account(
    espo: EspoClient, field: str, value: str,
) -> dict[str, Any] | None:
    """Search EspoCRM for an Account by a specific field value."""
    try:
        return espo.find_one_by_field("Account", field, value)
    except EspoClientError:
        log.warning("EspoCRM lookup failed: Account.%s = %s", field, value)
        return None


def _upsert_mapping(
    supa: SupabaseClient,
    *,
    source_id: str,
    espo_id: str | None,
    method: str,
    confidence: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "nowcerts_entity_type": "Insured",
        "nowcerts_id": source_id,
        "espocrm_entity_type": "Account",
        "espocrm_id": espo_id,
        "match_method": method,
        "match_confidence": confidence,
        "active": True,
    }
    return supa.upsert(
        "sync_mappings",
        row,
        on_conflict="nowcerts_entity_type,nowcerts_id",
    )


def _update_staging_status(
    supa: SupabaseClient, run_id: str, source_id: str, status: str,
) -> None:
    """Update the processing_status on a staging row."""
    try:
        rows = supa.select(
            "inbound_sync_staging",
            params={
                "run_id": f"eq.{run_id}",
                "source_object_id": f"eq.{source_id}",
            },
            limit=1,
        )
        if rows:
            supa.update("inbound_sync_staging", rows[0]["id"], {"processing_status": status})
    except SupabaseClientError:
        log.exception("Failed to update staging status for %s", source_id)


def _enqueue_outbound(
    supa: SupabaseClient,
    *,
    run_id: str,
    mapping_id: str | None,
    object_type: str,
    object_id: str | None,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return supa.insert(
        "outbound_sync_queue",
        {
            "run_id": run_id,
            "mapping_id": mapping_id,
            "object_type": object_type,
            "object_id": object_id,
            "destination_system": "espocrm",
            "action": action,
            "payload": payload,
            "status": "queued",
            "attempt_count": 0,
        },
    )


def _process_outbound_queue(
    supa: SupabaseClient,
    espo: EspoClient,
    *,
    run_id: str,
    result: SyncRunResult,
) -> None:
    """Dequeue outbound items for this run and apply to EspoCRM."""
    queued = supa.select(
        "outbound_sync_queue",
        params={
            "run_id": f"eq.{run_id}",
            "status": "eq.queued",
            "order": "created_at.asc",
        },
        limit=500,
    )
    log.info("Processing %d outbound queue items for run %s", len(queued), run_id)

    for item in queued:
        queue_id = item["id"]
        object_type = item["object_type"]
        object_id = item.get("object_id")
        action = item["action"]
        payload = dict(item.get("payload") or {})
        mapping_id = item.get("mapping_id")
        attempt = (item.get("attempt_count") or 0) + 1

        # Mark processing
        _update_queue_status(supa, queue_id, "processing", attempt_count=attempt)

        try:
            if action == "update" and object_id:
                crm_response = espo.update(object_type, object_id, payload)
                result.records_updated += 1
            elif action == "create":
                crm_response = espo.create(object_type, payload)
                result.records_created += 1
                # Update mapping with new EspoCRM ID
                new_id = crm_response.get("id") if isinstance(crm_response, dict) else None
                if new_id and mapping_id:
                    try:
                        supa.update("sync_mappings", mapping_id, {
                            "espocrm_id": new_id,
                            "last_synced_at": datetime.now(timezone.utc).isoformat(),
                        })
                    except SupabaseClientError:
                        log.exception("Failed to update mapping %s with new Espo ID", mapping_id)
            else:
                result.records_skipped += 1
                _update_queue_status(supa, queue_id, "completed", attempt_count=attempt)
                continue

            _update_queue_status(supa, queue_id, "completed", attempt_count=attempt)

            # Update mapping last_synced_at
            if mapping_id:
                try:
                    supa.update("sync_mappings", mapping_id, {"last_synced_at": datetime.now(timezone.utc).isoformat()})
                except SupabaseClientError:
                    pass

            # Audit success
            dest_id = object_id
            if not dest_id and isinstance(crm_response, dict):
                dest_id = crm_response.get("id")
            _log_audit(
                supa,
                run_id=run_id,
                object_type=object_type,
                source_id=payload.get("momentumClientId", ""),
                dest_id=dest_id,
                action=action,
                status="success",
                after_snapshot=crm_response if isinstance(crm_response, dict) else None,
                payload_hash=payload_hash(payload),
            )

        except EspoClientError as exc:
            log.warning("Outbound %s failed for queue %s: %s", action, queue_id, exc)
            result.records_failed += 1
            result.errors.append(f"{queue_id}: {exc}")
            _update_queue_status(supa, queue_id, "failed", last_error=str(exc), attempt_count=attempt)

            # Log error
            _log_error(
                supa,
                run_id=run_id,
                queue_id=queue_id,
                object_type=object_type,
                source_id=payload.get("momentumClientId", ""),
                error_message=str(exc),
            )

            # Audit failure
            _log_audit(
                supa,
                run_id=run_id,
                object_type=object_type,
                source_id=payload.get("momentumClientId", ""),
                dest_id=object_id,
                action="error",
                status="failed",
                message=str(exc)[:500],
                payload_hash=payload_hash(payload),
            )


def _update_queue_status(
    supa: SupabaseClient,
    queue_id: str,
    status: str,
    last_error: str | None = None,
    attempt_count: int = 1,
) -> None:
    data: dict[str, Any] = {"status": status, "attempt_count": attempt_count}
    if last_error:
        data["last_error"] = last_error[:2000]
    try:
        supa.update("outbound_sync_queue", queue_id, data)
    except SupabaseClientError:
        log.exception("Failed to update outbound queue status for %s", queue_id)


def _log_audit(
    supa: SupabaseClient,
    *,
    run_id: str,
    object_type: str,
    source_id: str | None,
    dest_id: str | None,
    action: str,
    status: str,
    before_snapshot: dict[str, Any] | None = None,
    after_snapshot: dict[str, Any] | None = None,
    payload_hash: str | None = None,
    message: str | None = None,
) -> None:
    try:
        row: dict[str, Any] = {
            "run_id": run_id,
            "object_type": object_type,
            "source_object_id": source_id,
            "dest_object_id": dest_id,
            "action": action,
            "status": status,
        }
        if before_snapshot:
            row["before_snapshot"] = before_snapshot
        if after_snapshot:
            row["after_snapshot"] = after_snapshot
        if payload_hash:
            row["payload_hash"] = payload_hash
        if message:
            row["message"] = message
        supa.insert("sync_audit_log", row)
    except SupabaseClientError:
        log.exception("Failed to write audit log")


def _log_error(
    supa: SupabaseClient,
    *,
    run_id: str,
    staging_id: str | None = None,
    queue_id: str | None = None,
    object_type: str,
    source_id: str | None,
    error_message: str,
    error_code: str | None = None,
    error_detail: dict[str, Any] | None = None,
) -> None:
    try:
        row: dict[str, Any] = {
            "run_id": run_id,
            "object_type": object_type,
            "source_object_id": source_id,
            "error_message": error_message[:2000],
            "retryable": True,
        }
        if staging_id:
            row["staging_id"] = staging_id
        if queue_id:
            row["queue_id"] = queue_id
        if error_code:
            row["error_code"] = error_code
        if error_detail:
            row["error_detail"] = error_detail
        supa.insert("sync_errors", row)
    except SupabaseClientError:
        log.exception("Failed to write sync error")


def _log_conflict(
    supa: SupabaseClient,
    *,
    run_id: str,
    mapping_id: str | None,
    source_id: str,
    dest_id: str,
    conflict: dict[str, str],
) -> None:
    try:
        row: dict[str, Any] = {
            "run_id": run_id,
            "object_type": "Account",
            "source_object_id": source_id,
            "dest_object_id": dest_id,
            "field_name": conflict["field_name"],
            "source_value": conflict["source_value"],
            "dest_value": conflict["dest_value"],
            "resolution": "pending",
        }
        if mapping_id:
            row["mapping_id"] = mapping_id
        supa.insert("sync_conflicts", row)
    except SupabaseClientError:
        log.exception("Failed to write sync conflict")
