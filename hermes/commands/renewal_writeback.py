"""NL command surface for the approval-gated NowCerts writeback.

Three verbs map onto ``hermes.renewals.writeback``:

  propose_handle  -> stage an UNAPPROVED NowCerts renewal action for a policy
                     (request terms / prepare options / client follow-up). AMS
                     *field* changes (update_ams) are produced by the worksheet /
                     case flow, never hand-typed here.
  show_handle     -> list the proposed-but-unapproved rows (for a policy or all)
  confirm_handle  -> approve every proposed row for one EXACT policy number,
                     which hands them to the Renewal Executor

Nothing here writes to NowCerts. Confirm only flips the approval flag; the
executor performs the actual read-before / verify / read-after write out-of-band.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from hermes.commands.renewal_desk import _parse_identity
from hermes.core.dispatcher import DispatchResult
from hermes.renewals import writeback
from hermes.renewals.executor import (
    ACTION_CLIENT_FOLLOW_UP,
    ACTION_PREPARE_OPTIONS,
    ACTION_REQUEST_TERMS,
    ACTION_UPDATE_AMS,
)

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient

# NL phrase -> (action, human expected-result label). First match wins.
_ACTION_KEYWORDS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\brequest\s+(renewal\s+)?terms\b", re.I), ACTION_REQUEST_TERMS, "Renewal terms requested from carrier"),
    (re.compile(r"\bprepare\s+options\b", re.I), ACTION_PREPARE_OPTIONS, "Renewal options prepared"),
    (re.compile(r"\b(client\s+)?follow[\s-]?up\b", re.I), ACTION_CLIENT_FOLLOW_UP, "Client follow-up logged"),
]
_NOTE_RE = re.compile(r"(?:note\s*:|[:—-])\s*(.+)$", re.I)


def _pick_action(text: str) -> tuple[str, str]:
    for pat, action, label in _ACTION_KEYWORDS:
        if pat.search(text):
            return action, label
    return ACTION_REQUEST_TERMS, "Renewal terms requested from carrier"


def _extract_note(text: str) -> str | None:
    m = _NOTE_RE.search(text)
    return m.group(1).strip() if m else None


def _renewal_id_for(supa: "SupabaseClient", policy_number: str) -> str | None:
    try:
        rows = supa.select(
            "project_85_renewals",
            columns="id",
            params={"policy_number": f"eq.{policy_number}"},
            limit=1,
        )
    except Exception:
        return None
    return str(rows[0]["id"]) if rows and rows[0].get("id") else None


def _fmt_pending(row: dict) -> str:
    p = row.get("payload") or {}
    action = p.get("action") or row.get("action") or "?"
    fields = p.get("fields") or {}
    detail = p.get("note") or (", ".join(f"{k}={v}" for k, v in fields.items()))
    rid = str(row.get("id") or "")[:8]
    tail = f" · {detail}" if detail else ""
    return f"- `{rid}` **{action}** — policy #{p.get('policy_number') or row.get('object_id')}{tail}"


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------

def propose_handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
) -> DispatchResult:
    """Stage an UNAPPROVED NowCerts renewal action for a policy. Nothing is written."""
    if supa is None:
        return DispatchResult(False, "Proposing a writeback needs the Supabase queue, which is not configured.")

    ident = _parse_identity(text)
    policy_number = ident["policy_number"]
    if not policy_number:
        return DispatchResult(
            False,
            "I need an exact **policy number** to propose a NowCerts write-back.\n"
            "Try: `propose nowcerts write-back for policy <number>: request terms — <note>`.",
            {"need_identifier": True},
        )

    if re.search(r"\bupdate\s+ams\b|\bset\s+\w+\s*=", text, re.I) or ACTION_UPDATE_AMS in text:
        return DispatchResult(
            False,
            "AMS *field* changes (premium, dates, etc.) are staged from the renewal worksheet, "
            "not typed free-form — that keeps the exact values reviewable.\n"
            f"Run `prepare renewal worksheet for policy {policy_number}` first.",
        )

    action, expected = _pick_action(text)
    note = _extract_note(text)
    renewal_id = _renewal_id_for(supa, policy_number)

    try:
        row = writeback.propose_writeback(
            supa,
            action=action,
            policy_number=policy_number,
            expected_result=expected,
            renewal_id=renewal_id,
            note=note,
            channel="task",
            proposed_by="hermes-nl",
        )
    except Exception as exc:
        return DispatchResult(False, f"Could not stage the proposal ({exc}).")

    rid = str(row.get("id") or "")[:8]
    return DispatchResult(
        True,
        f"📝 Proposed (NOT yet written) — policy #{policy_number}\n"
        f"- Action: **{action}** · {expected}\n"
        + (f"- Note: {note}\n" if note else "")
        + f"- Queue row: `{rid}` · status: awaiting approval\n\n"
        f"Review with `show proposed nowcerts changes for policy {policy_number}`, "
        f"then `approve the proposed nowcerts write-back for policy {policy_number}`.",
        {"queue_id": row.get("id"), "policy_number": policy_number, "action": action, "approved": False},
    )


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

def show_handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
) -> DispatchResult:
    """List proposed-but-unapproved NowCerts writebacks (for a policy, or all)."""
    if supa is None:
        return DispatchResult(False, "Reading proposed changes needs the Supabase queue, which is not configured.")

    policy_number = _parse_identity(text)["policy_number"]
    try:
        rows = writeback.list_pending(supa, policy_number=policy_number)
    except Exception as exc:
        return DispatchResult(False, f"Could not read proposed changes ({exc}).")

    scope = f"policy #{policy_number}" if policy_number else "all policies"
    if not rows:
        return DispatchResult(True, f"No proposed NowCerts changes awaiting approval for {scope}.", {"pending": []})

    lines = [f"🕵️ Proposed NowCerts changes — {scope} ({len(rows)} awaiting approval):"]
    lines += [_fmt_pending(r) for r in rows]
    if policy_number:
        lines.append(f"\nApprove with `approve the proposed nowcerts write-back for policy {policy_number}`.")
    return DispatchResult(True, "\n".join(lines), {"pending": rows, "count": len(rows)})


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------

def confirm_handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    approved_by: str = "lamar",
) -> DispatchResult:
    """Approve every proposed row for one EXACT policy → hands them to the executor."""
    if supa is None:
        return DispatchResult(False, "Confirming a writeback needs the Supabase queue, which is not configured.")

    policy_number = _parse_identity(text)["policy_number"]
    if not policy_number:
        return DispatchResult(
            False,
            "I won't approve writebacks in bulk. Name the exact policy: "
            "`approve the proposed nowcerts write-back for policy <number>`.",
            {"need_identifier": True},
        )

    # Show what will be approved first is the caller's job (`show ...`). Here we act.
    try:
        updated = writeback.confirm_pending_for_policy(
            supa, policy_number=policy_number, approved_by=approved_by
        )
    except Exception as exc:
        return DispatchResult(False, f"Could not approve the writeback ({exc}).")

    if not updated:
        return DispatchResult(
            False,
            f"No proposed (unapproved) NowCerts changes found for policy #{policy_number}. "
            "Nothing to approve.",
            {"approved": 0},
        )

    actions = ", ".join(sorted({(r.get("payload") or {}).get("action", "?") for r in updated}))
    return DispatchResult(
        True,
        f"✅ Approved {len(updated)} NowCerts write-back(s) for policy #{policy_number} ({actions}).\n"
        "The Renewal Executor will apply them on its next run (read-before → write → verify → receipt). "
        "Nothing was written synchronously here.",
        {"approved": len(updated), "policy_number": policy_number, "queue_ids": [r.get("id") for r in updated]},
    )
