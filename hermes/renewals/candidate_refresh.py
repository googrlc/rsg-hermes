"""Renewal candidate refresh — builds renewal_candidates from the live book.

Pipeline (contract §Required architecture change): NowCerts selection ->
renewal_candidate evaluation (the centralized rule in ``eligibility``) -> Supabase
renewal event -> project_85_renewals projection.

Policy source = ``canonical_policies`` (the clean, deduped NowCerts book that also
carries the ``renewed_policy`` lineage pointer the NowCerts API itself does not
expose). Insured-active = **live NowCerts** (`fetch_insureds`) per the approved
design. The result is one row per renewal *event*, keyed by identity, with an
eligibility verdict + reason; eligible events are projected to project_85_renewals
for the existing read consumers.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any

from hermes_integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.operations import renewal_classifier
from hermes_integrations.nowcerts_client import NowCertsClient

from . import corrections, eligibility as elig
from .eligibility import LineageContext

from hermes.ams import book as ams_book

log = logging.getLogger(__name__)

CANDIDATES_TABLE = "renewal_candidates"
P85_TABLE = "project_85_renewals"
_ALIGN_TOLERANCE_DAYS = 3

# Renewal floor: the pipeline only carries events on/after this date. Anything
# with a renewal event before it (ancient past-due / stale lapses) is dropped —
# "from June 1 going forward" (RSG). Override with HERMES_RENEWAL_FLOOR=YYYY-MM-DD.
DEFAULT_RENEWAL_FLOOR = date(2026, 6, 1)


def renewal_floor() -> date:
    raw = os.environ.get("HERMES_RENEWAL_FLOOR", "").strip()
    if raw:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            log.warning("invalid HERMES_RENEWAL_FLOOR=%r; using default %s", raw, DEFAULT_RENEWAL_FLOOR)
    return DEFAULT_RENEWAL_FLOOR

# Ranking for identity dedup (higher wins).
_STATE_RANK = {elig.STATE_ELIGIBLE: 3, elig.STATE_NEEDS_VERIFICATION: 2, elig.STATE_EXCLUDED: 1}
_BRANCH_RANK = {elig.BRANCH_STAGED_NEXT_TERM: 3, elig.BRANCH_MEDICARE_ANNUAL: 2, elig.BRANCH_CURRENT_TERM: 1}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pd(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _aligned(a: date | None, b: date | None) -> bool:
    return a is not None and b is not None and abs((a - b).days) <= _ALIGN_TOLERANCE_DAYS


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------
def _renewed_from(policy: dict[str, Any]) -> str | None:
    """The predecessor policy number this term renewed from (None if self/empty)."""
    rp = str(policy.get("renewed_policy") or "").strip()
    pn = str(policy.get("policy_number") or "").strip()
    return rp if rp and rp != pn else None


def _root_number(policy: dict[str, Any], by_number: dict[str, dict[str, Any]]) -> str:
    """Walk the renewed_policy chain to the earliest ancestor's number."""
    seen: set[str] = set()
    cur = policy
    while True:
        pn = str(cur.get("policy_number") or "").strip()
        pred = _renewed_from(cur)
        if not pred or pred in seen:
            return pn or pred or ""
        seen.add(pn)
        nxt = by_number.get(pred)
        if nxt is None:
            return pred  # predecessor number known but its row isn't in the book
        cur = nxt


def _is_current_active(policy: dict[str, Any], today: date) -> bool:
    status = elig.normalize_status(policy.get("status"))
    eff, exp = _pd(policy.get("effective_date")), _pd(policy.get("expiration_date"))
    return status in elig.CURRENT_STATUSES and eff is not None and exp is not None and eff <= today < exp


def _is_staged(policy: dict[str, Any]) -> bool:
    return elig.normalize_status(policy.get("status")) in elig.STAGED_STATUSES


def _lineage_for(
    policy: dict[str, Any],
    by_number: dict[str, dict[str, Any]],
    successors: dict[str, list[dict[str, Any]]],
    today: date,
) -> LineageContext:
    pn = str(policy.get("policy_number") or "").strip()
    pred = _renewed_from(policy)
    root = _root_number(policy, by_number)
    exp = _pd(policy.get("expiration_date"))
    eff = _pd(policy.get("effective_date"))

    # A staged/active successor whose effective aligns with this term's expiration.
    valid_successor = None
    for s in successors.get(pn, []):
        if (_is_staged(s) or elig.normalize_status(s.get("status")) in elig.CURRENT_STATUSES) and _aligned(
            _pd(s.get("effective_date")), exp
        ):
            valid_successor = s
            break

    # For a staged term: a predecessor that is a current active term whose
    # expiration aligns with this term's effective date.
    follows = False
    if pred:
        c = by_number.get(pred)
        if c is not None and _is_current_active(c, today) and _aligned(_pd(c.get("expiration_date")), eff):
            follows = True

    return LineageContext(
        lineage_id=elig.derive_lineage_id(policy, root_policy_number=root),
        predecessor_policy_number=pred,
        successor_policy_number=(str(valid_successor.get("policy_number")) if valid_successor else None),
        has_valid_successor=valid_successor is not None,
        follows_current_term=follows,
    )


def _account_premium(policies: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for p in policies:
        if not p.get("active"):
            continue
        guid = str(p.get("nowcerts_insured_guid") or "")
        prem = _num(p.get("annualized_premium")) or _num(p.get("premium_amount")) or 0.0
        totals[guid] = totals.get(guid, 0.0) + prem
    return totals


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def _event_date_for(result: elig.EligibilityResult, policy: dict[str, Any]) -> date | None:
    """Identity date: the computed event for eligible rows, else the policy's own
    expiration/effective so excluded/verify rows still form a valid identity.
    Truly dateless rows return None and are skipped (nothing to track)."""
    return result.event_date or _pd(policy.get("expiration_date")) or _pd(policy.get("effective_date"))


def _candidate_row(
    policy: dict[str, Any],
    result: elig.EligibilityResult,
    lineage: LineageContext,
    event_date: date,
    *,
    client_name: str | None,
    insured_active: bool,
    now_iso: str,
) -> dict[str, Any]:
    premium_current = _num(policy.get("premium_amount")) or _num(policy.get("annualized_premium"))
    eff, exp = _pd(policy.get("effective_date")), _pd(policy.get("expiration_date"))
    risk_status = None
    if result.eligible:
        risk_status = renewal_classifier.classify_risk(
            policy_status=result.normalized_status,
            expiration_date=event_date.isoformat(),
            today=date.today(),
        )
    return {
        "insured_id": str(policy.get("nowcerts_insured_guid") or ""),
        "policy_lineage_id": lineage.lineage_id,
        "renewal_event_date": event_date.isoformat(),
        "nowcerts_policy_guid": policy.get("policy_guid"),
        "policy_number": policy.get("policy_number"),
        "insured_active": insured_active,
        "policy_active": bool(policy.get("active")),
        "normalized_status": result.normalized_status,
        "branch": result.branch,
        "effective_date": eff.isoformat() if eff else None,
        "expiration_date": exp.isoformat() if exp else None,
        "predecessor_policy_number": lineage.predecessor_policy_number,
        "successor_policy_number": lineage.successor_policy_number,
        "eligibility_state": result.state,
        "eligibility_reason": result.reason,
        "last_verified_at": now_iso,
        "segment": result.segment,
        "line_of_business": result.line_of_business,
        "client_name": client_name,
        "in_working_queue": result.in_working_queue,
        "workflow_entry_date": result.workflow_entry_date.isoformat() if result.workflow_entry_date else None,
        "risk_status": risk_status,
        "premium_current": premium_current,
        "premium_renewal": None,
        "updated_at": now_iso,
    }


def build_candidates(
    policies: list[dict[str, Any]],
    insured: dict[str, dict[str, Any]],
    *,
    today: date,
    now_iso: str | None = None,
    floor: date | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every policy and collapse to one row per renewal-event identity.

    Events whose renewal date falls before ``floor`` (default the June-1 renewal
    floor) are dropped entirely — they never reach renewal_candidates, the
    pipeline, or the lapse-check list.
    """
    now_iso = now_iso or _utcnow_iso()
    floor = floor or renewal_floor()
    by_number: dict[str, dict[str, Any]] = {}
    successors: dict[str, list[dict[str, Any]]] = {}
    for p in policies:
        pn = str(p.get("policy_number") or "").strip()
        if pn:
            by_number.setdefault(pn, p)
        pred = _renewed_from(p)
        if pred:
            successors.setdefault(pred, []).append(p)
    acct = _account_premium(policies)

    best: dict[tuple[str, str, str], tuple[int, dict[str, Any]]] = {}
    for p in policies:
        guid = str(p.get("nowcerts_insured_guid") or "")
        ins = insured.get(guid) or {}
        insured_active = bool(ins.get("active"))
        lineage = _lineage_for(p, by_number, successors, today)
        result = elig.evaluate(
            p, insured_active=insured_active, today=today,
            account_active_premium=acct.get(guid), lineage=lineage,
        )
        event_date = _event_date_for(result, p)
        if event_date is None:
            continue  # truly dateless — nothing to key an event on
        if event_date < floor:
            continue  # renewal event before the June-1 floor — drop entirely
        row = _candidate_row(
            p, result, lineage, event_date,
            client_name=ins.get("name"), insured_active=insured_active, now_iso=now_iso,
        )
        key = (row["insured_id"], row["policy_lineage_id"], row["renewal_event_date"])
        rank = _STATE_RANK.get(result.state, 0) * 10 + _BRANCH_RANK.get(result.branch, 0)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, row)
    return [row for _, row in best.values()]


# ---------------------------------------------------------------------------
# Insured-active (live NowCerts)
# ---------------------------------------------------------------------------
def _insured_index(nowcerts: NowCertsClient) -> dict[str, dict[str, Any]]:
    """Live {insured_guid: {active, name}} from NowCerts InsuredDetailList."""
    index: dict[str, dict[str, Any]] = {}
    for row in nowcerts.fetch_insureds():
        guid = str(row.get("id") or row.get("databaseId") or row.get("insuredDatabaseId") or "").strip()
        if not guid:
            continue
        name = (
            row.get("commercialName")
            or " ".join(x for x in (row.get("firstName"), row.get("lastName")) if x).strip()
            or None
        )
        index[guid] = {"active": bool(row.get("active")), "name": name}
    return index


# ---------------------------------------------------------------------------
# Projection to project_85_renewals (eligible events only)
# ---------------------------------------------------------------------------
def _project_eligible(supa: SupabaseClient, eligible: list[dict[str, Any]]) -> dict[str, int]:
    eligible_pns: set[str] = set()
    payloads = [
        {
            "policy_number": str(c["policy_number"]),
            "client_name": c.get("client_name") or "Unknown client",
            "expiration_date": c.get("renewal_event_date"),
            "premium_current": c.get("premium_current"),
            "risk_status": c.get("risk_status") or "SAFE",
            "ai_strategy_notes": renewal_classifier.build_strategy_note(
                c.get("risk_status") or "SAFE", c.get("normalized_status"), None
            ),
        }
        for c in eligible if c.get("policy_number")
    ]
    # A correction made on the renewal desk outranks the projection. Without this
    # the rebuilt note, premium and risk call overwrite it every night — which is
    # exactly what made correcting a renewal feel pointless.
    for payload in corrections.apply(supa, payloads, surface=corrections.PROJECTION):
        pn = payload["policy_number"]
        if corrections.is_dismissed(corrections.PROJECTION, payload):
            continue          # removed from the worklist; not ours to hand back
        eligible_pns.add(pn)
        try:
            supa.upsert(P85_TABLE, corrections.strip_keys(payload), on_conflict="policy_number")
        except SupabaseClientError:
            log.exception("p85 projection upsert failed for %s", pn)

    # Prune stale rows: not eligible AND not referenced by any renewal_actions
    # (renewal_actions FK-cascades, so protect rows that carry history).
    protected = {
        r.get("renewal_id")
        for r in supa.select("renewal_actions", columns="renewal_id", limit=10000)
        if r.get("renewal_id")
    }
    existing = supa.select(P85_TABLE, columns="id,policy_number", limit=10000)
    pruned = 0
    for r in existing:
        if str(r.get("policy_number")) not in eligible_pns and r.get("id") not in protected:
            try:
                supa.delete(P85_TABLE, r["id"])
                pruned += 1
            except SupabaseClientError:
                log.exception("p85 prune failed for %s", r.get("id"))
    return {"projected": len(eligible_pns), "pruned": pruned}


# ---------------------------------------------------------------------------
# Lapse-check list (past-due-but-active, routed OFF the forward pipeline)
# ---------------------------------------------------------------------------
_LAPSE_FIELDS = (
    "policy_number,client_name,expiration_date,premium_current,line_of_business,"
    "normalized_status,eligibility_reason,insured_active,policy_active"
)


def lapse_check(supa: SupabaseClient, *, today: date | None = None) -> dict[str, Any]:
    """The 'lapse check' list — past-due-but-still-active renewals kept OFF the
    forward renewals pipeline (per RSG: renewals pipeline = forward window only).

    These are ``needs_verification`` events whose expiration already passed but
    sits on/after the June-1 floor — likely silent lapses to confirm in NowCerts.
    Derived read of ``renewal_candidates``; no separate table.
    """
    today = today or date.today()
    today_iso, floor_iso = today.isoformat(), renewal_floor().isoformat()
    rows = supa.select(
        CANDIDATES_TABLE,
        columns=_LAPSE_FIELDS,
        params={
            "eligibility_state": f"eq.{elig.STATE_NEEDS_VERIFICATION}",
            "order": "expiration_date.desc",
        },
        limit=5000,
    )
    items = [
        r for r in rows
        if (exp := r.get("expiration_date")) and floor_iso <= str(exp)[:10] < today_iso
    ]
    premium_at_risk = sum(_num(r.get("premium_current")) or 0.0 for r in items)
    return {
        "as_of": today_iso,
        "floor": floor_iso,
        "count": len(items),
        "premium_at_risk": round(premium_at_risk),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_refresh(
    *,
    supa: SupabaseClient | None = None,
    nowcerts: NowCertsClient | None = None,
    today: date | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Rebuild renewal_candidates from the live book and project eligible to p85."""
    supa = supa or SupabaseClient()
    nowcerts = nowcerts or NowCertsClient()
    today = today or date.today()
    now_iso = _utcnow_iso()

    # Live AMS book (facts) joined to policy_lineage (renewed_policy) — the AMS
    # exposes no renewal pointer, so lineage stays Supabase-owned.
    policies = ams_book.select_policies(supa, limit=10000)
    insured = _insured_index(nowcerts)
    rows = build_candidates(policies, insured, today=today, now_iso=now_iso)

    # Human corrections outrank the rebuild. Applied BEFORE the eligible split so
    # a dismissal ('excluded') keeps the row out of the p85 projection too —
    # otherwise every night hands back the renewal somebody took off the list,
    # and reverts the premium somebody fixed.
    overlaid = corrections.apply(supa, rows, surface=corrections.CANDIDATES)
    corrected = sum(1 for r in overlaid if r.get("_overridden"))
    rows = [corrections.strip_keys(r) for r in overlaid]

    counts = {"eligible": 0, "needs_verification": 0, "excluded": 0}
    for r in rows:
        counts[r["eligibility_state"]] = counts.get(r["eligibility_state"], 0) + 1
    eligible = [r for r in rows if r["eligibility_state"] == elig.STATE_ELIGIBLE]
    working = sum(1 for r in eligible if r.get("in_working_queue"))

    summary: dict[str, Any] = {
        "dry_run": dry_run, "as_of": today.isoformat(),
        "policies": len(policies), "candidates": len(rows),
        "in_working_queue": working, "corrected": corrected, **counts,
    }
    if dry_run:
        summary["sample_eligible"] = [
            {"policy_number": r["policy_number"], "branch": r["branch"],
             "event_date": r["renewal_event_date"], "segment": r["segment"],
             "in_working_queue": r["in_working_queue"], "risk_status": r["risk_status"]}
            for r in eligible[:15]
        ]
        return summary

    for r in rows:
        try:
            supa.upsert(CANDIDATES_TABLE, r, on_conflict="insured_id,policy_lineage_id,renewal_event_date")
        except SupabaseClientError:
            log.exception("renewal_candidates upsert failed for %s", r.get("policy_number"))
    summary.update(_project_eligible(supa, eligible))
    log.info("renewal refresh: %s", summary)
    return summary
