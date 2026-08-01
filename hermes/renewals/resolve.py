"""Resolve ONE renewal event by exact NowCerts identity — the renewal desk's front door.

NowCerts is the source of truth. A caller MUST
supply an exact policy number or NowCerts policy GUID. This module never
fuzzy-matches, never picks one of several matches, and never falls back to a
general report — an ambiguous or missing identifier is returned as such so the
caller can stop and ask, rather than guess.

Both ``open_exact_renewal`` and ``prepare_renewal_worksheet`` build on this so
they resolve identically. The returned ``policy`` dict is shaped for
``hermes.renewals.worksheet.build_worksheet_content``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from hermes.renewals import eligibility

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.integrations.nowcerts_client import NowCertsClient

# Resolution outcomes (ResolvedPolicy.reason)
RESOLVED = "resolved"
NOT_FOUND = "not_found"
AMBIGUOUS = "ambiguous"
NEED_IDENTIFIER = "need_identifier"


@dataclass
class ResolvedPolicy:
    """Outcome of an exact-identity resolve. ``ok`` iff ``reason == RESOLVED``."""

    ok: bool
    reason: str
    policy: dict[str, Any] | None = None          # normalized worksheet-shaped dict
    raw: dict[str, Any] | None = None             # raw NowCerts PolicyDetail record
    candidate: dict[str, Any] | None = None       # matched renewal_candidates row (if any)
    eligibility: eligibility.EligibilityResult | None = None
    matches: list[dict[str, Any]] | None = None   # populated when reason == AMBIGUOUS


def _policy_guid(raw: dict[str, Any]) -> str | None:
    for key in ("databaseId", "DatabaseId", "id", "Id"):
        val = raw.get(key)
        if val:
            return str(val)
    return None


def _iso_date(value: Any) -> str | None:
    return str(value)[:10] if value else None


def _premium(raw: dict[str, Any]) -> Any:
    for key in ("premium", "policyPremium", "premiumAmount"):
        if raw.get(key) is not None:
            return raw.get(key)
    return None


def normalize_nowcerts_policy(
    raw: dict[str, Any], *, client_name: str | None = None
) -> dict[str, Any]:
    """Map a NowCerts ``PolicyDetail`` record → the renewal dict the worksheet expects.

    Field names per docs/integrations/nowcerts-import-mapping.md §1.
    """
    return {
        "id": _policy_guid(raw),
        "policy_guid": _policy_guid(raw),
        "insured_database_id": raw.get("insuredDatabaseId") or raw.get("InsuredDatabaseId"),
        "policyNumber": raw.get("policyNumber") or raw.get("number"),
        "accountName": (
            client_name
            or raw.get("insuredCommercialName")
            or raw.get("insuredName")
            or raw.get("commercialName")
        ),
        "carrier": raw.get("carrierName") or raw.get("carrier"),
        "line_of_business": raw.get("lineOfBusiness") or raw.get("line_of_business"),
        "effective_date": _iso_date(raw.get("effectiveDate") or raw.get("effective_date")),
        "expiration_date": _iso_date(raw.get("expirationDate") or raw.get("expiration_date")),
        "current_premium": _premium(raw),
        "pipeline_stage": raw.get("policyStatus") or raw.get("status"),
        # eligibility.evaluate reads `status`/`normalized_status`, not pipeline_stage.
        "status": raw.get("policyStatus") or raw.get("status"),
        "source": "nowcerts",
    }


def _enrich_from_candidate(
    normalized: dict[str, Any], candidate: dict[str, Any] | None
) -> dict[str, Any]:
    """Backfill fields the sparse single-policy read is missing, from the ALREADY-VETTED
    renewal_candidate row (built by the nightly refresh off richer NowCerts data).

    Execution-time revalidation's job is to confirm the policy still exists + the
    insured is active — not to re-downgrade a vetted-eligible event just because
    ``find_policy_by_number`` returned a thin record (null effective date / status).
    """
    if not candidate:
        return normalized
    fills = {
        "effective_date": "effective_date",
        "expiration_date": "expiration_date",
        "line_of_business": "line_of_business",
        "current_premium": "premium_current",
        "status": "normalized_status",
        "pipeline_stage": "normalized_status",
    }
    for norm_key, cand_key in fills.items():
        if normalized.get(norm_key) in (None, ""):
            val = candidate.get(cand_key)
            if val not in (None, ""):
                normalized[norm_key] = val
    return normalized


def _candidate_by_guid(supa: "SupabaseClient", guid: str) -> list[dict[str, Any]]:
    return supa.select(
        "renewal_candidates",
        columns="*",
        params={"nowcerts_policy_guid": f"eq.{guid}"},
        limit=2,
    )


def _candidate_by_number(supa: "SupabaseClient", number: str) -> dict[str, Any] | None:
    rows = supa.select(
        "renewal_candidates",
        columns="*",
        params={"policy_number": f"eq.{number}"},
        limit=1,
    )
    return rows[0] if rows else None


def resolve_exact_policy(
    nowcerts: "NowCertsClient",
    *,
    policy_number: str | None = None,
    policy_guid: str | None = None,
    supa: "SupabaseClient | None" = None,
    today: date | None = None,
) -> ResolvedPolicy:
    """Resolve exactly one policy from NowCerts by number or GUID, with live eligibility.

    Resolution order:
      * GUID (when no number): looked up in ``renewal_candidates.nowcerts_policy_guid``
        to get the policy number (NowCerts has no reliable get-by-GUID filter). >1
        candidate for the GUID is AMBIGUOUS.
      * Number: ``NowCertsClient.find_policy_by_number`` — None -> NOT_FOUND,
        ``{"_ambiguous": True}`` -> AMBIGUOUS (duplicate numbers, never guessed).

    On a clean hit the policy is normalized and re-validated through the central
    eligibility rule (execution-time revalidation), so callers get a fresh verdict.
    """
    today = today or date.today()
    number = (policy_number or "").strip()
    guid = (policy_guid or "").strip()
    candidate: dict[str, Any] | None = None

    # GUID -> policy number via the candidate table (carries nowcerts_policy_guid).
    if guid and not number:
        if supa is None:
            return ResolvedPolicy(False, NEED_IDENTIFIER)
        rows = _candidate_by_guid(supa, guid)
        if len(rows) == 1:
            candidate = rows[0]
            number = str(candidate.get("policy_number") or "").strip()
        elif len(rows) > 1:
            return ResolvedPolicy(False, AMBIGUOUS, matches=rows)
        # zero rows -> fall through; number stays "" -> NEED_IDENTIFIER below

    if not number:
        return ResolvedPolicy(False, NEED_IDENTIFIER)

    raw = nowcerts.find_policy_by_number(number)
    if raw is None:
        return ResolvedPolicy(False, NOT_FOUND)
    if isinstance(raw, dict) and raw.get("_ambiguous"):
        return ResolvedPolicy(False, AMBIGUOUS, matches=raw.get("matches"))

    if candidate is None and supa is not None:
        candidate = _candidate_by_number(supa, number)

    client_name = (candidate or {}).get("client_name")
    normalized = normalize_nowcerts_policy(raw, client_name=client_name)
    normalized = _enrich_from_candidate(normalized, candidate)

    insured_guid = normalized.get("insured_database_id")
    insured_active = nowcerts.is_insured_active(insured_guid) if insured_guid else True
    verdict = eligibility.evaluate(normalized, insured_active=insured_active, today=today)

    return ResolvedPolicy(
        True,
        RESOLVED,
        policy=normalized,
        raw=raw,
        candidate=candidate,
        eligibility=verdict,
    )
