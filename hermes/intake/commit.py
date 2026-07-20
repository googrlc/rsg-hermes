"""Commit an approved new-business intake — Supabase pipeline + gated NowCerts create.

On approval this:
  1. Creates the Supabase ``opportunities`` rows (the pipeline — immediate, no AMS write).
  2. Stages an approval-gated NowCerts ``create_insured`` (prospect) job on
     ``outbound_sync_queue`` (object_type='intake', destination='nowcerts').
     The Espo drain already skips destination='nowcerts', so it can't be swept
     into an EspoCRM write. The intake executor drains it out-of-band.
  3. Creates the client's Nextcloud folder tree (if configured).

Nothing writes to NowCerts synchronously here — the insured create is queued and
only performed when the (opt-in) intake executor runs, so a dry-run can verify
the insert field casing before the first live write.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes.intake import nowcerts_map
from hermes.intake import opportunities as opp
from hermes.renewals.executor import DESTINATION_NOWCERTS, QUEUE_QUEUED, QUEUE_TABLE

if TYPE_CHECKING:
    from hermes.integrations.nextcloud_client import NextcloudClient
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

OBJECT_TYPE_INTAKE = "intake"
INTAKE_ACTION_CREATE_INSURED = "create_insured"
DEFAULT_ASSIGNEE = "gretchen"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_insured_job(
    supa: "SupabaseClient",
    *,
    insured_payload: dict[str, Any],
    client_identifier: str,
    opportunity_ids: list[str],
    approved_by: str,
) -> dict[str, Any]:
    return supa.insert(
        QUEUE_TABLE,
        {
            "object_type": OBJECT_TYPE_INTAKE,
            "object_id": client_identifier,
            "destination_system": DESTINATION_NOWCERTS,
            "action": "create",
            "payload": {
                "action": INTAKE_ACTION_CREATE_INSURED,
                "insured": insured_payload,
                "client_identifier": client_identifier,
                "opportunity_ids": opportunity_ids,
            },
            "status": QUEUE_QUEUED,
            "attempt_count": 0,
            "approved_by": approved_by,       # human approved the intake token
            "approved_at": _utcnow_iso(),
        },
    )


def commit_intake(
    supa: "SupabaseClient",
    *,
    account: dict[str, Any],
    opportunities_spec: list[dict[str, Any]],
    approved_by: str,
    prospect_type: str = "Prospect",
    insured_type: str | None = None,
    nextcloud: "NextcloudClient | None" = None,
    source: str | None = None,
    created_by: str = "hermes-intake",
    assigned_to: str = DEFAULT_ASSIGNEE,
) -> dict[str, Any]:
    """Commit an approved intake. Returns a summary. No synchronous NowCerts write."""
    if not opportunities_spec:
        raise ValueError("opportunities_spec must have at least one line of business")

    name = account.get("account_name") or account.get("commercial_name") or account.get("name")
    fein = account.get("fein") or account.get("ein")
    cid = opp.make_client_identifier(name, fein)
    itype = nowcerts_map.normalize_insured_type(
        insured_type or account.get("insured_type") or account.get("segment")
    )
    ptype = nowcerts_map.normalize_prospect_type(prospect_type)

    # 1. Supabase pipeline rows (one per LOB).
    opp_rows: list[dict[str, Any]] = []
    for spec in opportunities_spec:
        lob = spec.get("line_of_business")
        if not lob:
            continue
        row, _created = opp.create_opportunity(
            supa,
            client_identifier=cid,
            line_of_business=lob,
            insured_name=name,
            prospect_type=ptype,
            insured_type=itype,
            premium_estimate=spec.get("premium_estimate"),
            carrier=spec.get("carrier"),
            lead_source=account.get("lead_source"),
            assigned_to=assigned_to,
            source=source,
            created_by=created_by,
        )
        opp_rows.append(row)

    # 2. Gated NowCerts insured-create job.
    # ptype (Hot/Cold/Prospect) stays on the opportunities row; the NowCerts
    # insured write uses the connector's numeric type code (prospect=1).
    insured_payload = nowcerts_map.map_to_insured(account, insured_type=itype, is_prospect=True)
    job = _stage_insured_job(
        supa,
        insured_payload=insured_payload,
        client_identifier=cid,
        opportunity_ids=[r.get("id") for r in opp_rows],
        approved_by=approved_by,
    )

    # 2b. Prompt AMS pull-back: link an existing insured now (and pull its segment
    # fields onto the row), or kick the just-staged create/link job immediately —
    # so the pipeline row is tied to NowCerts within seconds, not next scheduler
    # cycle. Best-effort: never fails the commit.
    prime: dict[str, Any] = {}
    try:
        from hermes.intake.opportunity_priming import prime_new_opportunities

        prime = prime_new_opportunities(supa, opp_rows, kick_executor=True)
    except Exception:
        log.exception("intake commit: opportunity priming failed for %s", cid)

    # 3. Nextcloud client folder tree (best-effort).
    folder = None
    nc = nextcloud
    if nc is None:
        from hermes.integrations.nextcloud_client import NextcloudClient

        nc = NextcloudClient()
    try:
        if nc.is_configured() and name:
            folder = nc.ensure_client_folders(str(name))
    except Exception:
        folder = None

    return {
        "client_identifier": cid,
        "opportunities": opp_rows,
        "opportunity_count": len(opp_rows),
        "intake_job_id": job.get("id"),
        "insured_preview": insured_payload,
        "nextcloud_folder": folder,
        "prospect_type": ptype,
        "insured_type": itype,
        "prime": prime,
    }


_PERSONAL_LOBS = {
    "personal auto", "homeowners", "renters", "condo", "dwelling fire",
    "motorcycle", "boat", "rv", "umbrella (personal)",
}


def _derive_insured_type(account: dict[str, Any], opportunities_spec: list[dict[str, Any]]) -> str | None:
    explicit = nowcerts_map.normalize_insured_type(
        account.get("account_type") or account.get("insured_type") or account.get("segment") or account.get("type")
    )
    if explicit:
        return explicit
    lobs = {(s.get("line_of_business") or "").strip().lower() for s in opportunities_spec}
    if lobs & _PERSONAL_LOBS:
        return "Personal"
    return "Commercial" if any(lobs) else None


def commit_draft(
    supa: "SupabaseClient",
    payload: dict[str, Any],
    *,
    approved_by: str,
    nextcloud: "NextcloudClient | None" = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Adapt an intake draft/submission payload (account + opportunities) → commit_intake.

    This is the bridge the intake worker calls once a submission is approved.
    Raises ValueError if the draft carries no line of business to open a pipeline on.
    """
    account = payload.get("account") or {}
    opps = payload.get("opportunities") or []
    spec = [
        {
            "line_of_business": o.get("line_of_business"),
            "premium_estimate": o.get("premium") or o.get("target_premium"),
            "carrier": o.get("carrier"),
        }
        for o in opps
        if o.get("line_of_business")
    ]
    if not spec:
        raise ValueError("intake draft has no line_of_business to open an opportunity on")

    prospect_type = "Prospect"
    if str(account.get("account_type") or "").strip().lower() in ("hot", "hot prospect", "hot_prospect"):
        prospect_type = "Hot_Prospect"

    return commit_intake(
        supa,
        account=account,
        opportunities_spec=spec,
        approved_by=approved_by,
        prospect_type=prospect_type,
        insured_type=_derive_insured_type(account, spec),
        nextcloud=nextcloud,
        source=source or "agency_intake",
    )
