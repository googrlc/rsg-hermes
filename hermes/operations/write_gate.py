"""Central confirm-before-write helpers for Hermes mutating workflows."""

from __future__ import annotations

from dataclasses import dataclass

APPROVE_CRM_ONLY = "APPROVE CRM ONLY"
APPROVE_SUPABASE_ONLY = "APPROVE SUPABASE ONLY"
APPROVE_TASKS_ONLY = "APPROVE TASKS ONLY"
APPROVE_ALL = "APPROVE ALL"
REVISE = "REVISE"
CANCEL = "CANCEL"

_TOKENS = {
    APPROVE_CRM_ONLY,
    APPROVE_SUPABASE_ONLY,
    APPROVE_TASKS_ONLY,
    APPROVE_ALL,
    REVISE,
    CANCEL,
}


@dataclass(frozen=True)
class ApprovalDecision:
    token: str
    approve_crm: bool
    approve_supabase: bool
    approve_tasks: bool
    cancelled: bool = False
    revise_requested: bool = False


def parse_approval_token(text: str) -> ApprovalDecision | None:
    token = text.strip().upper()
    if token not in _TOKENS:
        return None
    if token == CANCEL:
        return ApprovalDecision(token=token, approve_crm=False, approve_supabase=False, approve_tasks=False, cancelled=True)
    if token == REVISE:
        return ApprovalDecision(token=token, approve_crm=False, approve_supabase=False, approve_tasks=False, revise_requested=True)
    if token == APPROVE_ALL:
        return ApprovalDecision(token=token, approve_crm=True, approve_supabase=True, approve_tasks=True)
    if token == APPROVE_CRM_ONLY:
        return ApprovalDecision(token=token, approve_crm=True, approve_supabase=False, approve_tasks=False)
    if token == APPROVE_SUPABASE_ONLY:
        return ApprovalDecision(token=token, approve_crm=False, approve_supabase=True, approve_tasks=False)
    return ApprovalDecision(token=token, approve_crm=False, approve_supabase=False, approve_tasks=True)
