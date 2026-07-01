"""Renewal Loop v6 plumbing for Espo webhooks, Supabase logging, and AMS writeback."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

from . import config, worksheet
from .momentum_mcp_client import MomentumMCPClient, MomentumMCPClientError

log = logging.getLogger(__name__)


def handle_disposition_webhook(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    supa: SupabaseClient | None = None,
    momentum: MomentumMCPClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    supa = supa or SupabaseClient()
    now = now or datetime.now(timezone.utc)
    summary = {"received": 0, "logged": 0, "writebacks": {"succeeded": 0, "retrying": 0, "failed": 0, "skipped": 0}}
    for item in _records(payload):
        summary["received"] += 1
        record = _record(item)
        renewal_id = _renewal_id(record)
        if not renewal_id:
            continue
        event_uuid = _event_uuid(item, "renewal.disposition_changed")
        _upsert_master(supa, record, now=now)
        _upsert_event(supa, event_uuid=event_uuid, renewal_id=renewal_id, event_type="renewal.disposition_changed", payload=record)
        _upsert_sync_log(supa, event_uuid=event_uuid, renewal_id=renewal_id, event_type="renewal.disposition_changed", status="received")
        _upsert_disposition(supa, event_uuid=event_uuid, record=record)
        result = _queue_and_attempt_writeback(
            supa,
            event_uuid=event_uuid,
            record=record,
            now=now,
            momentum=momentum,
        )
        summary["logged"] += 1
        state = result.get("state", "skipped")
        if state not in summary["writebacks"]:
            state = "skipped"
        summary["writebacks"][state] += 1
    return summary


def handle_worksheet_webhook(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    supa: SupabaseClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    supa = supa or SupabaseClient()
    now = now or datetime.now(timezone.utc)
    summary = {"received": 0, "logged": 0}
    for item in _records(payload):
        summary["received"] += 1
        record = _record(item)
        renewal_id = _renewal_id(record)
        if not renewal_id:
            continue
        event_uuid = _event_uuid(item, "renewal.worksheet_completed")
        _upsert_master(supa, record, now=now)
        _upsert_event(supa, event_uuid=event_uuid, renewal_id=renewal_id, event_type="renewal.worksheet_completed", payload=record)
        _upsert_sync_log(supa, event_uuid=event_uuid, renewal_id=renewal_id, event_type="renewal.worksheet_completed", status="received")
        summary["logged"] += 1
    return summary


def run_reconcile(
    *,
    supa: SupabaseClient | None = None,
    momentum: MomentumMCPClient | None = None,
    notifier_cls: type[SlackNotifier] = SlackNotifier,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    supa = supa or SupabaseClient()
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    queued = supa.select(
        "ams_writeback_log",
        params={
            "state": "in.(pending,retrying)",
            "next_retry_at": f"lte.{now_iso}",
            "order": "next_retry_at.asc",
        },
        limit=limit,
    )

    attempted = 0
    succeeded = 0
    retrying = 0
    for row in queued:
        attempted += 1
        result = _attempt_writeback_row(
            supa,
            row,
            record=(row.get("payload") or {}).get("renewal") or {},
            now=now,
            momentum=momentum,
            event_uuid=str(row.get("event_uuid") or ""),
        )
        if result["state"] == "succeeded":
            succeeded += 1
        elif result["state"] == "retrying":
            retrying += 1

    failed_rows = supa.select(
        "ams_writeback_log",
        params={"state": "eq.failed", "order": "updated_at.desc"},
        limit=25,
    )
    alerted = False
    if failed_rows:
        text = _failed_digest(failed_rows)
        try:
            notifier_cls(channel=config.SLACK_SYSTEMS_CHECK).post_message(text=text)
            alerted = True
        except (SlackNotifierError, Exception) as exc:
            log.warning("Renewal reconcile alert failed: %s", exc)

    return {
        "ok": True,
        "attempted": attempted,
        "succeeded": succeeded,
        "retrying": retrying,
        "failed": len(failed_rows),
        "alerted": alerted,
    }


def _records(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return [payload] if isinstance(payload, dict) else []


def _record(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data")
    if isinstance(data, dict):
        return data
    entity = item.get("entity")
    if isinstance(entity, dict):
        return entity
    return item


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _renewal_id(record: dict[str, Any]) -> str:
    return str(_first(record, "id", "renewalId", "entityId") or "").strip()


def _event_uuid(item: dict[str, Any], event_type: str) -> str:
    record = _record(item)
    explicit = _first(item, "event_uuid", "eventUuid", "eventId", "webhookEventId") or _first(
        record, "event_uuid", "eventUuid", "eventId", "webhookEventId",
    )
    if explicit:
        return str(explicit)
    raw = json.dumps({"event_type": event_type, "record": record}, sort_keys=True, default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


def _upsert_master(supa: SupabaseClient, record: dict[str, Any], *, now: datetime) -> None:
    worksheet_row = worksheet.worksheet_record(record)
    payload = {
        "renewal_id": _renewal_id(record),
        "account_id": record.get("accountId"),
        "account_name": record.get("accountName"),
        "line_of_business": record.get("line_of_business"),
        "expiration_date": record.get("expiration_date"),
        "pipeline_stage": _first(record, "pipeline_stage", "stage"),
        "disposition": record.get("disposition"),
        "current_premium": record.get("current_premium"),
        "renewal_proposed_premium": record.get("renewal_proposed_premium"),
        "renewal_premium": record.get("renewal_premium"),
        "premium_change": record.get("premium_change"),
        "carrier_premium_change": record.get("carrier_premium_change"),
        "worksheet_id": _first(record, *config.WORKSHEET_ID_KEYS),
        "worksheet_lob_variant": worksheet_row.get("lob_variant"),
        "completion_type": worksheet_row.get("completion_type"),
        "source_payload": record,
        "updated_at": now.isoformat(),
    }
    supa.upsert("renewals_master", payload, on_conflict="renewal_id")


def _upsert_event(
    supa: SupabaseClient,
    *,
    event_uuid: str,
    renewal_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    supa.upsert(
        "renewal_events",
        {
            "event_uuid": event_uuid,
            "renewal_id": renewal_id,
            "event_type": event_type,
            "source_system": "espocrm",
            "payload": payload,
        },
        on_conflict="event_uuid",
    )


def _upsert_sync_log(
    supa: SupabaseClient,
    *,
    event_uuid: str,
    renewal_id: str,
    event_type: str,
    status: str,
    destination_system: str = "supabase",
    message: str | None = None,
) -> None:
    payload = {
        "event_uuid": event_uuid,
        "renewal_id": renewal_id,
        "event_type": event_type,
        "source_system": "espocrm",
        "destination_system": destination_system,
        "status": status,
    }
    if message:
        payload["message"] = message
    supa.upsert("crm_sync_log", payload, on_conflict="event_uuid")


def _upsert_disposition(supa: SupabaseClient, *, event_uuid: str, record: dict[str, Any]) -> None:
    worksheet_row = worksheet.worksheet_record(record)
    supa.upsert(
        "crm_dispositions",
        {
            "event_uuid": event_uuid,
            "renewal_id": _renewal_id(record),
            "pipeline_stage": _first(record, "pipeline_stage", "stage"),
            "disposition": record.get("disposition"),
            "worksheet_id": _first(record, *config.WORKSHEET_ID_KEYS),
            "completion_type": worksheet_row.get("completion_type"),
            "payload": record,
        },
        on_conflict="event_uuid",
    )


def _queue_and_attempt_writeback(
    supa: SupabaseClient,
    *,
    event_uuid: str,
    record: dict[str, Any],
    now: datetime,
    momentum: MomentumMCPClient | None,
) -> dict[str, Any]:
    disposition = str(record.get("disposition") or "").strip()
    if not disposition:
        _upsert_sync_log(
            supa,
            event_uuid=event_uuid,
            renewal_id=_renewal_id(record),
            event_type="renewal.disposition_changed",
            status="skipped",
            destination_system="momentum",
            message="no disposition in payload",
        )
        supa.upsert(
            "ams_writeback_log",
            {
                "event_uuid": event_uuid,
                "renewal_id": _renewal_id(record),
                "target": config.MOMENTUM_MCP_TOOL_NOTES,
                "state": "skipped",
                "attempts": 0,
                "payload": {"renewal": record},
                "updated_at": now.isoformat(),
            },
            on_conflict="event_uuid",
        )
        return {"state": "skipped"}

    row = supa.upsert(
        "ams_writeback_log",
        {
            "event_uuid": event_uuid,
            "renewal_id": _renewal_id(record),
            "target": config.MOMENTUM_MCP_TOOL_NOTES,
            "state": "pending",
            "attempts": 0,
            "next_retry_at": now.isoformat(),
            "payload": {"renewal": record, "note": _writeback_payload(record)},
            "updated_at": now.isoformat(),
        },
        on_conflict="event_uuid",
    )
    return _attempt_writeback_row(supa, row, record=record, now=now, momentum=momentum, event_uuid=event_uuid)


def _attempt_writeback_row(
    supa: SupabaseClient,
    row: dict[str, Any],
    *,
    record: dict[str, Any],
    now: datetime,
    momentum: MomentumMCPClient | None,
    event_uuid: str,
) -> dict[str, Any]:
    attempts = int(row.get("attempts") or 0) + 1
    note_payload = ((row.get("payload") or {}).get("note")) or _writeback_payload(record)
    if not note_payload.get("databaseId"):
        message = "missing momentum_client_id / insuredMomentumId for AMS note writeback"
        _update_writeback(
            supa,
            event_uuid=event_uuid,
            payload={
                "state": "failed",
                "attempts": attempts,
                "last_error": message,
                "next_retry_at": None,
                "updated_at": now.isoformat(),
            },
        )
        _upsert_sync_log(
            supa,
            event_uuid=event_uuid,
            renewal_id=_renewal_id(record),
            event_type="renewal.disposition_changed",
            status="failed",
            destination_system="momentum",
            message=message,
        )
        return {"state": "failed"}
    try:
        client = momentum or MomentumMCPClient()
        result = client.manage_notes(note_payload)
    except MomentumMCPClientError as exc:
        state, next_retry_at = _failure_state(attempts, exc.retryable, now)
        _update_writeback(
            supa,
            event_uuid=event_uuid,
            payload={
                "state": state,
                "attempts": attempts,
                "last_error": str(exc),
                "next_retry_at": next_retry_at,
                "updated_at": now.isoformat(),
            },
        )
        _upsert_sync_log(
            supa,
            event_uuid=event_uuid,
            renewal_id=_renewal_id(record),
            event_type="renewal.disposition_changed",
            status=state,
            destination_system="momentum",
            message=str(exc),
        )
        return {"state": state}
    except Exception as exc:
        state, next_retry_at = _failure_state(attempts, True, now)
        _update_writeback(
            supa,
            event_uuid=event_uuid,
            payload={
                "state": state,
                "attempts": attempts,
                "last_error": str(exc),
                "next_retry_at": next_retry_at,
                "updated_at": now.isoformat(),
            },
        )
        _upsert_sync_log(
            supa,
            event_uuid=event_uuid,
            renewal_id=_renewal_id(record),
            event_type="renewal.disposition_changed",
            status=state,
            destination_system="momentum",
            message=str(exc),
        )
        return {"state": state}

    note_id = _first(result, "noteId", "note_id", "id")
    _update_writeback(
        supa,
        event_uuid=event_uuid,
        payload={
            "state": "succeeded",
            "attempts": attempts,
            "last_error": None,
            "next_retry_at": None,
            "response_payload": result,
            "posted_note_id": str(note_id) if note_id else None,
            "updated_at": now.isoformat(),
        },
    )
    _upsert_sync_log(
        supa,
        event_uuid=event_uuid,
        renewal_id=_renewal_id(record),
        event_type="renewal.disposition_changed",
        status="succeeded",
        destination_system="momentum",
    )
    return {"state": "succeeded", "response": result}


def _update_writeback(supa: SupabaseClient, *, event_uuid: str, payload: dict[str, Any]) -> None:
    supa.update_where("ams_writeback_log", payload, filters={"event_uuid": f"eq.{event_uuid}"})


def _failure_state(attempts: int, retryable: bool, now: datetime) -> tuple[str, str | None]:
    if not retryable:
        return "failed", None
    delay = config.WRITEBACK_RETRY_DELAYS[attempts - 1] if attempts - 1 < len(config.WRITEBACK_RETRY_DELAYS) else None
    if delay is None:
        return "failed", None
    return "retrying", (now + timedelta(seconds=delay)).isoformat()


def _writeback_payload(record: dict[str, Any]) -> dict[str, Any]:
    worksheet_row = worksheet.worksheet_record(record)
    client = record.get("accountName") or record.get("name") or "Client"
    disposition = str(record.get("disposition") or "unknown").replace("_", " ")
    body_lines = [
        f"Renewal disposition changed for {client}.",
        f"Pipeline stage: {_first(record, 'pipeline_stage', 'stage') or '—'}",
        f"Disposition: {disposition}",
        f"Line of business: {record.get('line_of_business') or '—'}",
    ]
    if worksheet_row.get("lob_variant"):
        body_lines.append(f"Worksheet variant: {worksheet_row.get('lob_variant')}")
    if worksheet_row.get("notes"):
        body_lines.append(f"Worksheet notes: {worksheet_row.get('notes')}")
    if record.get("renewal_notes"):
        body_lines.append(f"Client states: {record.get('renewal_notes')}")
    return {
        "operation": "create",
        "databaseId": _first(record, "momentum_client_id", "insuredMomentumId", "accountMomentumId"),
        "title": f"Renewal disposition — {client}",
        "note": "\n".join(body_lines),
        "renewalId": _renewal_id(record),
    }


def _failed_digest(rows: list[dict[str, Any]]) -> str:
    lines = [":rotating_light: Renewal Loop v6 writeback failures", ""]
    for row in rows:
        lines.append(
            f"- renewal_id: {row.get('renewal_id') or '—'} | "
            f"attempts: {row.get('attempts') or 0} | "
            f"error: {row.get('last_error') or 'unknown'}"
        )
    return "\n".join(lines)
