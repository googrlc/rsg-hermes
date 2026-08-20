"""AUT-10 closure control — operational work vs recordkeeping."""

from __future__ import annotations

from hermes.desk.routing import TicketSnapshot


def _blank(value: object) -> bool:
    return value is None or value == ""


def closure_blockers(ticket: TicketSnapshot, *, outstanding_tasks: bool = False) -> tuple[str, ...]:
    """Return the checks that must pass before Resolved or Closed sticks.

    Resolved means the operational work is complete. Closed means confirmation
    and recordkeeping are complete. Failed checks roll the ticket back to
    In Progress.
    """
    blockers: list[str] = []
    if _blank(ticket.resolution_type) and _blank(ticket.fields.get("cf_resolution_type")):
        blockers.append("cf_resolution_type")
    if ticket.status == "Closed" and not (
        ticket.customer_confirmation_sent or ticket.fields.get("cf_customer_confirmation_sent")
    ):
        blockers.append("cf_customer_confirmation_sent")
    if not ticket.ams_activity_posted:
        blockers.append("cf_ams_activity_posted")
    if not (ticket.final_documents_stored or ticket.fields.get("cf_final_documents_stored")):
        blockers.append("cf_final_documents_stored")
    if outstanding_tasks:
        blockers.append("outstanding_task")
    if ticket.carrier_action_required or ticket.fields.get("cf_carrier_action_required"):
        blockers.append("pending_carrier_requirement")
    if ticket.client_action_required or ticket.fields.get("cf_client_action_required"):
        blockers.append("pending_client_requirement")
    if ticket.cancellation_warning and _blank(ticket.documented_disposition):
        blockers.append("cf_documented_disposition")
    if ticket.status == "Cancelled" and _blank(ticket.cancellation_reason):
        blockers.append("cf_cancellation_reason")
    if ticket.status == "Duplicate" and _blank(ticket.related_ticket_number):
        blockers.append("cf_related_ticket_number")
    return tuple(blockers)


def may_close(ticket: TicketSnapshot, *, outstanding_tasks: bool = False) -> bool:
    return not closure_blockers(ticket, outstanding_tasks=outstanding_tasks)
