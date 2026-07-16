"""NowCerts → EspoCRM sync pipeline orchestrator.

One-line rule: NowCerts data enters inbound_sync_staging → matching updates
sync_mappings → outbound_sync_queue or direct API step → every step logs to
sync_audit_log (and sync_errors / sync_conflicts when needed) under a single
sync_runs id.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.operations.crm_queue_worker import enqueue_crm_write, process_queue
from hermes.sync.field_mapper import (
    INSURED_DEDUP_TARGET,
    detect_conflicts,
    map_insured_to_account,
    map_insured_to_contact,
    map_policy_to_opportunity,
    payload_hash,
)
from hermes.sync.metadata import (
    conform_payload_to_metadata,
    normalize_name,
    resolve_field_name,
)
from hermes.sync.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)

# Matches sync_runs.workflow_name for this pipeline (live sync_audit_log requires workflow_name).
WORKFLOW_INSURED_TO_ACCOUNT = "insured_to_account"

# outbound_sync_queue lifecycle. We keep the live vocabulary
# (queued → processing → completed | failed) and add ONE terminal state,
# 'dead', for rows that can never succeed (e.g. the Espo target was purged).
# No renames on the live table.
QUEUE_QUEUED = "queued"
QUEUE_PROCESSING = "processing"
QUEUE_COMPLETED = "completed"
QUEUE_FAILED = "failed"
QUEUE_DEAD = "dead"

DESTINATION_ESPOCRM = "espocrm"


@dataclass
class SyncRunResult:
    """Summary of a complete sync pipeline execution."""

    run_id: str = ""
    records_processed: int = 0
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
            f"processed={self.records_processed} created={self.records_created} "
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
        _fetched = len(raw_insureds)
        raw_insureds = [r for r in raw_insureds if r.get('active') is True]
        log.info('NowCerts active filter: %d active / %d fetched (%d dropped inactive)',
                 len(raw_insureds), _fetched, _fetched - len(raw_insureds))
        result.records_processed = len(raw_insureds)
        _update_run(supa, run_id, {"records_processed": len(raw_insureds)})

        if not raw_insureds:
            log.info("No insureds to sync")
            _finish_run(supa, run_id, result, status="success")
            return result

        # ── B (cont). Stage raw payloads ─────────────────────────────────
        for record in raw_insureds:
            source_id = str(record.get("id") or record.get("database_id") or record.get("databaseId") or "")
            if not source_id:
                log.warning("Insured record missing id/database_id, skipping")
                result.records_skipped += 1
                continue

            _stage_record(supa, run_id=run_id, source_id=source_id, record=record)

        # ── C. Normalize + match ─────────────────────────────────────────
        for record in raw_insureds:
            source_id = str(record.get("id") or record.get("database_id") or record.get("databaseId") or "")
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
            # Conform against live Espo metadata BEFORE enqueue: drop/remap
            # fields Espo doesn't accept (account_type, years_in_business,
            # momentum_client_id, momentum_last_synced, …) so the write can't
            # 400 on an unknown field. Unknown fields are dropped + logged once,
            # never fatal to the row.
            espo_payload = conform_payload_to_metadata(espo, "Account", espo_payload)

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
                    workflow_name=WORKFLOW_INSURED_TO_ACCOUNT,
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

        # ── E2. Sync Contacts from insured data ─────────────────────────
        _sync_contacts_for_insureds(
            espo, supa, raw_insureds, run_id=run_id, dry_run=dry_run,
        )

        # ── F. Sync Policies from NowCerts ───────────────────────────────
        _sync_policies(
            nc, espo, supa, run_id=run_id, since=since, dry_run=dry_run,
        )

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
        "records_processed": result.records_processed,
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
    email = nc_record.get("eMail") or nc_record.get("email")
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

    # 5. Name match (exact, then normalized). Normalized matching prevents the
    #    blind-create → 409 storm: "Shamira Douglas, LLC" resolves to an existing
    #    "Shamira Douglas" instead of POSTing a duplicate.
    insured_type = nc_record.get("insuredType", "")
    if insured_type == "Commercial":
        name = nc_record.get("commercialName", "")
    else:
        first = nc_record.get("firstName", "")
        last = nc_record.get("lastName", "")
        name = f"{first} {last}".strip()

    if name:
        espo_match = _find_espo_account(espo, "name", name)
        if not espo_match:
            espo_match = _find_espo_account_by_normalized_name(espo, name)
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
    """Search EspoCRM for an Account by a specific field value.

    The search attribute is resolved against live metadata first, so a dedup key
    written as ``momentum_client_id`` is queried on Espo's actual field name
    (``momentumClientId``) rather than silently matching nothing.
    """
    attr = resolve_field_name(espo, "Account", field) or field
    try:
        return espo.find_one_by_field("Account", attr, value)
    except EspoClientError:
        log.warning("EspoCRM lookup failed: Account.%s = %s", attr, value)
        return None


def _find_espo_account_by_normalized_name(
    espo: EspoClient, name: str,
) -> dict[str, Any] | None:
    """Find an Account whose normalized name equals the normalized query.

    Uses a ``contains`` search on the longest name token to keep the candidate
    set small, then compares normalized names in Python. Returns the first
    normalized-equal hit, or None.
    """
    target = normalize_name(name)
    if not target:
        return None
    tokens = sorted(target.split(), key=len, reverse=True)
    probe = tokens[0] if tokens else name
    try:
        candidates = espo.search("Account", probe, max_size=25, select="id,name")
    except EspoClientError:
        log.warning("EspoCRM normalized-name search failed for %r", name)
        return None
    if not isinstance(candidates, list):
        return None
    for cand in candidates:
        if isinstance(cand, dict) and normalize_name(cand.get("name")) == target:
            return cand
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
        "object_type": "Account",
        "source_system": "nowcerts",
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
) -> dict[str, Any] | None:
    """Enqueue an outbound write idempotently, never crashing the run.

    Idempotency (was: every daily run re-enqueued the same object as a NEW row,
    growing the backlog without bound):
      * If an open ``queued`` row already exists for the same
        (object_type, object_id, destination_system, action), return it instead
        of inserting a duplicate.
      * A partial unique index (see migration) is the race-safe backstop — a
        concurrent insert that trips it is swallowed, not raised.

    NULL ``object_id`` rows (creates) cannot be equality-deduped and bypass this
    check by design; the create path is dedup-protected upstream by mapping
    resolution + normalized-name matching instead.

    Wrapping (reimplements the 7/8 hotfix): any Supabase failure here is logged
    and returns None so one bad enqueue can't abort the whole sync run.
    """
    row = {
        "run_id": run_id,
        "mapping_id": mapping_id,
        "object_type": object_type,
        "object_id": object_id,
        "destination_system": DESTINATION_ESPOCRM,
        "action": action,
        "payload": payload,
        "status": QUEUE_QUEUED,
        "attempt_count": 0,
    }
    try:
        if object_id:
            existing = supa.select(
                "outbound_sync_queue",
                params={
                    "object_type": f"eq.{object_type}",
                    "object_id": f"eq.{object_id}",
                    "destination_system": f"eq.{DESTINATION_ESPOCRM}",
                    "action": f"eq.{action}",
                    "status": f"eq.{QUEUE_QUEUED}",
                },
                limit=1,
            )
            if existing:
                log.debug(
                    "Skip duplicate enqueue: %s %s %s already queued",
                    object_type, object_id, action,
                )
                return existing[0]
        return supa.insert("outbound_sync_queue", row)
    except SupabaseClientError as exc:
        # Unique-index backstop (concurrent enqueue of the same pending write):
        # treat as a benign no-op rather than failing the run.
        if _is_unique_violation(exc):
            log.debug(
                "Duplicate enqueue race for %s %s %s — index backstop hit",
                object_type, object_id, action,
            )
            return None
        log.exception(
            "Failed to enqueue outbound %s %s %s", object_type, object_id, action,
        )
        return None


def _is_unique_violation(exc: Exception) -> bool:
    """True when a SupabaseClientError reflects a Postgres unique violation."""
    text = str(exc)
    return "23505" in text or "duplicate key" in text.lower()


def _process_outbound_queue(
    supa: SupabaseClient,
    espo: EspoClient,
    *,
    run_id: str,
    result: SyncRunResult,
) -> None:
    """Dequeue this run's outbound items and apply them via crm_write_queue."""
    queued = supa.select(
        "outbound_sync_queue",
        params={
            "run_id": f"eq.{run_id}",
            "status": f"eq.{QUEUE_QUEUED}",
            "order": "created_at.asc",
        },
        limit=500,
    )
    log.info("Processing %d outbound queue items for run %s", len(queued), run_id)
    _dispatch_outbound_batch(supa, espo, queued, result=result)


def drain_outbound_queue(
    supa: SupabaseClient,
    espo: EspoClient,
    *,
    batch_size: int = 100,
    result: SyncRunResult | None = None,
) -> SyncRunResult:
    """Drain due ``queued`` outbound rows regardless of run (single scheduler).

    This is the run-agnostic entry point the Hermes scheduler calls every
    15 minutes. It picks up rows re-enqueued by 409/404 reconciliation as well
    as any that a run left behind, applying the same per-row typed handling.
    """
    result = result or SyncRunResult()
    now_iso = datetime.now(timezone.utc).isoformat()
    queued = supa.select(
        "outbound_sync_queue",
        params={
            "status": f"eq.{QUEUE_QUEUED}",
            "scheduled_for": f"lte.{now_iso}",
            # Safety guard: this drain dispatches every row to EspoCRM. NowCerts
            # renewal rows are owned by the Renewal Executor (approval-gated) and
            # must never be swept here — a proposed/unapproved writeback would
            # otherwise be mis-routed into an EspoCRM write.
            "destination_system": "neq.nowcerts",
            "order": "created_at.asc",
        },
        limit=batch_size,
    )
    log.info("Draining %d due outbound queue items", len(queued))
    _dispatch_outbound_batch(supa, espo, queued, result=result)
    return result


def run_outbound_drain_loop(
    supa: SupabaseClient,
    espo: EspoClient,
    *,
    poll_seconds: float = 900.0,
    batch_size: int = 100,
) -> None:
    """Continuously drain the outbound queue (Hermes's single scheduler).

    This replaces the retired pg_cron/edge-function outbound path: pg_cron jobs
    stay disabled and Hermes owns draining. Default cadence is 15 minutes.
    """
    interval = poll_seconds if poll_seconds > 0 else 900.0
    log.info("Starting outbound drain loop: interval=%ss batch_size=%s", interval, batch_size)
    while True:
        try:
            result = drain_outbound_queue(supa, espo, batch_size=batch_size)
            if result.records_processed or result.records_created or result.records_updated or result.records_failed:
                log.info(result.message)
        except Exception:  # noqa: BLE001 — never let one bad cycle kill the loop
            log.exception("Outbound drain cycle failed")
        time.sleep(interval)


def _dispatch_outbound_batch(
    supa: SupabaseClient,
    espo: EspoClient,
    queued: list[dict[str, Any]],
    *,
    result: SyncRunResult,
) -> None:
    """Route a batch of ``queued`` rows through crm_write_queue and reconcile.

    Each row's outcome is recorded on its OWN row and its OWN target — no
    run-level blobs. Typed failures (409/404) are reconciled in the sync layer;
    the generic worker stays out of it.
    """
    if not queued:
        return

    enqueued_items: list[dict[str, Any]] = []
    for item in queued:
        queue_id = item["id"]
        object_type = item["object_type"]
        object_id = item.get("object_id")
        action = item["action"]
        payload = dict(item.get("payload") or {})
        attempt = (item.get("attempt_count") or 0) + 1
        ctx = {
            "outbound_queue_id": queue_id,
            "run_id": item.get("run_id") or "",
            "object_type": object_type,
            "object_id": object_id,
            "action": action,
            "payload": payload,
            "mapping_id": item.get("mapping_id"),
            "attempt": attempt,
        }

        _update_queue_status(supa, queue_id, QUEUE_PROCESSING, attempt_count=attempt)

        if action not in {"update", "create"}:
            result.records_skipped += 1
            _update_queue_status(supa, queue_id, QUEUE_COMPLETED, attempt_count=attempt)
            continue

        try:
            crm_queue_row = enqueue_crm_write(
                supa,
                entity_type=object_type,
                entity_id=object_id,
                payload={
                    "action_type": _derive_queue_action_type(
                        object_type=object_type,
                        action=action,
                        payload=payload,
                    ),
                    "intent": action,
                    "context": payload,
                },
                created_by_role="sync_pipeline",
                priority=1,
            )
            ctx["crm_queue_id"] = crm_queue_row.get("id")
            enqueued_items.append(ctx)
        except (SupabaseClientError, ValueError) as exc:
            # Staging into crm_write_queue failed — own row, own error.
            _write_row_error(
                supa, ctx,
                error_type="other",
                message=str(exc),
                result=result,
            )

    if not enqueued_items:
        return

    process_result = process_queue(
        supa,
        espo,
        batch_size=len(enqueued_items),
        dry_run=False,
    )

    by_crm_id = {it["crm_queue_id"]: it for it in enqueued_items if it.get("crm_queue_id")}
    seen: set[str] = set()
    for res in process_result.items:
        ctx = by_crm_id.get(res.queue_id)
        if ctx is None:
            continue
        seen.add(res.queue_id)
        if res.ok:
            _complete_outbound_row(supa, ctx, result=result)
        elif res.error_type == "conflict_409":
            _reconcile_conflict_409(supa, espo, ctx, res, result=result)
        elif res.error_type == "missing_404":
            _reconcile_missing_404(supa, espo, ctx, res, result=result)
        else:
            _write_row_error(
                supa, ctx,
                error_type=res.error_type or "other",
                message=res.reason or res.message or "crm_write_queue failure",
                status_code=res.status_code,
                reason=res.reason,
                dest_id=ctx["object_id"],
                result=result,
            )

    # A row we staged but the worker didn't process this pass (the batch pulled
    # other pending rows first) must not be stranded in 'processing' — revert it
    # to 'queued' so the next drain retries it without burning an attempt.
    for crm_id, ctx in by_crm_id.items():
        if crm_id not in seen:
            _update_queue_status(
                supa, ctx["outbound_queue_id"], QUEUE_QUEUED,
                attempt_count=max(ctx["attempt"] - 1, 0),
            )


def _complete_outbound_row(
    supa: SupabaseClient, ctx: dict[str, Any], *, result: SyncRunResult,
) -> None:
    if ctx["action"] == "create":
        result.records_created += 1
    else:
        result.records_updated += 1
    _update_queue_status(
        supa, ctx["outbound_queue_id"], QUEUE_COMPLETED, attempt_count=ctx["attempt"],
    )
    if ctx["mapping_id"]:
        try:
            supa.update(
                "sync_mappings", ctx["mapping_id"],
                {"last_synced_at": datetime.now(timezone.utc).isoformat()},
            )
        except SupabaseClientError:
            pass
    _log_audit(
        supa,
        workflow_name=WORKFLOW_INSURED_TO_ACCOUNT,
        run_id=ctx["run_id"],
        object_type=ctx["object_type"],
        source_id=_source_id_of(ctx["payload"]),
        dest_id=ctx["object_id"],
        action=ctx["action"],
        status="success",
        payload_hash=payload_hash(ctx["payload"]),
    )


def _reconcile_conflict_409(
    supa: SupabaseClient,
    espo: EspoClient,
    ctx: dict[str, Any],
    res: Any,
    *,
    result: SyncRunResult,
) -> None:
    """409 on create: resolve to the existing Account, convert to update ONCE.

    Never blind-creates. Searches Espo by normalized name; if found, refreshes
    the mapping and re-enqueues the row as an update against the real id. If it
    was already reconciled once (still conflicting) or no match is found, the
    row fails with a readable, per-row error.
    """
    payload = ctx["payload"]
    name = str(payload.get("name") or "")
    already = bool(payload.get("__recon_409"))
    match = _find_espo_account_by_normalized_name(espo, name) if name and not already else None
    if match and match.get("id"):
        _refresh_mapping_target(supa, ctx["mapping_id"], match["id"])
        _reenqueue_as_update(supa, ctx, new_object_id=match["id"], marker="__recon_409")
        log.info("Reconciled 409 for %r → existing Account %s (re-queued as update)", name, match["id"])
        return
    reason = res.reason or ("duplicate persists after reconciliation" if already else f"no normalized-name match for {name!r}")
    _write_row_error(
        supa, ctx,
        error_type="conflict_409",
        message=reason,
        status_code=res.status_code,
        reason=res.reason,
        result=result,
    )


def _reconcile_missing_404(
    supa: SupabaseClient,
    espo: EspoClient,
    ctx: dict[str, Any],
    res: Any,
    *,
    result: SyncRunResult,
) -> None:
    """404 on update: invalidate the stale mapping, re-resolve, else mark dead.

    Sets ``sync_mappings.active = false`` for the missing espocrm_id, then tries
    to re-resolve by normalized name. A hit re-enqueues as an update against the
    live id; otherwise the target was purged (Carrier/MGA/Vendor migrated to
    Carrier Hub) → the row is terminal ``dead`` with reason ``target_purged``.
    """
    stale_id = ctx["object_id"]
    if stale_id:
        try:
            supa.update_where(
                "sync_mappings",
                {"active": False},
                filters={"espocrm_id": f"eq.{stale_id}"},
            )
        except SupabaseClientError:
            log.exception("Failed to deactivate stale mapping for espocrm_id=%s", stale_id)

    payload = ctx["payload"]
    name = str(payload.get("name") or "")
    already = bool(payload.get("__recon_404"))
    match = _find_espo_account_by_normalized_name(espo, name) if name and not already else None
    if match and match.get("id") and match["id"] != stale_id:
        _refresh_mapping_target(supa, ctx["mapping_id"], match["id"])
        _reenqueue_as_update(supa, ctx, new_object_id=match["id"], marker="__recon_404")
        log.info("Reconciled 404 for %r → live Account %s (re-queued as update)", name, match["id"])
        return
    _write_row_error(
        supa, ctx,
        error_type="missing_404",
        message="target_purged",
        status_code=res.status_code,
        reason=res.reason,
        dest_id=stale_id,
        terminal=True,
        result=result,
    )


def _refresh_mapping_target(
    supa: SupabaseClient, mapping_id: str | None, espocrm_id: str,
) -> None:
    if not mapping_id:
        return
    try:
        supa.update(
            "sync_mappings", mapping_id,
            {"espocrm_id": espocrm_id, "active": True},
        )
    except SupabaseClientError:
        log.exception("Failed to refresh mapping %s → %s", mapping_id, espocrm_id)


def _reenqueue_as_update(
    supa: SupabaseClient, ctx: dict[str, Any], *, new_object_id: str, marker: str,
) -> None:
    """Convert an outbound row to an update against ``new_object_id``, once.

    The ``marker`` payload flag makes reconciliation idempotent: a re-queued row
    that fails again will not loop back through resolution.
    """
    new_payload = dict(ctx["payload"])
    new_payload[marker] = True
    try:
        supa.update(
            "outbound_sync_queue", ctx["outbound_queue_id"],
            {
                "status": QUEUE_QUEUED,
                "action": "update",
                "object_id": new_object_id,
                "payload": new_payload,
                "attempt_count": ctx["attempt"],
                "last_error": None,
                "scheduled_for": datetime.now(timezone.utc).isoformat(),
            },
        )
    except SupabaseClientError:
        log.exception("Failed to re-enqueue outbound row %s as update", ctx["outbound_queue_id"])


def _write_row_error(
    supa: SupabaseClient,
    ctx: dict[str, Any],
    *,
    error_type: str,
    message: str,
    status_code: int | None = None,
    reason: str | None = None,
    dest_id: str | None = None,
    terminal: bool = False,
    result: SyncRunResult,
) -> None:
    """Record a per-row failure: own row, own target, typed error, no blob."""
    result.records_failed += 1
    status = QUEUE_DEAD if terminal else QUEUE_FAILED
    short = f"{error_type}: {message}"
    # Per-row line (own queue id) — never a run-level blob smeared across rows.
    result.errors.append(f"{ctx['outbound_queue_id']}: {short}")
    _update_queue_status(
        supa, ctx["outbound_queue_id"], status,
        last_error=short, attempt_count=ctx["attempt"],
    )
    _log_error(
        supa,
        run_id=ctx["run_id"],
        queue_id=ctx["outbound_queue_id"],
        object_type=ctx["object_type"],
        source_id=_source_id_of(ctx["payload"]),
        error_message=(reason or message),
        error_code=error_type,
        error_detail={
            "espocrm_id": dest_id or ctx.get("object_id"),
            "status_code": status_code,
            "x_status_reason": reason,
        },
    )
    _log_audit(
        supa,
        workflow_name=WORKFLOW_INSURED_TO_ACCOUNT,
        run_id=ctx["run_id"],
        object_type=ctx["object_type"],
        source_id=_source_id_of(ctx["payload"]),
        dest_id=dest_id or ctx.get("object_id"),
        action="error",
        status="dead" if terminal else "failed",
        message=short[:500],
        payload_hash=payload_hash(ctx["payload"]),
    )


def _source_id_of(payload: dict[str, Any]) -> str:
    """NowCerts source id from a payload, tolerant of camel/snake casing."""
    return str(
        payload.get("momentum_client_id")
        or payload.get("momentumClientId")
        or ""
    )


def _sync_contacts_for_insureds(
    espo: EspoClient,
    supa: SupabaseClient,
    raw_insureds: list[dict[str, Any]],
    *,
    run_id: str,
    dry_run: bool,
) -> None:
    """Create/update EspoCRM Contact records from NowCerts insured data.

    Runs after Account sync so Accounts exist. For each insured:
      - Finds the linked Account by momentumClientId
      - Upserts a primary Contact (and co-insured Contact if present)
    """
    for record in raw_insureds:
        source_id = str(record.get("id") or record.get("database_id") or record.get("databaseId") or "")
        if not source_id:
            continue

        # Resolve the Account ID in EspoCRM
        account_match = _find_espo_account(espo, INSURED_DEDUP_TARGET, source_id)
        account_id = account_match["id"] if account_match else None

        if not account_id:
            log.debug("No EspoCRM Account for NC insured %s — skipping Contact sync", source_id)
            continue

        # Primary Contact
        contact_payload = map_insured_to_contact(
            record, account_id=account_id, role="primary",
        )
        if contact_payload:
            if dry_run:
                log.info(
                    "DRY RUN: would upsert Contact %s %s → Account %s",
                    contact_payload.get("firstName", ""),
                    contact_payload.get("lastName", ""),
                    account_id,
                )
            else:
                try:
                    espo.upsert_contact(contact_payload)
                    _log_audit(
                        supa,
                        workflow_name=WORKFLOW_INSURED_TO_ACCOUNT,
                        run_id=run_id,
                        object_type="Contact",
                        source_id=source_id,
                        dest_id=account_id,
                        action="upsert",
                        status="success",
                    )
                except EspoClientError as exc:
                    log.warning("Contact upsert failed for NC insured %s: %s", source_id, exc)
                    _log_audit(
                        supa,
                        workflow_name=WORKFLOW_INSURED_TO_ACCOUNT,
                        run_id=run_id,
                        object_type="Contact",
                        source_id=source_id,
                        dest_id=account_id,
                        action="upsert",
                        status="failed",
                        message=str(exc)[:500],
                    )

        # Co-insured Contact (spouse)
        co_contact_payload = map_insured_to_contact(
            record, account_id=account_id, role="co_insured",
        )
        if co_contact_payload:
            if dry_run:
                log.info(
                    "DRY RUN: would upsert co-insured Contact %s %s → Account %s",
                    co_contact_payload.get("firstName", ""),
                    co_contact_payload.get("lastName", ""),
                    account_id,
                )
            else:
                try:
                    espo.upsert_contact(co_contact_payload)
                except EspoClientError as exc:
                    log.warning(
                        "Co-insured Contact upsert failed for NC insured %s: %s",
                        source_id, exc,
                    )


def _sync_policies(
    nc: NowCertsClient,
    espo: EspoClient,
    supa: SupabaseClient,
    *,
    run_id: str,
    since: str | None,
    dry_run: bool,
) -> None:
    """Fetch NowCerts policies and sync them as EspoCRM Opportunities.

    Each policy is linked to the correct Account via the insured's
    database_id → momentumClientId mapping.
    """
    try:
        raw_policies = nc.fetch_policies(since=since)
    except Exception as exc:
        log.warning("NowCerts policy fetch failed — skipping policy sync: %s", exc)
        return

    if not raw_policies:
        log.info("No policies to sync from NowCerts")
        return

    log.info("Syncing %d NowCerts policies → EspoCRM Opportunities", len(raw_policies))

    # Cache Account lookups to avoid redundant API calls when an insured
    # has many policies.
    _account_cache: dict[str, dict[str, Any] | None] = {}

    for policy in raw_policies:
        insured_id = str(
            policy.get("insuredDatabaseId")
            or policy.get("InsuredDatabaseId")
            or policy.get("insured_database_id")
            or ""
        )
        if not insured_id:
            continue

        # Resolve Account in EspoCRM via the insured's momentumClientId
        if insured_id not in _account_cache:
            _account_cache[insured_id] = _find_espo_account(espo, INSURED_DEDUP_TARGET, insured_id)
        account_match = _account_cache[insured_id]
        if not account_match:
            log.debug("No EspoCRM Account for NC insured %s — skipping policy", insured_id)
            continue

        account_id = account_match["id"]
        account_name = account_match.get("name", "")

        opp_payload = map_policy_to_opportunity(
            policy, account_id=account_id, account_name=account_name,
        )
        if not opp_payload:
            continue

        policy_number = opp_payload.get("policyNumber", "")

        if dry_run:
            log.info(
                "DRY RUN: would create Opportunity '%s' for Account %s",
                opp_payload.get("name", "?"),
                account_id,
            )
            continue

        # Dedup: check if an Opportunity with this policy number already exists
        if policy_number:
            try:
                existing = espo.find_one_by_field(
                    "Opportunity", "policyNumber", policy_number,
                    select="id,name",
                )
                if existing:
                    log.debug(
                        "Opportunity already exists for policy %s — skipping",
                        policy_number,
                    )
                    continue
            except EspoClientError:
                pass

        try:
            espo.create("Opportunity", opp_payload)
            _log_audit(
                supa,
                workflow_name=WORKFLOW_INSURED_TO_ACCOUNT,
                run_id=run_id,
                object_type="Opportunity",
                source_id=insured_id,
                dest_id=account_id,
                action="create",
                status="success",
            )
        except EspoClientError as exc:
            log.warning(
                "Opportunity create failed for policy %s: %s",
                policy_number or "?", exc,
            )
            _log_audit(
                supa,
                workflow_name=WORKFLOW_INSURED_TO_ACCOUNT,
                run_id=run_id,
                object_type="Opportunity",
                source_id=insured_id,
                dest_id=account_id,
                action="create",
                status="failed",
                message=str(exc)[:500],
            )


def _derive_queue_action_type(
    *,
    object_type: str,
    action: str,
    payload: dict[str, Any],
) -> str:
    """Infer first-class crm_write_queue action_type from sync object/action patterns."""
    normalized_action = action.strip().lower()
    normalized_entity = object_type.strip().lower()
    payload_keys = {str(key).strip().lower() for key in payload.keys()}

    if normalized_action == "create" and normalized_entity == "renewal":
        return "create_renewal"

    if normalized_action == "update":
        if payload_keys & {"status", "state", "stage", "renewalstatus", "taskstatus"}:
            return "update_status"
        if payload_keys & {"note", "notes", "comment", "comments", "internalnotes", "description"}:
            return "create_note"
        if payload_keys & {
            "requested_documents",
            "request_documents",
            "documents_requested",
            "document_request",
            "doc_request",
            "requested_by",
            "urgency",
        }:
            return "request_docs"
    return "legacy_write"


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
    workflow_name: str,
    run_id: str,
    object_type: str,
    source_id: str | None,
    dest_id: str | None,
    action: str,
    status: str,
    source_system: str = "nowcerts",
    destination_system: str = "espocrm",
    before_snapshot: dict[str, Any] | None = None,
    after_snapshot: dict[str, Any] | None = None,
    payload_hash: str | None = None,
    message: str | None = None,
) -> None:
    """Write sync_audit_log; columns align with live Supabase (NOT NULL workflow_name, object_id, systems)."""
    try:
        object_id = (dest_id or source_id or "").strip() or "unknown"
        row: dict[str, Any] = {
            "workflow_name": workflow_name,
            "run_id": run_id,
            "object_type": object_type,
            "object_id": object_id,
            "source_system": source_system,
            "destination_system": destination_system,
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
    workflow_name: str = WORKFLOW_INSURED_TO_ACCOUNT,
    source_system: str = "nowcerts",
    destination_system: str = "espocrm",
) -> None:
    """Write sync_errors; columns align with live Supabase (NOT NULL workflow_name, object_id, error_type)."""
    try:
        object_id = (source_id or queue_id or "").strip() or "unknown"
        payload: dict[str, Any] = dict(error_detail) if error_detail else {}
        if staging_id:
            payload["staging_id"] = staging_id
        if queue_id:
            payload["queue_id"] = queue_id
        row: dict[str, Any] = {
            "workflow_name": workflow_name,
            "run_id": run_id,
            "object_type": object_type,
            "object_id": object_id,
            "source_system": source_system,
            "destination_system": destination_system,
            "error_type": (error_code or "sync_pipeline")[:100],
            "error_message": error_message[:2000],
            "payload": payload or None,
        }
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
    # NOTE: column names mirror the live `sync_conflicts` schema (nowcerts_*/
    # espocrm_* jsonb pair, *_id text columns). The table has no run_id/
    # mapping_id columns, so those linkages are not persisted here.
    try:
        row: dict[str, Any] = {
            "object_type": "Account",
            "nowcerts_id": source_id,
            "espocrm_id": dest_id,
            "dest_object_id": dest_id,
            "field_name": conflict["field_name"],
            "nowcerts_value": conflict["source_value"],
            "espocrm_value": conflict["dest_value"],
            "status": "pending",
            "resolution": "pending",
        }
        supa.insert("sync_conflicts", row)
    except SupabaseClientError:
        log.exception("Failed to write sync conflict")
