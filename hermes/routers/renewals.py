"""Renewals — the desk's own data surface.

The forward-looking renewal worklist plus the corrections applied on top of it:
field overrides, dismissals, and their withdrawal.

None of this writes to the AMS. A correction here is a `portal_overrides` row
keyed on policy_number, and a dismissal excludes the candidate — both durable,
named and reversible, because the nightly re-projection rebuilds
`renewal_candidates` and would otherwise revert a bare row edit. The AMS writes
live in the renewal executor, behind the approved-queue contract.

Split out of hermes/api.py under docs/repo-split-plan.md Phase 2. Renewals is
the most connected of the six domains, so this router is deliberately only the
HTTP surface — the module-level split (renewals/cases.py holds generic case and
task persistence that belongs with cases) is still outstanding and blocks the
repo extraction, not this.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermes_app import deps

log = logging.getLogger(__name__)

router = APIRouter()


def _renewal_outcome(r: dict) -> str:
    """Won (retained) / Lost / Open, from the candidate's lineage + status."""
    ns = str(r.get("normalized_status") or "").strip().lower()
    if r.get("successor_policy_number") or ns == "renewed":
        return "Won"
    if ns in _RENEWAL_LOST:
        return "Lost"
    return "Open"


# Personal-lines LOBs get a tight 30-day renewal window; everything else
# (commercial) gets 120 days. Keyed off line_of_business because the `segment`
# column mislabels every personal policy as commercial.
_PERSONAL_LOB_RE = re.compile(
    r"(personal auto|personalauto|personsl auto|homeowner|dwelling fire|"
    r"motorcycle|personal umbrella|condo owners)",
    re.I,
)
def _env_int(name: str, default: int) -> int:
    """Read an int from env, falling back to default on unset/blank/garbage."""
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        log.warning("invalid int for %s=%r; using %d", name, os.getenv(name), default)
        return default


# Forward-look windows, tunable via env (set in the box .env, then restart).
_PERSONAL_WINDOW_DAYS = _env_int("RENEWAL_WINDOW_PERSONAL_DAYS", 30)
_COMMERCIAL_WINDOW_DAYS = _env_int("RENEWAL_WINDOW_COMMERCIAL_DAYS", 120)


def _renewal_window_days(lob: str | None) -> int:
    """Forward-look window for a renewal, by line of business."""
    return _PERSONAL_WINDOW_DAYS if _PERSONAL_LOB_RE.search(lob or "") else _COMMERCIAL_WINDOW_DAYS


@router.get("/api/renewals")
def list_renewals_endpoint(limit: int = 1000):
    """Upcoming renewal worklist from renewal_candidates.

    Forward window only: personal lines +30 days, commercial +120 days. Rows on
    expired/inactive policies and non-events (eligibility_state='excluded') are
    dropped, so dead AMS deep-links and already-renewed future-dated rows never
    appear. Carries the NowCerts insured GUID (AMS deep-link), the candidate id
    and natural key (so a row can be corrected from the card) and a derived
    Won/Lost/Open outcome per renewal.

    Human corrections are overlaid before the filters run, so correcting an
    expiration date moves the row into the window and dismissing a renewal drops
    it out — both take effect on the next load, not at the next refresh."""
    from hermes.renewals import corrections as corr

    supa = deps.get_supa()
    rows = supa.select(
        "renewal_candidates",
        columns="id,insured_id,policy_lineage_id,policy_number,client_name,line_of_business,"
                "renewal_event_date,expiration_date,normalized_status,successor_policy_number,"
                "risk_status,segment,in_working_queue,eligibility_state,premium_current,"
                "premium_renewal,policy_active",
        params={"order": "expiration_date.asc"}, limit=limit,
    )
    today = date.today()
    out: list[dict[str, Any]] = []
    for r in corr.apply(supa, rows, surface=corr.CANDIDATES):
        if not r.get("policy_active"):
            continue
        if str(r.get("eligibility_state") or "").strip().lower() == "excluded":
            continue
        raw_exp = r.get("expiration_date")
        if not raw_exp:
            continue
        try:
            exp = date.fromisoformat(str(raw_exp)[:10])
        except ValueError:
            continue
        if exp < today or exp > today + timedelta(days=_renewal_window_days(r.get("line_of_business"))):
            continue
        r["outcome"] = _renewal_outcome(r)
        out.append(r)
    return {"renewals": out, "count": len(out)}


# ---------------------------------------------------------------------------
# Correcting a renewal.
#
# The renewal desk works project_85_renewals, and that table is re-projected from
# renewal_candidates every night. A fix typed onto the row is therefore gone by
# morning — which is why a premium that came over wrong has always stayed wrong.
#
# Every correction is recorded as an override keyed on the policy number
# (hermes/renewals/corrections.py) AND written onto the row, so the number is
# right immediately and the refresh re-applies the correction instead of
# reverting it. Removing a renewal is the same mechanism, on both tables: the
# projection stops showing it and the event stops being projected.
#
# None of this writes to NowCerts. The AMS is fixed by hand, and each correction
# retires itself once the two agree.
# ---------------------------------------------------------------------------
@router.get("/api/renewals/overrides")
def list_renewal_overrides(status: str = "active", limit: int = 500):
    """Corrections on renewals — active, plus anything a refresh conflicted."""
    from hermes.renewals.corrections import PROJECTION

    params: dict[str, str] = {"entity_type": f"eq.{PROJECTION.entity_type}",
                              "order": "approved_at.desc"}
    if status and status.lower() != "all":
        params["status"] = f"eq.{status}"
    rows = deps.get_supa().select("portal_overrides", columns="*", params=params, limit=limit)
    return {"overrides": rows, "count": len(rows)}


def _renewal_row(supa, renewal_id: str) -> dict[str, Any]:
    rows = supa.select("project_85_renewals", columns="*",
                       params={"id": f"eq.{renewal_id}"}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="renewal not found")
    return rows[0]


def _write_through(supa, table: str, record_id: str, fields: dict[str, Any]) -> None:
    """Put the corrected value on the row itself.

    The override is what makes a correction durable; this is what makes it
    visible. Everything reading Supabase directly — the retention scan, the
    renewal desk, the skills — would otherwise see the wrong number until the
    next refresh. Best-effort: the override is already recorded, so a failure
    here costs a night, not the correction."""
    try:
        supa.update(table, record_id,
                    {**fields, "updated_at": datetime.now(timezone.utc).isoformat()})
    except Exception:  # noqa: BLE001
        log.exception("renewal write-through failed: %s %s %s", table, record_id, list(fields))


def _dismiss_candidates(supa, policy_number: str | None, actor: str, reason: str | None) -> int:
    """Take the underlying renewal EVENTS off the list too.

    Dismissing only the projection row would last until 2:30am: the refresh
    re-projects from renewal_candidates, and the renewal would be back with the
    morning list. Excluding the event is what makes a removal stick."""
    from hermes.renewals import corrections as corr
    from hermes_core.overrides.store import set_override

    if not policy_number:
        return 0
    try:
        rows = supa.select("renewal_candidates", columns="*",
                           params={"policy_number": f"eq.{policy_number}"}, limit=50)
    except Exception:  # noqa: BLE001
        log.exception("candidate lookup failed for %s", policy_number)
        return 0

    dismissed = 0
    for row in rows:
        if corr.is_dismissed(corr.CANDIDATES, row):
            continue
        try:
            set_override(
                supa,
                entity_type=corr.CANDIDATES.entity_type,
                entity_key=corr.candidate_key(row),
                field_name=corr.CANDIDATES.dismiss_field,
                override_value=corr.CANDIDATES.dismiss_value,
                original_value=row.get(corr.CANDIDATES.dismiss_field),
                approved_by=actor,
                reason=reason or "removed from the renewal worklist",
            )
        except Exception:  # noqa: BLE001 — the projection dismissal still stands
            log.exception("candidate dismissal failed for %s", row.get("policy_number"))
            continue
        _write_through(supa, "renewal_candidates", row["id"], {
            corr.CANDIDATES.dismiss_field: corr.CANDIDATES.dismiss_value,
            "eligibility_reason": f"removed from the worklist by {actor}"
                                  + (f": {reason}" if reason else ""),
        })
        dismissed += 1
    return dismissed


def _restore_candidates(supa, policy_number: str, actor: str) -> int:
    """Undo the event-level exclusions a removal wrote, so the renewal returns.

    Keyed through the candidates themselves rather than by parsing override keys:
    the natural key carries the lineage root, which is not always this policy's
    own number."""
    from hermes.renewals import corrections as corr
    from hermes_core.overrides.store import withdraw

    if not policy_number:
        return 0
    try:
        rows = supa.select("renewal_candidates", columns="*",
                           params={"policy_number": f"eq.{policy_number}"}, limit=50)
    except Exception:  # noqa: BLE001
        log.exception("candidate lookup failed for %s", policy_number)
        return 0

    restored = 0
    for row in rows:
        try:
            active = supa.select("portal_overrides", columns="*", params={
                "entity_type": f"eq.{corr.CANDIDATES.entity_type}",
                "entity_key": f"eq.{corr.candidate_key(row)}",
                "field_name": f"eq.{corr.CANDIDATES.dismiss_field}",
                "status": "eq.active",
            }, limit=1)
            if not active:
                continue
            undone = withdraw(supa, active[0]["id"], actor=actor)
        except Exception:  # noqa: BLE001 — the projection half already stands
            log.exception("candidate restore failed for %s", row.get("policy_number"))
            continue
        _write_through(supa, "renewal_candidates", row["id"], {
            corr.CANDIDATES.dismiss_field: undone.get("original_value"),
            "eligibility_reason": f"put back on the worklist by {actor}",
        })
        restored += 1
    return restored


class RenewalCorrectionRequest(BaseModel):
    field_name: str
    value: Any = None
    approved_by: str
    reason: str | None = None


@router.post("/api/renewals/{renewal_id}/override")
def correct_renewal_field(renewal_id: str, req: RenewalCorrectionRequest):
    """Correct one field on a renewal, durably.

    The plain PATCH below writes the row and nothing else, so the nightly
    projection overwrites it. This records the same change as a named, reversible
    override first — that is what survives the rebuild."""
    from hermes.renewals import corrections as corr
    from hermes_core.overrides.store import set_override

    supa = deps.get_supa()
    deps.require_users(supa, [("approved_by", req.approved_by)])

    row = _renewal_row(supa, renewal_id)
    field_name = (req.field_name or "").strip()
    try:
        value = corr.coerce(corr.PROJECTION, field_name, req.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    policy_number = row.get("policy_number")
    if not policy_number:
        raise HTTPException(
            status_code=409,
            detail="this renewal has no policy number to key a correction on",
        )

    try:
        override = set_override(
            supa,
            entity_type=corr.PROJECTION.entity_type,
            entity_key=policy_number,
            field_name=field_name,
            override_value=value,
            original_value=row.get(field_name),   # the SOURCE value, for reconcile
            approved_by=req.approved_by,
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("renewal correction failed: %s %s", renewal_id, field_name)
        raise HTTPException(status_code=502, detail=str(exc))

    _write_through(supa, corr.PROJECTION.entity_type, renewal_id, {field_name: value})
    return {"ok": True, "override": override,
            "note": "Corrected in the CRM and held through the nightly refresh. "
                    "NowCerts still needs the same fix by hand."}


class RenewalDismissRequest(BaseModel):
    deleted_by: str
    reason: str | None = None


@router.delete("/api/renewals/{renewal_id}")
def dismiss_renewal(renewal_id: str, req: RenewalDismissRequest):
    """Take a renewal off the worklist.

    Recorded as a removal, not a row DELETE. renewal_actions cascades off this
    table, so deleting the row would erase the record of the work already done on
    the renewal, and the nightly refresh would re-project it from the same policy
    anyway. The event underneath is excluded too, which is what makes it stick.
    Reversible: undo the correction and the renewal comes back."""
    from hermes.renewals import corrections as corr
    from hermes_core.overrides.store import set_override

    supa = deps.get_supa()
    deps.require_users(supa, [("deleted_by", req.deleted_by)])
    row = _renewal_row(supa, renewal_id)
    policy_number = row.get("policy_number")
    if not policy_number:
        raise HTTPException(
            status_code=409,
            detail="this renewal has no policy number to key a removal on",
        )

    try:
        override = set_override(
            supa,
            entity_type=corr.PROJECTION.entity_type,
            entity_key=policy_number,
            field_name=corr.PROJECTION.dismiss_field,
            override_value=corr.PROJECTION.dismiss_value,
            original_value=None,      # not a column — the source never says this
            approved_by=req.deleted_by,
            reason=req.reason or "removed from the renewal worklist",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("renewal removal failed: %s", renewal_id)
        raise HTTPException(status_code=502, detail=str(exc))

    events = _dismiss_candidates(supa, policy_number, req.deleted_by, req.reason)
    return {"ok": True, "override": override, "events_excluded": events,
            "note": "Off the worklist. The renewal and its history are kept, "
                    "marked removed — undo the correction to bring it back."}


@router.delete("/api/renewals/overrides/{override_id}")
def withdraw_renewal_override(override_id: str, approved_by: str):
    """Undo a correction — including a removal, which puts the renewal back.

    Withdrawing restores what the source said on the row as well, so the list
    stops showing a number nobody stands behind."""
    from hermes.renewals import corrections as corr
    from hermes_core.overrides.store import withdraw

    supa = deps.get_supa()
    deps.require_users(supa, [("approved_by", approved_by)])
    try:
        row = withdraw(supa, override_id, actor=approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.exception("renewal override withdraw failed: %s", override_id)
        raise HTTPException(status_code=502, detail=str(exc))

    field_name = row.get("field_name")
    if field_name == corr.PROJECTION.dismiss_field:
        # Putting a renewal back means putting the EVENT back too. The removal
        # excluded both; undoing only the projection half would leave the
        # renewal visible today and gone again after the next refresh.
        restored = _restore_candidates(supa, str(row.get("entity_key") or ""), approved_by)
        return {"ok": True, "override": row, "events_restored": restored}

    if field_name and field_name in corr.PROJECTION.editable:
        try:
            rows = supa.select("project_85_renewals", columns="id",
                               params={"policy_number": f"eq.{row.get('entity_key')}"}, limit=1)
        except Exception:  # noqa: BLE001 — the withdraw itself stands
            log.exception("renewal restore lookup failed: %s", override_id)
            rows = []
        if rows:
            _write_through(supa, corr.PROJECTION.entity_type, rows[0]["id"],
                           {field_name: row.get("original_value")})
    return {"ok": True, "override": row}


class RenewalUpdateRequest(BaseModel):
    """Editable renewal-desk fields on project_85_renewals.

    Both premiums are here because both get worked: the expiring number is often
    wrong in the mirror, and the renewal number only exists once a carrier quotes
    it. ``increase_percentage`` is deliberately absent — it is a generated column
    computed from the two, so writing it is both refused by Postgres and the
    wrong idea. Fix the premiums and the change follows.
    """
    premium_current: float | None = None
    premium_renewal: float | None = None
    risk_status: str | None = None
    ai_strategy_notes: str | None = None
    last_contact_date: str | None = None


@router.patch("/api/renewals/{renewal_id}")
def update_renewal_endpoint(renewal_id: str, req: RenewalUpdateRequest):
    """Update a renewal's working detail. The cockpit was read-only, so a premium
    that came over wrong stayed wrong and every downstream number inherited it."""
    from hermes.operations.renewal_tracker import VALID_RISK_STATUSES

    supa = deps.get_supa()
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields provided")

    risk = fields.get("risk_status")
    if risk is not None:
        risk = str(risk).strip().upper()
        if risk not in VALID_RISK_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"unknown risk_status '{risk}'; must be one of {list(VALID_RISK_STATUSES)}",
            )
        fields["risk_status"] = risk

    rows = supa.select("project_85_renewals", columns="*",
                       params={"id": f"eq.{renewal_id}"}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="renewal not found")

    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        row = supa.update("project_85_renewals", renewal_id, fields)
    except Exception as exc:
        log.exception("update renewal failed: %s", renewal_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "renewal": row}
