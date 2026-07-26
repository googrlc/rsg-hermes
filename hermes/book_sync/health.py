"""Book-sync health: do NowCerts and the canonical book agree?

This is **not** the same as `/api/hermes/sync-health`, which reports queue
depth. This module compares actual book-of-business facts between the AMS
(NowCerts, the system of record) and the Supabase mirror the cockpit reads
(`canonical_policies`):

  * Policy count (active in NowCerts vs active in canonical_policies)
  * Tombstoned policies — active in NowCerts but missing or marked inactive in
    the mirror. This is the check that catches a second writer clearing rows it
    could not see; see the 2026-07-24 canonical-book incident.
  * Per-carrier premium totals
  * Carrier-name agreement per policy (NowCerts is canonical)
  * Orphan commissions (commission rows whose policy was soft-deleted)
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

CANONICAL_POLICIES_TABLE = "canonical_policies"

# Tolerances — drift above these flags the check as not-ok.
POLICY_COUNT_TOLERANCE_PCT = 2.0       # 2% diff between NC and canonical counts
PREMIUM_TOLERANCE_PCT = 1.0            # 1% diff per carrier on premium totals
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
    canonical_policy_count: int
    nowcerts_premium: float
    canonical_premium: float
    premium_delta: float
    premium_delta_pct: float
    in_tolerance: bool


@dataclass
class CarrierNameMismatch:
    """A single policy where NowCerts and the canonical book disagree.

    Surfaced for reconciliation — we do NOT auto-merge. The NowCerts value is
    treated as canonical; reconciliation pushes it to the mirror.
    """
    policy_number: str
    nowcerts_carrier: str
    canonical_carrier: str


@dataclass
class BookSyncReport:
    generated_at: str
    ok: bool
    checks: list[DriftCheck] = field(default_factory=list)
    carrier_breakdown: list[CarrierBreakdown] = field(default_factory=list)
    carrier_name_mismatches: list[CarrierNameMismatch] = field(default_factory=list)
    tombstoned_policies: list[str] = field(default_factory=list)
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
            "tombstoned_policies": self.tombstoned_policies,
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
            lines.append("Carrier breakdown (NowCerts vs canonical premium):")
            for b in self.carrier_breakdown:
                marker = " " if b.in_tolerance else "!"
                lines.append(
                    f"  {marker} {b.carrier:30s} "
                    f"NC ${b.nowcerts_premium:>12,.0f}  "
                    f"Book ${b.canonical_premium:>12,.0f}  "
                    f"Δ {b.premium_delta_pct:+6.1f}%"
                )
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for err in self.errors:
                lines.append(f"  - {err}")
        return lines


# ---------------------------------------------------------------------------
# Individual checks. Each one is a pure function that takes the rows it needs
# and returns a DriftCheck. Failures inside a single check are caught and
# reported as an error string on the report — they never bubble up so the
# endpoint can always return a partial picture.
# ---------------------------------------------------------------------------


def check_policy_count_agreement(
    *,
    nowcerts_policies: list[dict[str, Any]],
    canonical_policies: list[dict[str, Any]],
) -> DriftCheck:
    nc_active = [p for p in nowcerts_policies if _is_active_nc(p)]
    book_active = [p for p in canonical_policies if _is_active_canonical(p)]

    nc_count = len(nc_active)
    book_count = len(book_active)
    diff = abs(nc_count - book_count)
    pct = (diff / max(nc_count, 1)) * 100.0
    ok = pct <= POLICY_COUNT_TOLERANCE_PCT

    return DriftCheck(
        name="policy_count_agreement",
        ok=ok,
        detail=(
            f"NowCerts active={nc_count}, canonical active={book_count}, "
            f"Δ={diff} ({pct:.1f}%)"
        ),
        metrics={
            "nowcerts_active": nc_count,
            "canonical_active": book_count,
            "delta": diff,
            "delta_pct": round(pct, 2),
            "tolerance_pct": POLICY_COUNT_TOLERANCE_PCT,
        },
    )


def find_tombstoned_policies(
    *,
    nowcerts_policies: list[dict[str, Any]],
    canonical_policies: list[dict[str, Any]],
    limit: int = 50,
) -> tuple[list[str], dict[str, int]]:
    """Policies active in NowCerts that the mirror has lost or marked inactive.

    A second writer that pulls a narrower slice of NowCerts than we do will
    tombstone everything it cannot see. That failure is invisible in a count
    comparison when creates and tombstones roughly cancel out, so it gets its
    own check. Returns (policy_numbers_capped_at_limit, metrics).
    """
    book_by_polnum = {
        polnum: p
        for p in canonical_policies
        if (polnum := _polnum_canonical(p))
    }

    missing: list[str] = []
    inactive: list[str] = []
    for p in nowcerts_policies:
        if not _is_active_nc(p):
            continue
        polnum = _polnum_nc(p)
        if not polnum:
            continue
        row = book_by_polnum.get(polnum)
        if row is None:
            missing.append(polnum)
        elif not _is_active_canonical(row):
            inactive.append(polnum)

    affected = sorted(missing + inactive)
    metrics = {
        "missing_count": len(missing),
        "inactive_count": len(inactive),
        "affected_count": len(affected),
    }
    return affected[:limit], metrics


def check_orphan_commissions(supa: Any) -> DriftCheck:
    """Commission rows whose underlying policy is soft-deleted.

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
    canonical_policies: list[dict[str, Any]],
) -> list[CarrierBreakdown]:
    """Per-carrier policy count and premium, keyed on the **raw NowCerts name**.

    Pass-through philosophy: NowCerts owns the carrier name. We do NOT merge
    'PROGRESSIVE MOUNTAIN INS CO' with 'Progressive Insurance' — reconciliation
    surfaces those gaps as drift instead.
    """
    nc_by_carrier: dict[str, dict[str, Any]] = {}
    for p in nowcerts_policies:
        if not _is_active_nc(p):
            continue
        carrier = (_carrier_nc(p) or "(unknown)").strip()
        b = nc_by_carrier.setdefault(carrier, {"count": 0, "premium": 0.0})
        b["count"] += 1
        b["premium"] += float(p.get("premium") or p.get("totalPremium") or 0.0)

    book_by_carrier: dict[str, dict[str, Any]] = {}
    for p in canonical_policies:
        if not _is_active_canonical(p):
            continue
        carrier = (_carrier_canonical(p) or "(unknown)").strip()
        b = book_by_carrier.setdefault(carrier, {"count": 0, "premium": 0.0})
        b["count"] += 1
        b["premium"] += float(p.get("premium_amount") or p.get("current_term_amount") or 0.0)

    carriers = sorted(set(nc_by_carrier) | set(book_by_carrier))
    out: list[CarrierBreakdown] = []
    for carrier in carriers:
        nc = nc_by_carrier.get(carrier, {"count": 0, "premium": 0.0})
        book = book_by_carrier.get(carrier, {"count": 0, "premium": 0.0})
        delta = float(nc["premium"]) - float(book["premium"])
        base = max(float(nc["premium"]), 1.0)
        pct = (delta / base) * 100.0
        out.append(
            CarrierBreakdown(
                carrier=carrier,
                nowcerts_policy_count=int(nc["count"]),
                canonical_policy_count=int(book["count"]),
                nowcerts_premium=round(float(nc["premium"]), 2),
                canonical_premium=round(float(book["premium"]), 2),
                premium_delta=round(delta, 2),
                premium_delta_pct=round(pct, 2),
                in_tolerance=abs(pct) <= PREMIUM_TOLERANCE_PCT,
            )
        )
    return out


def find_carrier_name_mismatches(
    *,
    nowcerts_policies: list[dict[str, Any]],
    canonical_policies: list[dict[str, Any]],
    limit: int = 50,
) -> tuple[list[CarrierNameMismatch], dict[str, int]]:
    """For policies present in BOTH systems (joined on policy number), surface
    every per-policy carrier-name disagreement.

    Returns (mismatches_capped_at_limit, metrics). Metrics include:
      joined_count, agree_count, mismatch_count, nc_only_count, book_only_count.
    """
    nc_by_polnum: dict[str, dict[str, Any]] = {}
    for p in nowcerts_policies:
        if not _is_active_nc(p):
            continue
        polnum = _polnum_nc(p)
        if polnum:
            nc_by_polnum[polnum] = p

    book_by_polnum: dict[str, dict[str, Any]] = {}
    for p in canonical_policies:
        if not _is_active_canonical(p):
            continue
        polnum = _polnum_canonical(p)
        if polnum:
            book_by_polnum[polnum] = p

    joined_keys = set(nc_by_polnum) & set(book_by_polnum)
    nc_only = set(nc_by_polnum) - set(book_by_polnum)
    book_only = set(book_by_polnum) - set(nc_by_polnum)

    mismatches: list[CarrierNameMismatch] = []
    agree = 0
    for polnum in sorted(joined_keys):
        nc_carrier = (_carrier_nc(nc_by_polnum[polnum]) or "").strip()
        book_carrier = (_carrier_canonical(book_by_polnum[polnum]) or "").strip()
        if nc_carrier == book_carrier:
            agree += 1
        else:
            mismatches.append(
                CarrierNameMismatch(
                    policy_number=polnum,
                    nowcerts_carrier=nc_carrier,
                    canonical_carrier=book_carrier,
                )
            )

    metrics = {
        "joined_count": len(joined_keys),
        "agree_count": agree,
        "mismatch_count": len(mismatches),
        "nc_only_count": len(nc_only),
        "book_only_count": len(book_only),
    }
    return mismatches[:limit], metrics


# ---------------------------------------------------------------------------
# Field-extraction helpers — isolate the column-name quirks of each system so
# the checks above stay readable.
# ---------------------------------------------------------------------------


_LIVE_NC_STATUSES: set[str] = {
    "active", "renewed", "rewritten",
    "in-force", "in force", "inforce", "bound",
}

# Carrier-name normalization — strip legal-entity suffixes / prefixes /
# punctuation so 'GEICO CHOICE INS CO', 'x_Geico', and 'Geico' all map to
# 'geico'. Keeps multi-word brand stems intact ('Geico Marine' stays separate
# from 'Geico'). Calibrated against real NowCerts data probed 2026-06-26.
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


def _is_active_canonical(p: dict[str, Any]) -> bool:
    """canonical_policies carries an explicit boolean; fall back to status text."""
    active = p.get("active")
    if active is not None:
        return bool(active)
    status = str(p.get("status") or "").strip().lower()
    return status in _LIVE_NC_STATUSES


def _carrier_nc(p: dict[str, Any]) -> str | None:
    return p.get("carrierName") or p.get("carrier") or p.get("companyName")


def _carrier_canonical(p: dict[str, Any]) -> str | None:
    return p.get("carrier")


def _polnum_nc(p: dict[str, Any]) -> str:
    """Canonical NowCerts policy number for join. The raw field is `number`
    (probed 2026-06-26: '9300341695' on /policiesByListView/Json). Fall back to
    legacy keys defensively for forward compatibility."""
    raw = p.get("number") or p.get("policyNumber") or p.get("policyNo") or p.get("name") or ""
    return str(raw).strip().upper()


def _polnum_canonical(p: dict[str, Any]) -> str:
    return str(p.get("policy_number") or "").strip().upper()


def _fetch_canonical_policies(
    supa: Any,
    *,
    page_size: int = 1000,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Page through canonical_policies via PostgREST offset/limit."""
    out: list[dict[str, Any]] = []
    for page in range(max_pages):
        rows = supa.select(
            CANONICAL_POLICIES_TABLE,
            columns="policy_guid,policy_number,carrier,status,active,premium_amount,current_term_amount",
            params={"order": "policy_guid.asc", "offset": str(page * page_size)},
            limit=page_size,
        )
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
    return out


# ---------------------------------------------------------------------------
# Orchestrator. Called by /api/hermes/book-sync.
# ---------------------------------------------------------------------------


def run_book_sync_health(
    *,
    nowcerts_client: Any,
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
    canonical_policies: list[dict[str, Any]] = []

    # --- Fetch policies (best-effort) ---
    try:
        nowcerts_policies = nowcerts_client.fetch_policies(
            page_size=100, max_pages=max_pages
        )
    except Exception as exc:
        report.errors.append(f"NowCerts fetch_policies failed: {exc}")

    try:
        canonical_policies = _fetch_canonical_policies(supa, max_pages=max_pages)
    except Exception as exc:
        report.errors.append(f"canonical_policies fetch failed: {exc}")

    report.counts = {
        "nowcerts_policies_fetched": len(nowcerts_policies),
        "canonical_policies_fetched": len(canonical_policies),
    }

    # --- Run checks (each is wrapped) ---
    for fn in (
        lambda: check_policy_count_agreement(
            nowcerts_policies=nowcerts_policies,
            canonical_policies=canonical_policies,
        ),
        lambda: check_orphan_commissions(supa),
        lambda: check_rate_drift(supa),
    ):
        try:
            report.checks.append(fn())
        except Exception as exc:  # pragma: no cover - defensive
            report.errors.append(f"check failed: {exc}")

    # --- Tombstoned policies (the two-writer detector) ---
    try:
        affected, tomb_metrics = find_tombstoned_policies(
            nowcerts_policies=nowcerts_policies,
            canonical_policies=canonical_policies,
        )
        report.tombstoned_policies = affected
        report.checks.append(
            DriftCheck(
                name="canonical_tombstones",
                ok=tomb_metrics["affected_count"] == 0,
                detail=(
                    f"{tomb_metrics['affected_count']} policies active in NowCerts "
                    f"are not live in the canonical book "
                    f"({tomb_metrics['missing_count']} missing, "
                    f"{tomb_metrics['inactive_count']} marked inactive)"
                ),
                metrics=tomb_metrics,
            )
        )
    except Exception as exc:  # pragma: no cover
        report.errors.append(f"tombstone check failed: {exc}")

    # --- Per-carrier breakdown ---
    try:
        report.carrier_breakdown = build_carrier_breakdown(
            nowcerts_policies=nowcerts_policies,
            canonical_policies=canonical_policies,
        )
    except Exception as exc:  # pragma: no cover
        report.errors.append(f"carrier breakdown failed: {exc}")

    # --- Per-policy carrier-name consistency (NowCerts is canonical) ---
    try:
        mismatches, name_metrics = find_carrier_name_mismatches(
            nowcerts_policies=nowcerts_policies,
            canonical_policies=canonical_policies,
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
                    f"{name_metrics['nc_only_count']} NowCerts-only, "
                    f"{name_metrics['book_only_count']} book-only"
                ),
                metrics=name_metrics,
            )
        )
    except Exception as exc:  # pragma: no cover
        report.errors.append(f"carrier name consistency failed: {exc}")

    report.ok = all(c.ok for c in report.checks) and not report.errors
    return report
