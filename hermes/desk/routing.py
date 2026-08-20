"""Desk automation rules AUT-01 through AUT-14.

These are configuration proposals encoded as pure functions so Hermes can
apply the same decisions Desk workflows will later enforce. They do not call
the Desk API.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from hermes.desk.spec import (
    AUTO_DRIVER_SUBTYPES,
    CATEGORY_TO_LAYOUT,
    CATEGORY_TO_TEAM,
    COMMERCIAL_AUTO_LINES,
    PERSONAL_LINES,
    TERMINAL_STATUSES,
)
from hermes.desk.priority import HIGH, URGENT, recommend_priority

SALES_HANDOFF_KEYWORDS = (
    "new coverage",
    "quote",
    "cross-sell",
    "cross sell",
    "additional location",
    "new location",
    "new entity",
    "another policy",
    "add a location",
)


@dataclass(frozen=True)
class TicketSnapshot:
    """Minimal ticket state the rules engine needs."""

    status: str = "New"
    category: str | None = None
    subtype: str | None = None
    team: str | None = None
    layout: str | None = None
    priority: str = "Normal"
    urgency_reason: str | None = None
    business_impact: str | None = None
    source: str | None = None
    contact_id: str | None = None
    account_id: str | None = None
    ams_client_id: str | None = None
    policy_number: str | None = None
    carrier: str | None = None
    line_of_business: str | None = None
    owner: str | None = None
    previous_owner: str | None = None
    required_by: date | None = None
    next_follow_up: date | None = None
    missing_information: str | None = None
    last_client_contact: datetime | None = None
    last_carrier_contact: datetime | None = None
    cancellation_warning: bool = False
    cancellation_date: date | None = None
    documented_disposition: str | None = None
    sales_opportunity: bool = False
    crm_record_id: str | None = None
    ams_activity_posted: bool = False
    customer_confirmation_sent: bool = False
    final_documents_stored: bool = False
    carrier_action_required: bool = False
    client_action_required: bool = False
    resolution_type: str | None = None
    integration_status: str | None = None
    integration_error: str | None = None
    reopened_reason: str | None = None
    reopen_count: int = 0
    related_ticket_number: str | None = None
    cancellation_reason: str | None = None
    subject: str = ""
    description: str = ""
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingAction:
    automation_ids: tuple[str, ...] = ()
    status: str | None = None
    team: str | None = None
    layout: str | None = None
    priority: str | None = None
    owner: str | None = None
    waiting_on: str | None = None
    notifications: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    require_fields: tuple[str, ...] = ()
    flags: dict[str, Any] = field(default_factory=dict)
    block_status: str | None = None
    rollback_to: str | None = None
    possible_duplicate: bool = False
    notes: tuple[str, ...] = ()


def _merge(*actions: RoutingAction) -> RoutingAction:
    merged = RoutingAction()
    ids: list[str] = []
    notifications: list[str] = []
    tasks: list[str] = []
    emails: list[str] = []
    require: list[str] = []
    notes: list[str] = []
    flags: dict[str, Any] = {}
    for action in actions:
        ids.extend(action.automation_ids)
        notifications.extend(action.notifications)
        tasks.extend(action.tasks)
        emails.extend(action.emails)
        require.extend(action.require_fields)
        notes.extend(action.notes)
        flags.update(action.flags)
        merged = replace(
            merged,
            status=action.status if action.status is not None else merged.status,
            team=action.team if action.team is not None else merged.team,
            layout=action.layout if action.layout is not None else merged.layout,
            priority=action.priority if action.priority is not None else merged.priority,
            owner=action.owner if action.owner is not None else merged.owner,
            waiting_on=action.waiting_on if action.waiting_on is not None else merged.waiting_on,
            block_status=action.block_status or merged.block_status,
            rollback_to=action.rollback_to or merged.rollback_to,
            possible_duplicate=merged.possible_duplicate or action.possible_duplicate,
        )
    return replace(
        merged,
        automation_ids=tuple(dict.fromkeys(ids)),
        notifications=tuple(dict.fromkeys(notifications)),
        tasks=tuple(dict.fromkeys(tasks)),
        emails=tuple(dict.fromkeys(emails)),
        require_fields=tuple(dict.fromkeys(require)),
        notes=tuple(dict.fromkeys(notes)),
        flags=flags,
    )


def _blank(value: object) -> bool:
    return value is None or value == ""


def team_for(ticket: TicketSnapshot) -> str:
    if ticket.category == "Policy Change" and ticket.subtype in AUTO_DRIVER_SUBTYPES:
        if ticket.line_of_business in PERSONAL_LINES:
            return "Personal Lines Service"
        if ticket.line_of_business in COMMERCIAL_AUTO_LINES or ticket.line_of_business is None:
            return "Commercial Auto Service"
        return "Commercial Lines Service"
    if ticket.category:
        return CATEGORY_TO_TEAM.get(ticket.category, "Service Intake")
    return "Service Intake"


def layout_for(ticket: TicketSnapshot) -> str:
    if ticket.category == "Policy Change" and ticket.subtype in AUTO_DRIVER_SUBTYPES:
        return "auto_driver"
    if ticket.category:
        return CATEGORY_TO_LAYOUT.get(ticket.category, "shared")
    return "shared"


def auto_driver_required_fields(subtype: str | None) -> tuple[str, ...]:
    base = ("cf_effective_date_requested",)
    if subtype in {"Add vehicle", "Remove vehicle", "Replace vehicle"}:
        extra = ("cf_vin",)
        if subtype in {"Add vehicle", "Replace vehicle"}:
            extra = extra + ("cf_vehicle_year", "cf_vehicle_make", "cf_vehicle_model")
        return base + extra
    if subtype in {"Add driver", "Remove driver"}:
        extra = ("cf_driver_name",)
        if subtype == "Add driver":
            extra = extra + ("cf_license_state",)
        return base + extra
    if subtype == "Change garaging":
        return base + ("cf_garaging_address",)
    if subtype == "Change use":
        return base + ("cf_vehicle_use",)
    return base


def missing_required(ticket: TicketSnapshot, names: tuple[str, ...]) -> tuple[str, ...]:
    present = dict(ticket.fields)
    present.setdefault("cf_effective_date_requested", ticket.fields.get("cf_effective_date_requested"))
    missing: list[str] = []
    for name in names:
        if _blank(present.get(name)):
            missing.append(name)
    return tuple(missing)


def aut_01_new_ticket(ticket: TicketSnapshot) -> RoutingAction:
    uncertain = _blank(ticket.category) or (_blank(ticket.account_id) and _blank(ticket.ams_client_id))
    notifications = ()
    if _blank(ticket.account_id) and _blank(ticket.ams_client_id) and _blank(ticket.contact_id):
        notifications = ("no_account_match",)
    return RoutingAction(
        automation_ids=("AUT-01",),
        status="New",
        team="Service Intake" if uncertain else team_for(ticket),
        flags={"received_at": datetime.now(timezone.utc).isoformat(), "source": ticket.source or "Email"},
        notifications=notifications,
        notes=("Stamp received date/time and attempt contact/account association.",),
    )


def aut_02_certificate(ticket: TicketSnapshot) -> RoutingAction | None:
    if ticket.category != "Certificate Request":
        return None
    required = ("cf_holder_name", "cf_holder_address", "cf_required_by_date", "cf_ams_client_id", "cf_policy_number")
    missing = missing_required(ticket, required)
    tasks = ("complete_certificate_intake",) if missing else ()
    priority = recommend_priority(
        urgency_reason=ticket.urgency_reason,
        required_by=ticket.required_by,
        business_impact=ticket.business_impact,
    )
    return RoutingAction(
        automation_ids=("AUT-02",),
        layout="certificate",
        team="Certificates",
        priority=priority,
        emails=("Certificate request received",),
        tasks=tasks,
        require_fields=missing,
    )


def aut_03_auto_driver(ticket: TicketSnapshot) -> RoutingAction | None:
    if ticket.category != "Policy Change" or ticket.subtype not in AUTO_DRIVER_SUBTYPES:
        return None
    required = auto_driver_required_fields(ticket.subtype)
    missing = missing_required(ticket, required)
    block = "Ready for Processing" if missing else None
    return RoutingAction(
        automation_ids=("AUT-03",),
        layout="auto_driver",
        team=team_for(ticket),
        require_fields=missing,
        block_status=block,
        notes=("Requested effective date is required.",),
    )


def aut_04_cancellation_warning(ticket: TicketSnapshot, *, today: date | None = None) -> RoutingAction | None:
    billingish = ticket.category in {"Billing and Payments", "Cancellations and Reinstatements"}
    if not billingish or not ticket.cancellation_warning:
        return None
    today = today or date.today()
    reason = ticket.urgency_reason
    if ticket.cancellation_date and ticket.cancellation_date <= today + timedelta(days=3):
        reason = reason or "Cancellation or lapse imminent"
    priority = recommend_priority(
        urgency_reason=reason,
        cancellation_warning=True,
        cancellation_date=ticket.cancellation_date,
        today=today,
    )
    follow_up = None
    if ticket.cancellation_date:
        follow_up = ticket.cancellation_date - timedelta(days=1)
    return RoutingAction(
        automation_ids=("AUT-04",),
        team="Billing and Retention",
        priority=priority if priority in {URGENT, HIGH} else HIGH,
        notifications=("case_owner", "escalation_recipient"),
        tasks=("follow_up_before_cancellation_date",),
        flags={"next_follow_up": follow_up.isoformat() if follow_up else None, "disposition_required": True},
        notes=("Documented disposition is required before resolution.",),
    )


def aut_05_waiting_on_client(ticket: TicketSnapshot) -> RoutingAction | None:
    if ticket.status != "Waiting on Client":
        return None
    require = []
    if _blank(ticket.missing_information):
        require.append("cf_missing_information")
    if ticket.next_follow_up is None:
        require.append("cf_next_follow_up_date")
    return RoutingAction(
        automation_ids=("AUT-05",),
        waiting_on="Client",
        require_fields=tuple(require),
        emails=("Missing information",),
        tasks=("follow_up_waiting_on_client",),
        notifications=("escalate_if_deadline_passes",),
    )


def aut_06_waiting_on_carrier(ticket: TicketSnapshot) -> RoutingAction | None:
    if ticket.status != "Waiting on Carrier":
        return None
    require = []
    if _blank(ticket.carrier):
        require.append("cf_carrier")
    if ticket.last_carrier_contact is None:
        require.append("cf_last_carrier_contact_date")
    if ticket.next_follow_up is None:
        require.append("cf_next_follow_up_date")
    return RoutingAction(
        automation_ids=("AUT-06",),
        waiting_on="Carrier",
        require_fields=tuple(require),
        tasks=("follow_up_waiting_on_carrier",),
        notifications=("notify_owner_when_follow_up_due", "escalate_aging_by_priority"),
    )


def aut_07_client_reply(ticket: TicketSnapshot) -> RoutingAction | None:
    if ticket.status != "Waiting on Client":
        return None
    return RoutingAction(
        automation_ids=("AUT-07",),
        status="Ready for Processing",
        owner=ticket.previous_owner or ticket.owner,
        waiting_on="None",
        notifications=("notify_owner",),
        flags={"clear_waiting_reason": True},
    )


def aut_08_carrier_reply(ticket: TicketSnapshot) -> RoutingAction | None:
    if ticket.status != "Waiting on Carrier":
        return None
    return RoutingAction(
        automation_ids=("AUT-08",),
        status="In Progress",
        notifications=("notify_owner",),
        tasks=("cancel_obsolete_carrier_follow_up",),
    )


def aut_09_required_by_reminder(
    ticket: TicketSnapshot, *, today: date | None = None
) -> RoutingAction | None:
    if ticket.required_by is None or ticket.status in TERMINAL_STATUSES:
        return None
    today = today or date.today()
    remaining = (ticket.required_by - today).days
    if remaining > 3:
        return None
    notifications = ["notify_owner"]
    if remaining <= 0:
        notifications.append("escalate_service_lead")
    from hermes.desk.priority import escalate_for_required_by

    return RoutingAction(
        automation_ids=("AUT-09",),
        priority=escalate_for_required_by(ticket.priority, required_by=ticket.required_by, today=today),
        notifications=tuple(notifications),
    )


def aut_10_closure_control(ticket: TicketSnapshot, *, outstanding_tasks: bool = False) -> RoutingAction | None:
    if ticket.status not in {"Resolved", "Closed"}:
        return None
    from hermes.desk.closure import closure_blockers

    blockers = closure_blockers(ticket, outstanding_tasks=outstanding_tasks)
    if not blockers:
        return RoutingAction(automation_ids=("AUT-10",))
    return RoutingAction(
        automation_ids=("AUT-10",),
        rollback_to="In Progress",
        status="In Progress",
        require_fields=blockers,
        notes=("Required closure checks failed; return to In Progress.",),
    )


def aut_11_reopen(ticket: TicketSnapshot) -> RoutingAction:
    return RoutingAction(
        automation_ids=("AUT-11",),
        status="In Progress",
        owner=ticket.previous_owner or ticket.owner,
        require_fields=("cf_reopened_reason",) if _blank(ticket.reopened_reason) else (),
        flags={"reopen_count": ticket.reopen_count + 1},
        notifications=("notify_owner",),
    )


def aut_13_crm_handoff(ticket: TicketSnapshot) -> RoutingAction | None:
    blob = f"{ticket.subject} {ticket.description}".lower()
    flagged = ticket.sales_opportunity or any(token in blob for token in SALES_HANDOFF_KEYWORDS)
    if not flagged:
        return None
    return RoutingAction(
        automation_ids=("AUT-13",),
        flags={"sales_opportunity": True, "keep_service_ticket_open": True},
        tasks=("internal_sales_follow_up",),
        notes=("Create or update the CRM opportunity through the approved integration.",),
    )


def aut_14_ams_posting(ticket: TicketSnapshot, *, ams_post_succeeded: bool | None = None) -> RoutingAction | None:
    if ticket.status not in {"Ready for Delivery", "Resolved"}:
        return None
    if ams_post_succeeded is True:
        return RoutingAction(
            automation_ids=("AUT-14",),
            flags={"ams_activity_posted": True, "integration_status": "Synced"},
        )
    if ams_post_succeeded is False:
        return RoutingAction(
            automation_ids=("AUT-14",),
            flags={"ams_activity_posted": False, "integration_status": "Failed"},
            tasks=("integration_exception",),
            notifications=("integration_exceptions_view",),
        )
    return RoutingAction(
        automation_ids=("AUT-14",),
        flags={"ams_post_pending": True},
        notes=("Include ticket number, contact, policy, category, summary, owner, and disposition.",),
    )


def apply_event(
    ticket: TicketSnapshot,
    event: str,
    *,
    today: date | None = None,
    outstanding_tasks: bool = False,
    ams_post_succeeded: bool | None = None,
    possible_duplicate: bool = False,
) -> RoutingAction:
    """Apply the automations that fire for ``event``."""
    actions: list[RoutingAction] = []
    if event in {"ticket_created", "category_changed"}:
        if event == "ticket_created":
            actions.append(aut_01_new_ticket(ticket))
        for builder in (aut_02_certificate, aut_03_auto_driver, aut_04_cancellation_warning, aut_13_crm_handoff):
            if builder is aut_04_cancellation_warning:
                result = builder(ticket, today=today)
            else:
                result = builder(ticket)
            if result:
                actions.append(result)
        if possible_duplicate:
            actions.append(
                RoutingAction(
                    automation_ids=("AUT-12",),
                    possible_duplicate=True,
                    notes=("Flag possible duplicate for review. Do not auto-delete.",),
                )
            )
    elif event == "status_changed":
        for builder in (aut_05_waiting_on_client, aut_06_waiting_on_carrier, aut_10_closure_control):
            if builder is aut_10_closure_control:
                result = builder(ticket, outstanding_tasks=outstanding_tasks)
            else:
                result = builder(ticket)
            if result:
                actions.append(result)
        if ticket.status in {"Ready for Delivery", "Resolved"}:
            result = aut_14_ams_posting(ticket, ams_post_succeeded=ams_post_succeeded)
            if result:
                actions.append(result)
    elif event == "customer_reply":
        result = aut_07_client_reply(ticket)
        if result:
            actions.append(result)
    elif event == "carrier_reply":
        result = aut_08_carrier_reply(ticket)
        if result:
            actions.append(result)
    elif event == "required_by_approaching":
        result = aut_09_required_by_reminder(ticket, today=today)
        if result:
            actions.append(result)
    elif event == "client_reply_after_resolution":
        actions.append(aut_11_reopen(ticket))
    if not actions:
        return RoutingAction()
    return _merge(*actions)
