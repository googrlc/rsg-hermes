"""NowCerts/Supabase renewal ledger → Zoho CRM Renewal_Events + Renewals + Deals.

Feeds the Creator Renewals Desk. Eligibility still lives in
``hermes/renewals/eligibility.py``; this module upserts CRM rows and
captures Creator corrections as ``portal_overrides`` so the next
``--renewal-refresh`` does not clobber them.

The Renewals desk table and the CRM **Renewals pipeline** (Deals with
``Opportunity_Type=Renewals``) are a 1:1 projection linked by
``Renewals.Related_Deal``. A row belongs on the desk only when that Deal
exists. A Deal on the Renewals pipeline belongs on the desk.

Desk-owned fields (stage, disposition, recommended action, touch dates)
are set on create (Desk_Stage=Identified) and never overwritten on update.
``Related_Deal`` is filled when empty and then left alone.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from hermes.renewals import corrections as corr
from hermes.renewals import desk as desk
from hermes.renewals.config import (
    LOSS_DISPOSITIONS,
    PIPELINE_STAGE_CLOSED,
    PIPELINE_STAGE_IDENTIFIED,
    WIN_DISPOSITIONS,
)
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
DEALS_MODULE = os.environ.get("ZOHO_DEALS_MODULE", "Deals")
ACCOUNTS_MODULE = "Accounts"
OPPORTUNITY_TYPE_RENEWALS = "Renewals"

# Optional. Do not fall back to ZOHO_PIPELINE_ID (that is New Business).
RENEWALS_PIPELINE_ID = (os.environ.get("ZOHO_RENEWALS_PIPELINE_ID") or "").strip()

# Stable namespace so a CRM-only Deal always maps to the same Hermes_Renewal_ID.
_DEAL_HERMES_NS = uuid.UUID("6f1c2d90-4b8e-5a17-9c3d-0a1b2c3d4e5f")

# Deal pipeline labels — exact ``picklists_nowcerts_seed.csv`` / FIELD_CREATE_CHECKLIST.
DEAL_STAGE_90 = "Renewal in 90 days"
DEAL_STAGE_60 = "Renewal in 60 days"
DEAL_STAGE_30 = "Renewal in 30 days"
DEAL_STAGE_REQUOTE = "Requote Renewal"
DEAL_STAGE_REVIEW = "Annual Policy Review"
DEAL_STAGE_AUTO = "Complete/Auto-Renewal"
DEAL_STAGE_WON = "Bound / Won"
DEAL_STAGE_LOST = "Not Renewed"

DEAL_STAGE_OPTION_IDS: dict[str, str] = {
    DEAL_STAGE_90: "bb6eb18f-8b31-cf43-3b57-45cea520183a",
    DEAL_STAGE_60: "f7ffbbe0-2f08-3e1a-5765-f5fe6e8ce997",
    DEAL_STAGE_30: "0c76b0dc-acf4-72f1-9a01-1222dede624f",
    DEAL_STAGE_REQUOTE: "9fea61ef-40d1-c7a8-58b5-01b7a74c617b",
    DEAL_STAGE_REVIEW: "b917834c-4262-9863-6720-f912daa6f219",
    DEAL_STAGE_AUTO: "8eb2161c-1925-43d0-602b-5c1486f93def",
    DEAL_STAGE_WON: "76a8a582-6a6f-dbf7-2929-50096e26cb50",
    DEAL_STAGE_LOST: "cbad2c95-ef0a-94f2-a534-fc38f2907b02",
}

DEAL_STAGE_PROBABILITY: dict[str, int] = {
    DEAL_STAGE_90: 40,
    DEAL_STAGE_60: 55,
    DEAL_STAGE_30: 70,
    DEAL_STAGE_REQUOTE: 60,
    DEAL_STAGE_REVIEW: 50,
    DEAL_STAGE_AUTO: 100,
    DEAL_STAGE_WON: 100,
    DEAL_STAGE_LOST: 0,
}

# Gretchen-moved pipeline stages. Window math must not pull these back to 90/60/30.
HUMAN_PROTECTED_DEAL_STAGES = frozenset({
    DEAL_STAGE_REQUOTE,
    DEAL_STAGE_REVIEW,
    DEAL_STAGE_AUTO,
})

REMARKET_ACTIONS = frozenset({"REMARKET_SAMPLE", "REMARKET_FULL"})

ACTOR_CREATOR = "zoho_creator"

# Fields Hermes may write on an *update*. Everything else is Creator-owned.
# Related_Deal is filled when empty (see run loop) but is not in this set.
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
    deals_created: int = 0
    deals_updated: int = 0
    deals_skipped: int = 0
    pipeline_desk_created: int = 0
    pipeline_desk_updated: int = 0
    pipeline_desk_skipped: int = 0
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
            f"Deals +{self.deals_created}/~{self.deals_updated} "
            f"skipped {self.deals_skipped}; "
            f"pipeline→desk +{self.pipeline_desk_created}/~{self.pipeline_desk_updated} "
            f"skipped {self.pipeline_desk_skipped}; "
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


def _picklist_label(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "").strip()
    return str(value or "").strip()


def deal_window_stage(expiration: date | str | None, *, today: date | None = None) -> str:
    """Calendar window → Renewals pipeline stage. Independent of desk Window_Bucket."""
    days = desk.days_to_expiration(expiration, today=today)
    if days is None:
        return DEAL_STAGE_90
    if days < 0 or days <= 30:
        return DEAL_STAGE_30
    if days <= 60:
        return DEAL_STAGE_60
    return DEAL_STAGE_90


def resolve_deal_stage(
    *,
    expiration: date | str | None = None,
    desk_stage: str | None = None,
    disposition: str | None = None,
    recommended_action: str | None = None,
    dismissed: bool = False,
    existing_stage: str | None = None,
    today: date | None = None,
) -> str:
    """Desk state + window → Deal Stage. Does not skip Gretchen's pipeline moves."""
    existing = _picklist_label(existing_stage)
    disp = (disposition or "").strip()
    stage = (desk_stage or "").strip()
    if dismissed or disp in LOSS_DISPOSITIONS:
        return DEAL_STAGE_LOST
    if stage == PIPELINE_STAGE_CLOSED and disp in WIN_DISPOSITIONS:
        return DEAL_STAGE_WON
    if existing in HUMAN_PROTECTED_DEAL_STAGES:
        return existing
    if (recommended_action or "").strip() in REMARKET_ACTIONS:
        return DEAL_STAGE_REQUOTE
    return deal_window_stage(expiration, today=today)


def deal_status_for_stage(stage: str | None) -> str:
    label = _picklist_label(stage)
    if label == DEAL_STAGE_WON:
        return "won"
    if label == DEAL_STAGE_LOST:
        return "lost"
    return "open"


def hermes_id_for_crm_deal(deal: dict[str, Any]) -> str:
    existing = str(deal.get("Hermes_Opportunity_ID") or "").strip()
    if existing:
        return existing
    did = str(deal.get("id") or "").strip()
    if did:
        return str(uuid.uuid5(_DEAL_HERMES_NS, f"zoho-deal:{did}"))
    return str(uuid.uuid4())


def map_renewal_to_deal(
    row: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    account_id: str | None = None,
    existing_deal: dict[str, Any] | None = None,
    desk_row: dict[str, Any] | None = None,
    today: date | None = None,
    creating: bool = False,
) -> dict[str, Any]:
    """Desk / project_85 row → Renewals-pipeline Deal fields."""
    policy = policy or {}
    desk_row = desk_row or {}
    existing_deal = existing_deal or {}
    pn = str(row.get("policy_number") or "").strip()
    rid = str(row.get("id") or "").strip()
    client = row.get("client_name") or "Unknown client"
    lob = row.get("line_of_business") or policy.get("lines_of_business") or policy.get("line_of_business")
    carrier = row.get("carrier") or policy.get("carrier")
    exp = _iso_date(row.get("expiration_date"))
    dismissed = _as_bool(row.get("dismissed")) or _as_bool(desk_row.get("Dismissed"))
    stage = resolve_deal_stage(
        expiration=exp or row.get("expiration_date"),
        desk_stage=desk_row.get("Desk_Stage"),
        disposition=desk_row.get("Disposition") or row.get("disposition"),
        recommended_action=desk_row.get("Recommended_Action") or row.get("recommended_action"),
        dismissed=dismissed,
        existing_stage=existing_deal.get("Stage"),
        today=today,
    )
    guid = str(
        policy.get("nowcerts_insured_guid")
        or row.get("insured_id")
        or ""
    ).strip() or None
    deal_name = f"{client} — {lob or pn or 'policy'} renewal"
    payload: dict[str, Any] = {
        "Deal_Name": deal_name[:255],
        "Hermes_Opportunity_ID": rid or None,
        "Opportunity_Type": OPPORTUNITY_TYPE_RENEWALS,
        "Line_of_Business": lob or None,
        "Bound_Policy_Number": pn or None,
        "NowCerts_Policy_GUID": str(policy.get("policy_guid") or "").strip() or None,
        "NowCerts_Insured_GUID": guid,
        "Client_Identifier": guid or client,
        "Insured_Name": client,
        "Carrier": carrier or None,
        "Stage": stage,
        "Stage_Option_ID": DEAL_STAGE_OPTION_IDS.get(stage),
        "Deal_Status": deal_status_for_stage(stage),
        "Probability": DEAL_STAGE_PROBABILITY.get(stage),
        "Amount": row.get("premium_renewal") or row.get("premium_current"),
        "Premium_Estimate": row.get("premium_renewal") or row.get("premium_current"),
        "Expiration_Date": exp,
        "Closing_Date": exp,
        "Effective_Date": _iso_date(row.get("effective_date") or policy.get("effective_date")),
        "Account_Name": _lookup(account_id),
        "Sync_Source": "hermes_renewals_sync",
    }
    if RENEWALS_PIPELINE_ID:
        payload["Pipeline"] = {"id": RENEWALS_PIPELINE_ID}
    if creating:
        payload["Win_Likelihood"] = "Good"
        payload["Created_By_Hermes"] = "hermes --sync-zoho-renewals"
        payload["Intake_Source"] = "hermes_renewals_sync"
    return {k: v for k, v in payload.items() if v is not None and v != ""}


def map_deal_to_renewal(
    deal: dict[str, Any],
    *,
    creating: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """CRM Renewals-pipeline Deal → desk Renewals row."""
    pn = str(deal.get("Bound_Policy_Number") or "").strip()
    client = (
        str(deal.get("Insured_Name") or "").strip()
        or _picklist_label(deal.get("Account_Name"))
        or "Unknown client"
    )
    exp = _iso_date(deal.get("Expiration_Date") or deal.get("Closing_Date"))
    lob = _picklist_label(deal.get("Line_of_Business")) or None
    stage_label = _picklist_label(deal.get("Stage"))
    dismissed = stage_label == DEAL_STAGE_LOST
    hermes_id = hermes_id_for_crm_deal(deal)
    name = f"{client} — {pn or 'policy'} {exp or 'renewal'}"
    payload: dict[str, Any] = {
        "Name": name[:255],
        "Hermes_Renewal_ID": hermes_id,
        "Policy_Number": pn or None,
        "Client_Name": client,
        "Expiration_Date": exp,
        "Premium_Current": deal.get("Premium_Actual") or deal.get("Premium_Estimate") or deal.get("Amount"),
        "Premium_Renewal": deal.get("Premium_Estimate") or deal.get("Amount"),
        "Carrier": deal.get("Carrier") or None,
        "Line_of_Business": lob,
        "Effective_Date": _iso_date(deal.get("Effective_Date")),
        "Dismissed": dismissed,
        "Window_Bucket": desk.window_bucket(exp, lob, today=today),
        "Account_Name": _lookup(desk.lookup_id(deal.get("Account_Name"))),
        "Related_Deal": _lookup(str(deal.get("id") or "").strip() or None),
        "Risk_Status": "SAFE",
    }
    if creating:
        if stage_label == DEAL_STAGE_WON:
            payload["Desk_Stage"] = PIPELINE_STAGE_CLOSED
            payload["Disposition"] = "renewed"
        elif dismissed:
            payload["Desk_Stage"] = PIPELINE_STAGE_CLOSED
            payload["Disposition"] = "do_not_renew"
        else:
            payload["Desk_Stage"] = PIPELINE_STAGE_IDENTIFIED
    else:
        payload = {k: v for k, v in payload.items() if k in RENEWAL_SYNC_FIELDS or k == "Related_Deal"}
    return {k: v for k, v in payload.items() if v is not None and v != ""}


def is_renewals_pipeline_deal(deal: dict[str, Any] | None) -> bool:
    if not deal:
        return False
    return _picklist_label(deal.get("Opportunity_Type")) == OPPORTUNITY_TYPE_RENEWALS


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


def _get_record(client: ZohoClient, module: str, record_id: str | None) -> dict[str, Any] | None:
    if not record_id:
        return None
    getter = getattr(client, "get_record", None)
    if callable(getter):
        try:
            row = getter(module, record_id)
        except ZohoClientError:
            log.exception("Zoho get %s/%s failed", module, record_id)
            return None
        return row if isinstance(row, dict) else None
    try:
        rows = client.search_records(
            module, f"(id:equals:{_escape_criteria_value(str(record_id))})"
        )
    except ZohoClientError:
        return None
    return rows[0] if rows else None


def find_renewal_deal(
    client: ZohoClient,
    *,
    hermes_renewal_id: str | None = None,
    policy_number: str | None = None,
    related_deal_id: str | None = None,
) -> dict[str, Any] | None:
    """Locate the Renewals-pipeline Deal for a desk row. Never reuse New Business."""
    if related_deal_id:
        row = _get_record(client, DEALS_MODULE, related_deal_id)
        if is_renewals_pipeline_deal(row):
            return row
    if hermes_renewal_id:
        hit = client._find_first(  # noqa: SLF001
            DEALS_MODULE,
            f"(Hermes_Opportunity_ID:equals:{_escape_criteria_value(hermes_renewal_id)})",
        )
        if is_renewals_pipeline_deal(hit) or (hit and not _picklist_label(hit.get("Opportunity_Type"))):
            return hit
    if policy_number:
        criteria = (
            f"((Bound_Policy_Number:equals:{_escape_criteria_value(policy_number)})"
            f"and(Opportunity_Type:equals:{OPPORTUNITY_TYPE_RENEWALS}))"
        )
        hit = client._find_first(DEALS_MODULE, criteria)  # noqa: SLF001
        if hit:
            return hit
    return None


def find_desk_for_deal(
    client: ZohoClient,
    deal: dict[str, Any],
) -> dict[str, Any] | None:
    deal_id = str(deal.get("id") or "").strip()
    hermes_id = str(deal.get("Hermes_Opportunity_ID") or "").strip()
    pn = str(deal.get("Bound_Policy_Number") or "").strip()
    if deal_id:
        hit = client._find_first(  # noqa: SLF001
            RENEWALS_MODULE,
            f"(Related_Deal:equals:{_escape_criteria_value(deal_id)})",
        )
        if hit:
            return hit
    if hermes_id:
        hit = client._find_first(  # noqa: SLF001
            RENEWALS_MODULE,
            f"(Hermes_Renewal_ID:equals:{_escape_criteria_value(hermes_id)})",
        )
        if hit:
            return hit
    if pn:
        hit = client._find_first(  # noqa: SLF001
            RENEWALS_MODULE,
            f"(Policy_Number:equals:{_escape_criteria_value(pn)})",
        )
        if hit:
            return hit
    return None


def _write_deal(
    client: ZohoClient,
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Create or update a Deal. Uses id when we already matched one."""
    if dry_run:
        if existing and existing.get("id"):
            return {"id": str(existing["id"]), "action": "updated"}
        return {"id": "dry-run", "action": "created"}
    if existing and existing.get("id"):
        updater = getattr(client, "update_record", None)
        if callable(updater):
            return updater(DEALS_MODULE, str(existing["id"]), payload)
        return client.upsert_by_field(
            DEALS_MODULE,
            payload,
            match_field="Hermes_Opportunity_ID",
            match_value=payload.get("Hermes_Opportunity_ID"),
        )
    return client.upsert_by_field(
        DEALS_MODULE,
        payload,
        match_field="Hermes_Opportunity_ID",
        match_value=payload.get("Hermes_Opportunity_ID"),
    )


def _iter_renewal_deals(client: ZohoClient):
    iterator = getattr(client, "iter_records", None)
    criteria = f"(Opportunity_Type:equals:{OPPORTUNITY_TYPE_RENEWALS})"
    if callable(iterator):
        yield from iterator(DEALS_MODULE, criteria=criteria)
        return
    yield from client.search_records(DEALS_MODULE, criteria)


def run_zoho_renewals_sync(
    *,
    supa: Any,
    zoho: ZohoClient | None = None,
    today: date | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> ZohoRenewalsSyncResult:
    """Upsert Renewal_Events, Renewals, and matching Renewals-pipeline Deals."""
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

    linked_deal_ids: set[str] = set()
    p85_policy_numbers: set[str] = set()

    for row in overlaid_p85:
        pn = str(row.get("policy_number") or "").strip()
        rid = str(row.get("id") or "").strip()
        if not pn or not rid:
            result.renewals_skipped += 1
            continue
        p85_policy_numbers.add(pn)
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
            existing_deal = find_renewal_deal(
                zoho,
                hermes_renewal_id=rid,
                policy_number=pn,
                related_deal_id=desk.lookup_id((existing or {}).get("Related_Deal")),
            )
            deal_payload = map_renewal_to_deal(
                row,
                policy=policy,
                account_id=account_id,
                existing_deal=existing_deal,
                desk_row=existing or {},
                today=today,
                creating=existing_deal is None,
            )
            deal_id: str | None = None
            try:
                if not deal_payload.get("Hermes_Opportunity_ID"):
                    result.deals_skipped += 1
                else:
                    written = _write_deal(
                        zoho, deal_payload, existing=existing_deal, dry_run=dry_run
                    )
                    deal_id = str(written.get("id") or "") or None
                    if deal_id == "dry-run":
                        deal_id = desk.lookup_id((existing_deal or {}).get("id")) or "dry-run"
                    if deal_id and deal_id != "dry-run":
                        linked_deal_ids.add(deal_id)
                    if written.get("action") == "created":
                        result.deals_created += 1
                    else:
                        result.deals_updated += 1
            except Exception as exc:  # noqa: BLE001
                log.exception("Renewals-pipeline Deal upsert failed for %s", pn)
                result.errors.append(f"deal {pn}: {exc}")

            payload = map_projection_to_renewal(
                row,
                policy=policy,
                account_id=account_id,
                policy_id=policy_id,
                event_id=event_by_pn.get(pn),
                today=today,
                creating=creating,
            )
            linked = desk.lookup_id((existing or {}).get("Related_Deal"))
            if deal_id and deal_id != "dry-run" and not linked:
                payload["Related_Deal"] = _lookup(deal_id)
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

    try:
        for deal in _iter_renewal_deals(zoho):
            deal_id = str(deal.get("id") or "").strip()
            if not deal_id:
                result.pipeline_desk_skipped += 1
                continue
            if deal_id in linked_deal_ids:
                continue
            pn = str(deal.get("Bound_Policy_Number") or "").strip()
            if pn in p85_policy_numbers:
                # Book pass already created/linked the pair.
                continue
            existing_desk = find_desk_for_deal(zoho, deal)
            if existing_desk:
                patch: dict[str, Any] = {}
                if not desk.lookup_id(existing_desk.get("Related_Deal")):
                    patch["Related_Deal"] = _lookup(deal_id)
                stage_label = _picklist_label(deal.get("Stage"))
                dismissed = _as_bool(existing_desk.get("Dismissed"))
                if stage_label == DEAL_STAGE_LOST and not dismissed:
                    patch["Dismissed"] = True
                elif stage_label != DEAL_STAGE_LOST and dismissed:
                    patch["Dismissed"] = False
                hermes_id = str(existing_desk.get("Hermes_Renewal_ID") or "").strip()
                if not patch or not hermes_id:
                    result.pipeline_desk_skipped += 1
                    continue
                if dry_run:
                    result.pipeline_desk_updated += 1
                    continue
                zoho.upsert_by_field(
                    RENEWALS_MODULE, patch, match_field="Hermes_Renewal_ID", match_value=hermes_id
                )
                result.pipeline_desk_updated += 1
                continue
            if not pn:
                result.pipeline_desk_skipped += 1
                result.errors.append(
                    f"pipeline deal {deal_id}: no Bound_Policy_Number; cannot create desk row"
                )
                continue
            payload = map_deal_to_renewal(deal, creating=True, today=today)
            hermes_id = str(payload.get("Hermes_Renewal_ID") or "").strip()
            if dry_run:
                result.pipeline_desk_created += 1
                continue
            upserted = zoho.upsert_by_field(
                RENEWALS_MODULE,
                payload,
                match_field="Hermes_Renewal_ID",
                match_value=hermes_id,
            )
            linked_deal_ids.add(deal_id)
            if upserted.get("action") == "created":
                result.pipeline_desk_created += 1
            else:
                result.pipeline_desk_updated += 1
            if hermes_id and hermes_id != str(deal.get("Hermes_Opportunity_ID") or ""):
                try:
                    _write_deal(
                        zoho,
                        {"Hermes_Opportunity_ID": hermes_id},
                        existing=deal,
                        dry_run=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception("back-write Hermes_Opportunity_ID failed for deal %s", deal_id)
                    result.errors.append(f"deal {deal_id} hermes id: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("Renewals-pipeline → desk reverse sync failed")
        result.errors.append(f"pipeline→desk: {exc}")

    return result
