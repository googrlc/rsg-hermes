"""In-chat approval surface for crm_change_proposals.

Handles: APPROVE CHANGE <id>, APPROVE CHANGE ALL, LIST CHANGES, REJECT CHANGE <id>.

Lamar can inspect and approve/reject staged CRM change proposals
directly from chat — no Supabase dashboard needed.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult
from hermes.operations.crm_proposals import (
    ProposalError,
    approve_proposal,
    list_proposals,
    reject_proposal,
)

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

_APPROVE_ONE = re.compile(r"^\s*(?:APPROVE|COMMIT)\s+CHANGE\s+(?P<id>\S+)\s*$", re.I)
_APPROVE_ALL = re.compile(r"^\s*(?:APPROVE|COMMIT)\s+CHANGE\s+ALL\s*$", re.I)
_LIST = re.compile(r"^\s*(?:LIST|SHOW)\s+CHANGES?\s*$", re.I)
_REJECT = re.compile(r"^\s*(?:REJECT|CANCEL)\s+CHANGE\s+(?P<id>\S+)(?:\s+(?P<reason>.+))?\s*$", re.I)


def handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    reviewer: str = "lamar",
) -> DispatchResult:
    """Route crm_change_proposal approval commands."""
    if supa is None:
        return DispatchResult(False, "Supabase is not available — cannot read proposals.")

    m = _LIST.match(text)
    if m:
        return _handle_list(supa)

    m = _APPROVE_ALL.match(text)
    if m:
        return _handle_approve_all(client, supa, reviewer)

    m = _APPROVE_ONE.match(text)
    if m:
        proposal_id = m.group("id")
        return _handle_approve_one(client, supa, proposal_id, reviewer)

    m = _REJECT.match(text)
    if m:
        proposal_id = m.group("id")
        reason = (m.group("reason") or "").strip() or None
        return _handle_reject(supa, proposal_id, reviewer, reason)

    return DispatchResult(False, "Unrecognized change proposal command.")


def _handle_list(supa: SupabaseClient) -> DispatchResult:
    try:
        proposals = list_proposals(supa, status="pending", limit=20)
    except Exception as exc:
        return DispatchResult(False, f"Failed to list proposals: {exc}")

    if not proposals:
        return DispatchResult(True, "No pending change proposals.")

    lines = [f"**{len(proposals)} pending change proposal(s):**\n"]
    for p in proposals:
        pid = p.get("id", "?")
        entity = p.get("entity", "?")
        op = p.get("op", "?")
        espocrm_id = p.get("espocrm_id") or "new"
        after = p.get("after") or {}
        rationale = p.get("rationale") or ""
        fields = ", ".join(f"{k}={v}" for k, v in after.items())
        lines.append(
            f"• `{pid}` — {op} **{entity}** `{espocrm_id}`: {fields}"
        )
        if rationale:
            lines.append(f"  _{rationale}_")
    return DispatchResult(True, "\n".join(lines), {"proposals": proposals})


def _handle_approve_one(
    client: "EspoClient",
    supa: SupabaseClient,
    proposal_id: str,
    reviewer: str,
) -> DispatchResult:
    try:
        result = approve_proposal(supa, proposal_id, reviewer=reviewer, espo=client)
    except ProposalError as exc:
        return DispatchResult(False, str(exc))
    except Exception as exc:
        return DispatchResult(False, f"Approval failed: {exc}")

    msg = (
        f"Approved proposal `{proposal_id}`: " f"{result.get('entity')} {result.get('action', result.get('status'))}."
    )
    if result.get("queue_id"):
        msg += f" Enqueued → crm_write_queue `{result['queue_id']}`."
    return DispatchResult(True, msg, {"result": result})


def _handle_approve_all(
    client: "EspoClient",
    supa: SupabaseClient,
    reviewer: str,
) -> DispatchResult:
    try:
        proposals = list_proposals(supa, status="pending", limit=50)
    except Exception as exc:
        return DispatchResult(False, f"Failed to list proposals: {exc}")

    if not proposals:
        return DispatchResult(True, "No pending change proposals to approve.")

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for p in proposals:
        pid = str(p.get("id") or "")
        try:
            r = approve_proposal(supa, pid, reviewer=reviewer, espo=client)
            results.append(r)
        except Exception as exc:
            errors.append(f"`{pid}`: {exc}")

    msg = f"Approved {len(results)}/{len(proposals)} proposals."
    if errors:
        msg += f" Errors: {'; '.join(errors)}"
    return DispatchResult(True, msg, {"results": results, "errors": errors})


def _handle_reject(
    supa: SupabaseClient,
    proposal_id: str,
    reviewer: str,
    reason: str | None,
) -> DispatchResult:
    try:
        result = reject_proposal(supa, proposal_id, reviewer=reviewer, reason=reason)
    except ProposalError as exc:
        return DispatchResult(False, str(exc))
    except Exception as exc:
        return DispatchResult(False, f"Rejection failed: {exc}")
    return DispatchResult(True, f"Rejected proposal `{proposal_id}`.", {"result": result})
