"""CRM write queue worker: dequeues staged mutations and applies them to EspoCRM."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.operations.guardrails import log_guardrail_event

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
DEFAULT_CONCURRENCY = 5
DEFAULT_POLL_SECONDS = 5.0
ALLOWED_ACTION_TYPES = {
    "request_docs",
    "update_status",
    "create_note",
    "create_renewal",
    "legacy_write",
}


def enqueue_crm_write(
    supa: SupabaseClient,
    *,
    entity_type: str,
    payload: dict[str, Any],
    created_by_role: str,
    entity_id: str | None = None,
    target_system: str = "EspoCRM",
    priority: int = 1,
) -> dict[str, Any]:
    """Stage a CRM mutation in ``crm_write_queue`` with status PENDING."""
    if str(target_system).strip().lower() != "espocrm":
        raise ValueError("crm_write_queue only supports target_system='EspoCRM'")
    if priority < 1:
        raise ValueError("priority must be >= 1")
    normalized_payload = _normalize_queue_payload(payload)

    row = supa.insert(
        "crm_write_queue",
        {
            "target_system": "EspoCRM",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": normalized_payload,
            "status": "PENDING",
            "priority": priority,
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
    concurrency: int = DEFAULT_CONCURRENCY,
) -> ProcessResult:
    """Dequeue PENDING/FAILED(retriable) items and apply them to EspoCRM.

    Returns a summary of processed, succeeded, and failed items.
    
    Args:
        supa: Supabase client instance
        espo: EspoCRM client instance
        batch_size: Number of items to process per run
        dry_run: If True, simulate processing without writing
        concurrency: Number of concurrent async tasks (default: 5)
    """
    pending = supa.select(
        "crm_write_queue",
        params={
            "status": "in.(PENDING,FAILED)",
            "attempt_count": f"lt.{MAX_ATTEMPTS}",
            "target_system": "eq.EspoCRM",
            "order": "priority.asc,created_at.asc",
        },
        limit=batch_size,
    )
    
    if not pending:
        return ProcessResult(
            total=0,
            succeeded=0,
            failed=0,
            blocked=0,
            errors=[],
            dry_run=dry_run,
        )
    
    # Run async processing
    return asyncio.run(
        _process_queue_async(
            supa, espo, pending, 
            dry_run=dry_run, 
            concurrency=concurrency
        )
    )


async def _process_queue_async(
    supa: SupabaseClient,
    espo: EspoClient,
    pending: list[dict[str, Any]],
    *,
    dry_run: bool,
    concurrency: int,
) -> ProcessResult:
    """Async implementation of queue processing with concurrent execution."""
    semaphore = asyncio.Semaphore(concurrency)
    succeeded = 0
    failed = 0
    blocked = 0
    errors: list[str] = []
    
    async def _process_item(item: dict[str, Any]) -> tuple[bool, str | None]:
        """Process a single queue item asynchronously."""
        async with semaphore:
            queue_id = item["id"]
            entity_type = item["entity_type"]
            entity_id = item.get("entity_id")
            payload = dict(item.get("payload") or {})
            attempt_count = (item.get("attempt_count") or 0) + 1
            role = item.get("created_by_role", "unknown")
            
            # Check max attempts
            if attempt_count > MAX_ATTEMPTS:
                _update_status(supa, queue_id, "FAILED", attempt_count)
                _log_failed_queue_guardrail(
                    supa,
                    queue_id=queue_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    role=role,
                    attempt_count=attempt_count,
                    reason="max attempts exceeded before processing",
                )
                _alert_slack_on_terminal_failure(
                    queue_id=queue_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    role=role,
                    attempt_count=attempt_count,
                    error_message="max attempts exceeded before processing",
                )
                return False, f"{queue_id}: max attempts exceeded"
            
            # Dry run mode
            if dry_run:
                log.info("DRY RUN: would process queue_id=%s entity=%s", queue_id, entity_type)
                return True, None
            
            # Mark as processing
            _update_status(supa, queue_id, "PROCESSING", attempt_count)
            
            try:
                # Execute CRM operation in thread pool (EspoClient is sync)
                loop = asyncio.get_event_loop()
                crm_response = await loop.run_in_executor(
                    None,
                    _apply_to_espo, espo, entity_type, entity_id, payload
                )
            except EspoClientError as exc:
                log.warning("CRM write failed for queue_id=%s: %s", queue_id, exc)
                _update_status(supa, queue_id, "FAILED", attempt_count)
                _log_failed_queue_guardrail(
                    supa,
                    queue_id=queue_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    role=role,
                    attempt_count=attempt_count,
                    reason=str(exc),
                )
                if attempt_count >= MAX_ATTEMPTS:
                    _alert_slack_on_terminal_failure(
                        queue_id=queue_id,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        role=role,
                        attempt_count=attempt_count,
                        error_message=str(exc),
                    )
                return False, f"{queue_id}: {exc}"
            
            # Record receipt
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
            return True, None
    
    # Process all items concurrently
    tasks = [_process_item(item) for item in pending]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Aggregate results
    for result in results:
        if isinstance(result, Exception):
            failed += 1
            errors.append(f"Unexpected error: {result}")
        elif isinstance(result, tuple):
            success, error_msg = result
            if success:
                succeeded += 1
            else:
                failed += 1
                if error_msg:
                    errors.append(error_msg)
    
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
    context = work.get("context")
    if isinstance(context, dict):
        action_type = str(work.get("action_type", "")).strip().lower()
        intent = str(work.get("intent", "")).strip().lower()
        properties = dict(context)
        if not intent:
            if action_type in {"update_status", "create_note", "request_docs"}:
                intent = "update"
            elif action_type in {"create_renewal", "create"}:
                intent = "create"
            else:
                intent = "update" if entity_id else "create"
    else:
        intent = str(work.pop("intent", "")).strip().lower()
        properties = work.pop("properties", work)
        if isinstance(properties, dict) and not properties:
            properties = work

    if entity_id and intent in ("update", "annotate", ""):
        return espo.update(entity_type, entity_id, properties)
    return espo.create(entity_type, properties)


def _normalize_queue_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate queue payload and normalize it to action_type/context form."""
    action_type = str(payload.get("action_type", "")).strip().lower()
    if action_type:
        context = payload.get("context")
        if not isinstance(context, dict):
            raise ValueError("payload.context must be an object when action_type is set")
        if action_type not in ALLOWED_ACTION_TYPES:
            raise ValueError(f"Unsupported payload.action_type '{action_type}'")
        normalized: dict[str, Any] = {
            "action_type": action_type,
            "context": context,
        }
        intent = payload.get("intent")
        if isinstance(intent, str) and intent.strip():
            normalized["intent"] = intent.strip().lower()
        return normalized

    # Backward compatibility for older queue callers that send intent/properties.
    properties = payload.get("properties")
    if properties is None:
        properties = {k: v for k, v in payload.items() if k not in {"intent", "properties"}}
    if not isinstance(properties, dict):
        raise ValueError("payload.properties must be an object")
    intent = str(payload.get("intent", "")).strip().lower()
    normalized_payload: dict[str, Any] = {
        "action_type": "legacy_write",
        "context": properties,
    }
    if intent:
        normalized_payload["intent"] = intent
    return normalized_payload


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


def _log_failed_queue_guardrail(
    supa: SupabaseClient,
    *,
    queue_id: str,
    entity_type: str,
    entity_id: str | None,
    role: str,
    attempt_count: int,
    reason: str,
) -> None:
    """Log all failed queue items to guardrail_logs."""
    severity = "CRITICAL" if attempt_count >= MAX_ATTEMPTS else "HIGH"
    try:
        log_guardrail_event(
            supa,
            agent_role=role,
            attempted_action=f"crm_write_queue:{entity_type}",
            rule_violated="crm_queue_item_failed",
            context_payload={
                "queue_id": queue_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "attempt_count": attempt_count,
                "max_attempts": MAX_ATTEMPTS,
                "error": reason,
            },
            severity=severity,
        )
    except SupabaseClientError:
        log.exception("Failed to persist guardrail log for queue_id=%s", queue_id)


def _alert_slack_on_terminal_failure(
    *,
    queue_id: str,
    entity_type: str,
    entity_id: str | None,
    role: str,
    attempt_count: int,
    error_message: str,
) -> None:
    """Alert Slack when a queue item fails terminally after retries."""
    if not os.environ.get("SLACK_BOT_TOKEN", "").strip():
        return
    try:
        notifier = SlackNotifier()
        notifier.post_message(
            text=(
                ":rotating_light: Hermes CRM queue terminal failure\n"
                f"- queue_id: {queue_id}\n"
                f"- entity: {entity_type}\n"
                f"- entity_id: {entity_id or 'n/a'}\n"
                f"- created_by_role: {role}\n"
                f"- attempt: {attempt_count}/{MAX_ATTEMPTS}\n"
                f"- error: {error_message}"
            )
        )
    except SlackNotifierError:
        log.exception("Failed to post Slack terminal-failure alert for queue_id=%s", queue_id)


def run_worker_loop(
    supa: SupabaseClient,
    espo: EspoClient,
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    batch_size: int = 25,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> None:
    """Continuously poll crm_write_queue every N seconds for systemd workers."""
    interval = poll_seconds if poll_seconds > 0 else DEFAULT_POLL_SECONDS
    log.info("Starting CRM queue loop: interval=%ss batch_size=%s", interval, batch_size)
    while True:
        result = process_queue(
            supa,
            espo,
            batch_size=batch_size,
            dry_run=False,
            concurrency=concurrency,
        )
        if result.total:
            log.info(result.message)
        time.sleep(interval)


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
