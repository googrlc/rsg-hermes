"""Record gateway AMS writes into portal_write_log + outbound_sync_queue."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from hermes_core.overrides.store import write_log
from hermes_core.queue import (
    DESTINATION_NOWCERTS,
    OBJECT_TYPE_INTAKE_AMS,
    QUEUE_COMPLETED,
    QUEUE_TABLE,
)

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_intake_ams_write(supa, payload: dict[str, Any]) -> dict[str, Any]:
    """Audit a gateway AMS create/adopt into portal_write_log + completed queue row."""
    insured_id = str(payload.get("object_id") or payload.get("insured_database_id") or "").strip()
    if not insured_id:
        raise ValueError("object_id (NowCerts insured GUID) is required")
    action = str(payload.get("action") or "create").strip() or "create"
    actor = str(payload.get("approved_by") or payload.get("actor") or "cptintake").strip()
    adopted = bool(payload.get("adopted"))
    note = (
        f"intake gateway {'adopt' if adopted else action}; "
        f"source={payload.get('source') or 'cptintake_gateway'}; "
        f"fingerprint={payload.get('fingerprint') or '-'}"
    )

    write_log(
        supa,
        entity_type="client",
        entity_key=insured_id,
        action="ams_push",
        actor=actor,
        field_name=None,
        before={"adopted": adopted},
        after={"verified": payload.get("verified"), "fingerprint": payload.get("fingerprint")},
        note=note,
    )

    row = supa.insert(
        QUEUE_TABLE,
        {
            "object_type": OBJECT_TYPE_INTAKE_AMS,
            "object_id": insured_id,
            "destination_system": DESTINATION_NOWCERTS,
            "action": "adopt" if adopted else action,
            "payload": {
                "source": payload.get("source") or "cptintake_gateway",
                "fingerprint": payload.get("fingerprint"),
                "adopted": adopted,
                "verified": payload.get("verified"),
            },
            "status": QUEUE_COMPLETED,
            "attempt_count": 1,
            "approved_by": actor,
            "approved_at": _utcnow(),
            "updated_at": _utcnow(),
        },
    )
    return {"ok": True, "queue_id": row.get("id"), "insured_database_id": insured_id}
