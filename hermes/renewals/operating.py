"""Renewal Operating System overlay on top of stored Desk_Stage.

Zoho CRM still stores ``Desk_Stage`` / ``Stage`` as the six values in
``hermes.renewals.desk.DESK_STAGES``. This module is the workstation layer:
6 operating labels, checkpoints, scorecard rails, and the rule that a
checkpoint complete may advance *one* stage only when that stage's required
checkpoints are all done. Hermes / nightly sync must pass ``actor="hermes"``
so they never advance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from hermes.renewals.config import (
    PIPELINE_STAGE_CLOSED,
    PIPELINE_STAGE_IDENTIFIED,
    PIPELINE_STAGE_NEGOTIATING,
    PIPELINE_STAGE_OUTREACH_SENT,
    PIPELINE_STAGE_PROPOSAL_SENT,
    PIPELINE_STAGE_QUOTE_REQUESTED,
)
from hermes.renewals.desk import (
    DESK_STAGES,
    disposition_ok,
    stage_change_allowed,
)

ACTOR_USER = "user"
ACTOR_HERMES = "hermes"

STATUS_NOT_STARTED = "Not Started"
STATUS_IN_PROGRESS = "In Progress"
STATUS_WAITING = "Waiting"
STATUS_BLOCKED = "Blocked"
STATUS_COMPLETE = "Complete"
CHECKPOINT_STATUSES: tuple[str, ...] = (
    STATUS_NOT_STARTED,
    STATUS_IN_PROGRESS,
    STATUS_WAITING,
    STATUS_BLOCKED,
    STATUS_COMPLETE,
)

COMPLETE_ALIASES = frozenset({
    "complete", "completed", "done", "closed", "true", "1",
})

# Stored Desk_Stage → CSR-facing operating label. Do not rename the stored enum.
OPERATING_STAGES: tuple[dict[str, str], ...] = (
    {"stage": PIPELINE_STAGE_IDENTIFIED, "label": "Review Account"},
    {"stage": PIPELINE_STAGE_OUTREACH_SENT, "label": "Pre-Renewal Outreach"},
    {"stage": PIPELINE_STAGE_QUOTE_REQUESTED, "label": "Market Renewal"},
    {"stage": PIPELINE_STAGE_PROPOSAL_SENT, "label": "Build Renewal Options"},
    {"stage": PIPELINE_STAGE_NEGOTIATING, "label": "Present Renewal"},
    {"stage": PIPELINE_STAGE_CLOSED, "label": "Close Renewal"},
)

# Live Catalyst close UI — keep these six labels. Do not fork a second picklist.
OS_DISPOSITIONS: tuple[tuple[str, str], ...] = (
    ("renewed", "Renewed"),
    ("rewritten", "Rewritten"),
    ("lost_price", "Lost — Price"),
    ("lost_coverage", "Lost — Coverage"),
    ("lost_no_response", "Lost — No response"),
    ("do_not_renew", "Do not renew"),
)

# Names that must map onto the live six — never shown as extra close options.
DISPOSITION_ALIASES: dict[str, str] = {
    "marketed": "rewritten",
    "cancelled": "do_not_renew",
    "canceled": "do_not_renew",
    "lost to competitor": "lost_price",
    "non-renewed": "do_not_renew",
    "non renewed": "do_not_renew",
    "nonrenewed": "do_not_renew",
}

RAIL_ACCOUNT = "account_reviewed"
RAIL_OUTREACH = "outreach_completed"
RAIL_MARKETS = "markets_requested"
RAIL_QUOTES = "quotes_received"
RAIL_PROPOSAL = "proposal_pending"
RAIL_DECISION = "client_decision_pending"
RAIL_CLOSED = "closed"

SCORECARD_RAILS: tuple[dict[str, str], ...] = (
    {"key": RAIL_ACCOUNT, "label": "Account Reviewed"},
    {"key": RAIL_OUTREACH, "label": "Outreach Completed"},
    {"key": RAIL_MARKETS, "label": "Markets Requested"},
    {"key": RAIL_QUOTES, "label": "Quotes Received"},
    {"key": RAIL_PROPOSAL, "label": "Proposal Pending"},
    {"key": RAIL_DECISION, "label": "Client Decision Pending"},
    {"key": RAIL_CLOSED, "label": "Closed"},
)

RAIL_DONE = "done"
RAIL_ACTIVE = "active"
RAIL_EMPTY = "empty"


@dataclass(frozen=True)
class CheckpointDef:
    key: str
    title: str
    stage: str
    required: bool
    complete_rule: str
    aliases: tuple[str, ...] = ()
    owner_role: str = "csr"
    detail: str = ""


# Live Catalyst/Zoho task subjects (keep seeding these) + repo Deluge titles.
_PULL_DEC_ALIASES = (
    "Pull the expiring declaration and review exposures",
    "Pull renewal declaration & review exposures",
)
_REQUEST_TERMS_ALIASES = (
    "Request renewal terms from the carrier",
    "Request renewal terms from carrier",
)
_BUILD_OPTIONS_ALIASES = (
    "Build the renewal options and premium-change explanation",
    "Prepare renewal options / comparison",
)
_SEND_REVIEW_ALIASES = (
    "Send the renewal review and get the client's decision",
    "Send renewal review to client",
)
_CLOSE_PREMIUM_ALIASES = (
    "Enter Premium Renewal and mark Won or Lost",
    "Update AMS (NowCerts) & file worksheet",
)


CHECKPOINTS: tuple[CheckpointDef, ...] = (
    CheckpointDef(
        "verify_customer_info", "Verify customer info",
        PIPELINE_STAGE_IDENTIFIED, True, "all",
        detail="Confirm named insured, contacts, and account match the file.",
    ),
    CheckpointDef(
        "verify_policy_info", "Verify policy info",
        PIPELINE_STAGE_IDENTIFIED, True, "all",
        aliases=_PULL_DEC_ALIASES,
        detail="Confirm policy number, carrier, LOB, dates, and limits.",
    ),
    CheckpointDef(
        "review_claims_history", "Review claims history",
        PIPELINE_STAGE_IDENTIFIED, True, "all",
        detail="Pull loss runs / claims notes. Do not invent a claim.",
    ),
    CheckpointDef(
        "review_renewability", "Review renewability",
        PIPELINE_STAGE_IDENTIFIED, True, "all",
        detail="Incumbent appetite, non-renew notice, or remarket flag.",
    ),
    CheckpointDef(
        "review_current_premium", "Review current premium",
        PIPELINE_STAGE_IDENTIFIED, True, "all",
        detail="Confirm Premium Current on the desk matches the dec.",
    ),
    CheckpointDef(
        "review_renewal_timeline", "Review renewal timeline",
        PIPELINE_STAGE_IDENTIFIED, True, "all",
        detail="Confirm x-date, window bucket, and who owns the next touch.",
    ),
    CheckpointDef(
        "send_questionnaire", "Send questionnaire",
        PIPELINE_STAGE_OUTREACH_SENT, False, "customer_response",
        detail="Send the renewal questionnaire. Do not auto-email from Hermes.",
    ),
    CheckpointDef(
        "gather_exposure_changes", "Gather exposure changes",
        PIPELINE_STAGE_OUTREACH_SENT, False, "customer_response",
        detail="Drivers, locations, payroll, vehicles, or property changes.",
    ),
    CheckpointDef(
        "verify_contact_info", "Verify contact info",
        PIPELINE_STAGE_OUTREACH_SENT, False, "customer_response",
        detail="Phone, email, and decision maker are current.",
    ),
    CheckpointDef(
        "record_customer_response", "Record customer response",
        PIPELINE_STAGE_OUTREACH_SENT, True, "customer_response",
        detail="Log that the customer replied (or that we are waiting).",
    ),
    CheckpointDef(
        "request_carrier_terms", "Request carrier terms",
        PIPELINE_STAGE_QUOTE_REQUESTED, False, "carrier_response",
        aliases=_REQUEST_TERMS_ALIASES,
        detail="Request incumbent terms. Queues AMS via Hermes; never writes NowCerts.",
    ),
    CheckpointDef(
        "request_alternative_quotes", "Request alternative quotes",
        PIPELINE_STAGE_QUOTE_REQUESTED, False, "carrier_response",
        detail="Remarket only when recommended. Still a queue, not an AMS write.",
    ),
    CheckpointDef(
        "record_carrier_responses", "Record carrier responses",
        PIPELINE_STAGE_QUOTE_REQUESTED, True, "carrier_response",
        detail="At least one carrier response is on the file.",
    ),
    CheckpointDef(
        "follow_up_pending_markets", "Follow up pending markets",
        PIPELINE_STAGE_QUOTE_REQUESTED, False, "carrier_response",
        detail="Chase markets still outstanding.",
    ),
    CheckpointDef(
        "analyze_carrier_response", "Analyze carrier response",
        PIPELINE_STAGE_PROPOSAL_SENT, False, "proposal_package",
        detail="Read terms, deductibles, exclusions, and premium.",
    ),
    CheckpointDef(
        "compare_alternatives", "Compare alternatives",
        PIPELINE_STAGE_PROPOSAL_SENT, False, "proposal_package",
        detail="Side-by-side. Do not invent a quote number.",
    ),
    CheckpointDef(
        "review_coverage_differences", "Review coverage differences",
        PIPELINE_STAGE_PROPOSAL_SENT, False, "proposal_package",
    ),
    CheckpointDef(
        "prepare_recommendations", "Prepare recommendations / proposal package",
        PIPELINE_STAGE_PROPOSAL_SENT, True, "proposal_package",
        aliases=_BUILD_OPTIONS_ALIASES,
        detail="Proposal package generated for the client.",
    ),
    CheckpointDef(
        "deliver_proposal", "Deliver proposal",
        PIPELINE_STAGE_NEGOTIATING, False, "customer_decision",
        aliases=_SEND_REVIEW_ALIASES,
        detail="Send the package. Desk does not email by itself unless asked.",
    ),
    CheckpointDef(
        "contact_customer", "Contact customer",
        PIPELINE_STAGE_NEGOTIATING, False, "customer_decision",
    ),
    CheckpointDef(
        "record_customer_selection", "Record customer selection",
        PIPELINE_STAGE_NEGOTIATING, True, "customer_decision",
        detail="What the customer chose, in their words.",
    ),
    CheckpointDef(
        "record_final_premium", "Record final premium",
        PIPELINE_STAGE_CLOSED, False, "disposition",
        aliases=_CLOSE_PREMIUM_ALIASES,
        detail="Premium Renewal on the desk row. Correctable overlay.",
    ),
    CheckpointDef(
        "record_disposition", "Record disposition",
        PIPELINE_STAGE_CLOSED, True, "disposition",
        detail="Required to close. Live labels: Renewed, Rewritten, Lost — Price/Coverage/No response, Do not renew.",
    ),
    CheckpointDef(
        "record_bound_carrier", "Record bound carrier",
        PIPELINE_STAGE_CLOSED, False, "disposition",
    ),
    CheckpointDef(
        "record_effective_date", "Record effective date",
        PIPELINE_STAGE_CLOSED, False, "disposition",
    ),
    CheckpointDef(
        "queue_ams_update", "Queue AMS update",
        PIPELINE_STAGE_CLOSED, False, "disposition",
        detail="Enqueue AMS_Write_Queue. Hermes is the only NowCerts writer.",
    ),
)

CHECKPOINT_BY_KEY: dict[str, CheckpointDef] = {c.key: c for c in CHECKPOINTS}


def stored_desk_stage(row: dict[str, Any] | None) -> str:
    """Live CRM uses Stage on some orgs; the field pack names it Desk_Stage."""
    row = row or {}
    for key in ("Desk_Stage", "Stage", "desk_stage"):
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("value")
        label = str(value or "").strip()
        if label:
            return label
    return PIPELINE_STAGE_IDENTIFIED


def operating_label(stage: str | None) -> str:
    stored = (stage or PIPELINE_STAGE_IDENTIFIED).strip() or PIPELINE_STAGE_IDENTIFIED
    for row in OPERATING_STAGES:
        if row["stage"] == stored:
            return row["label"]
    return stored


def _disposition_key(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace("_", " ")
        .replace("—", "-")
        .replace("–", "-")
    )


def normalize_disposition(value: str | None) -> str | None:
    """Map live labels and leftover names onto the six stored codes."""
    raw = str(value or "").strip()
    if not raw:
        return None
    key = _disposition_key(raw)
    for code, label in OS_DISPOSITIONS:
        if raw == code or key == _disposition_key(code) or key == _disposition_key(label):
            return code
    return DISPOSITION_ALIASES.get(key)


def normalize_checkpoint_status(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return STATUS_NOT_STARTED
    low = raw.lower()
    if low in COMPLETE_ALIASES:
        return STATUS_COMPLETE
    if "block" in low:
        return STATUS_BLOCKED
    if "wait" in low or "pending" in low:
        return STATUS_WAITING
    if "progress" in low or "working" in low:
        return STATUS_IN_PROGRESS
    if low in {"not started", "not_started", "open"}:
        return STATUS_NOT_STARTED
    return STATUS_NOT_STARTED


def is_complete(status: Any) -> bool:
    return normalize_checkpoint_status(status) == STATUS_COMPLETE


def _alias_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in CHECKPOINTS:
        out[spec.title.strip().lower()] = spec.key
        out[spec.key.lower()] = spec.key
        for alias in spec.aliases:
            out[alias.strip().lower()] = spec.key
    return out


_ALIAS_TO_KEY = _alias_map()


def checkpoint_key_for_title(title: str | None) -> str | None:
    return _ALIAS_TO_KEY.get(str(title or "").strip().lower())


def seed_tasks() -> list[dict[str, str]]:
    """CRM Tasks to create once per renewal (idempotent by Subject)."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for spec in CHECKPOINTS:
        titles = (spec.title, *spec.aliases)
        for title in titles:
            if title in seen:
                continue
            seen.add(title)
            out.append({
                "key": spec.key,
                "Subject": title,
                "Description": spec.detail or spec.title,
                "stage": spec.stage,
            })
    return out


def states_from_tasks(tasks: Iterable[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Map Zoho CRM Tasks onto checkpoint keys. Latest complete wins."""
    out: dict[str, dict[str, Any]] = {}
    for task in tasks or []:
        key = checkpoint_key_for_title(task.get("Subject") or task.get("key"))
        if not key:
            explicit = str(task.get("key") or "").strip()
            if explicit in CHECKPOINT_BY_KEY:
                key = explicit
            else:
                continue
        status = normalize_checkpoint_status(
            task.get("status") or task.get("Status")
        )
        current = out.get(key)
        if current and is_complete(current.get("status")) and not is_complete(status):
            continue
        owner = task.get("Owner") or task.get("owner")
        if isinstance(owner, dict):
            owner_name = owner.get("name") or owner.get("email")
        else:
            owner_name = owner
        out[key] = {
            "key": key,
            "status": status,
            "owner": owner_name,
            "due_date": task.get("Due_Date") or task.get("due_date"),
            "completed_at": task.get("Closed_Time") or task.get("completed_at"),
            "notes": task.get("Description") or task.get("notes"),
            "task_id": task.get("id") or task.get("task_id"),
            "title": task.get("Subject") or CHECKPOINT_BY_KEY[key].title,
        }
    return out


def checkpoints_for_stage(stage: str | None) -> tuple[CheckpointDef, ...]:
    stored = stored_desk_stage({"Desk_Stage": stage})
    return tuple(c for c in CHECKPOINTS if c.stage == stored)


def required_for_stage(stage: str | None) -> tuple[CheckpointDef, ...]:
    return tuple(c for c in checkpoints_for_stage(stage) if c.required)


def remaining_required(
    stage: str | None,
    states: dict[str, dict[str, Any]] | None,
) -> list[str]:
    states = states or {}
    return [
        spec.key
        for spec in required_for_stage(stage)
        if not is_complete((states.get(spec.key) or {}).get("status"))
    ]


def stage_complete_rule_met(
    stage: str | None,
    states: dict[str, dict[str, Any]] | None,
) -> bool:
    """Stage-specific complete rule. Required checkpoints are the gate."""
    return not remaining_required(stage, states)


def next_stage(stage: str | None) -> str | None:
    stored = stored_desk_stage({"Desk_Stage": stage})
    try:
        idx = DESK_STAGES.index(stored)
    except ValueError:
        return None
    if idx >= len(DESK_STAGES) - 1:
        return None
    return DESK_STAGES[idx + 1]


def _stage_rank(stage: str | None) -> int:
    stored = stored_desk_stage({"Desk_Stage": stage})
    try:
        return DESK_STAGES.index(stored)
    except ValueError:
        return 0


def _rail_state(done: bool, active: bool) -> str:
    if done:
        return RAIL_DONE
    if active:
        return RAIL_ACTIVE
    return RAIL_EMPTY


def scorecard(
    stage: str | None,
    states: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Renewal Health % plus the seven rails. Computed from checkpoints + stage."""
    states = states or {}
    rank = _stage_rank(stage)
    stored = stored_desk_stage({"Desk_Stage": stage})

    def done_or_past(checkpoint_key: str, past_rank: int) -> bool:
        if rank > past_rank:
            return True
        return is_complete((states.get(checkpoint_key) or {}).get("status"))

    def stage_done(at_rank: int, stage_name: str) -> bool:
        if rank > at_rank:
            return True
        if rank < at_rank:
            return False
        return stage_complete_rule_met(stage_name, states)

    rails = {
        RAIL_ACCOUNT: _rail_state(
            stage_done(0, PIPELINE_STAGE_IDENTIFIED),
            rank == 0,
        ),
        RAIL_OUTREACH: _rail_state(
            stage_done(1, PIPELINE_STAGE_OUTREACH_SENT),
            rank == 1,
        ),
        RAIL_MARKETS: _rail_state(
            done_or_past("request_carrier_terms", 2) or rank > 2,
            rank == 2 and not is_complete((states.get("request_carrier_terms") or {}).get("status")),
        ),
        RAIL_QUOTES: _rail_state(
            done_or_past("record_carrier_responses", 2) or rank > 2,
            rank == 2,
        ),
        RAIL_PROPOSAL: _rail_state(
            stage_done(3, PIPELINE_STAGE_PROPOSAL_SENT),
            rank == 3,
        ),
        RAIL_DECISION: _rail_state(
            stage_done(4, PIPELINE_STAGE_NEGOTIATING),
            rank == 4,
        ),
        RAIL_CLOSED: _rail_state(
            stored == PIPELINE_STAGE_CLOSED,
            rank == 5 and stored != PIPELINE_STAGE_CLOSED,
        ),
    }
    # Markets requested: if we are past Quote Requested, it's done even without the task.
    if rank > 2:
        rails[RAIL_MARKETS] = RAIL_DONE
        rails[RAIL_QUOTES] = RAIL_DONE

    total = len(CHECKPOINTS)
    completed = sum(
        1 for spec in CHECKPOINTS
        if is_complete((states.get(spec.key) or {}).get("status"))
        or _stage_rank(spec.stage) < rank
    )
    health = int(round(100 * completed / total)) if total else 0
    if stored == PIPELINE_STAGE_CLOSED:
        health = 100
        for key in rails:
            rails[key] = RAIL_DONE

    return {
        "health": health,
        "stage": stored,
        "label": operating_label(stored),
        "rails": [
            {
                "key": spec["key"],
                "label": spec["label"],
                "state": rails[spec["key"]],
                "mark": {"done": "✅", "active": "🟨", "empty": "⬜"}[rails[spec["key"]]],
            }
            for spec in SCORECARD_RAILS
        ],
        "remaining": remaining_required(stored, states),
    }


def checkpoint_rows(
    stage: str | None,
    states: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Current-stage checkpoints for the card middle pane."""
    states = states or {}
    rows = []
    for spec in checkpoints_for_stage(stage):
        hit = states.get(spec.key) or {}
        rows.append({
            "key": spec.key,
            "title": spec.title,
            "required": spec.required,
            "complete_rule": spec.complete_rule,
            "owner_role": spec.owner_role,
            "detail": spec.detail,
            "status": normalize_checkpoint_status(hit.get("status")),
            "owner": hit.get("owner"),
            "due_date": hit.get("due_date"),
            "completed_at": hit.get("completed_at"),
            "notes": hit.get("notes"),
            "task_id": hit.get("task_id"),
        })
    return rows


def next_required_action(
    stage: str | None,
    states: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stored = stored_desk_stage({"Desk_Stage": stage})
    remaining = remaining_required(stored, states)
    if stored == PIPELINE_STAGE_CLOSED:
        return {
            "key": "done",
            "title": "Renewal closed",
            "owner_role": "csr",
        }
    if remaining:
        spec = CHECKPOINT_BY_KEY[remaining[0]]
        return {
            "key": spec.key,
            "title": spec.title,
            "owner_role": spec.owner_role,
            "detail": spec.detail,
        }
    nxt = next_stage(stored)
    return {
        "key": "advance",
        "title": f"Advance to {operating_label(nxt)}" if nxt else "Close renewal",
        "next_stage": nxt,
        "owner_role": "csr",
    }


@dataclass
class CompleteResult:
    ok: bool
    advanced: bool = False
    desk_stage: str = PIPELINE_STAGE_IDENTIFIED
    remaining: list[str] = field(default_factory=list)
    error: str | None = None
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    scorecard: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "advanced": self.advanced,
            "desk_stage": self.desk_stage,
            "remaining": self.remaining,
            "error": self.error,
            "states": self.states,
            "scorecard": self.scorecard,
        }


def complete_checkpoint(
    stage: str | None,
    states: dict[str, dict[str, Any]] | None,
    key: str,
    *,
    actor: str = ACTOR_USER,
    disposition: str | None = None,
    producer_confirmed: bool = False,
) -> CompleteResult:
    """Mark a checkpoint complete. Advance at most one Desk_Stage, and only for a user.

    Remaining required checkpoints on the current stage block advance. Hermes
    (``actor="hermes"``) never advances.
    """
    stored = stored_desk_stage({"Desk_Stage": stage})
    spec = CHECKPOINT_BY_KEY.get(key)
    if spec is None:
        return CompleteResult(
            ok=False, desk_stage=stored, error=f"unknown checkpoint {key!r}",
            states=dict(states or {}),
        )
    new_states = {k: dict(v) for k, v in (states or {}).items()}
    row = dict(new_states.get(key) or {"key": key})
    row["status"] = STATUS_COMPLETE
    new_states[key] = row

    remaining = remaining_required(stored, new_states)
    result = CompleteResult(
        ok=True,
        desk_stage=stored,
        remaining=remaining,
        states=new_states,
    )
    if actor != ACTOR_USER:
        result.scorecard = scorecard(stored, new_states)
        return result
    if remaining:
        result.scorecard = scorecard(stored, new_states)
        return result
    nxt = next_stage(stored)
    if not nxt:
        result.scorecard = scorecard(stored, new_states)
        return result
    # Advance only from the current stage's last required checkpoint, or from
    # record_disposition which is the Close gate while still on Negotiating.
    on_current = spec.stage == stored
    closing = nxt == PIPELINE_STAGE_CLOSED and key == "record_disposition"
    if not on_current and not closing:
        result.scorecard = scorecard(stored, new_states)
        return result
    disposition = normalize_disposition(disposition)
    if nxt == PIPELINE_STAGE_CLOSED and not disposition_ok(disposition):
        result.error = "Closed requires a Disposition"
        result.scorecard = scorecard(stored, new_states)
        return result
    allowed, reason = stage_change_allowed(stored, nxt, producer_confirmed=producer_confirmed)
    if not allowed:
        result.ok = False
        result.error = reason
        result.scorecard = scorecard(stored, new_states)
        return result
    result.advanced = True
    result.desk_stage = nxt
    result.remaining = remaining_required(nxt, new_states)
    result.scorecard = scorecard(nxt, new_states)
    return result
