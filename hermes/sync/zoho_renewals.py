"""NowCerts/Supabase renewal ledger → Zoho CRM Renewal_Events + Renewals.

Feeds the Creator Renewals Desk. Eligibility still lives in
``hermes/renewals/eligibility.py``; this module only upserts CRM rows and
captures Creator corrections as ``portal_overrides`` so the next
``--renewal-refresh`` does not clobber them.

Desk-owned fields (stage, disposition, recommended action, touch dates,
Related_Deal) are set on create (Desk_Stage=Identified) and never overwritten
on update.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from hermes.renewals import corrections as corr
from hermes.renewals import desk as desk
from hermes.renewals.config import PIPELINE_STAGE_IDENTIFIED
from hermes_core.overrides.core import same_value
from hermes_core.overrides.store import set_override
from hermes_integrations.zoho_client import ZohoClient, ZohoClientError, _escape_criteria_value

log = logging.getLogger(__name__)

CANDIDATES_TABLE = "renewal_candidates"
P85_TABLE = "project_85_renewals"
POLICIES_TABLE = "canonical_policies"

EVENTS_MODULE = os.environ.get("ZOHO_RENEWAL_EVENTS_MODULE", "Renewal_Events")
RENEWALS_MODULE = os.environ.get("ZOHO_RENEWALS_MODULE", "Renewals")
POLICIES_MODULE = os.environ.get("ZOHO_POLICIES_MODULE", "Policies")
ACCOUNTS_MODULE = "Accounts"

ACTOR_CREATOR = "zoho_creator"

# Fields Hermes may write on an *update*. Everything else is Creator-owned.
RENEWAL_SYNC_FIELDS = frozenset({
    "Name",
    "Hermes_Renewal_ID",
    "Policy_Number",
    "Policy",
    "Account_Name",
    "Client_Name",
    "Expiration_Date",
    "Premium_Current",
    "Premium_Renewal",
    "Risk_Status",
    "Strategy_Notes",
    "Last_Contact_Date",
    "Carrier",
    "Line_of_Business",
    "Effective_Date",
    "Dismissed",
    "Related_Renewal_Event",
    "Window_Bucket",
})


@dataclass
class ZohoRenewalsSyncResult:
    candidates_scanned: int = 0
    events_created: int = 0
    events_updated: int = 0
    events_skipped: int = 0
    renewals_created: int = 0
    renewals_updated: int = 0
    renewals_skipped: int = 0
    overrides_captured: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        mode = "dry-run" if self.dry_run else "live"
        return (
            f"Zoho renewals sync ({mode}): "
            f"{self.candidates_scanned} events → "
            f"Renewal_Events +{self.events_created}/~{self.events_updated} "
            f"skipped {self.events_skipped}; "
            f"Renewals +{self.renewals_created}/~{self.renewals_updated} "
            f"skipped {self.renewals_skipped}; "
            f"overrides captured {self.overrides_captured}; "
            f"errors {len(self.errors)}"
        )


def _iso_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)[:10]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _lookup(record_id: str | None) -> dict[str, str] | None:
    if not record_id:
        return None
    return {"id": str(record_id)}


def map_candidate_to_renewal_event(
    row: dict[str, Any],
    *,
    account_id: str | None = None,
    policy_id: str | None = None,
) -> dict[str, Any]:
    """``renewal_candidates`` row → Zoho Renewal_Events fields."""
    pn = str(row.get("policy_number") or "").strip()
    event_date = _iso_date(row.get("renewal_event_date"))
    key = corr.candidate_key(row) if row.get("insured_id") else None
    name = " ".join(p for p in (pn, event_date) if p) or "Renewal Event"
    payload: dict[str, Any] = {
        "Name": name[:255],
        "Hermes_Candidate_ID": str(row.get("id") or "").strip() or None,
        "NowCerts_Insured_GUID": str(row.get("insured_id") or "").strip() or None,
        "Policy_Lineage_ID": str(row.get("policy_lineage_id") or "").strip() or None,
        "Renewal_Event_Date": event_date,
        "Renewal_Key": key or None,
        "NowCerts_Policy_GUID": str(row.get("nowcerts_policy_guid") or "").strip() or None,
        "Policy_Number": pn or None,
        "Insured_Active": bool(row.get("insured_active")),
        "Policy_Active": bool(row.get("policy_active")),
        "Normalized_Status": row.get("normalized_status") or None,
        "Branch": row.get("branch") or None,
        "Effective_Date": _iso_date(row.get("effective_date")),
        "Expiration_Date": _iso_date(row.get("expiration_date")),
        "Predecessor_Policy": row.get("predecessor_policy_number") or None,
        "Successor_Policy": row.get("successor_policy_number") or None,
        "Eligibility": row.get("eligibility_state") or None,
        "Eligibility_Reason": row.get("eligibility_reason") or None,
        "Last_Verified": row.get("last_verified_at") or None,
        "Segment": row.get("segment") or None,
        "Line_of_Business": row.get("line_of_business") or None,
        "Client_Name": row.get("client_name") or None,
        "In_Working_Queue": bool(row.get("in_working_queue")),
        "Workflow_Entry_Date": _iso_date(row.get("workflow_entry_date")),
        "Risk_Status": row.get("risk_status") or None,
        "Premium_Current": row.get("premium_current"),
        "Premium_Renewal": row.get("premium_renewal"),
        "Account_Name": _lookup(account_id),
        "Policy": _lookup(policy_id),
    }
    return {k: v for k, v in payload.items() if v is not None and v != ""}


def map_projection_to_renewal(
    row: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    account_id: str | None = None,
    policy_id: str | None = None,
    event_id: str | None = None,
    today: date | None = None,
    creating: bool = False,
) -> dict[str, Any]:
    """``project_85_renewals`` (+ optional canonical policy) → Zoho Renewals.

    When ``creating`` is false, desk-owned fields are omitted so a nightly
    upsert cannot reset Gretchen's stage.
    """
    policy = policy or {}
    pn = str(row.get("policy_number") or "").strip()
    exp = _iso_date(row.get("expiration_date"))
    lob = row.get("line_of_business") or policy.get("lines_of_business") or policy.get("line_of_business")
    carrier = row.get("carrier") or policy.get("carrier")
    client = row.get("client_name") or "Unknown client"
    name = f"{client} — {pn or 'policy'} {exp or 'renewal'}"
    dismissed = _as_bool(row.get("dismissed"))
    payload: dict[str, Any] = {
        "Name": name[:255],
        "Hermes_Renewal_ID": str(row.get("id") or "").strip() or None,
        "Policy_Number": pn or None,
        "Client_Name": client,
        "Expiration_Date": exp,
        "Premium_Current": row.get("premium_current"),
        "Premium_Renewal": row.get("premium_renewal"),
        "Risk_Status": row.get("risk_status") or "SAFE",
        "Strategy_Notes": row.get("ai_strategy_notes") or None,
        "Last_Contact_Date": _iso_date(row.get("last_contact_date")),
        "Carrier": carrier or None,
        "Line_of_Business": lob or None,
        "Effective_Date": _iso_date(row.get("effective_date") or policy.get("effective_date")),
        "Dismissed": dismissed,
        "Window_Bucket": desk.window_bucket(exp, lob, today=today),
        "Policy": _lookup(policy_id),
        "Account_Name": _lookup(account_id),
        "Related_Renewal_Event": _lookup(event_id),
    }
    if creating:
        payload["Desk_Stage"] = PIPELINE_STAGE_IDENTIFIED
    if not creating:
        payload = {k: v for k, v in payload.items() if k in RENEWAL_SYNC_FIELDS}
    return {k: v for k, v in payload.items() if v is not None and v != ""}


def creator_override_diffs(
    source: dict[str, Any],
    zoho_row: dict[str, Any] | None,
    *,
    active_overrides: dict[tuple[str, str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Creator-edited correctable fields that are not already the active override.

    ``source`` is the projection row *before* overlay (what the book says).
    ``active_overrides`` maps ``(policy_number, field_name) -> override_value``.
    """
    if not zoho_row:
        return []
    pn = str(source.get("policy_number") or zoho_row.get("Policy_Number") or "").strip()
    if not pn:
        return []
    known = active_overrides or {}
    out: list[dict[str, Any]] = []
    for zoho_field, hermes_field in desk.ZOHO_CORRECTABLE.items():
        zoho_val = zoho_row.get(zoho_field)
        if zoho_field in {"Expiration_Date", "Last_Contact_Date"} and zoho_val:
            zoho_val = str(zoho_val)[:10]
        source_val = source.get(hermes_field)
        if hermes_field in {"expiration_date", "last_contact_date"} and source_val:
            source_val = str(source_val)[:10]
        if same_value(zoho_val, source_val):
            continue
        current = known.get((pn, hermes_field))
        if current is not None and same_value(zoho_val, current):
            continue
        # Empty Zoho on a field we never sent is not a Creator edit.
        if zoho_val is None or zoho_val == "":
            continue
        out.append({
            "entity_type": corr.PROJECTION.entity_type,
            "entity_key": pn,
            "field_name": hermes_field,
            "override_value": zoho_val,
            "original_value": source_val,
            "approved_by": ACTOR_CREATOR,
            "reason": "captured from Zoho Creator Renewals Desk",
        })
    if _as_bool(zoho_row.get("Dismissed")) and not _as_bool(source.get("dismissed")):
        current = known.get((pn, corr.PROJECTION.dismiss_field))
        if not same_value(current, True):
            out.append({
                "entity_type": corr.PROJECTION.entity_type,
                "entity_key": pn,
                "field_name": corr.PROJECTION.dismiss_field,
                "override_value": True,
                "original_value": False,
                "approved_by": ACTOR_CREATOR,
                "reason": "dismissed on Zoho Creator Renewals Desk",
            })
    return out


def _index_policies(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        pn = str(row.get("policy_number") or "").strip()
        if pn:
            out[pn] = row
    return out


def _find_id(client: ZohoClient, module: str, field: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        rows = client.search_records(
            module, f"({field}:equals:{_escape_criteria_value(str(value))})"
        )
    except ZohoClientError:
        log.exception("Zoho search %s %s failed", module, field)
        return None
    if rows and rows[0].get("id"):
        return str(rows[0]["id"])
    return None


def run_zoho_renewals_sync(
    *,
    supa: Any,
    zoho: ZohoClient | None = None,
    today: date | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> ZohoRenewalsSyncResult:
    """Upsert Renewal_Events from candidates and Renewals from the projection."""
    from hermes_integrations.zoho_client import get_client

    result = ZohoRenewalsSyncResult(dry_run=dry_run)
    today = today or date.today()
    zoho = zoho or get_client()

    candidates = supa.select(CANDIDATES_TABLE, columns="*", limit=limit or 10000)
    projections = supa.select(P85_TABLE, columns="*", limit=limit or 10000)
    policies = _index_policies(
        supa.select(
            POLICIES_TABLE,
            columns="policy_number,policy_guid,nowcerts_insured_guid,carrier,"
                    "lines_of_business,effective_date,premium_amount",
            limit=10000,
        )
    )
    result.candidates_scanned = len(candidates)

    overlaid_events = corr.apply(supa, candidates, surface=corr.CANDIDATES)
    overlaid_p85 = corr.apply(supa, projections, surface=corr.PROJECTION)

    source_by_pn = {str(r.get("policy_number") or ""): r for r in projections}
    overlay_index: dict[tuple[str, str], Any] = {}
    for row in overlaid_p85:
        pn = str(row.get("policy_number") or "")
        for field_name in desk.ZOHO_CORRECTABLE.values():
            if row.get("_overridden") or field_name in row:
                # Stash overlay values we might already have recorded.
                overlay_index[(pn, field_name)] = row.get(field_name)

    event_zoho_ids: dict[str, str] = {}  # hermes candidate id → zoho id

    for row in overlaid_events:
        cid = str(row.get("id") or "").strip()
        if not cid:
            result.events_skipped += 1
            continue
        pn = str(row.get("policy_number") or "").strip()
        policy = policies.get(pn) or {}
        try:
            account_id = _find_id(
                zoho, ACCOUNTS_MODULE, "NowCerts_Insured_GUID", row.get("insured_id")
            )
            policy_id = _find_id(
                zoho, POLICIES_MODULE, "NowCerts_Policy_GUID",
                row.get("nowcerts_policy_guid") or policy.get("policy_guid"),
            )
            payload = map_candidate_to_renewal_event(
                row, account_id=account_id, policy_id=policy_id
            )
            if dry_run:
                existing = zoho._find_first(  # noqa: SLF001 — reuse match helper
                    EVENTS_MODULE,
                    f"(Hermes_Candidate_ID:equals:{_escape_criteria_value(cid)})",
                )
                if existing:
                    result.events_updated += 1
                    event_zoho_ids[cid] = str(existing["id"])
                else:
                    result.events_created += 1
                continue
            upserted = zoho.upsert_by_field(
                EVENTS_MODULE, payload, match_field="Hermes_Candidate_ID", match_value=cid
            )
            if upserted.get("action") == "created":
                result.events_created += 1
            else:
                result.events_updated += 1
            if upserted.get("id"):
                event_zoho_ids[cid] = str(upserted["id"])
        except Exception as exc:  # noqa: BLE001
            log.exception("Renewal_Events upsert failed for %s", cid)
            result.errors.append(f"event {cid}: {exc}")

    # Match projection rows to a candidate event for Related_Renewal_Event.
    event_by_pn: dict[str, str] = {}
    for row in overlaid_events:
        pn = str(row.get("policy_number") or "").strip()
        cid = str(row.get("id") or "").strip()
        if pn and cid and cid in event_zoho_ids:
            event_by_pn[pn] = event_zoho_ids[cid]

    for row in overlaid_p85:
        pn = str(row.get("policy_number") or "").strip()
        rid = str(row.get("id") or "").strip()
        if not pn or not rid:
            result.renewals_skipped += 1
            continue
        policy = policies.get(pn) or {}
        source = source_by_pn.get(pn) or {}
        try:
            existing = zoho._find_first(  # noqa: SLF001
                RENEWALS_MODULE,
                f"(Hermes_Renewal_ID:equals:{_escape_criteria_value(rid)})",
            )
            diffs = creator_override_diffs(source, existing, active_overrides=overlay_index)
            for diff in diffs:
                result.overrides_captured += 1
                overlay_index[(diff["entity_key"], diff["field_name"])] = diff["override_value"]
                if not dry_run:
                    set_override(supa, **diff)
                # Apply onto the row we are about to upsert.
                row = dict(row)
                row[diff["field_name"]] = diff["override_value"]

            account_id = desk.lookup_id((existing or {}).get("Account_Name")) or _find_id(
                zoho, ACCOUNTS_MODULE, "NowCerts_Insured_GUID",
                policy.get("nowcerts_insured_guid"),
            )
            policy_id = desk.lookup_id((existing or {}).get("Policy")) or _find_id(
                zoho, POLICIES_MODULE, "NowCerts_Policy_GUID", policy.get("policy_guid"),
            )
            creating = existing is None
            payload = map_projection_to_renewal(
                row,
                policy=policy,
                account_id=account_id,
                policy_id=policy_id,
                event_id=event_by_pn.get(pn),
                today=today,
                creating=creating,
            )
            if dry_run:
                if creating:
                    result.renewals_created += 1
                else:
                    result.renewals_updated += 1
                continue
            upserted = zoho.upsert_by_field(
                RENEWALS_MODULE, payload, match_field="Hermes_Renewal_ID", match_value=rid
            )
            if upserted.get("action") == "created":
                result.renewals_created += 1
            else:
                result.renewals_updated += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("Renewals upsert failed for %s", pn)
            result.errors.append(f"renewal {pn}: {exc}")

    return result
