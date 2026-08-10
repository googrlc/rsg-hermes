"""NowCerts picklist options — CRM stores option_id, not free-form labels."""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

log = logging.getLogger(__name__)

TABLE = "nowcerts_picklist_options"

LIST_PIPELINE_NB = "pipeline_new_business"
LIST_PIPELINE_RN = "pipeline_renewal"
LIST_LEAD_STATUS = "lead_status"
LIST_RENEWAL_STATUS = "renewal_status"
LIST_ENDORSEMENT = "endorsement_type"


def stable_option_id(list_key: str, label: str) -> str:
    h = hashlib.sha1(f"{list_key}:{label}".encode()).hexdigest()
    return str(uuid.UUID(h[:32]))


def list_options(supa, list_key: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    params: dict[str, str] = {"list_key": f"eq.{list_key}", "order": "sort_order.asc"}
    if active_only:
        params["active"] = "eq.true"
    try:
        return list(
            supa.select(
                TABLE,
                columns="list_key,option_id,label,sort_order,active",
                params=params,
                limit=500,
            )
        )
    except Exception:  # noqa: BLE001
        log.warning("picklist %s unavailable", list_key)
        return []


def resolve(
    supa,
    list_key: str,
    *,
    option_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any] | None:
    oid = (option_id or "").strip()
    lab = (label or "").strip()
    rows = list_options(supa, list_key, active_only=not bool(oid))
    if oid:
        for row in rows:
            if str(row.get("option_id")) == oid:
                return {"option_id": str(row["option_id"]), "label": row.get("label")}
        # also search inactive
        rows = list_options(supa, list_key, active_only=False)
        for row in rows:
            if str(row.get("option_id")) == oid:
                return {"option_id": str(row["option_id"]), "label": row.get("label")}
        return None
    if lab:
        for row in rows:
            if str(row.get("label") or "").strip().lower() == lab.lower():
                return {"option_id": str(row["option_id"]), "label": row.get("label")}
    return None


def require_option(
    supa,
    list_key: str,
    *,
    option_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    hit = resolve(supa, list_key, option_id=option_id, label=label)
    if not hit:
        raise ValueError(
            f"Unknown {list_key} option "
            f"(pass a NowCerts option_id, or a seeded label). "
            f"got option_id={option_id!r} label={label!r}"
        )
    return hit


def pipeline_list_for_type(opportunity_type: str | None) -> str:
    t = (opportunity_type or "").strip().lower()
    if "renew" in t:
        return LIST_PIPELINE_RN
    return LIST_PIPELINE_NB
