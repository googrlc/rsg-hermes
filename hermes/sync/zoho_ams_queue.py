"""Zoho AMS_Write_Queue → Supabase ``outbound_sync_queue`` (renewal jobs only).

Creator enqueues structured CRM rows. Hermes mirrors approved ones into the
existing queue so ``hermes/renewals/executor.py`` stays the only NowCerts
writer. Idempotent on ``payload.zoho_queue_id``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from hermes.renewals.executor import (
    ACTION_UPDATE_AMS,
    ACTIONS,
    DESTINATION_NOWCERTS,
    OBJECT_TYPE_RENEWAL,
    QUEUE_QUEUED,
    QUEUE_TABLE,
)
from hermes_core.queue import QUEUE_PROCESSING
from hermes_integrations.zoho_client import ZohoClient, ZohoClientError

log = logging.getLogger(__name__)

QUEUE_MODULE = os.environ.get("ZOHO_AMS_WRITE_QUEUE_MODULE", "AMS_Write_Queue")
P85_TABLE = "project_85_renewals"

OPEN_STATUSES = frozenset({QUEUE_QUEUED, QUEUE_PROCESSING})
MIRRORABLE_ZOHO_STATUSES = frozenset({"queued"})


@dataclass
class ZohoQueueMirrorResult:
    scanned: int = 0
    mirrored: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        mode = "dry-run" if self.dry_run else "live"
        return (
            f"Zoho AMS queue mirror ({mode}): scanned {self.scanned}, "
            f"mirrored {self.mirrored}, skipped {self.skipped}, "
            f"errors {len(self.errors)}"
        )


def parse_payload(raw: Any) -> dict[str, Any]:
    """AMS_Write_Queue.Payload is JSON (string or already a dict)."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def is_mirrorable(zoho_row: dict[str, Any]) -> tuple[bool, str]:
    """Only approved, queued, renewal jobs with a complete executor payload."""
    object_type = str(zoho_row.get("Object_Type") or "").strip().lower()
    if object_type != OBJECT_TYPE_RENEWAL:
        return False, "not a renewal job"
    status = str(zoho_row.get("Status") or "").strip().lower()
    if status not in MIRRORABLE_ZOHO_STATUSES:
        return False, f"status {status!r} is not queued"
    approved_by = str(zoho_row.get("Approved_By") or "").strip()
    approved_at = zoho_row.get("Approved_At")
    if not approved_by or not approved_at:
        return False, "missing Approved_By or Approved_At"
    payload = parse_payload(zoho_row.get("Payload"))
    action = str(payload.get("action") or "").strip()
    if action not in ACTIONS:
        return False, f"payload.action {action!r} is not an executor action"
    if not str(payload.get("expected_result") or "").strip():
        return False, "payload.expected_result is required"
    if not str(payload.get("renewal_id") or "").strip() and not str(
        payload.get("policy_number") or zoho_row.get("Object_ID") or ""
    ).strip():
        return False, "need payload.renewal_id or policy_number"
    return True, "ok"


def zoho_queue_id(zoho_row: dict[str, Any]) -> str | None:
    qid = zoho_row.get("Queue_ID") or zoho_row.get("id")
    return str(qid).strip() if qid else None


def already_mirrored(
    existing: list[dict[str, Any]], *, zoho_id: str | None, policy_number: str, queue_action: str
) -> bool:
    """True if this Zoho row is already on the outbound queue, or open work collides."""
    for row in existing:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if zoho_id and str(payload.get("zoho_queue_id") or "") == zoho_id:
            return True
        if (
            str(row.get("status") or "") in OPEN_STATUSES
            and str(row.get("object_id") or "") == policy_number
            and str(row.get("action") or "") == queue_action
            and str(row.get("object_type") or "") == OBJECT_TYPE_RENEWAL
        ):
            return True
    return False


def to_outbound_row(
    zoho_row: dict[str, Any],
    *,
    renewal_id: str,
    policy_number: str,
) -> dict[str, Any]:
    payload = parse_payload(zoho_row.get("Payload"))
    action = str(payload.get("action") or "").strip()
    queue_action = "update" if action == ACTION_UPDATE_AMS else "create"
    out_payload = {
        "action": action,
        "renewal_id": renewal_id,
        "policy_number": policy_number,
        "expected_result": str(payload.get("expected_result") or "").strip(),
        "channel": payload.get("channel") or "task",
        "zoho_queue_id": zoho_queue_id(zoho_row),
    }
    if payload.get("fields"):
        out_payload["fields"] = payload["fields"]
    if payload.get("note"):
        out_payload["note"] = payload["note"]
    return {
        "object_type": OBJECT_TYPE_RENEWAL,
        "object_id": policy_number,
        "destination_system": DESTINATION_NOWCERTS,
        "action": queue_action,
        "payload": out_payload,
        "status": QUEUE_QUEUED,
        "attempt_count": 0,
        "approved_by": str(zoho_row.get("Approved_By") or "").strip(),
        "approved_at": zoho_row.get("Approved_At"),
    }


def _resolve_renewal(supa: Any, payload: dict[str, Any], zoho_row: dict[str, Any]) -> dict[str, Any] | None:
    rid = str(payload.get("renewal_id") or "").strip()
    if rid:
        rows = supa.select(P85_TABLE, columns="id,policy_number", params={"id": f"eq.{rid}"}, limit=1)
        if rows:
            return rows[0]
    pn = str(payload.get("policy_number") or zoho_row.get("Object_ID") or "").strip()
    if not pn:
        return None
    rows = supa.select(
        P85_TABLE, columns="id,policy_number", params={"policy_number": f"eq.{pn}"}, limit=1
    )
    return rows[0] if rows else None


def run_zoho_ams_queue_mirror(
    *,
    supa: Any,
    zoho: ZohoClient | None = None,
    dry_run: bool = False,
) -> ZohoQueueMirrorResult:
    """Copy approved Zoho renewal jobs into ``outbound_sync_queue``."""
    from hermes_integrations.zoho_client import get_client

    result = ZohoQueueMirrorResult(dry_run=dry_run)
    zoho = zoho or get_client()

    try:
        rows = list(
            zoho.iter_records(
                QUEUE_MODULE,
                criteria="(Object_Type:equals:renewal)",
            )
        )
    except ZohoClientError as exc:
        result.errors.append(str(exc))
        return result

    result.scanned = len(rows)
    for zoho_row in rows:
        ok, reason = is_mirrorable(zoho_row)
        if not ok:
            result.skipped += 1
            log.debug("skip AMS_Write_Queue %s: %s", zoho_row.get("id"), reason)
            continue
        payload = parse_payload(zoho_row.get("Payload"))
        renewal = _resolve_renewal(supa, payload, zoho_row)
        if not renewal:
            result.errors.append(
                f"queue {zoho_queue_id(zoho_row)}: renewal_id did not resolve in project_85_renewals"
            )
            continue
        renewal_id = str(renewal["id"])
        policy_number = str(renewal.get("policy_number") or payload.get("policy_number") or "")
        action = str(payload.get("action") or "").strip()
        queue_action = "update" if action == ACTION_UPDATE_AMS else "create"
        existing = supa.select(
            QUEUE_TABLE,
            columns="id,status,object_id,object_type,action,payload",
            params={"object_type": f"eq.{OBJECT_TYPE_RENEWAL}", "object_id": f"eq.{policy_number}"},
            limit=200,
        )
        if already_mirrored(
            existing,
            zoho_id=zoho_queue_id(zoho_row),
            policy_number=policy_number,
            queue_action=queue_action,
        ):
            result.skipped += 1
            continue
        outbound = to_outbound_row(zoho_row, renewal_id=renewal_id, policy_number=policy_number)
        if dry_run:
            result.mirrored += 1
            continue
        try:
            supa.insert(QUEUE_TABLE, outbound)
            result.mirrored += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("outbound_sync_queue insert failed for %s", zoho_queue_id(zoho_row))
            result.errors.append(f"insert {zoho_queue_id(zoho_row)}: {exc}")

    return result
