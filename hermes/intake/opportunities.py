"""Opportunities pipeline — the Supabase-native sales pipeline, mirroring the
NowCerts Opportunity object so a deal can be worked from either side.

Stages and Opportunity Type come straight from NowCerts. There are TWO stage sets
— new-business vs renewal — selected by ``opportunity_type`` (so the cockpit shows
them on separate boards). Win likelihood is a NowCerts-required categorical; on our
side it's a stage-driven percentage (``probability``) mapped to that category,
defaulted to ``Good`` so a NowCerts save never blocks, and editable in the CRM
(not synced back to the AMS). ``disposition`` is a free-text outcome (the NowCerts
dropdown is currently empty) — not a stage. ``referral_source`` is READ-ONLY,
pulled from NowCerts by the sync (not editable in the CRM).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient

TABLE = "opportunities"

# --- Opportunity types (NowCerts vocab) --------------------------------------
TYPE_NEW_BUSINESS = "New Business"
TYPE_RENEWALS = "Renewals"
OPPORTUNITY_TYPES = (
    "Bundling", "Competitive Replacements (BOR)", "Cross-selling", "Life Events",
    TYPE_NEW_BUSINESS, "Remarket", TYPE_RENEWALS, "Seasonal / Event", "Upselling",
)
# Types worked on the RENEWAL pipeline; every other type is new-business.
RENEWAL_TYPES = frozenset({TYPE_RENEWALS})

# --- Stages (NowCerts vocab), per pipeline -----------------------------------
# New business / acquisition (ordered).
STAGE_PREP = "Preparing Application"
STAGE_NOT_ASSIGNED = "Not Assigned"
STAGE_SENT_QUOTING = "Sent For Quoting"
STAGE_QUOTES_RECEIVED = "Quotes Received"
STAGE_SENT_PROPOSAL = "Sent Proposal"
STAGE_REQUEST_BIND = "Request to Bind"
STAGE_BOUND = "Bound / Won"   # NowCerts' terminal new-business win stage (verbatim)
STAGE_LOST = "Lost"
NEW_BUSINESS_STAGES = (
    STAGE_NOT_ASSIGNED, STAGE_PREP, STAGE_SENT_QUOTING, STAGE_QUOTES_RECEIVED,
    STAGE_SENT_PROPOSAL, STAGE_REQUEST_BIND, STAGE_BOUND, STAGE_LOST,
)
# Renewal (ordered).
STAGE_RENEWAL_90 = "Renewal in 90 days"
STAGE_RENEWAL_60 = "Renewal in 60 days"
STAGE_RENEWAL_30 = "Renewal in 30 days"
STAGE_REQUOTE_RENEWAL = "Requote Renewal"
STAGE_ANNUAL_REVIEW = "Annual Policy Review"
STAGE_COMPLETE_RENEWAL = "Complete/Auto-Renewal"
STAGE_NOT_RENEWED = "Not Renewed"
RENEWAL_STAGES = (
    STAGE_RENEWAL_90, STAGE_RENEWAL_60, STAGE_RENEWAL_30, STAGE_REQUOTE_RENEWAL,
    STAGE_ANNUAL_REVIEW, STAGE_COMPLETE_RENEWAL, STAGE_BOUND, STAGE_NOT_RENEWED,
)
# All valid stages (dedup, order-preserving) — for loose, type-agnostic validation.
STAGES = tuple(dict.fromkeys(NEW_BUSINESS_STAGES + RENEWAL_STAGES))

# "Bound" (pre-alignment value) kept as a won synonym for already-migrated rows.
WON_STAGES = frozenset({STAGE_BOUND, "Bound", STAGE_COMPLETE_RENEWAL})
LOST_STAGES = frozenset({STAGE_LOST, STAGE_NOT_RENEWED})

STATUS_OPEN = "open"
STATUS_WON = "won"
STATUS_LOST = "lost"

# NowCerts prospect_type / insured_type vocab (observed live).
PROSPECT_TYPES = ("Prospect", "Hot_Prospect", "Cold_Prospect")
INSURED_TYPES = ("Personal", "Commercial")

# --- Win likelihood (NowCerts required) <-> stage-driven percentage ----------
LIKELIHOOD_EXCELLENT = "Excellent"
LIKELIHOOD_VERY_GOOD = "Very Good"
LIKELIHOOD_GOOD = "Good"
LIKELIHOOD_MODERATE = "Moderate"
LIKELIHOOD_NOT_LIKELY = "Not Likely"
LIKELIHOODS = (
    LIKELIHOOD_EXCELLENT, LIKELIHOOD_VERY_GOOD, LIKELIHOOD_GOOD,
    LIKELIHOOD_MODERATE, LIKELIHOOD_NOT_LIKELY,
)
DEFAULT_LIKELIHOOD = LIKELIHOOD_GOOD  # keeps a NowCerts save from ever blocking

# Stage → default win probability % (the pipeline drives the number).
STAGE_PROBABILITY = {
    STAGE_NOT_ASSIGNED: 5, STAGE_PREP: 10, STAGE_SENT_QUOTING: 25, STAGE_QUOTES_RECEIVED: 50,
    STAGE_SENT_PROPOSAL: 65, STAGE_REQUEST_BIND: 85, STAGE_BOUND: 100, STAGE_LOST: 0,
    STAGE_RENEWAL_90: 40, STAGE_RENEWAL_60: 55, STAGE_RENEWAL_30: 70,
    STAGE_REQUOTE_RENEWAL: 60, STAGE_ANNUAL_REVIEW: 50, STAGE_COMPLETE_RENEWAL: 100, STAGE_NOT_RENEWED: 0,
}


def stages_for_type(opportunity_type: str) -> tuple[str, ...]:
    """The ordered stage set for a type — renewal set for Renewals, else new-business."""
    return RENEWAL_STAGES if opportunity_type in RENEWAL_TYPES else NEW_BUSINESS_STAGES


def default_stage_for_type(opportunity_type: str) -> str:
    # A manually-created opp opens at a working stage, not the 'Not Assigned' column.
    return STAGE_RENEWAL_90 if opportunity_type in RENEWAL_TYPES else STAGE_PREP


def probability_for_stage(stage: str) -> int:
    return STAGE_PROBABILITY.get(stage, 0)


def likelihood_for_probability(pct: int | None) -> str:
    """Map a win-probability % to the NowCerts likelihood category."""
    if pct is None:
        return DEFAULT_LIKELIHOOD
    if pct >= 90:
        return LIKELIHOOD_EXCELLENT
    if pct >= 70:
        return LIKELIHOOD_VERY_GOOD
    if pct >= 45:
        return LIKELIHOOD_GOOD
    if pct >= 20:
        return LIKELIHOOD_MODERATE
    return LIKELIHOOD_NOT_LIKELY


def status_for_stage(stage: str) -> str:
    """Classify a stage as won/lost/open. Predicate-based so it works for any
    NowCerts stage string (e.g. 'Bound / Won', 'Complete/Auto-Renewal', 'Not
    Renewed'), not just our known enum."""
    s = str(stage or "").strip().lower()
    if stage in WON_STAGES or "won" in s or "bound" in s or s.startswith("complete"):
        return STATUS_WON
    if stage in LOST_STAGES or "lost" in s or "not renewed" in s or "dead" in s:
        return STATUS_LOST
    return STATUS_OPEN


# --- Dates -------------------------------------------------------------------
# Where a projected close date comes from, best source first. A board without a
# date on the card is a list of names — you cannot see what lands this month, and
# a deal whose date went by yesterday looks exactly like one that closes in June.
#
# NowCerts has no estimated-close field, so the forecast (expected_close_date) is
# CRM-owned and set by hand. Until someone sets it, fall back to the AMS dates
# that bound it. Each is reported with the basis it came from — a date nobody
# chose must not be presented as a commitment.
#
# The fallback differs by pipeline, because the two are closing different things.
# New business closes when cover has to START: neededBy, else the quote's
# effective date. A renewal closes when the expiring policy ENDS — its effective
# date is when the term being renewed BEGAN, which is in the past and reads as a
# deal that slipped months ago.
_CLOSE_DATE_SOURCES = (
    ("expected_close_date", "set"),
    ("needed_by", "needed by"),
    ("effective_date", "effective"),
)
_RENEWAL_CLOSE_DATE_SOURCES = (
    ("expected_close_date", "set"),
    ("expiration_date", "expires"),
    ("needed_by", "needed by"),
)


def projected_close(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """(date, basis) for an opportunity's projected close — see the source tuples.

    Returns (None, None) when the row carries no date at all, rather than
    inventing one from the stage.
    """
    renewal = str(row.get("opportunity_type") or "").strip() in RENEWAL_TYPES
    sources = _RENEWAL_CLOSE_DATE_SOURCES if renewal else _CLOSE_DATE_SOURCES
    for field, basis in sources:
        value = str(row.get(field) or "").strip()
        if value:
            return value[:10], basis
    return None, None


def with_projected_close(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate rows in place with ``projected_close_date`` + ``projected_close_basis``."""
    for row in rows:
        date_value, basis = projected_close(row)
        row["projected_close_date"] = date_value
        row["projected_close_basis"] = basis
    return rows


# --- What kind of deal this is, from who it is with -------------------------
TYPE_CROSS_SELL = "Cross-selling"
TYPE_UPSELL = "Upselling"


def derive_opportunity_type(supa: "SupabaseClient", insured_id: str | None, line_of_business: str) -> str:
    """New business, cross-sell or upsell — decided by the AMS id, not by hand.

    Whether a deal is new business is not a matter of opinion: it depends on
    whether the other party is already a client, and a client is exactly someone
    who has a NowCerts insured id. So:

      * no id            → a prospect            → New Business
      * id, no policy in this line → an existing client buying something new → Cross-selling
      * id, already has this line  → more of what they have → Upselling

    Chosen by hand, this drifts — everything gets typed New Business, and then the
    board cannot tell you how much of the pipeline is growth of the existing book
    versus genuinely new names. Falls back to Cross-selling (never New Business)
    when the book cannot be read: the id already proves they are a client, which
    is the part that matters.
    """
    if not str(insured_id or "").strip():
        return TYPE_NEW_BUSINESS
    want = _norm_lob(line_of_business)
    try:
        rows = supa.select(
            "canonical_policies",
            columns="lines_of_business,active",
            params={"nowcerts_insured_guid": f"eq.{insured_id}"},
            limit=500,
        )
    except Exception:  # noqa: BLE001 — see the docstring
        return TYPE_CROSS_SELL
    for row in rows:
        if row.get("active") and _norm_lob(row.get("lines_of_business")) == want:
            return TYPE_UPSELL
    return TYPE_CROSS_SELL


def _norm_lob(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def make_client_identifier(name: str | None, fein: str | None = None) -> str:
    """Stable idempotency key from a client name (+ FEIN when present)."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    digits = re.sub(r"\D", "", str(fein or ""))
    if digits:
        return f"{base or 'unknown'}:{digits}"
    return base or "unknown"


def create_opportunity(
    supa: "SupabaseClient",
    *,
    client_identifier: str,
    line_of_business: str,
    opportunity_type: str = TYPE_NEW_BUSINESS,
    insured_name: str | None = None,
    insured_id: str | None = None,
    prospect_type: str | None = None,
    insured_type: str | None = None,
    stage: str | None = None,
    premium_estimate: float | None = None,
    carrier: str | None = None,
    lead_source: str | None = None,
    referral_source: str | None = None,
    assigned_to: str | None = None,
    assigned_to_email: str | None = None,
    next_action: str | None = None,
    description: str | None = None,
    probability: int | None = None,
    likelihood: str | None = None,
    disposition: str | None = None,
    expected_close_date: str | None = None,
    # When the coverage being chased starts and ends. ``expiration_date`` is the
    # x-date — the reason a deal has a deadline at all, and what the lead station
    # carries across on conversion so a converted lead does not arrive undated.
    effective_date: str | None = None,
    expiration_date: str | None = None,
    source: str | None = None,
    created_by: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Idempotent per (client_identifier, line_of_business, opportunity_type).

    ``stage`` defaults to the first stage of the type's pipeline. ``probability``
    defaults from the stage; ``likelihood`` defaults from the probability (→ Good).
    Returns (row, created).
    """
    if opportunity_type not in OPPORTUNITY_TYPES:
        raise ValueError(f"Unknown opportunity_type '{opportunity_type}'; must be one of {list(OPPORTUNITY_TYPES)}")
    if not client_identifier or not line_of_business:
        raise ValueError("client_identifier and line_of_business are required")
    valid_stages = stages_for_type(opportunity_type)
    if stage is None:
        stage = default_stage_for_type(opportunity_type)
    if stage not in valid_stages:
        raise ValueError(f"Unknown stage '{stage}' for type '{opportunity_type}'; must be one of {list(valid_stages)}")
    if likelihood is not None and likelihood not in LIKELIHOODS:
        raise ValueError(f"Unknown likelihood '{likelihood}'; must be one of {list(LIKELIHOODS)}")

    existing = supa.select(
        TABLE,
        columns="*",
        params={
            "client_identifier": f"eq.{client_identifier}",
            "line_of_business": f"eq.{line_of_business}",
            "opportunity_type": f"eq.{opportunity_type}",
        },
        limit=1,
    )
    if existing:
        return existing[0], False

    if probability is None:
        probability = probability_for_stage(stage)
    if likelihood is None:
        likelihood = likelihood_for_probability(probability)

    row = supa.insert(
        TABLE,
        {
            "client_identifier": client_identifier,
            "line_of_business": line_of_business,
            "opportunity_type": opportunity_type,
            "insured_name": insured_name,
            "insured_id": insured_id,
            "prospect_type": prospect_type,
            "insured_type": insured_type,
            "stage": stage,
            "status": status_for_stage(stage),
            "premium_estimate": premium_estimate,
            "carrier": carrier,
            "lead_source": lead_source,
            "referral_source": referral_source,
            "assigned_to": assigned_to,
            "assigned_to_email": assigned_to_email,
            "next_action": next_action,
            "description": description,
            "probability": probability,
            "likelihood": likelihood,
            "disposition": disposition,
            "expected_close_date": expected_close_date,
            "effective_date": effective_date,
            "expiration_date": expiration_date,
            "source": source,
            "created_by": created_by,
        },
    )
    return row, True


# --- A deal's timeline -------------------------------------------------------
EVENTS_TABLE = "opportunity_events"

EVENT_NOTE = "note"
EVENT_STAGE = "stage"
EVENT_CREATED = "created"
EVENT_AMS = "ams"


def log_event(
    supa: "SupabaseClient",
    opportunity_id: str,
    *,
    summary: str,
    event_type: str = EVENT_NOTE,
    details: dict[str, Any] | None = None,
    actor_email: str | None = None,
) -> dict[str, Any] | None:
    """Append to a deal's timeline. Best-effort: never fail the thing being logged.

    A stage move that succeeded but could not be written to the timeline is still a
    stage move — losing the audit line is bad, refusing the move because of it is
    worse.
    """
    try:
        return supa.insert(EVENTS_TABLE, {
            "opportunity_id": opportunity_id,
            "event_type": event_type,
            "summary": summary,
            "details": details or {},
            "actor_email": actor_email,
        })
    except Exception:  # noqa: BLE001 — see the docstring
        import logging

        logging.getLogger(__name__).exception("opportunity event log failed: %s", opportunity_id)
        return None


def list_events(supa: "SupabaseClient", opportunity_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """A deal's timeline, newest first."""
    return supa.select(
        EVENTS_TABLE, columns="*",
        params={"opportunity_id": f"eq.{opportunity_id}", "order": "created_at.desc"},
        limit=limit,
    )


def advance_stage(
    supa: "SupabaseClient",
    opportunity_id: str,
    stage: str,
    *,
    lost_reason: str | None = None,
    moved_by: str | None = None,
) -> dict[str, Any]:
    """Move an opportunity to *stage*, syncing status + the stage-driven win
    probability/likelihood (won = Bound/Complete·Auto-Renewal, lost = Lost/Not Renewed).

    The move is written to the deal's timeline with who made it — a stage change
    applied in place, with the previous value overwritten, is unauditable, and Lost
    is exactly the stage someone will later want explained."""
    # Any non-empty stage is accepted — NowCerts is the source of truth for the
    # pipeline vocabulary, so we don't gate drag-to-stage on our known enum.
    stage = str(stage or "").strip()
    if not stage:
        raise ValueError("stage is required")
    # Read the old stage BEFORE the update: "moved to Lost" is half a fact, and the
    # row is about to stop being able to tell us where it came from.
    try:
        prior = supa.select(TABLE, columns="stage", params={"id": f"eq.{opportunity_id}"}, limit=1)
        was = str((prior[0] if prior else {}).get("stage") or "") or None
    except Exception:  # noqa: BLE001 — the move matters more than its provenance
        was = None
    pct = probability_for_stage(stage)
    payload: dict[str, Any] = {
        "stage": stage,
        "status": status_for_stage(stage),
        "probability": pct,
        "likelihood": likelihood_for_probability(pct),
        # A CRM stage move claims the row — the inbound AMS sync must stop overwriting
        # it (the CRM is now the persistent working copy). Pairs with the sync's
        # sync_source='crm' skip.
        "sync_source": "crm",
    }
    if status_for_stage(stage) == STATUS_LOST and lost_reason:
        payload["lost_reason"] = lost_reason
    row = supa.update(TABLE, opportunity_id, payload)

    summary = f"Moved from {was} to {stage}" if was and was != stage else f"Moved to {stage}"
    if lost_reason:
        summary += f" — {lost_reason}"
    log_event(
        supa, opportunity_id,
        event_type=EVENT_STAGE, summary=summary, actor_email=moved_by,
        details={"from": was, "to": stage, "status": payload["status"], "lost_reason": lost_reason},
    )
    return row


def link_nowcerts(
    supa: "SupabaseClient",
    opportunity_id: str,
    *,
    insured_id: str | None = None,
    quote_number: str | None = None,
    nowcerts_quote_guid: str | None = None,
) -> dict[str, Any]:
    """Backfill NowCerts identifiers once the insured/quote is created."""
    payload = {
        k: v
        for k, v in {
            "insured_id": insured_id,
            "quote_number": quote_number,
            "nowcerts_quote_guid": nowcerts_quote_guid,
        }.items()
        if v is not None
    }
    if not payload:
        return {}
    return supa.update(TABLE, opportunity_id, payload)


def list_opportunities(
    supa: "SupabaseClient",
    *,
    stage: str | None = None,
    status: str | None = STATUS_OPEN,
    opportunity_type: str | None = None,
    assigned_to: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"order": "updated_at.desc"}
    if stage:
        params["stage"] = f"eq.{stage}"
    if status:
        params["status"] = f"eq.{status}"
    if opportunity_type:
        params["opportunity_type"] = f"eq.{opportunity_type}"
    if assigned_to:
        params["assigned_to"] = f"eq.{assigned_to}"
    return supa.select(TABLE, columns="*", params=params, limit=limit)
