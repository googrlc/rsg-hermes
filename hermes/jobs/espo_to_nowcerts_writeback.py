"""Espo -> NowCerts write-back — mirror EspoCRM service Cases and client-linked
Tasks into the NowCerts task ledger (the AMS is the historic system of record).

Governance ([[rsg-ams-source-of-truth-governance]]): NowCerts is the source of
truth; EspoCRM writes UP only through narrow, additive channels. This job covers
two of them:

* **Cases** — every service-request Case becomes a NowCerts Task. The Case
  service-request ``type`` rides along as ``category_name``.
* **Tasks** — client-linked Tasks become NowCerts Tasks (``taskType`` ->
  ``category_name``), EXCEPT internal auto-generated ones (``syncSource`` in
  ``_TASK_SKIP_SOURCES``) which are workflow prompts, not client-service records.

For both: the linked Account's ``momentum_client_id`` is the
``insured_database_id``; idempotency via ``<entity>.momentumTaskId`` (create then
update). Client-linked only — no GUID means no client in the AMS, so skip. Never
deletes, never overwrites an AMS field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from hermes.core.client import EspoClient, EspoClientError
from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError

log = logging.getLogger(__name__)

# Entity status -> NowCerts task ledger state (Open unless in the closed set).
_CASE_CLOSED = frozenset({"Closed", "Cancelled"})
_TASK_CLOSED = frozenset({"Completed", "Cancelled"})
# Priority/urgency -> NowCerts task priority.
_PRIORITY_MAP = {"Low": "Low", "Normal": "Medium", "High": "High", "Urgent": "High"}
# Auto-generated internal Task sources that should NOT reach the AMS ledger.
_TASK_SKIP_SOURCES = frozenset({"Hermes"})

_CASE_SELECT = ",".join([
    "id", "name", "number", "status", "type", "description", "priority",
    "accountId", "accountName", "assignedUserName",
    "createdAt", "modifiedAt", "momentumTaskId", "momentumLastSynced",
])
_TASK_SELECT = ",".join([
    "id", "name", "status", "taskType", "urgency", "priority", "description",
    "accountId", "accountName", "parentId", "parentType", "assignedUserName",
    "policyNumber", "dateEnd", "createdAt", "modifiedAt", "syncSource",
    "momentumTaskId", "momentumLastSynced",
])


@dataclass
class WritebackResult:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped_no_client: int = 0
    skipped_internal: int = 0
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
            f"{prefix}Espo->NowCerts write-back: {self.total} scanned, "
            f"{self.created} created, {self.updated} updated, "
            f"{self.skipped_no_client} skipped (no client GUID), "
            f"{self.skipped_internal} internal skipped, {self.failed} failed."
        )


def _cutoff(since_hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M:%S")


def _now_espo() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _extract_task_id(resp: Any) -> str | None:
    """Pull the task database_id out of an InsertTask response.

    NowCerts wraps the created task under ``data``:
    ``{"status": 1, "data": {"database_id": "...", ...}, "message": "..."}``.
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


# ── per-entity client-account resolvers ───────────────────────────────────

def _case_account(case: dict[str, Any]) -> str | None:
    return case.get("accountId")


def _task_account(task: dict[str, Any]) -> str | None:
    if task.get("accountId"):
        return str(task["accountId"])
    if task.get("parentType") == "Account" and task.get("parentId"):
        return str(task["parentId"])
    return None


# ── per-entity NowCerts payload builders (snake_case bodies) ───────────────

def _case_payload(case: dict[str, Any], insured_guid: str) -> dict[str, Any]:
    status = (case.get("status") or "New").strip()
    priority = _PRIORITY_MAP.get((case.get("priority") or "Normal").strip(), "Medium")
    due = (case.get("createdAt") or "")[:10] or _today()
    payload: dict[str, Any] = {
        "title": case.get("name") or f"Service Request {case.get('number', '')}".strip(),
        "description": case.get("description") or "",
        "status": "Closed" if status in _CASE_CLOSED else "Open",
        "priority": priority,
        "due_date": due,
        "category_name": case.get("type") or "Other",
        "insured_database_id": insured_guid,
    }
    if case.get("assignedUserName"):
        payload["assigned_to"] = [case["assignedUserName"]]
    if case.get("momentumTaskId"):
        payload["database_id"] = case["momentumTaskId"]
    return payload


def _task_payload(task: dict[str, Any], insured_guid: str) -> dict[str, Any]:
    status = (task.get("status") or "Inbox").strip()
    raw_pri = (task.get("urgency") or task.get("priority") or "Normal").strip()
    priority = _PRIORITY_MAP.get(raw_pri, "Medium")
    due = (task.get("dateEnd") or task.get("createdAt") or "")[:10] or _today()
    payload: dict[str, Any] = {
        "title": task.get("name") or "Client task",
        "description": task.get("description") or "",
        "status": "Closed" if status in _TASK_CLOSED else "Open",
        "priority": priority,
        "due_date": due,
        "category_name": task.get("taskType") or "Client Service",
        "insured_database_id": insured_guid,
    }
    if task.get("policyNumber"):
        payload["policy_number"] = task["policyNumber"]
    if task.get("assignedUserName"):
        payload["assigned_to"] = [task["assignedUserName"]]
    if task.get("momentumTaskId"):
        payload["database_id"] = task["momentumTaskId"]
    return payload


# ── Espo helpers ───────────────────────────────────────────────────────────

def _fetch_modified(espo: EspoClient, entity: str, select: str, cutoff: str, max_size: int) -> list[dict[str, Any]]:
    body = espo.get(entity, params={
        "maxSize": max_size,
        "select": select,
        "where": [{"type": "after", "attribute": "modifiedAt", "value": cutoff}],
        "orderBy": "modifiedAt",
        "order": "desc",
    })
    return body.get("list", []) if isinstance(body, dict) else []


def _account_guid(espo: EspoClient, account_id: str) -> str | None:
    rec = espo.get(f"Account/{account_id}", params={"select": "id,momentum_client_id"})
    if isinstance(rec, dict):
        guid = rec.get("momentum_client_id")
        return str(guid) if guid else None
    return None


def _stamp_synced(espo: EspoClient, entity: str, rec_id: str, task_id: str) -> None:
    espo.patch(f"{entity}/{rec_id}", json={"momentumTaskId": task_id, "momentumLastSynced": _now_espo()})


def _process(
    espo: EspoClient,
    nowcerts: NowCertsClient,
    entity: str,
    records: list[dict[str, Any]],
    account_fn: Callable[[dict[str, Any]], str | None],
    payload_fn: Callable[[dict[str, Any], str], dict[str, Any]],
    guid_cache: dict[str, str | None],
    dry_run: bool,
    result: WritebackResult,
) -> None:
    for rec in records:
        rid = rec.get("id")
        try:
            account_id = account_fn(rec)
            if not account_id:
                result.skipped_no_client += 1
                continue
            if account_id not in guid_cache:
                guid_cache[account_id] = _account_guid(espo, account_id)
            guid = guid_cache[account_id]
            if not guid:
                result.skipped_no_client += 1
                continue

            payload = payload_fn(rec, guid)
            is_update = bool(rec.get("momentumTaskId"))

            if dry_run:
                log.info("[DRY RUN] would %s NowCerts task for %s %s (%s) type=%s",
                         "update" if is_update else "create", entity, rid,
                         rec.get("name"), payload["category_name"])
                result.updated += is_update
                result.created += not is_update
                continue

            if is_update:
                nowcerts.update_task(payload)
                _stamp_synced(espo, entity, rid, rec["momentumTaskId"])
                result.updated += 1
            else:
                resp = nowcerts.insert_task(payload)
                task_id = _extract_task_id(resp)
                if not task_id:
                    result.failed += 1
                    result.errors.append(f"{entity} {rid}: InsertTask returned no database_id ({resp!r})")
                    log.warning("%s %s: InsertTask returned no task id (%r)", entity, rid, resp)
                    continue
                _stamp_synced(espo, entity, rid, task_id)
                result.created += 1

        except (EspoClientError, NowCertsClientError) as exc:
            result.failed += 1
            result.errors.append(f"{entity} {rid}: {exc}")
            log.warning("%s %s write-back failed: %s", entity, rid, exc)


def run_writeback(
    espo: EspoClient | None = None,
    nowcerts: NowCertsClient | None = None,
    *,
    dry_run: bool = False,
    since_hours: int = 24,
    max_size: int = 200,
    include_tasks: bool = True,
) -> WritebackResult:
    """Write EspoCRM service Cases (and client-linked Tasks) to the NowCerts ledger."""
    espo = espo or EspoClient()
    nowcerts = nowcerts or NowCertsClient()
    result = WritebackResult(dry_run=dry_run)
    cutoff = _cutoff(since_hours)
    guid_cache: dict[str, str | None] = {}

    # Cases channel.
    cases = _fetch_modified(espo, "Case", _CASE_SELECT, cutoff, max_size)
    result.total += len(cases)
    log.info("Cases->NowCerts: %d case(s) modified since %s", len(cases), cutoff)
    _process(espo, nowcerts, "Case", cases, _case_account, _case_payload, guid_cache, dry_run, result)

    # Tasks channel (client-linked, excluding internal auto-generated).
    if include_tasks:
        tasks = _fetch_modified(espo, "Task", _TASK_SELECT, cutoff, max_size)
        kept = [t for t in tasks if (t.get("syncSource") or "") not in _TASK_SKIP_SOURCES]
        result.skipped_internal += len(tasks) - len(kept)
        result.total += len(kept)
        log.info("Tasks->NowCerts: %d client task(s) since %s (%d internal skipped)",
                 len(kept), cutoff, len(tasks) - len(kept))
        _process(espo, nowcerts, "Task", kept, _task_account, _task_payload, guid_cache, dry_run, result)

    log.info(result.message)
    return result
