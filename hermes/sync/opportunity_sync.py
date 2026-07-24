"""NowCerts Opportunity → Supabase opportunities pipeline sync.

A NowCerts Opportunity is a first-class object (the agency's real pipeline),
distinct from a policy/quote. This reads ``OpportunitiesList`` and mirrors each
opportunity onto the ``opportunities`` table, keyed by the NowCerts opportunity id.

Faithful mirror:
  * stage is stored **verbatim** from ``opportunityStageName`` (e.g. 'Bound / Won',
    'Annual Policy Review', 'Renewal in 30 days') — the AMS is the source of truth
    for the pipeline; we do not squeeze it into an invented enum.
  * ``winProbability`` (categorical Excellent..NotLikely) → ``likelihood``.
  * ``createdFromRenewal`` → ``opportunity_type`` (Renewals vs New Business).

Reconciliation (idempotent, additive): match an existing row by the NowCerts
opportunity id, else by (client_identifier, line_of_business, opportunity_type) —
so a manually/intake-created opp gets adopted, not duplicated — else insert.
Schema-adaptive writes; dry_run is side-effect-free.
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.intake import opportunities as opp
from hermes.sync.field_mapper import _strip_date

from hermes.ams import book as ams_book

log = logging.getLogger(__name__)

SYNC_SOURCE = "nowcerts-opportunity-sync"

# NowCerts winProbability (concatenated) → our likelihood (display form).
_LIKELIHOOD_MAP = {
    "excellent": "Excellent", "verygood": "Very Good", "very good": "Very Good",
    "good": "Good", "moderate": "Moderate", "notlikely": "Not Likely", "not likely": "Not Likely",
}

# Columns this sync may write (intersected with live columns for safety).
_COLS = {
    "client_identifier", "line_of_business", "opportunity_type", "insured_id", "insured_name",
    "stage", "status", "likelihood", "referral_source", "description", "assigned_to",
    "needed_by", "effective_date", "stage_due_date", "nowcerts_opportunity_id",
    "premium_estimate", "synced_at", "sync_source",
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _norm_lob(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip()


def _default_premium() -> float | None:
    """Fallback estimated premium when the client has no matching book policy.
    Set HERMES_OPPORTUNITY_DEFAULT_PREMIUM to a number; unset → leave it blank."""
    return _num(os.environ.get("HERMES_OPPORTUNITY_DEFAULT_PREMIUM"))


def _premium_lookup(supa: Any, guids: list[str]) -> dict[tuple[str, str], float]:
    """(insured_guid, normalized LOB) → current active premium from the canonical
    book — a realistic estimated premium so the CRM pipeline can total book size."""
    lut: dict[tuple[str, str], float] = {}
    uniq = [g for g in {g for g in guids if g}]
    for i in range(0, len(uniq), 50):
        batch = uniq[i:i + 50]
        try:
            rows = ams_book.select_policies(
                supa,
                columns="nowcerts_insured_guid,lines_of_business,active,annualized_premium,premium_amount",
                params={"nowcerts_insured_guid": f"in.({','.join(batch)})"},
                limit=5000,
            )
        except Exception:  # noqa: BLE001 — estimate is best-effort
            rows = []
        for p in rows:
            if not p.get("active"):
                continue
            prem = _num(p.get("annualized_premium")) or _num(p.get("premium_amount"))
            if prem is None:
                continue
            lut[(str(p.get("nowcerts_insured_guid")), _norm_lob(p.get("lines_of_business")))] = prem
    return lut


def _lob_averages(supa: Any) -> dict[str, float]:
    """Agency-wide average active premium per LOB — the estimate for a NEW-business
    opportunity (the client has no policy in that line yet)."""
    try:
        rows = ams_book.select_policies(
            supa,
            columns="lines_of_business,active,annualized_premium,premium_amount",
            limit=20000,
        )
    except Exception:  # noqa: BLE001
        return {}
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for p in rows:
        if not p.get("active"):
            continue
        prem = _num(p.get("annualized_premium")) or _num(p.get("premium_amount"))
        if prem is None:
            continue
        a = acc[_norm_lob(p.get("lines_of_business"))]
        a[0] += prem
        a[1] += 1
    return {lob: round(total / n) for lob, (total, n) in acc.items() if n}


def _name(o: dict[str, Any]) -> str:
    c = o.get("insuredCommercialName")
    if c:
        return str(c).strip()
    parts = [str(o.get("insuredFirstName") or "").strip(), str(o.get("insuredLastName") or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _likelihood(o: dict[str, Any]) -> str | None:
    return _LIKELIHOOD_MAP.get(str(o.get("winProbability") or "").strip().lower())


def _stage(o: dict[str, Any]) -> str:
    return str(o.get("opportunityStageName") or "").strip() or "Preparing Application"


def _type(o: dict[str, Any], stage: str) -> str:
    if o.get("createdFromRenewal"):
        return opp.TYPE_RENEWALS
    s = stage.lower()
    if "renewal" in s or "annual policy review" in s:
        return opp.TYPE_RENEWALS
    return opp.TYPE_NEW_BUSINESS


def _status(stage: str) -> str:
    s = stage.lower()
    if "won" in s or "bound" in s or "complete" in s:
        return opp.STATUS_WON
    if "lost" in s or "dead" in s or "not renewed" in s:
        return opp.STATUS_LOST
    return opp.STATUS_OPEN


@dataclass
class OpportunitySyncResult:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped_worked: int = 0
    skipped_no_client: int = 0
    previews: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        return (
            f"nowcerts opportunities → pipeline: fetched={self.fetched} created={self.created} "
            f"updated={self.updated} skipped_worked={self.skipped_worked} "
            f"skipped_no_client={self.skipped_no_client} errors={len(self.errors)}"
        )


def _discover_columns(supa: Any) -> set[str]:
    try:
        rows = supa.select(opp.TABLE, columns="*", limit=1)
    except Exception:  # noqa: BLE001
        return set(_COLS)
    if rows and isinstance(rows[0], dict):
        return set(rows[0].keys())
    return set(_COLS)


def _payload(o: dict[str, Any], *, now_iso: str, cols: set[str]) -> dict[str, Any]:
    stage = _stage(o)
    needed = _strip_date(o.get("neededBy"))
    raw = {
        "opportunity_type": _type(o, stage),
        "insured_id": str(o.get("insuredDatabaseId") or "").strip() or None,
        "stage": stage,
        "status": _status(stage),
        "likelihood": _likelihood(o),
        "referral_source": o.get("referralSourceName") or None,
        "description": o.get("description") or None,
        "assigned_to": o.get("assignedTo") or None,
        "needed_by": needed,
        "effective_date": needed,
        "stage_due_date": _strip_date(o.get("currentStageDueDate")),
        "nowcerts_opportunity_id": str(o.get("id") or "").strip() or None,
        "synced_at": now_iso,
        "sync_source": SYNC_SOURCE,
    }
    return {k: v for k, v in raw.items() if v is not None and k in cols}


def _find_existing(supa: Any, *, opp_id: str | None, cid: str, lob: str, otype: str) -> dict[str, Any] | None:
    if opp_id:
        rows = supa.select(opp.TABLE, columns="*", params={"nowcerts_opportunity_id": f"eq.{opp_id}"}, limit=1)
        if rows:
            return rows[0]
    rows = supa.select(
        opp.TABLE, columns="*",
        params={"client_identifier": f"eq.{cid}", "line_of_business": f"eq.{lob}", "opportunity_type": f"eq.{otype}"},
        limit=1,
    )
    return rows[0] if rows else None


def run_opportunity_sync(
    nc: Any,
    supa: Any,
    *,
    dry_run: bool = False,
    page_size: int = 100,
    limit: int | None = None,
) -> OpportunitySyncResult:
    """Mirror NowCerts Opportunities onto the pipeline. Idempotent; additive."""
    result = OpportunitySyncResult()
    opps = nc.fetch_opportunities(page_size=page_size)
    if limit:
        opps = opps[:limit]
    result.fetched = len(opps)
    log.info("opportunity sync: %d NowCerts opportunities (dry_run=%s)", len(opps), dry_run)
    if not opps:
        return result

    cols = _discover_columns(supa)
    now_iso = _utcnow_iso()
    prem_lut = _premium_lookup(supa, [str(o.get("insuredDatabaseId") or "") for o in opps]) if not dry_run else {}
    lob_avg = _lob_averages(supa) if not dry_run else {}
    default_prem = _default_premium()

    for o in opps:
        name = _name(o)
        lob = str(o.get("lineOfBusinessName") or "").strip()
        if not name or not lob:
            result.skipped_no_client += 1
            continue
        cid = opp.make_client_identifier(name)
        stage = _stage(o)
        otype = _type(o, stage)
        payload = _payload(o, now_iso=now_iso, cols=cols)
        # Estimated premium (CRM-side): client's current book premium for this LOB →
        # agency LOB-average (new business has no policy yet) → configured default,
        # so every opportunity carries a number the pipeline can total.
        nl = _norm_lob(lob)
        est = prem_lut.get((str(o.get("insuredDatabaseId") or "").strip(), nl))
        if est is None:
            est = lob_avg.get(nl)
        if est is None:
            est = default_prem
        try:
            existing = _find_existing(supa, opp_id=payload.get("nowcerts_opportunity_id"), cid=cid, lob=lob, otype=otype)
            if dry_run:
                result.previews.append({"action": "update" if existing else "create", "client": name, "lob": lob, "stage": stage})
                if existing:
                    result.updated += 1
                else:
                    result.created += 1
                continue
            if existing:
                # Once a deal is being worked in the CRM (sync_source='crm'), the
                # inbound AMS sync no longer overwrites it — it lives in the CRM until
                # terminal (Bound/Won or Lost), when the outbound step writes back.
                if str(existing.get("sync_source") or "") == "crm":
                    result.skipped_worked += 1
                    continue
                # Don't clobber a human-entered estimate; fill it only if blank.
                if est is not None and "premium_estimate" in cols and not existing.get("premium_estimate"):
                    payload["premium_estimate"] = est
                supa.update(opp.TABLE, existing["id"], payload)
                result.updated += 1
            else:
                base = {"client_identifier": cid, "line_of_business": lob, "insured_name": name}
                if est is not None:
                    base["premium_estimate"] = est
                base = {k: v for k, v in base.items() if k in cols}
                supa.insert(opp.TABLE, {**base, **payload})
                result.created += 1
        except Exception as exc:  # noqa: BLE001 — one bad opp shouldn't abort the run
            ref = payload.get("nowcerts_opportunity_id") or name
            result.errors.append(f"opportunity {ref}: {exc}")
            log.warning("opportunity sync: %s failed: %s", ref, exc)

    log.info("opportunity sync done: %s", result.message)
    return result
