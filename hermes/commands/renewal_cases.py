"""NL commands to create a renewal case and its tasks (Supabase-native workspace).

  create_case_handle   -> resolve the exact policy, derive the renewal-event
                          identity, upsert a renewal_cases row. If the request
                          also says "and tasks", seed the default task set.
  create_tasks_handle  -> ensure a case exists for the policy, then seed the
                          default renewal task set under it (idempotent).

Both require an exact policy number / GUID and resolve through NowCerts — no
general report, no guessing.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from hermes.commands.renewal_desk import _get_nowcerts, _parse_identity
from hermes.core.dispatch import DispatchResult
from hermes.renewals import cases, resolve

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.integrations.nowcerts_client import NowCertsClient

_DEFAULT_ASSIGNEE_EMAIL = os.environ.get("HERMES_RENEWAL_ASSIGNEE_EMAIL", "gretchen@risksolutionsgroup.net")
_WITH_TASKS_RE = re.compile(r"\b(and|with|\+)\s+tasks?\b", re.I)


def _resolve_or_error(
    text: str, *, supa: "SupabaseClient", nowcerts: "NowCertsClient | None"
) -> resolve.ResolvedPolicy | DispatchResult:
    ident = _parse_identity(text)
    if not (ident["policy_number"] or ident["policy_guid"]):
        return DispatchResult(
            False,
            "I need an exact **policy number** or **NowCerts GUID** to open a renewal case.",
            {"need_identifier": True},
        )
    nc = _get_nowcerts(nowcerts)
    if nc is None:
        return DispatchResult(False, "NowCerts is not reachable right now.")
    resolved = resolve.resolve_exact_policy(
        nc, policy_number=ident["policy_number"], policy_guid=ident["policy_guid"], supa=supa
    )
    if resolved.reason == resolve.NOT_FOUND:
        return DispatchResult(False, "⚠️ Reconciliation needed — that policy was not found in NowCerts.",
                              {"reconciliation_needed": True})
    if resolved.reason == resolve.AMBIGUOUS:
        return DispatchResult(False, "⚠️ Ambiguous identifier — escalate to reconcile before opening a case.",
                              {"ambiguous": True, "matches": resolved.matches})
    if not resolved.ok:
        return DispatchResult(False, "Could not resolve that policy (need an exact policy number or GUID).")
    return resolved


def _event_identity(resolved: resolve.ResolvedPolicy) -> tuple[str, str, str] | None:
    """(insured_id, policy_lineage_id, renewal_event_date) from candidate, else policy fallback."""
    p = resolved.policy or {}
    cand = resolved.candidate or {}
    insured_id = cand.get("insured_id") or p.get("insured_database_id")
    lineage = cand.get("policy_lineage_id") or p.get("policyNumber")
    event_date = cand.get("renewal_event_date") or p.get("expiration_date")
    if insured_id and lineage and event_date:
        return str(insured_id), str(lineage), str(event_date)[:10]
    return None


def _ensure_case(resolved: resolve.ResolvedPolicy, supa: "SupabaseClient") -> tuple[dict[str, Any], bool] | None:
    ident = _event_identity(resolved)
    if ident is None:
        return None
    insured_id, lineage, event_date = ident
    p = resolved.policy or {}
    cand = resolved.candidate or {}
    return cases.create_case(
        supa,
        insured_id=insured_id,
        policy_lineage_id=lineage,
        renewal_event_date=event_date,
        policy_number=p.get("policyNumber"),
        nowcerts_policy_guid=p.get("policy_guid"),
        client_name=p.get("accountName") or cand.get("client_name"),
        line_of_business=p.get("line_of_business"),
        segment=cand.get("segment"),
        owner_email=_DEFAULT_ASSIGNEE_EMAIL,
    )


def create_case_handle(
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
) -> DispatchResult:
    if supa is None:
        return DispatchResult(False, "Creating a renewal case needs Supabase, which is not configured.")
    resolved = _resolve_or_error(text, supa=supa, nowcerts=nowcerts)
    if isinstance(resolved, DispatchResult):
        return resolved

    made = _ensure_case(resolved, supa)
    if made is None:
        return DispatchResult(
            False,
            "Can't form the renewal-event identity for this policy (missing insured / lineage / event date). "
            "Reconcile it in the candidate index first.",
            {"reconciliation_needed": True},
        )
    case, created = made
    p = resolved.policy or {}

    owner = case.get("owner_email") or _DEFAULT_ASSIGNEE_EMAIL
    tasks_made: list[dict[str, Any]] = []
    if _WITH_TASKS_RE.search(text):
        tasks_made = cases.create_tasks(
            supa, case_id=case["id"],
            tasks=cases.default_tasks(assigned_to_email=owner),
            default_assignee_email=owner,
        )

    verb = "Opened" if created else "Found existing"
    msg = (
        f"🗂️ {verb} renewal case — {case.get('insured_name') or p.get('policyNumber')} "
        f"(policy #{case.get('policy_number')})\n"
        f"- Case: `{str(case.get('id'))[:8]}` · status {case.get('status')} · owner {owner}"
    )
    if tasks_made:
        msg += f"\n- Seeded {len(tasks_made)} tasks."
    elif _WITH_TASKS_RE.search(text):
        msg += "\n- Tasks already existed (none added)."
    return DispatchResult(
        True, msg,
        {"case_id": case["id"], "created": created, "tasks_created": len(tasks_made)},
    )


def create_tasks_handle(
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
) -> DispatchResult:
    if supa is None:
        return DispatchResult(False, "Creating renewal tasks needs Supabase, which is not configured.")
    resolved = _resolve_or_error(text, supa=supa, nowcerts=nowcerts)
    if isinstance(resolved, DispatchResult):
        return resolved

    made = _ensure_case(resolved, supa)   # create the case if it doesn't exist yet
    if made is None:
        return DispatchResult(
            False,
            "Can't form the renewal-event identity for this policy — reconcile it in the candidate index first.",
            {"reconciliation_needed": True},
        )
    case, _ = made
    owner = case.get("owner_email") or _DEFAULT_ASSIGNEE_EMAIL
    tasks_made = cases.create_tasks(
        supa, case_id=case["id"],
        tasks=cases.default_tasks(assigned_to_email=owner),
        default_assignee_email=owner,
    )
    if not tasks_made:
        return DispatchResult(
            True,
            f"All default renewal tasks already exist for policy #{case.get('policy_number')}. Nothing added.",
            {"case_id": case["id"], "tasks_created": 0},
        )
    lines = [f"✅ Added {len(tasks_made)} renewal tasks for policy #{case.get('policy_number')} "
             f"(assigned {owner}):"]
    lines += [f"- {t.get('title')}" for t in tasks_made]
    return DispatchResult(True, "\n".join(lines), {"case_id": case["id"], "tasks_created": len(tasks_made)})
