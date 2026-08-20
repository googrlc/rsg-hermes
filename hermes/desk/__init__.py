"""Zoho Desk case-management rules for RSG service work.

Desk owns the work. Momentum owns the policy record. Zoho CRM owns the sales
opportunity. See ``docs/zoho-desk/`` for the operator configuration pack.
"""

from __future__ import annotations

from hermes.desk.blueprints import BLUEPRINTS, can_transition
from hermes.desk.classify import classify_request
from hermes.desk.closure import closure_blockers, may_close
from hermes.desk.duplicates import possible_duplicates
from hermes.desk.matching import resolve_account
from hermes.desk.priority import recommend_priority
from hermes.desk.renewals import renewal_identity
from hermes.desk.routing import TicketSnapshot, apply_event
from hermes.desk.spec import DEPARTMENT, LAUNCH_WORKFLOWS, SYSTEMS_OF_RECORD
from hermes.desk.titles import case_title
from hermes.desk.crm_button import BUTTON_NAME, build_ticket_payload

__all__ = [
    "BLUEPRINTS",
    "BUTTON_NAME",
    "DEPARTMENT",
    "LAUNCH_WORKFLOWS",
    "SYSTEMS_OF_RECORD",
    "TicketSnapshot",
    "apply_event",
    "build_ticket_payload",
    "can_transition",
    "case_title",
    "classify_request",
    "closure_blockers",
    "may_close",
    "possible_duplicates",
    "recommend_priority",
    "renewal_identity",
    "resolve_account",
]
