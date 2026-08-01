"""Persistence for portal overrides — the I/O half of ``overrides.core``.

Every mutation writes a ``portal_write_log`` row. The log is the record; the
override table is only current state. If you add a path that changes an
override without logging it, you have removed the reason this exists.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes_core.overrides.core import (
    ACTION_CONFLICT,
    ACTION_RETIRE,
    STATUS_ACTIVE,
    STATUS_CONFLICTED,
    STATUS_RETIRED,
    Override,
    index_overrides,
    reconcile,
)

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

OVERRIDES_TABLE = "portal_overrides"
WRITE_LOG_TABLE = "portal_write_log"

ACT_SET = "override_set"
ACT_SUPERSEDED = "override_superseded"
ACT_RETIRED = "override_retired"
ACT_CONFLICTED = "override_conflicted"
ACT_WITHDRAWN = "override_withdrawn"

REASON_SUPERSEDED = "superseded"
REASON_WITHDRAWN = "withdrawn"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_log(
    supa: "SupabaseClient",
    *,
    entity_type: str,
    entity_key: str,
    action: str,
    actor: str,
    field_name: str | None = None,
    before: Any = None,
    after: Any = None,
    note: str | None = None,
) -> None:
    """Append to the audit log. Best-effort: a logging failure must not roll back
    a write that already happened — but it is always reported."""
    try:
        supa.insert(WRITE_LOG_TABLE, {
            "entity_type": entity_type,
            "entity_key": entity_key,
            "field_name": field_name,
            "action": action,
            "before_value": before,
            "after_value": after,
            "actor": actor,
            "note": note,
        })
    except Exception:  # noqa: BLE001
        log.exception(
            "portal_write_log failed: %s %s.%s by %s",
            action, entity_type, field_name, actor,
        )


def active_overrides(
    supa: "SupabaseClient",
    entity_type: str,
    *,
    limit: int = 5000,
) -> dict[tuple[str, str, str], Override]:
    """(entity_type, entity_key, field) -> active Override."""
    rows = supa.select(
        OVERRIDES_TABLE,
        columns="*",
        params={"entity_type": f"eq.{entity_type}", "status": f"eq.{STATUS_ACTIVE}"},
        limit=limit,
    )
    return index_overrides(rows)


def _find_active(
    supa: "SupabaseClient", entity_type: str, entity_key: str, field_name: str
) -> dict[str, Any] | None:
    rows = supa.select(
        OVERRIDES_TABLE,
        columns="*",
        params={
            "entity_type": f"eq.{entity_type}",
            "entity_key": f"eq.{entity_key}",
            "field_name": f"eq.{field_name}",
            "status": f"eq.{STATUS_ACTIVE}",
        },
        limit=1,
    )
    return rows[0] if rows else None


def set_override(
    supa: "SupabaseClient",
    *,
    entity_type: str,
    entity_key: str,
    field_name: str,
    override_value: Any,
    original_value: Any,
    approved_by: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create an override, superseding any active one on the same field.

    ``original_value`` must be the value the SOURCE currently reports — not the
    previous override. Reconciliation compares against it to decide whether the
    AMS has caught up, so passing the wrong thing makes the override immortal.
    """
    entity_key = str(entity_key).strip()
    if not entity_key or not field_name:
        raise ValueError("entity_key and field_name are required")
    if not approved_by:
        raise ValueError("approved_by is required — an override is a named decision")

    existing = _find_active(supa, entity_type, entity_key, field_name)
    if existing:
        supa.update(OVERRIDES_TABLE, existing["id"], {
            "status": STATUS_RETIRED,
            "retired_at": _now(),
            "retired_reason": REASON_SUPERSEDED,
            "updated_at": _now(),
        })
        write_log(
            supa, entity_type=entity_type, entity_key=entity_key,
            field_name=field_name, action=ACT_SUPERSEDED, actor=approved_by,
            before=existing.get("override_value"), after=override_value,
        )

    row = supa.insert(OVERRIDES_TABLE, {
        "entity_type": entity_type,
        "entity_key": entity_key,
        "field_name": field_name,
        "original_value": original_value,
        "override_value": override_value,
        "status": STATUS_ACTIVE,
        "reason": reason,
        "approved_by": approved_by,
        "approved_at": _now(),
    })
    write_log(
        supa, entity_type=entity_type, entity_key=entity_key,
        field_name=field_name, action=ACT_SET, actor=approved_by,
        before=original_value, after=override_value, note=reason,
    )
    return row


def withdraw(
    supa: "SupabaseClient", override_id: str, *, actor: str, note: str | None = None
) -> dict[str, Any]:
    """Retire an override by hand — the correction was wrong, or no longer wanted."""
    rows = supa.select(OVERRIDES_TABLE, columns="*",
                       params={"id": f"eq.{override_id}"}, limit=1)
    if not rows:
        raise ValueError(f"override {override_id} not found")
    current = rows[0]
    updated = supa.update(OVERRIDES_TABLE, override_id, {
        "status": STATUS_RETIRED,
        "retired_at": _now(),
        "retired_reason": REASON_WITHDRAWN,
        "updated_at": _now(),
    })
    write_log(
        supa, entity_type=current["entity_type"], entity_key=current["entity_key"],
        field_name=current.get("field_name"), action=ACT_WITHDRAWN, actor=actor,
        before=current.get("override_value"), after=current.get("original_value"),
        note=note,
    )
    return updated


def reconcile_overrides(
    supa: "SupabaseClient",
    entity_type: str,
    source_values: dict[tuple[str, str], Any],
    *,
    actor: str = "sync",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compare every active override against fresh source values.

    ``source_values`` maps ``(entity_key, field_name) -> current source value``.
    A field absent from the map is left alone — the source not reporting it is
    not evidence the override is stale.

    Retires the ones the source has caught up to, flags the ones where it moved
    somewhere unexpected, leaves the rest. ``dry_run`` reports without writing.
    """
    summary: dict[str, Any] = {
        "checked": 0, "retired": 0, "conflicted": 0, "kept": 0,
        "dry_run": dry_run, "details": [],
    }

    for (etype, key, field), override in active_overrides(supa, entity_type).items():
        if (key, field) not in source_values:
            summary["kept"] += 1
            continue

        summary["checked"] += 1
        source_value = source_values[(key, field)]
        outcome = reconcile(source_value, override)

        if outcome.action == ACTION_RETIRE:
            summary["retired"] += 1
            summary["details"].append(
                {"key": key, "field": field, "action": "retire", "source": source_value}
            )
            if not dry_run:
                supa.update(OVERRIDES_TABLE, override.id, {
                    "status": STATUS_RETIRED,
                    "retired_at": _now(),
                    "retired_reason": outcome.reason,
                    "updated_at": _now(),
                })
                write_log(
                    supa, entity_type=etype, entity_key=key, field_name=field,
                    action=ACT_RETIRED, actor=actor,
                    before=override.override_value, after=source_value,
                    note=outcome.reason,
                )
        elif outcome.action == ACTION_CONFLICT:
            summary["conflicted"] += 1
            summary["details"].append(
                {"key": key, "field": field, "action": "conflict",
                 "source": source_value, "override": override.override_value}
            )
            if not dry_run:
                supa.update(OVERRIDES_TABLE, override.id, {
                    "status": STATUS_CONFLICTED,
                    "conflict_value": source_value,
                    "updated_at": _now(),
                })
                write_log(
                    supa, entity_type=etype, entity_key=key, field_name=field,
                    action=ACT_CONFLICTED, actor=actor,
                    before=override.override_value, after=source_value,
                    note=outcome.reason,
                )
        else:
            summary["kept"] += 1

    return summary
