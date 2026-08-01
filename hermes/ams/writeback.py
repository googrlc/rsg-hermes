"""Push a CRM correction to NowCerts, keyed on the record's AMS identifier.

The CRM has been able to correct a client or a policy for a while, but the
correction stayed in the portal: an override that outranks the mirror until the
AMS catches up on its own — which, for a typo in a phone number, is never. This
is the other half. The record's NowCerts GUID is the whole reason it can exist:
``bfe42b77-…`` identifies the insured, ``policy_guid`` identifies the policy, and
both endpoints upsert on exactly that.

Shape of one push, borrowed from the renewal executor's job contract:

    stage (approved) → read NowCerts → write → read back → verify → record

The queue row is the durable part. It is written **before** the AMS call and
closed after, so a push that dies mid-flight — NowCerts down, container
restarted — leaves a row that says so and can be retried, rather than a change
that silently never happened. That matters more here than it looks: the renewal
executor's cron is not enabled on the box, so nothing else would ever come along
and drain it.

Identifiers are never in the payload. They are how the AMS finds the record; a
correction that rewrites one is not a correction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes_core.queue import (
    DESTINATION_NOWCERTS,
    QUEUE_COMPLETED,
    QUEUE_FAILED,
    QUEUE_QUEUED,
    QUEUE_TABLE,
)

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient
    from hermes_integrations.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)

OBJECT_TYPE_CLIENT = "client"
OBJECT_TYPE_POLICY = "policy"

# CRM column → NowCerts field. Only what is listed here can be pushed; anything
# else is refused by name rather than dropped silently, so a caller finds out.
#
# The NowCerts names are the connector's PascalCase common fields, matching
# hermes/intake/nowcerts_map.py (insured) and hermes/quotes/executor.py (policy)
# — the two payload builders already proven against the live API.
# `active` is absent on purpose, on both sides. A client's active flag is
# recomputed by a database trigger from that client's policies; a policy's is
# derived from its status. Neither is a field you set — you bind or cancel a
# policy in the AMS and both follow.
CLIENT_FIELD_MAP = {
    "insured_name": "CommercialName",
    "email": "EMail",
    "phone": "PhoneNumber",
    "address": "AddressLine1",
    "city": "City",
    "state": "State",
    "zip": "Zip",
}
POLICY_FIELD_MAP = {
    "carrier": "CarrierName",
    "lines_of_business": "LineOfBusinessName",
    "premium_amount": "Premium",
    "annualized_premium": "Premium",
    "effective_date": "EffectiveDate",
    "expiration_date": "ExpirationDate",
}
FIELD_MAPS = {OBJECT_TYPE_CLIENT: CLIENT_FIELD_MAP, OBJECT_TYPE_POLICY: POLICY_FIELD_MAP}

# Fields whose value must reach NowCerts as a number, not the string an <input>
# hands back. NowCerts silently ignores a premium of "4200".
_NUMERIC = {"Premium"}


class AmsWritebackError(Exception):
    """The push did not reach NowCerts, or reached it unverified."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def map_fields(object_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    """CRM field names → a NowCerts payload fragment (no identifiers).

    Raises ValueError naming any field that has no mapping, rather than dropping
    it: a Save that reports success while quietly discarding half the change is
    worse than one that fails.
    """
    field_map = FIELD_MAPS.get(object_type)
    if field_map is None:
        raise ValueError(f"unknown object_type {object_type!r}")

    unknown = [k for k in fields if k not in field_map]
    if unknown:
        raise ValueError(
            f"cannot push {sorted(unknown)} to NowCerts; pushable fields are "
            f"{sorted(field_map)}"
        )

    out: dict[str, Any] = {}
    for key, value in fields.items():
        target = field_map[key]
        if value in (None, ""):
            out[target] = None
            continue
        if target in _NUMERIC:
            try:
                out[target] = float(str(value).replace(",", "").replace("$", ""))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number, got {value!r}")
        else:
            out[target] = str(value)
    return out


def _stage(
    supa: "SupabaseClient",
    *,
    object_type: str,
    object_id: str,
    fields: dict[str, Any],
    payload_fields: dict[str, Any],
    actor: str,
) -> str | None:
    """Write the queue row that makes this push durable. Approved on the spot —
    the human gate is the confirmation the portal takes before calling us."""
    now = _utcnow_iso()
    try:
        row = supa.insert(QUEUE_TABLE, {
            "object_type": object_type,
            "object_id": object_id,
            "destination_system": DESTINATION_NOWCERTS,
            "action": "update",
            "payload": {
                "crm_fields": fields,
                "nowcerts_fields": payload_fields,
                "expected_result": f"NowCerts {object_type} {object_id} reflects "
                                   f"{sorted(fields)}",
            },
            "status": QUEUE_QUEUED,
            "attempt_count": 1,
            "approved_by": actor,
            "approved_at": now,
        })
    except Exception:  # noqa: BLE001 — see below; the push is still worth trying
        # A queue row we could not write costs us the retry, not the write. Push
        # anyway and say so in the log: refusing to correct the AMS because the
        # bookkeeping table was unavailable helps nobody.
        log.exception("ams.writeback: could not stage queue row for %s %s", object_type, object_id)
        return None
    return row.get("id") if isinstance(row, dict) else None


def _close(supa: "SupabaseClient", queue_id: str | None, status: str, error: str | None = None) -> None:
    if not queue_id:
        return
    payload: dict[str, Any] = {"status": status, "updated_at": _utcnow_iso()}
    if error:
        payload["last_error"] = error[:2000]
    try:
        supa.update(QUEUE_TABLE, queue_id, payload)
    except Exception:  # noqa: BLE001
        log.exception("ams.writeback: could not close queue row %s", queue_id)


def _audit(
    supa: "SupabaseClient",
    *,
    object_type: str,
    object_id: str,
    before: Any,
    after: Any,
    actor: str,
    note: str | None,
) -> None:
    from hermes.overrides.store import write_log

    write_log(
        supa,
        entity_type=f"nowcerts_{object_type}",
        entity_key=object_id,
        action="ams_push",
        actor=actor,
        before=before,
        after=after,
        note=note,
    )


def _verify(after: dict[str, Any] | None, sent: dict[str, Any]) -> tuple[bool, list[str]]:
    """Did the read-back actually come back carrying what we sent?

    NowCerts returns its own casing, so compare case-insensitively on the key and
    value-wise on the content. An unverified push is not a failed one — the write
    may well have landed — so the caller reports it rather than raising.
    """
    if not after:
        return False, ["no record returned on read-back"]
    lowered = {str(k).lower(): v for k, v in after.items()}
    mismatched: list[str] = []
    for key, expected in sent.items():
        got = lowered.get(key.lower())
        if expected is None:
            continue
        if isinstance(expected, float):
            try:
                if abs(float(got) - expected) > 0.01:
                    mismatched.append(key)
            except (TypeError, ValueError):
                mismatched.append(key)
        elif str(got or "").strip().casefold() != str(expected).strip().casefold():
            mismatched.append(key)
    return (not mismatched), mismatched


def push(
    supa: "SupabaseClient",
    nowcerts: "NowCertsClient",
    *,
    object_type: str,
    object_id: str,
    fields: dict[str, Any],
    actor: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Push one record's corrected fields to NowCerts. Returns the outcome.

    Never raises for an AMS failure — the CRM correction has already been saved
    and is still true; this reports whether the AMS caught up, so the portal can
    say "saved, not yet synced" instead of pretending either way.
    """
    if object_type not in FIELD_MAPS:
        raise ValueError(f"unknown object_type {object_type!r}")
    if not object_id:
        raise ValueError("object_id (the NowCerts identifier) is required")
    if not fields:
        raise ValueError("nothing to push")

    payload_fields = map_fields(object_type, fields)   # raises on unknown fields
    queue_id = _stage(supa, object_type=object_type, object_id=object_id,
                      fields=fields, payload_fields=payload_fields, actor=actor)

    before: dict[str, Any] | None = None
    try:
        before = _read(nowcerts, object_type, object_id)
        if before is None:
            raise AmsWritebackError(
                f"could not confirm {object_type} {object_id} in NowCerts — not pushing. "
                f"The correction is saved in the CRM; nothing was written to the AMS."
            )
        _write(nowcerts, object_type, object_id, payload_fields)
        after = _read(nowcerts, object_type, object_id)
    except Exception as exc:  # noqa: BLE001 — reported, never fatal to the save
        log.exception("ams.writeback: push failed for %s %s", object_type, object_id)
        _close(supa, queue_id, QUEUE_FAILED, str(exc))
        _audit(supa, object_type=object_type, object_id=object_id, before=before,
               after=None, actor=actor, note=f"failed: {exc}")
        return {"pushed": False, "verified": False, "queue_id": queue_id,
                "error": str(exc), "fields": sorted(fields)}

    verified, mismatched = _verify(after, payload_fields)
    _close(supa, queue_id, QUEUE_COMPLETED if verified else QUEUE_FAILED,
           None if verified else f"unverified: {mismatched}")
    _audit(supa, object_type=object_type, object_id=object_id, before=before,
           after=after, actor=actor, note=note)
    return {"pushed": True, "verified": verified, "queue_id": queue_id,
            "unverified_fields": mismatched, "fields": sorted(fields)}


# Reading one record out of NowCerts by its GUID is less certain than it sounds:
# ``is_insured_active`` notes that $filter on the insured id is not reliably
# supported, and the two list endpoints spell the key differently. So try each
# spelling in turn rather than trusting one.
_READ_ATTEMPTS = {
    OBJECT_TYPE_CLIENT: (
        ("/api/InsuredList", "id"),
        ("/api/InsuredDetailList", "databaseId"),
    ),
    OBJECT_TYPE_POLICY: (
        ("/api/PolicyDetailList", "databaseId"),
        ("/api/PolicyDetailList", "id"),
    ),
}


def list_failed(supa: "SupabaseClient", *, limit: int = 50) -> list[dict[str, Any]]:
    """Pushes that did not land. The portal shows these, because a correction the
    AMS never took is invisible otherwise: the CRM shows the corrected value, so
    the record looks right on the screen where you would go to check it."""
    rows = supa.select(
        QUEUE_TABLE, columns="*",
        params={
            "object_type": f"in.({OBJECT_TYPE_CLIENT},{OBJECT_TYPE_POLICY})",
            "destination_system": f"eq.{DESTINATION_NOWCERTS}",
            "status": f"eq.{QUEUE_FAILED}",
            "order": "updated_at.desc",
        },
        limit=limit,
    )
    return rows


def retry(
    supa: "SupabaseClient", nowcerts: "NowCertsClient", *, queue_id: str, actor: str
) -> dict[str, Any]:
    """Re-drive one failed push from its own queue row.

    Repeating a push is safe by construction — both AMS endpoints upsert on
    DatabaseId — so this replays the recorded fields rather than asking the
    caller to remember what they were.
    """
    rows = supa.select(QUEUE_TABLE, columns="*", params={"id": f"eq.{queue_id}"}, limit=1)
    if not rows:
        raise ValueError(f"queue row {queue_id} not found")
    job = rows[0]
    if job.get("object_type") not in FIELD_MAPS:
        raise ValueError("only client and policy AMS pushes are retried here")
    if job.get("status") != QUEUE_FAILED:
        raise ValueError(f"job status is {job.get('status')!r}; only failed pushes can be retried")
    payload = job.get("payload") or {}
    fields = payload.get("crm_fields") or {}
    if not fields:
        raise ValueError("queue row carries no fields to replay")
    return push(supa, nowcerts, object_type=job["object_type"], object_id=job["object_id"],
                fields=fields, actor=actor, note=f"retry of {queue_id}")


def _read(nowcerts: "NowCertsClient", object_type: str, object_id: str) -> dict[str, Any] | None:
    """The mandatory read-before / read-after, by GUID.

    Returns None when nothing resolves — which the caller treats as a stop. That
    is deliberate and it is the important safety property here: ``Insured/Insert``
    upserts on DatabaseId **or CommercialName**, so pushing a name against a GUID
    the AMS cannot confirm is exactly how you mint another duplicate insured into
    a book that already has them.
    """
    quoted = str(object_id).replace("'", "''")   # OData single-quote escape
    for path, key in _READ_ATTEMPTS[object_type]:
        try:
            body = nowcerts._get(path, params={"$filter": f"{key} eq '{quoted}'"})
        except Exception:  # noqa: BLE001 — an unsupported filter is a miss, not a failure
            continue
        rows = body if isinstance(body, list) else (body or {}).get("value", [])
        if rows and isinstance(rows[0], dict):
            return rows[0]
    return None


def _write(nowcerts: "NowCertsClient", object_type: str, object_id: str,
           payload_fields: dict[str, Any]) -> dict[str, Any]:
    """Both endpoints upsert on DatabaseId, which is why this is safe to repeat."""
    payload = {"DatabaseId": object_id, **payload_fields}
    if object_type == OBJECT_TYPE_CLIENT:
        return nowcerts.create_insured(payload)
    return nowcerts.update_policy(payload)
