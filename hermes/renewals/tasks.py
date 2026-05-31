"""Create EspoCRM Tasks for Renewal records, assigned to Gretchen.

EspoClient seams (confirmed in hermes/core/client.py):
  - get(path, *, params=...)  -> raw body dict; use it for where-filtered reads
    (the built-in search() only does name-contains and returns a bare list, so
    we go through get() for structured where queries, like find_one_by_field does)
  - create(entity, payload)   -> created record dict (has "id")
  - find_one_by_field(entity, field, value) -> record dict or None
"""
from __future__ import annotations

import logging
from typing import Any

from . import config
from .card import build_card

log = logging.getLogger(__name__)


def _rows(body: Any) -> list[dict]:
    """Normalize an EspoCRM list response to a list of record dicts."""
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [r for r in body["list"] if isinstance(r, dict)]
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    return []


def resolve_gretchen_id(espo) -> str | None:
    """Return Gretchen's EspoCRM user id (explicit env override, else live lookup)."""
    if config.GRETCHEN_USER_ID:
        return config.GRETCHEN_USER_ID
    user = espo.find_one_by_field("User", "userName", config.GRETCHEN_USERNAME)
    if user:
        return user.get("id")
    log.warning("Could not resolve Gretchen by userName=%s; task will be unassigned",
                config.GRETCHEN_USERNAME)
    return None


def has_existing_task(espo, renewal_id: str) -> bool:
    """True if a renewal-prep task already exists for this Renewal (dedup)."""
    body = espo.get(config.TASK_ENTITY, params={
        "maxSize": 1,
        "select": "id",
        "where": [
            {"type": "equals", "attribute": "parentType", "value": config.RENEWAL_ENTITY},
            {"type": "equals", "attribute": "parentId", "value": renewal_id},
            {"type": "equals", "attribute": "taskType", "value": config.TASK_TYPE_RENEWAL},
        ],
    })
    return bool(_rows(body))


def create_renewal_task(espo, renewal: dict, assignee_id: str | None) -> dict | None:
    renewal_id = renewal.get("id")
    account = renewal.get("accountName") or renewal.get("name") or "Client"
    lob = renewal.get("lineOfBusiness") or "Renewal"

    payload = {
        "name": f"Renewal prep — {account} ({lob})",
        "status": config.TASK_STATUS_INBOX,
        "priority": "Normal",
        "taskType": config.TASK_TYPE_RENEWAL,
        "taskSource": config.TASK_SOURCE_ACCOUNT,
        "syncSource": config.TASK_SYNC_SOURCE,
        "parentType": config.RENEWAL_ENTITY,
        "parentId": renewal_id,
        "description": build_card(renewal),
    }
    if renewal.get("accountId"):
        payload["accountId"] = renewal["accountId"]
    if assignee_id:
        payload["assignedUserId"] = assignee_id

    task = espo.create(config.TASK_ENTITY, payload)
    task = task if isinstance(task, dict) else None
    log.info("Created renewal task for Renewal %s -> Task %s",
             renewal_id, (task or {}).get("id"))
    return task
