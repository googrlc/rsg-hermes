"""Renewal desk read routes — queue, open-exact, and client research.

NowCerts + the reconciled ``renewal_candidates`` index are the source of truth;
EspoCRM is not consulted. These are the client-facing entry points a staffer
uses before the worksheet / case / writeback steps.

Routes:
  * get_renewal_queue     -> queue_handle    (list eligible renewals)
  * open_exact_renewal    -> open_handle     (ONE renewal, exact identity only)
  * research_renewal_client -> research_handle (web research on the client)

The open route is deliberately strict: it requires an exact NowCerts policy
number or policy GUID and will NOT fall back to a general report if one is
missing — that fallback is exactly the "returned the Sentinel report" bug.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from hermes.commands.renewal_worksheet import _candidates_by_name, parse_request
from hermes.core.dispatcher import DispatchResult
from hermes.renewals import resolve

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.sync.nowcerts_client import NowCertsClient

# CRITICAL first, then AT_RISK, then SAFE, then anything else.
_RISK_ORDER = {"CRITICAL": 0, "AT_RISK": 1, "SAFE": 2}
_RISK_ICON = {"CRITICAL": "🔴", "AT_RISK": "🟠", "SAFE": "🟢"}

_GUID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
_LIMIT_RE = re.compile(r"\b(?:top|first|limit)\s+(\d{1,3})\b", re.I)


def _get_nowcerts(nowcerts: "NowCertsClient | None") -> "NowCertsClient | None":
    if nowcerts is not None:
        return nowcerts
    try:
        from hermes.sync.nowcerts_client import NowCertsClient

        return NowCertsClient()
    except Exception:  # pragma: no cover - env/config dependent
        return None


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# get_renewal_queue
# ---------------------------------------------------------------------------

def _risk_key(row: dict[str, Any]) -> tuple[int, str]:
    risk = str(row.get("risk_status") or "").upper()
    return (_RISK_ORDER.get(risk, 3), str(row.get("renewal_event_date") or "9999-12-31"))


def queue_handle(
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
) -> DispatchResult:
    """List the eligible renewal queue from ``renewal_candidates`` (urgency-sorted)."""
    if supa is None:
        return DispatchResult(False, "The renewal queue needs the Supabase candidate index, which is not configured.")

    include_verify = bool(re.search(r"\b(needs?\s+verif|verify|unmatched)\b", text, re.I))
    lm = _LIMIT_RE.search(text)
    limit = min(int(lm.group(1)), 100) if lm else 15

    states = "in.(eligible,needs_verification)" if include_verify else "eq.eligible"
    try:
        rows = supa.select(
            "renewal_candidates",
            columns="client_name,policy_number,line_of_business,segment,expiration_date,"
            "renewal_event_date,risk_status,premium_current,eligibility_state,nowcerts_policy_guid",
            params={"eligibility_state": states, "order": "renewal_event_date.asc"},
            limit=500,
        )
    except Exception as exc:
        return DispatchResult(False, f"Could not read the renewal queue ({exc}).")

    if not rows:
        return DispatchResult(True, "No eligible renewals in the queue right now.", {"queue": [], "count": 0})

    rows.sort(key=_risk_key)
    shown = rows[:limit]

    by_risk: dict[str, int] = {}
    for r in rows:
        k = str(r.get("risk_status") or "—").upper()
        by_risk[k] = by_risk.get(k, 0) + 1
    summary = "  ".join(f"{_RISK_ICON.get(k, '')}{k} {n}" for k, n in sorted(by_risk.items(), key=lambda kv: _RISK_ORDER.get(kv[0], 3)))

    lines = [f"📋 Renewal queue — {len(rows)} eligible ({summary})"]
    if include_verify:
        lines[0] += "  ·  includes needs-verification"
    for r in shown:
        icon = _RISK_ICON.get(str(r.get("risk_status") or "").upper(), "•")
        verify = " 🔍" if r.get("eligibility_state") == "needs_verification" else ""
        lines.append(
            f"{icon} {r.get('client_name') or 'Unknown'} — policy #{r.get('policy_number') or '?'} "
            f"| {r.get('line_of_business') or '?'} | exp {r.get('expiration_date') or '?'} "
            f"| {_money(r.get('premium_current'))}{verify}"
        )
    if len(rows) > limit:
        lines.append(f"…and {len(rows) - limit} more. Add `top N` to show more, or open one: `open renewal for policy <number>`.")

    return DispatchResult(True, "\n".join(lines), {"queue": shown, "count": len(rows), "by_risk": by_risk})


# ---------------------------------------------------------------------------
# open_exact_renewal
# ---------------------------------------------------------------------------

def _parse_identity(text: str) -> dict[str, str | None]:
    """Extract an exact policy number and/or NowCerts policy GUID from *text*."""
    guid_m = _GUID_RE.search(text)
    guid = guid_m.group(1) if guid_m else None
    # Reuse the worksheet parser for the policy-number form; strip a matched GUID
    # first so it is never mistaken for a policy number.
    without_guid = _GUID_RE.sub("", text) if guid else text
    req = parse_request(without_guid)
    return {"policy_number": req.get("policy_number"), "policy_guid": guid}


def _render_open(resolved: resolve.ResolvedPolicy) -> str:
    p = resolved.policy or {}
    v = resolved.eligibility
    state = getattr(v, "state", "") or "unknown"
    reason = getattr(v, "reason", "") or ""
    icon = {"eligible": "✅", "needs_verification": "🔍", "excluded": "⛔"}.get(state, "")
    return (
        f"📇 Renewal — {p.get('accountName') or 'client'} (policy #{p.get('policyNumber')})\n"
        f"{icon} Eligibility: **{state.replace('_', ' ').title()}**"
        + (f" — {reason}\n" if reason else "\n")
        + f"- LOB: {p.get('line_of_business') or '?'}   Carrier: {p.get('carrier') or '?'}\n"
        f"- Effective: {p.get('effective_date') or '?'}   Expires: {p.get('expiration_date') or '?'}\n"
        f"- Expiring premium: {_money(p.get('current_premium'))}\n"
        f"- NowCerts policy GUID: `{p.get('policy_guid') or '?'}`\n\n"
        "Next: `prepare renewal worksheet for policy "
        f"{p.get('policyNumber')}` · `research renewal client for {p.get('accountName') or ''}`"
    )


def open_handle(
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
) -> DispatchResult:
    """Open exactly ONE renewal by NowCerts policy number or GUID. Never guesses."""
    ident = _parse_identity(text)
    policy_number = ident["policy_number"]
    policy_guid = ident["policy_guid"]

    if not policy_number and not policy_guid:
        return DispatchResult(
            False,
            "I need an exact NowCerts **policy number** or **policy GUID** to open a specific renewal — "
            "I won't substitute a general renewal report.\n"
            "Try: `open renewal for policy <policy number>`.",
            {"need_identifier": True},
        )

    nc = _get_nowcerts(nowcerts)
    if nc is None:
        return DispatchResult(False, "NowCerts is not reachable right now.")

    resolved = resolve.resolve_exact_policy(
        nc, policy_number=policy_number, policy_guid=policy_guid, supa=supa
    )
    if resolved.reason == resolve.NOT_FOUND:
        ref = policy_number or policy_guid
        return DispatchResult(
            False,
            f"⚠️ Reconciliation needed — no policy matching **{ref}** was found in NowCerts.",
            {"reconciliation_needed": True},
        )
    if resolved.reason == resolve.AMBIGUOUS:
        n = len(resolved.matches or [])
        return DispatchResult(
            False,
            f"⚠️ Ambiguous — {n} records match that identifier. Escalate to reconcile the duplicate; nothing was opened.",
            {"ambiguous": True, "matches": resolved.matches},
        )
    if resolved.reason == resolve.NEED_IDENTIFIER:
        return DispatchResult(
            False,
            "That GUID isn't in the candidate index yet, and no policy number was given. "
            "Re-try with the exact policy number.",
            {"need_identifier": True},
        )
    return DispatchResult(
        True,
        _render_open(resolved),
        {
            "policy_number": (resolved.policy or {}).get("policyNumber"),
            "policy_guid": (resolved.policy or {}).get("policy_guid"),
            "eligibility_state": getattr(resolved.eligibility, "state", None),
            "source": "nowcerts",
        },
    )


# ---------------------------------------------------------------------------
# research_renewal_client — renewal EXPOSURE review (NOT intake NAICS discovery)
# ---------------------------------------------------------------------------
# What a renewal review must re-confirm or update to re-rate the policy — i.e.
# what changed since last term / what's missing. This is renewal-specific;
# NAICS / company-identity discovery is an intake concern and lives there.

_EXPOSURE_CHECKLISTS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"work.?comp|workers|\bwc\b", re.I), [
        "Updated annual payroll by class code",
        "Employee headcount changes (hires / terms)",
        "New or changed job duties / class codes",
        "Owner / officer inclusion-exclusion elections",
        "New locations or states of operation",
        "Loss runs (current + prior 3 years)",
    ]),
    (re.compile(r"commercial\s*auto|business\s*auto|\bbap\b|trucking|fleet", re.I), [
        "Updated vehicle schedule (adds / deletes, VINs, values)",
        "Updated driver list + MVRs (new / removed drivers)",
        "Radius of operation / garaging changes",
        "Annual mileage & use changes",
        "Hired / non-owned auto exposure",
        "Loss runs (current + prior 3 years)",
    ]),
    (re.compile(r"general\s*liab|\bgl\b|premises|products", re.I), [
        "Updated annual revenue / receipts",
        "Updated payroll (if payroll-rated)",
        "Change in operations / new services",
        "Subcontractor use + certificates on file",
        "New locations / square footage",
        "Loss runs (current + prior 3 years)",
    ]),
    (re.compile(r"propert|building|\bbop\b|business\s*owner", re.I), [
        "Updated building & contents replacement values",
        "Roof / systems updates or renovations",
        "Business income / extra-expense worksheet",
        "New locations or vacated premises",
        "Protective safeguards (alarm / sprinkler) changes",
        "Loss runs (current + prior 3 years)",
    ]),
    (re.compile(r"umbrella|excess", re.I), [
        "Confirm all underlying schedules & limits still accurate",
        "Any new underlying exposures (auto / GL / employers liability)",
        "Updated revenue / vehicle / payroll drivers",
    ]),
    (re.compile(r"professional|e&o|errors|cyber|d&o|management", re.I), [
        "Updated revenue / number of professionals",
        "Change in services or client types",
        "Prior-acts / retro-date confirmation",
        "Any incidents / claims / circumstances",
        "Cyber: records count, controls (MFA / backups) changes",
    ]),
    (re.compile(r"home|dwelling|ho-?\d|renter|condo", re.I), [
        "Updated dwelling replacement cost / renovations",
        "Roof age & updates",
        "Protective devices (alarm) & wind mitigation",
        "Occupancy / rental-use changes",
        "Prior claims since last term",
    ]),
    (re.compile(r"personal\s*auto|\bppa\b", re.I), [
        "Driver adds / removes (new drivers in household)",
        "Vehicle adds / removes + VINs",
        "Mileage / use / garaging changes",
        "MVR / violations since last term",
        "Prior claims since last term",
    ]),
]

_GENERIC_EXPOSURES = [
    "Confirm named insured & mailing address",
    "Confirm current exposures vs. the expiring policy",
    "Loss runs (current + prior 3 years)",
    "Any material changes in operations since last term",
]


def _exposures_for(lob: str) -> list[str]:
    for pat, items in _EXPOSURE_CHECKLISTS:
        if pat.search(lob or ""):
            return items
    return _GENERIC_EXPOSURES


def _resolve_for_research(
    text: str, *, supa: "SupabaseClient | None", nowcerts: "NowCertsClient | None"
) -> resolve.ResolvedPolicy | DispatchResult:
    """Resolve the exact policy to review, or return a DispatchResult explaining why not."""
    nc = _get_nowcerts(nowcerts)
    if nc is None:
        return DispatchResult(False, "NowCerts is not reachable right now.")

    ident = _parse_identity(text)
    if ident["policy_number"] or ident["policy_guid"]:
        return resolve.resolve_exact_policy(
            nc, policy_number=ident["policy_number"], policy_guid=ident["policy_guid"], supa=supa
        )

    name = parse_request(text).get("client_name")
    if not name:
        return DispatchResult(
            False,
            "Tell me which renewal to review: include the policy number, or "
            "`research renewal client for <client name>`.",
        )
    rows = _candidates_by_name(supa, name) if supa is not None else []
    if not rows:
        return DispatchResult(False, f"No renewal candidate found for **{name}**.", {"client": name})
    if len(rows) > 1:
        lines = [f"Multiple policies match **{name}** — name the policy to review exposures:"]
        lines += [
            f"- policy #{r.get('policy_number')} | {r.get('line_of_business') or '?'} | exp {r.get('expiration_date') or '?'}"
            for r in rows
        ]
        return DispatchResult(False, "\n".join(lines), {"ambiguous": True, "candidates": rows})
    return resolve.resolve_exact_policy(nc, policy_number=rows[0].get("policy_number"), supa=supa)


def research_handle(
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
) -> DispatchResult:
    """Renewal exposure review — surface what to re-confirm / update to re-rate. No writes."""
    resolved = _resolve_for_research(text, supa=supa, nowcerts=nowcerts)
    if isinstance(resolved, DispatchResult):
        return resolved
    if not resolved.ok:
        return DispatchResult(
            False,
            "Could not resolve that policy in NowCerts (need an exact policy number or GUID).",
            {"reconciliation_needed": True},
        )

    p = resolved.policy or {}
    lob = p.get("line_of_business") or ""
    items = _exposures_for(lob)
    checklist = "\n".join(f"- [ ] {it}" for it in items)
    return DispatchResult(
        True,
        f"🧭 Renewal exposure review — {p.get('accountName') or 'client'} (policy #{p.get('policyNumber')})\n"
        f"Line of business: {lob or '?'}   Expires: {p.get('expiration_date') or '?'}\n\n"
        f"Confirm / update these before quoting the renewal:\n{checklist}\n\n"
        f"Once collected: `prepare renewal worksheet for policy {p.get('policyNumber')}`.",
        {
            "policy_number": p.get("policyNumber"),
            "line_of_business": lob,
            "missing_exposures": items,
            "source": "nowcerts",
        },
    )
