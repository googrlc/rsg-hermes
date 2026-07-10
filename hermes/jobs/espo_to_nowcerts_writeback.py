"""Espo → NowCerts write-back — mirror EspoCRM service Cases into the NowCerts
task ledger (the AMS is the historic system of record).

Governance ([[rsg-ams-source-of-truth-governance]]): NowCerts is the source of
truth; EspoCRM writes UP only through narrow, additive channels. This job is the
**Cases** channel: every service-request Case that belongs to a client with a
NowCerts insured GUID is written back as a NowCerts Task via
``/api/Zapier/InsertTask`` (create) or ``/api/Zapier/UpdateTask`` (update).

Mapping: the Case service-request ``type`` rides along as the task
``category_name``; the linked Account's ``momentum_client_id`` is the
``insured_database_id``. Idempotency: the NowCerts task database_id is stored on
``Case.momentumTaskId`` so daily re-runs UPDATE instead of duplicating.

Client-linked only: a Case whose Account has no ``momentum_client_id`` GUID is
skipped — there is no client in the AMS to attach the ledger entry to. This job
never deletes and never overwrites AMS fields.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError

log = logging.getLogger(__name__)

# Espo Case.status -> NowCerts task ledger state.
_CLOSED_STATUSES = frozenset({"Closed", "Cancelled"})
# Espo Case.priority -> NowCerts task priority.
_PRIORITY_MAP = {"Low": "Low", "Normal": "Medium", "High": "High", "Urgent": "High"}

_CASE_SELECT = ",".join([
    "id", "name", "number", "status", "type", "description", "priority",
    "accountId", "accountName", "assignedUserName",
    "createdAt", "modifiedAt", "momentumTaskId", "momentumLastSynced",
])


@dataclass
class WritebackResult:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped_no_client: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @property
    def message(self) -> str:
        prefix = "[DRY RUN] " if self.dry_run else ""
        return (
            f"{prefix}Cases->NowCerts write-back: {self.total} scanned, "
            f"{self.created} created, {self.updated} updated, "
            f"{self.skipped_no_client} skipped (no client GUID), "
            f"{self.failed} failed."
        )


def _cutoff(since_hours: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _now_espo() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _extract_task_id(resp: Any) -> str | None:
    """Pull the task database_id out of an InsertTask response.

    NowCerts wraps the created task under ``data``:
    ``{"status": 1, "data": {"database_id": "...", ...}, "message": "..."}``.
    We check the nested ``data`` object first, then the top level defensively.
    """
    if not isinstance(resp, dict):
        return None
    candidates = [resp]
    data = resp.get("data")
    if isinstance(data, dict):
        candidates.insert(0, data)
    for obj in candidates:
        for key in ("database_id", "databaseId", "DatabaseId", "id", "taskId", "TaskId"):
            val = obj.get(key)
            if val:
                return str(val)
    return None


def _nc_status(case_status: str) -> str:
    return "Closed" if case_status in _CLOSED_STATUSES else "Open"


def _build_task_payload(case: dict[str, Any], insured_guid: str) -> dict[str, Any]:
    """Map an Espo Case to a NowCerts InsertTask body (snake_case)."""
    status = (case.get("status") or "New").strip()
    priority = _PRIORITY_MAP.get((case.get("priority") or "Normal").strip(), "Medium")
    # Cases carry no due date — use the created date as the ledger date.
    created = (case.get("createdAt") or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = case.get("name") or f"Service Request {case.get('number', '')}".strip()

    payload: dict[str, Any] = {
        "title": title,
        "description": case.get("description") or "",
        "status": _nc_status(status),
        "priority": priority,
        "due_date": created,
        "category_name": case.get("type") or "Other",
        "insured_database_id": insured_guid,
    }
    assigned = case.get("assignedUserName")
    if assigned:
        payload["assigned_to"] = [assigned]
    existing = case.get("momentumTaskId")
    if existing:
        payload["database_id"] = existing
    return payload


def _account_guid(espo: EspoClient, account_id: str) -> str | None:
    """Fetch an Account's NowCerts insured GUID (Account.momentum_client_id)."""
    rec = espo.get(f"Account/{account_id}", params={"select": "id,momentum_client_id"})
    if isinstance(rec, dict):
        guid = rec.get("momentum_client_id")
        return str(guid) if guid else None
    return None


def _stamp_synced(espo: EspoClient, case_id: str, task_id: str) -> None:
    """Record the NowCerts task id + sync timestamp back on the Espo Case."""
    espo.patch(
        f"Case/{case_id}",
        json={"momentumTaskId": task_id, "momentumLastSynced": _now_espo()},
    )


def run_writeback(
    espo: EspoClient | None = None,
    nowcerts: NowCertsClient | None = None,
    *,
    dry_run: bool = False,
    since_hours: int = 24,
    max_size: int = 200,
) -> WritebackResult:
    """Write EspoCRM service Cases back to the NowCerts task ledger.

    Scans Cases modified in the last ``since_hours`` and, for each Case whose
    Account carries a NowCerts insured GUID, upserts a NowCerts Task (create if
    new, update if already linked via ``momentumTaskId``). Additive only.
    """
    espo = espo or EspoClient()
    nowcerts = nowcerts or NowCertsClient()
    result = WritebackResult(dry_run=dry_run)

    cutoff = _cutoff(since_hours)
    body = espo.get(
        "Case",
        params={
            "maxSize": max_size,
            "select": _CASE_SELECT,
            "where": [{"type": "after", "attribute": "modifiedAt", "value": cutoff}],
            "orderBy": "modifiedAt",
            "order": "desc",
        },
    )
    cases = body.get("list", []) if isinstance(body, dict) else []
    result.total = len(cases)
    log.info("Cases->NowCerts: %d case(s) modified since %s", result.total, cutoff)

    guid_cache: dict[str, str | None] = {}

    for case in cases:
        cid = case.get("id")
        try:
            account_id = case.get("accountId")
            if not account_id:
                result.skipped_no_client += 1
                log.debug("Case %s has no account link — skipped", cid)
                continue

            if account_id not in guid_cache:
                guid_cache[account_id] = _account_guid(espo, account_id)
            insured_guid = guid_cache[account_id]
            if not insured_guid:
                result.skipped_no_client += 1
                log.debug("Case %s account %s lacks momentum_client_id — skipped", cid, account_id)
                continue

            payload = _build_task_payload(case, insured_guid)
            is_update = bool(case.get("momentumTaskId"))

            if dry_run:
                log.info(
                    "[DRY RUN] would %s NowCerts task for Case %s (%s) type=%s status=%s",
                    "update" if is_update else "create",
                    cid, case.get("name"), payload["category_name"], payload["status"],
                )
                result.updated += is_update
                result.created += not is_update
                continue

            if is_update:
                nowcerts.update_task(payload)
                _stamp_synced(espo, cid, case["momentumTaskId"])
                result.updated += 1
            else:
                resp = nowcerts.insert_task(payload)
                task_id = _extract_task_id(resp)
                if not task_id:
                    # Without the returned id we can't dedup on re-run — fail
                    # loudly rather than silently duplicate the ledger entry.
                    result.failed += 1
                    result.errors.append(f"{cid}: InsertTask returned no database_id ({resp!r})")
                    log.warning("Case %s: InsertTask returned no task id (%r)", cid, resp)
                    continue
                _stamp_synced(espo, cid, task_id)
                result.created += 1

        except (EspoClientError, NowCertsClientError) as exc:
            result.failed += 1
            result.errors.append(f"{case.get('id', '?')}: {exc}")
            log.warning("Case %s write-back failed: %s", cid, exc)

    log.info(result.message)
    return result
