"""Prepare a client-specific renewal worksheet on demand — NowCerts-sourced.

Recognises variants such as:
  - prepare a renewal worksheet for <client>
  - prepare renewal worksheet for policy <policy number>
  - create/build/generate a renewal worksheet for <client>

Route precedence: this handler is registered BEFORE the broad renewal/revenue
route so it intercepts worksheet requests first.

Resolution rules (NowCerts is the source of truth)
---------------------------------------------
* An exact policy number resolves one policy via
  ``hermes.renewals.resolve.resolve_exact_policy`` (NowCerts
  ``find_policy_by_number``). Duplicate policy numbers are a stop-and-escalate
  condition, never guessed.
* When only a client name is supplied, matching renewal candidates are listed
  from the reconciled ``renewal_candidates`` index and no worksheet is generated
  until the caller picks an exact policy number.
* When a policy is absent from NowCerts the handler returns an explicit
  reconciliation-needed response and never creates or merges a record.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from hermes.core.dispatch import DispatchResult
from hermes.renewals import resolve, worksheet

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.integrations.nowcerts_client import NowCertsClient


# ---------------------------------------------------------------------------
# Text parsing helpers
# ---------------------------------------------------------------------------

# A valid policy number must be at least 3 characters (1 lead + 2 body chars).
_POLICY_NUMBER_RE = re.compile(
    r"\bpolic(?:y|ies)\s+(?:number\s+|#\s*|no\.?\s+)?([A-Z0-9][-A-Z0-9/ ]{2,})",
    re.I,
)
# Client name captured from "for <name>" — bounded to avoid runaway matches.
_MAX_CLIENT_NAME_LEN = 150
_FOR_CLIENT_RE = re.compile(
    rf"\bfor\s+([A-Za-z0-9 &',.\-]{{1,{_MAX_CLIENT_NAME_LEN}}})$",
    re.I,
)


def _normalise_policy_number(raw: str) -> str:
    """Strip surrounding whitespace and fold to upper-case for exact comparison."""
    return " ".join(raw.split()).upper()


def parse_request(text: str) -> dict[str, str | None]:
    """Return ``{"policy_number": ..., "client_name": ...}`` from *text*.

    At least one key will be non-``None`` when the route pattern matched.
    """
    policy_number: str | None = None
    client_name: str | None = None

    m = _POLICY_NUMBER_RE.search(text)
    if m:
        policy_number = _normalise_policy_number(m.group(1))

    # Remove the matched policy fragment before looking for client name.
    remaining = _POLICY_NUMBER_RE.sub("", text) if m else text

    for_match = _FOR_CLIENT_RE.search(remaining)
    if for_match:
        candidate = for_match.group(1).strip()
        # Strip trailing noise tokens (e.g. "for Test Corp policy") that may
        # remain after policy-fragment removal.
        for suffix in (" policy", " number", " #", " no.", " no"):
            if candidate.lower().endswith(suffix):
                candidate = candidate[: -len(suffix)].strip()
                break
        if candidate:
            client_name = candidate

    return {"policy_number": policy_number, "client_name": client_name}


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _eligibility_banner(verdict: Any) -> str:
    """One-line eligibility note for the worksheet header (informational only)."""
    if verdict is None:
        return ""
    state = getattr(verdict, "state", "") or ""
    reason = getattr(verdict, "reason", "") or ""
    icon = {"eligible": "✅", "needs_verification": "🔍", "excluded": "⛔"}.get(state, "")
    label = state.replace("_", " ").title() if state else "Unknown"
    tail = f" — {reason}" if reason else ""
    return f"{icon} Eligibility: **{label}**{tail}"


def _candidate_summary(row: dict[str, Any]) -> str:
    name = row.get("client_name") or "Unknown"
    lob = row.get("line_of_business") or "?"
    pnum = row.get("policy_number") or "?"
    exp = row.get("expiration_date") or "?"
    risk = row.get("risk_status") or ""
    risk_tag = f" | {risk}" if risk else ""
    return f"- {name} | {lob} | policy #{pnum} | exp {exp}{risk_tag}"


def _worksheet_result(resolved: resolve.ResolvedPolicy) -> DispatchResult:
    policy = resolved.policy or {}
    content = worksheet.build_worksheet_content(policy)
    acct = policy.get("accountName") or policy.get("policyNumber") or "client"
    banner = _eligibility_banner(resolved.eligibility)
    header = f"📄 Renewal Worksheet — {acct} (policy #{policy.get('policyNumber')})"
    body = f"{header}\n{banner}\n\n{content}" if banner else f"{header}\n\n{content}"
    return DispatchResult(
        True,
        body,
        {
            "worksheet": policy,
            "source": "nowcerts",
            "policy_guid": policy.get("policy_guid"),
            "eligibility_state": getattr(resolved.eligibility, "state", None),
        },
    )


# ---------------------------------------------------------------------------
# Client-name lookup (reconciled candidate index — never NowCerts fuzzy search)
# ---------------------------------------------------------------------------

def escape_ilike(value: str) -> str:
    """Neutralize PostgREST/SQL-LIKE metacharacters so a name matches literally.

    Backslash first (LIKE escape char), then the ``%``/``_`` wildcards; strip the
    PostgREST ``*`` wildcard and commas (PostgREST value separators). Prevents a
    name like ``A%`` or ``a_b`` from broadening the discovery match.
    """
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped.replace("*", "").replace(",", " ").strip()


def _candidates_by_name(
    supa: "SupabaseClient", client_name: str
) -> list[dict[str, Any]]:
    needle = escape_ilike(client_name)
    try:
        return supa.select(
            "renewal_candidates",
            columns="client_name,policy_number,line_of_business,expiration_date,"
            "risk_status,eligibility_state,nowcerts_policy_guid",
            # Discovery surfaces the actionable pool only — excluded (cancelled/
            # expired/superseded) candidates never appear in a name search.
            params={"client_name": f"ilike.*{needle}*", "eligibility_state": "neq.excluded"},
            limit=25,
        )
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

def handle(
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
) -> DispatchResult:
    """Prepare a renewal worksheet for the requested client or policy.

    All reads come from NowCerts + the Supabase candidate index.
    """
    req = parse_request(text)
    policy_number = req["policy_number"]
    client_name = req["client_name"]

    if not policy_number and not client_name:
        return DispatchResult(
            False,
            "Could not identify a client name or policy number in your request.\n"
            "Try: `prepare renewal worksheet for <client name>` or "
            "`prepare renewal worksheet for policy <policy number>`.",
        )

    if nowcerts is None:
        try:
            from hermes.integrations.nowcerts_client import NowCertsClient

            nowcerts = NowCertsClient()
        except Exception as exc:  # pragma: no cover - env/config dependent
            return DispatchResult(
                False, f"NowCerts is not reachable right now ({exc})."
            )

    # --- Exact policy-number path ---
    if policy_number:
        resolved = resolve.resolve_exact_policy(
            nowcerts, policy_number=policy_number, supa=supa
        )
        if resolved.reason == resolve.NOT_FOUND:
            return DispatchResult(
                False,
                f"⚠️ Reconciliation needed — policy **{policy_number}** was not found in NowCerts.\n"
                "No worksheet was generated and no record was created.",
                {"reconciliation_needed": True, "policy_number": policy_number},
            )
        if resolved.reason == resolve.AMBIGUOUS:
            n = len(resolved.matches or [])
            return DispatchResult(
                False,
                f"⚠️ Ambiguous match — {n} policies share policy number **{policy_number}** in NowCerts. "
                "No worksheet was generated. Escalate to reconcile the duplicate before proceeding.",
                {"ambiguous": True, "matches": resolved.matches, "policy_number": policy_number},
            )
        if not resolved.ok:
            return DispatchResult(
                False,
                f"Could not resolve policy **{policy_number}** (need an exact policy number or NowCerts GUID).",
            )
        return _worksheet_result(resolved)

    # --- Client-name path (list candidates; never auto-pick) ---
    assert client_name is not None
    if supa is None:
        return DispatchResult(
            False,
            f"To look up **{client_name}** by name I need the candidate index.\n"
            "Re-submit with the exact policy number: "
            "`prepare renewal worksheet for policy <policy number>`.",
        )
    rows = _candidates_by_name(supa, client_name)
    if not rows:
        return DispatchResult(
            False,
            f"⚠️ Reconciliation needed — no renewal candidates found for **{client_name}**.\n"
            "No worksheet was generated. Check the exact client name, or supply the policy number.",
            {"reconciliation_needed": True, "client_name": client_name},
        )
    if len(rows) > 1:
        lines = [
            f"Multiple policies match **{client_name}** ({len(rows)} found). "
            "No worksheet was generated. Pick one:",
        ]
        lines.extend(_candidate_summary(r) for r in rows)
        lines.append(
            "\nRe-submit with the specific policy number: "
            "`prepare renewal worksheet for policy <policy number>`."
        )
        return DispatchResult(
            False,
            "\n".join(lines),
            {"ambiguous": True, "candidates": rows, "client_name": client_name},
        )

    # Exactly one candidate → resolve it exactly by its policy number.
    only = rows[0]
    resolved = resolve.resolve_exact_policy(
        nowcerts, policy_number=only.get("policy_number"), supa=supa
    )
    if not resolved.ok:
        return DispatchResult(
            False,
            f"⚠️ Reconciliation needed — candidate for **{client_name}** "
            f"(policy #{only.get('policy_number')}) did not resolve in NowCerts.",
            {"reconciliation_needed": True, "client_name": client_name, "candidate": only},
        )
    return _worksheet_result(resolved)
