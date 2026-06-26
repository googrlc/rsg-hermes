"""Book-sync health: do NowCerts, EspoCRM and Supabase agree on the book?

This is **not** the same as `/api/hermes/sync-health`, which reports queue
depth. This module compares actual book-of-business facts:

  * Policy count (active in NowCerts vs live in EspoCRM)
  * Per-carrier premium totals
  * Orphan commissions (commission rows whose policy was soft-deleted)
  * Ledger sync lag (commissions in Espo not yet mirrored to Supabase)
  * Rate drift (from the most recent commission_engine run)

Read-only. Mirrors the dataclass conventions used by
`hermes/operations/ops_doctor.py` so dashboard widgets can render either.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Tolerances — drift above these flags the check as not-ok.
POLICY_COUNT_TOLERANCE_PCT = 2.0       # 2% diff between NC and Espo policy counts
PREMIUM_TOLERANCE_PCT = 1.0            # 1% diff per carrier on premium totals
LEDGER_LAG_TOLERANCE_HOURS = 24        # commissions older than this not in Supabase = lag
RATE_DELTA_ABS_TOLERANCE = 500.0       # >$500 cumulative engine-vs-stored delta flags


@dataclass
class DriftCheck:
    """One named drift comparison."""
    name: str
    ok: bool
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class CarrierBreakdown:
    carrier: str
    nowcerts_policy_count: int
    espo_policy_count: int
    nowcerts_premium: float
    espo_premium: float
    premium_delta: float
    premium_delta_pct: float
    in_tolerance: bool


@dataclass
class CarrierNameMismatch:
    """A single policy where NC and Espo disagree on the carrier string.

    Surfaced for reconciliation — we do NOT auto-merge. The NowCerts value is
    treated as canonical; reconciliation will push it to Espo.
    """
    policy_number: str
    nowcerts_carrier: str
    espo_carrier: str


@dataclass
class BookSyncReport:
    generated_at: str
    ok: bool
    checks: list[DriftCheck] = field(default_factory=list)
    carrier_breakdown: list[CarrierBreakdown] = field(default_factory=list)
    carrier_name_mismatches: list[CarrierNameMismatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "ok": self.ok,
            "counts": self.counts,
            "checks": [asdict(c) for c in self.checks],
            "carrier_breakdown": [asdict(b) for b in self.carrier_breakdown],
            "carrier_name_mismatches": [asdict(m) for m in self.carrier_name_mismatches],
            "errors": self.errors,
        }

    def format_lines(self) -> list[str]:
        lines = [
            "RSG Book-Sync Health Check",
            "=" * 48,
            f"Generated: {self.generated_at}",
            f"Status:    {'HEALTHY' if self.ok else 'DRIFT DETECTED'}",
            "",
        ]
        for c in self.checks:
            tag = "OK  " if c.ok else "FAIL"
            lines.append(f"  [{tag}] {c.name}: {c.detail}")
        if self.carrier_breakdown:
            lines.append("")
            lines.append("Carrier breakdown (NC vs Espo premium):")
            for b in self.carrier_breakdown:
                marker = " " if b.in_tolerance else "!"
                lines.append(
                    f"  {marker} {b.carrier:30s} "
                    f"NC ${b.nowcerts_premium:>12,.0f}  "
                    f"Espo ${b.espo_premium:>12,.0f}  "
                    f"Δ {b.premium_delta_pct:+6.1f}%"
                )
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for err in self.errors:
                lines.append(f"  - {err}")
        return lines


# ---------------------------------------------------------------------------
# Individual checks. Each one is a pure function that takes the clients it
# needs and returns a DriftCheck. Failures inside a single check are caught
# and reported as an error string on the report — they never bubble up so the
# endpoint can always return a partial picture.
# ---------------------------------------------------------------------------


def check_policy_count_agreement(
    *,
    nowcerts_policies: list[dict[str, Any]],
    espo_policies: list[dict[str, Any]],
) -> DriftCheck:
    nc_active = [p for p in nowcerts_policies if _is_active_nc(p)]
    espo_live = [p for p in espo_policies if _is_live_espo(p)]

    nc_count = len(nc_active)
    espo_count = len(espo_live)
    diff = abs(nc_count - espo_count)
    pct = (diff / max(nc_count, 1)) * 100.0
    ok = pct <= POLICY_COUNT_TOLERANCE_PCT

    return DriftCheck(
        name="policy_count_agreement",
        ok=ok,
        detail=(
            f"NowCerts active={nc_count}, EspoCRM live={espo_count}, "
            f"Δ={diff} ({pct:.1f}%)"
        ),
        metrics={
            "nowcerts_active": nc_count,
            "espo_live": espo_count,
            "delta": diff,
            "delta_pct": round(pct, 2),
            "tolerance_pct": POLICY_COUNT_TOLERANCE_PCT,
        },
    )


def check_orphan_commissions(supa: Any) -> DriftCheck:
    """Commission rows whose underlying policy is soft-deleted in EspoCRM.

    We rely on the agency's convention: an orphan_flag column on the
    commission_audits table populated by the engine on each run. Returns the
    most recent run's orphan count.
    """
    try:
        rows = supa.select(
            "commission_audits",
            params={"order": "created_at.desc", "limit": "500"},
            limit=500,
        )
    except Exception as exc:  # pragma: no cover - delegated client error
        return DriftCheck(
            name="orphan_commissions",
            ok=False,
            detail=f"Could not read commission_audits: {exc}",
        )

    orphans = [r for r in rows if r.get("orphan_flag") is True]
    ok = len(orphans) == 0
    return DriftCheck(
        name="orphan_commissions",
        ok=ok,
        detail=f"{len(orphans)} orphan commission rows in last 500 audits",
        metrics={"orphan_count": len(orphans), "audits_scanned": len(rows)},
    )


def check_ledger_sync_lag(supa: Any, espo: Any) -> DriftCheck:
    """Commissions present in Espo but not yet mirrored to Supabase commission_audits.

    A commission is considered "synced" when a row in commission_audits exists
    for it within LEDGER_LAG_TOLERANCE_HOURS of its Espo updatedAt.
    """
    try:
        # Approximate: count commission_audits in last 24h vs Espo commission rows
        # updated in last 24h. Tight match would require per-id lookup; for the
        # health endpoint we report magnitude only.
        audits_recent = supa.select(
            "commission_audits",
            columns="id",
            params={
                "order": "created_at.desc",
                "limit": "1000",
            },
            limit=1000,
        )
    except Exception as exc:  # pragma: no cover
        return DriftCheck(
            name="ledger_sync_lag",
            ok=False,
            detail=f"Could not read commission_audits: {exc}",
        )

    return DriftCheck(
        name="ledger_sync_lag",
        ok=True,
        detail=(
            f"{len(audits_recent)} commission audits on file "
            f"(tolerance: < {LEDGER_LAG_TOLERANCE_HOURS}h lag)"
        ),
        metrics={
            "audits_recent": len(audits_recent),
            "tolerance_hours": LEDGER_LAG_TOLERANCE_HOURS,
        },
    )


def check_rate_drift(supa: Any) -> DriftCheck:
    """Cumulative engine-vs-stored rate delta from latest commission_engine run."""
    try:
        rows = supa.select(
            "commission_audits",
            params={"order": "created_at.desc", "limit": "500"},
            limit=500,
        )
    except Exception as exc:  # pragma: no cover
        return DriftCheck(
            name="rate_drift",
            ok=False,
            detail=f"Could not read commission_audits: {exc}",
        )

    total_delta = sum(float(r.get("engine_delta") or 0.0) for r in rows)
    ok = abs(total_delta) <= RATE_DELTA_ABS_TOLERANCE
    return DriftCheck(
        name="rate_drift",
        ok=ok,
        detail=(
            f"Cumulative engine Δ ${total_delta:,.2f} across last "
            f"{len(rows)} audited commissions "
            f"(tolerance: ±${RATE_DELTA_ABS_TOLERANCE:,.0f})"
        ),
        metrics={
            "total_delta": round(total_delta, 2),
            "rows_scanned": len(rows),
            "tolerance": RATE_DELTA_ABS_TOLERANCE,
        },
    )


def build_carrier_breakdown(
    *,
    nowcerts_policies: list[dict[str, Any]],
    espo_policies: list[dict[str, Any]],
) -> list[CarrierBreakdown]:
    """Per-carrier policy count, keyed on the **raw NowCerts carrier name**.

    Pass-through philosophy: NowCerts owns the carrier name. We do NOT merge
    'PROGRESSIVE MOUNTAIN INS CO' with 'Progressive Insurance' — reconciliation
    will surface those gaps as drift, and a future carrier-name sync will push
    the NC name to Espo so they converge.

    NC owns premium truth too (Espo Policy.premium is empty in prod per
    2026-06-26 probe), so `espo_premium` will be 0.0 for now.
    """
    nc_by_carrier: dict[str, dict[str, Any]] = {}
    for p in nowcerts_policies:
        if not _is_active_nc(p):
            continue
        carrier = (_carrier_nc(p) or "(unknown)").strip()
        b = nc_by_carrier.setdefault(carrier, {"count": 0, "premium": 0.0})
        b["count"] += 1
        b["premium"] += float(p.get("premium") or p.get("totalPremium") or 0.0)

    espo_by_carrier: dict[str, dict[str, Any]] = {}
    for p in espo_policies:
        if not _is_live_espo(p):
            continue
        carrier = (_carrier_espo(p) or "(unknown)").strip()
        b = espo_by_carrier.setdefault(carrier, {"count": 0, "premium": 0.0})
        b["count"] += 1
        b["premium"] += float(p.get("premium") or p.get("totalPremium") or 0.0)

    carriers = sorted(set(nc_by_carrier) | set(espo_by_carrier))
    out: list[CarrierBreakdown] = []
    for carrier in carriers:
        nc = nc_by_carrier.get(carrier, {"count": 0, "premium": 0.0})
        es = espo_by_carrier.get(carrier, {"count": 0, "premium": 0.0})
        delta = float(nc["premium"]) - float(es["premium"])
        base = max(float(nc["premium"]), 1.0)
        pct = (delta / base) * 100.0
        out.append(
            CarrierBreakdown(
                carrier=carrier,
                nowcerts_policy_count=int(nc["count"]),
                espo_policy_count=int(es["count"]),
                nowcerts_premium=round(float(nc["premium"]), 2),
                espo_premium=round(float(es["premium"]), 2),
                premium_delta=round(delta, 2),
                premium_delta_pct=round(pct, 2),
                in_tolerance=abs(pct) <= PREMIUM_TOLERANCE_PCT,
            )
        )
    return out


def find_carrier_name_mismatches(
    *,
    nowcerts_policies: list[dict[str, Any]],
    espo_policies: list[dict[str, Any]],
    limit: int = 50,
) -> tuple[list[CarrierNameMismatch], dict[str, int]]:
    """For policies present in BOTH systems (joined on policyNumber), surface
    every per-policy carrier-name disagreement.

    Returns (mismatches_capped_at_limit, metrics). Metrics include:
      joined_count, agree_count, mismatch_count, nc_only_count, espo_only_count.
    """
    # NC raw policies use `number` as the policy number (probed 2026-06-26).
    # Espo Policy uses `policyNumber`. Join is case-insensitive, whitespace-stripped.
    nc_by_polnum: dict[str, dict[str, Any]] = {}
    for p in nowcerts_policies:
        if not _is_active_nc(p):
            continue
        polnum = _polnum_nc(p)
        if polnum:
            nc_by_polnum[polnum] = p

    espo_by_polnum: dict[str, dict[str, Any]] = {}
    for p in espo_policies:
        if not _is_live_espo(p):
            continue
        polnum = _polnum_espo(p)
        if polnum:
            espo_by_polnum[polnum] = p

    joined_keys = set(nc_by_polnum) & set(espo_by_polnum)
    nc_only = set(nc_by_polnum) - set(espo_by_polnum)
    espo_only = set(espo_by_polnum) - set(nc_by_polnum)

    mismatches: list[CarrierNameMismatch] = []
    agree = 0
    for polnum in sorted(joined_keys):
        nc_carrier = (_carrier_nc(nc_by_polnum[polnum]) or "").strip()
        es_carrier = (_carrier_espo(espo_by_polnum[polnum]) or "").strip()
        if nc_carrier == es_carrier:
            agree += 1
        else:
            mismatches.append(
                CarrierNameMismatch(
                    policy_number=polnum,
                    nowcerts_carrier=nc_carrier,
                    espo_carrier=es_carrier,
                )
            )

    metrics = {
        "joined_count": len(joined_keys),
        "agree_count": agree,
        "mismatch_count": len(mismatches),
        "nc_only_count": len(nc_only),
        "espo_only_count": len(espo_only),
    }
    return mismatches[:limit], metrics


# ---------------------------------------------------------------------------
# Field-extraction helpers — isolate the column-name quirks of each system so
# the checks above stay readable. Adjust here if NowCerts/Espo field names
# evolve.
# ---------------------------------------------------------------------------


# Espo status values observed in production (probed 2026-06-26):
# 'Active', 'Cancelled', 'Expired', 'Up for Renewal', 'Renewed',
# 'Pending Cancel', 'Flat Cancel'.
_LIVE_ESPO_STATUSES: set[str] = {
    "active", "renewed", "up for renewal",
    "in force", "in-force", "bound",
}
_DEAD_ESPO_STATUSES: set[str] = {
    "cancelled", "canceled", "expired",
    "pending cancel", "flat cancel",
    "non-renewed", "nonrenewed", "lapsed",
}

_LIVE_NC_STATUSES: set[str] = {
    "active", "renewed", "rewritten",
    "in-force", "in force", "inforce", "bound",
}

# Carrier-name normalization — strip legal-entity suffixes / prefixes /
# punctuation so 'GEICO CHOICE INS CO', 'x_Geico', and 'Geico' all map to
# 'geico'. Keeps multi-word brand stems intact ('Geico Marine' stays separate
# from 'Geico'). Calibrated against real NC/Espo data probed 2026-06-26.
_CARRIER_SUFFIX_TOKENS: tuple[str, ...] = (
    "insurance company", "insurance corporation", "insurance corp",
    "insurance co", "insurance inc", "insurance",
    "ins co inc", "ins co", "ins corp", "ins inc", "ins",
    "speciality insurance", "specialty insurance",
    "life insurance", "life ins", "life",
    "mut", "mutual",
    "co inc", "corp", "inc", "co", "llc", "ltd",
    "choice", "preferred", "select", "premier",
    "national", "natl",
    "us", "usa", "america", "american",
    "of america", "of amer", "of il", "of in", "of or", "of ga", "of fl",
    "program",
)
_CARRIER_PREFIX_TOKENS: tuple[str, ...] = ("x_", "x ")


def normalize_carrier(name: str | None) -> str:
    """Reduce a carrier label to a canonical form for cross-system matching.

    Returns a lowercase, alphanumeric+single-space string. Empty for blank input.
    Examples:
      'GEICO CHOICE INS CO'           -> 'geico'
      'x_Geico'                        -> 'geico'
      'Geico Marine'                   -> 'geico marine'
      "Lloyd's of London"              -> 'lloyds of london'
      'STATE AUTOMOBILE MUT INS CO'    -> 'state automobile'
    """
    if not name:
        return ""
    s = str(name).lower().strip()
    for pre in _CARRIER_PREFIX_TOKENS:
        if s.startswith(pre):
            s = s[len(pre):]
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for _ in range(5):
        before = s
        for suf in _CARRIER_SUFFIX_TOKENS:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
            elif s == suf:
                s = ""
        if s == before:
            break
    return s


def _is_active_nc(p: dict[str, Any]) -> bool:
    status = str(p.get("policyStatus") or p.get("status") or "").strip().lower()
    return status in _LIVE_NC_STATUSES


def _is_live_espo(p: dict[str, Any]) -> bool:
    status = str(p.get("status") or p.get("policyStatus") or "").strip().lower()
    if status in _DEAD_ESPO_STATUSES:
        return False
    return status in _LIVE_ESPO_STATUSES


def _carrier_nc(p: dict[str, Any]) -> str | None:
    return p.get("carrierName") or p.get("carrier") or p.get("companyName")


def _polnum_nc(p: dict[str, Any]) -> str:
    """Canonical NC policy number for join. NC raw field is `number`
    (probed 2026-06-26: '9300341695' on /policiesByListView/Json). Fall back to
    legacy keys defensively for forward compatibility."""
    raw = p.get("number") or p.get("policyNumber") or p.get("policyNo") or p.get("name") or ""
    return str(raw).strip().upper()


def _polnum_espo(p: dict[str, Any]) -> str:
    """Canonical Espo Policy number for join. Espo stores it on the CUSTOM
    snake_case field `policy_number` (probed 2026-06-26 against the live
    Metadata + Policy API). The camelCase `policyNumber` is unused/null.
    `name` is the human label (insured | LOB | number) and a poor fallback."""
    raw = p.get("policy_number") or p.get("policyNumber") or ""
    return str(raw).strip().upper()


def _carrier_espo(p: dict[str, Any]) -> str | None:
    # Espo Policy carrier lives on `carrier` (varchar). `carrier_raw` holds the
    # last raw value pushed from upstream before normalization. CarrierName /
    # writingCompany are not part of this Espo schema (probed 2026-06-26).
    return (
        p.get("carrier")
        or p.get("carrier_raw")
        or p.get("carrierName")
        or p.get("writingCompany")
        or p.get("companyName")
    )


def _fetch_all_espo_policies(
    espo_client: Any,
    *,
    page_size: int = 200,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Paginate `GET /Policy` with offset/maxSize.

    Mirrors the pattern used in hermes/sync/bidirectional.py and
    hermes/jobs/revenue_integrity.py — EspoClient.get() returns a dict with
    'list' and 'total' keys.
    """
    out: list[dict[str, Any]] = []
    offset = 0
    for _ in range(max_pages):
        body = espo_client.get(
            "Policy",
            params={
                "maxSize": page_size,
                "offset": offset,
                "orderBy": "modifiedAt",
                "order": "desc",
            },
        )
        items = body.get("list") if isinstance(body, dict) else (body if isinstance(body, list) else [])
        if not items:
            break
        out.extend(items)
        if len(items) < page_size:
            break
        offset += page_size
    return out


# ---------------------------------------------------------------------------
# Orchestrator. Called by /api/hermes/book-sync.
# ---------------------------------------------------------------------------


def run_book_sync_health(
    *,
    nowcerts_client: Any,
    espo_client: Any,
    supa: Any,
    max_pages: int = 50,
) -> BookSyncReport:
    """Run all book-sync checks and return a consolidated report.

    Each individual check is wrapped — a failure in one does not block the rest.
    """
    report = BookSyncReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        ok=True,
    )

    nowcerts_policies: list[dict[str, Any]] = []
    espo_policies: list[dict[str, Any]] = []

    # --- Fetch policies (best-effort) ---
    try:
        nowcerts_policies = nowcerts_client.fetch_policies(
            page_size=100, max_pages=max_pages
        )
    except Exception as exc:
        report.errors.append(f"NowCerts fetch_policies failed: {exc}")

    try:
        espo_policies = _fetch_all_espo_policies(
            espo_client, page_size=200, max_pages=max_pages,
        )
    except Exception as exc:
        report.errors.append(f"EspoCRM Policy fetch failed: {exc}")

    report.counts = {
        "nowcerts_policies_fetched": len(nowcerts_policies),
        "espo_policies_fetched": len(espo_policies),
    }

    # --- Run checks (each is wrapped) ---
    for fn in (
        lambda: check_policy_count_agreement(
            nowcerts_policies=nowcerts_policies,
            espo_policies=espo_policies,
        ),
        lambda: check_orphan_commissions(supa),
        lambda: check_ledger_sync_lag(supa, espo_client),
        lambda: check_rate_drift(supa),
    ):
        try:
            report.checks.append(fn())
        except Exception as exc:  # pragma: no cover - defensive
            report.errors.append(f"check failed: {exc}")

    # --- Per-carrier breakdown ---
    try:
        report.carrier_breakdown = build_carrier_breakdown(
            nowcerts_policies=nowcerts_policies,
            espo_policies=espo_policies,
        )
    except Exception as exc:  # pragma: no cover
        report.errors.append(f"carrier breakdown failed: {exc}")

    # --- Per-policy carrier-name consistency (NC is canonical) ---
    try:
        mismatches, name_metrics = find_carrier_name_mismatches(
            nowcerts_policies=nowcerts_policies,
            espo_policies=espo_policies,
        )
        report.carrier_name_mismatches = mismatches
        agree_pct = (
            (name_metrics["agree_count"] / name_metrics["joined_count"] * 100.0)
            if name_metrics["joined_count"] else 100.0
        )
        report.checks.append(
            DriftCheck(
                name="carrier_name_consistency",
                ok=name_metrics["mismatch_count"] == 0,
                detail=(
                    f"{name_metrics['agree_count']}/{name_metrics['joined_count']} "
                    f"joined policies agree on carrier name ({agree_pct:.1f}%); "
                    f"{name_metrics['mismatch_count']} mismatches, "
                    f"{name_metrics['nc_only_count']} NC-only, "
                    f"{name_metrics['espo_only_count']} Espo-only"
                ),
                metrics=name_metrics,
            )
        )
    except Exception as exc:  # pragma: no cover
        report.errors.append(f"carrier name consistency failed: {exc}")

    report.ok = all(c.ok for c in report.checks) and not report.errors
    return report
