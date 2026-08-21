"""Creator Renewals Desk — working-state vocab and derived buckets.

The Zoho Creator desk and ``hermes --sync-zoho-renewals`` share these values so
the CRM picklists, Deluge guards, and Python mapping cannot drift. Eligibility
and AMS writes still live in ``eligibility.py`` / ``executor.py``; this module
is only the workstation overlay (stage, disposition, recommended action, window).
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

from hermes.renewals.config import (
    DISPOSITION_DO_NOT_RENEW,
    DISPOSITION_LOST_COVERAGE,
    DISPOSITION_LOST_NO_RESPONSE,
    DISPOSITION_LOST_PRICE,
    DISPOSITION_RENEWED,
    DISPOSITION_REWRITTEN,
    PIPELINE_STAGE_CLOSED,
    PIPELINE_STAGE_IDENTIFIED,
    PIPELINE_STAGE_NEGOTIATING,
    PIPELINE_STAGE_OUTREACH_SENT,
    PIPELINE_STAGE_PROPOSAL_SENT,
    PIPELINE_STAGE_QUOTE_REQUESTED,
    TERMINAL_DISPOSITIONS,
)

# Ordered desk stages — never skip. Moving backward requires producer identity.
DESK_STAGES: tuple[str, ...] = (
    PIPELINE_STAGE_IDENTIFIED,
    PIPELINE_STAGE_OUTREACH_SENT,
    PIPELINE_STAGE_QUOTE_REQUESTED,
    PIPELINE_STAGE_PROPOSAL_SENT,
    PIPELINE_STAGE_NEGOTIATING,
    PIPELINE_STAGE_CLOSED,
)

DISPOSITIONS: tuple[str, ...] = (
    DISPOSITION_RENEWED,
    DISPOSITION_REWRITTEN,
    DISPOSITION_LOST_PRICE,
    DISPOSITION_LOST_COVERAGE,
    DISPOSITION_LOST_NO_RESPONSE,
    DISPOSITION_DO_NOT_RENEW,
)

RECOMMENDED_ACTIONS: tuple[str, ...] = (
    "RETAIN_AS_IS",
    "RETAIN_WITH_NEGOTIATION",
    "REMARKET_SAMPLE",
    "REMARKET_FULL",
    "ESCALATE_HUMAN",
    "MOVE_TO_AT_RISK_LIST",
)

WINDOW_90 = "90"
WINDOW_60 = "60"
WINDOW_30 = "30"
WINDOW_PERSONAL = "personal"
WINDOW_PAST_DUE = "past_due"
WINDOW_BUCKETS: tuple[str, ...] = (
    WINDOW_90,
    WINDOW_60,
    WINDOW_30,
    WINDOW_PERSONAL,
    WINDOW_PAST_DUE,
)

# Same LOB detector as the worklist API (hermes/routers/renewals.py). The
# `segment` column mislabels personal policies, so line_of_business is the key.
PERSONAL_LOB_RE = re.compile(
    r"(personal auto|personalauto|personsl auto|homeowner|dwelling fire|"
    r"motorcycle|personal umbrella|condo owners)",
    re.I,
)

EXECUTOR_ACTIONS: tuple[str, ...] = (
    "request_terms",
    "prepare_options",
    "client_follow_up",
    "update_ams",
)
NON_MUTATING_ACTIONS = frozenset({"prepare_options"})

PROJECTION_EDITABLE = (
    "client_name",
    "premium_current",
    "premium_renewal",
    "risk_status",
    "expiration_date",
    "last_contact_date",
    "ai_strategy_notes",
)

# Zoho Renewals field → project_85 / overlay field.
ZOHO_CORRECTABLE = {
    "Client_Name": "client_name",
    "Premium_Current": "premium_current",
    "Premium_Renewal": "premium_renewal",
    "Risk_Status": "risk_status",
    "Expiration_Date": "expiration_date",
    "Last_Contact_Date": "last_contact_date",
    "Strategy_Notes": "ai_strategy_notes",
}

DEFAULT_TASK_TITLES: tuple[str, ...] = (
    "Pull renewal declaration & review exposures",
    "Request renewal terms from carrier",
    "Prepare renewal options / comparison",
    "Send renewal review to client",
    "Update AMS (NowCerts) & file worksheet",
)
# OS checkpoints (and live Catalyst subject aliases) live in operating.py.
# Keep these five titles stable — they are already in Zoho CRM.


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def is_personal_lob(lob: str | None) -> bool:
    return bool(PERSONAL_LOB_RE.search(lob or ""))


def days_to_expiration(expiration: date | str | None, *, today: date | None = None) -> int | None:
    """Signed days until x-date. Negative means past due. None if undated."""
    if expiration is None or expiration == "":
        return None
    raw = expiration if isinstance(expiration, date) else date.fromisoformat(str(expiration)[:10])
    return (raw - (today or date.today())).days


def window_bucket(
    expiration: date | str | None,
    lob: str | None = None,
    *,
    today: date | None = None,
) -> str | None:
    """Desk KPI bucket. Personal LOB is always ``personal`` (even when past due)."""
    days = days_to_expiration(expiration, today=today)
    if days is None:
        return None
    if is_personal_lob(lob):
        return WINDOW_PERSONAL if days >= 0 else WINDOW_PAST_DUE
    if days < 0:
        return WINDOW_PAST_DUE
    if days <= 30:
        return WINDOW_30
    if days <= 60:
        return WINDOW_60
    return WINDOW_90


def stage_index(stage: str | None) -> int:
    label = (stage or "").strip()
    try:
        return DESK_STAGES.index(label)
    except ValueError:
        raise ValueError(f"unknown desk stage {stage!r}; must be one of {list(DESK_STAGES)}")


def stage_change_allowed(
    current: str | None,
    proposed: str | None,
    *,
    producer_confirmed: bool = False,
) -> tuple[bool, str]:
    """Never skip. Backward moves require ``producer_confirmed``.

    Same stage is a no-op (allowed). Unknown stages are refused.
    """
    try:
        here = stage_index(current or PIPELINE_STAGE_IDENTIFIED)
        there = stage_index(proposed)
    except ValueError as exc:
        return False, str(exc)
    if there == here:
        return True, "unchanged"
    if there == here + 1:
        return True, "advance"
    if there > here + 1:
        return False, "cannot skip desk stages"
    if producer_confirmed:
        return True, "backward_with_producer"
    return False, "moving backward requires producer confirmation"


def disposition_required(stage: str | None) -> bool:
    return (stage or "").strip() == PIPELINE_STAGE_CLOSED


def disposition_ok(value: str | None) -> bool:
    return (value or "").strip() in TERMINAL_DISPOSITIONS


def recommended_action_ok(value: str | None) -> bool:
    if value is None or str(value).strip() == "":
        return True
    return str(value).strip() in RECOMMENDED_ACTIONS


def executor_action_ok(action: str | None) -> bool:
    return (action or "").strip() in EXECUTOR_ACTIONS


def lookup_id(value: Any) -> str | None:
    """Zoho lookup field → record id."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        rid = value.get("id")
        return str(rid) if rid else None
    return str(value)


def linked_deal_id(row: dict[str, Any] | None) -> str | None:
    """Pipeline join. Live Catalyst uses ``Deal_Id``; the field pack names it ``Related_Deal``."""
    row = row or {}
    return lookup_id(row.get("Related_Deal")) or lookup_id(row.get("Deal_Id"))


def has_pipeline_deal(row: dict[str, Any] | None) -> bool:
    """Worklist membership: hide desk-only leftovers with no Renewals-pipeline Deal."""
    return linked_deal_id(row) is not None
