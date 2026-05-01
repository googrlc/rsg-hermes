"""CRM write queue worker: dequeues staged mutations and applies them to EspoCRM."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.operations.guardrails import log_guardrail_event

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def enqueue_crm_write(
    supa: SupabaseClient,
    *,
    entity_type: str,
    payload: dict[str, Any],
    created_by_role: str,
    entity_id: str | None = None,
    target_system: str = "EspoCRM",
) -> dict[str, Any]:
    """Stage a CRM mutation in ``crm_write_queue`` with status PENDING."""
    row = supa.insert(
        "crm_write_queue",
        {
            "target_system": target_system,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
            "status": "PENDING",
            "attempt_count": 0,
            "created_by_role": created_by_role,
        },
    )
    log.info(
        "Enqueued CRM write: entity=%s role=%s queue_id=%s",
        entity_type,
        created_by_role,
        row.get("id"),
    )
    return row


def process_queue(
    supa: SupabaseClient,
    espo: EspoClient,
    *,
    batch_size: int = 10,
    dry_run: bool = False,
) -> ProcessResult:
    """Dequeue PENDING items and apply them to EspoCRM.

    Returns a summary of processed, succeeded, and failed items.
    """
    pending = supa.select(
        "crm_write_queue",
        params={
            "status": "eq.PENDING",
            "target_system": "eq.EspoCRM",
            "order": "created_at.asc",
        },
        limit=batch_size,
    )
    succeeded = 0
    failed = 0
    blocked = 0
    errors: list[str] = []

    for item in pending:
        queue_id = item["id"]
        entity_type = item["entity_type"]
        entity_id = item.get("entity_id")
        payload = dict(item.get("payload") or {})
        attempt_count = (item.get("attempt_count") or 0) + 1
        role = item.get("created_by_role", "unknown")

        if attempt_count > MAX_ATTEMPTS:
            _update_status(supa, queue_id, "FAILED", attempt_count)
            failed += 1
            errors.append(f"{queue_id}: max attempts exceeded")
            continue

        if dry_run:
            log.info("DRY RUN: would process queue_id=%s entity=%s", queue_id, entity_type)
            succeeded += 1
            continue

        _update_status(supa, queue_id, "PROCESSING", attempt_count)

        try:
            crm_response = _apply_to_espo(espo, entity_type, entity_id, payload)
        except EspoClientError as exc:
            log.warning("CRM write failed for queue_id=%s: %s", queue_id, exc)
            _update_status(supa, queue_id, "FAILED", attempt_count)
            failed += 1
            errors.append(f"{queue_id}: {exc}")
            continue

        transaction_id = _extract_transaction_id(crm_response, queue_id)
        try:
            supa.insert(
                "crm_receipts",
                {
                    "queue_id": queue_id,
                    "transaction_id": transaction_id,
                    "raw_response": crm_response if isinstance(crm_response, dict) else {"raw": str(crm_response)},
                },
            )
        except SupabaseClientError:
            log.exception("Failed to write CRM receipt for queue_id=%s", queue_id)

        _update_status(supa, queue_id, "SUCCESS", attempt_count)
        succeeded += 1

    return ProcessResult(
        total=len(pending),
        succeeded=succeeded,
        failed=failed,
        blocked=blocked,
        errors=errors,
        dry_run=dry_run,
    )


def _apply_to_espo(
    espo: EspoClient,
    entity_type: str,
    entity_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any] | list[Any]:
    """Dispatch create or update based on whether entity_id is present."""
    work = dict(payload)
    intent = str(work.pop("intent", "")).strip()
    properties = work.pop("properties", work)
    if isinstance(properties, dict) and not properties:
        properties = work

    if entity_id and intent in ("update", "annotate", ""):
        return espo.update(entity_type, entity_id, properties)
    return espo.create(entity_type, properties)


def _update_status(
    supa: SupabaseClient,
    queue_id: str,
    status: str,
    attempt_count: int,
) -> None:
    """PATCH the queue row status and attempt_count via PostgREST."""
    try:
        supa.update(
            "crm_write_queue",
            queue_id,
            {"status": status, "attempt_count": attempt_count},
        )
    except SupabaseClientError:
        log.exception("Failed to update queue status for %s", queue_id)


def _extract_transaction_id(
    response: dict[str, Any] | list[Any],
    fallback_queue_id: str,
) -> str:
    """Pull the CRM record ID from the response or fall back to a generated one."""
    if isinstance(response, dict):
        crm_id = response.get("id")
        if crm_id:
            return f"espo_{crm_id}"
    return f"hermes_{fallback_queue_id}_{uuid.uuid4().hex[:8]}"


class ProcessResult:
    """Summary of a queue processing run."""

    def __init__(
        self,
        *,
        total: int,
        succeeded: int,
        failed: int,
        blocked: int,
        errors: list[str],
        dry_run: bool,
    ) -> None:
        self.total = total
        self.succeeded = succeeded
        self.failed = failed
        self.blocked = blocked
        self.errors = errors
        self.dry_run = dry_run

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @property
    def message(self) -> str:
        prefix = "DRY RUN: " if self.dry_run else ""
        return (
            f"{prefix}CRM queue processed: {self.total} items "
            f"({self.succeeded} succeeded, {self.failed} failed, {self.blocked} blocked)"
        )
