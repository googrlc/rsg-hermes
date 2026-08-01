"""Agency snapshot writer — compute the book and retention from live data.

``agency_snapshots`` held exactly ONE row until 2026-07-26: a hand-typed baseline
dated 2026-03-31 (retention 54.92%). Nothing ever wrote a second one, so every
"how's the book?" answer quoted a stale manual number and no trend existed. This
job computes the snapshot instead of typing it.

Reads go through ``hermes_core.book.select_policies``, so with
``HERMES_AMS_LIVE_READS`` set the numbers come from NowCerts directly rather than
the ``canonical_policies`` mirror. That matters: the mirror is wrong in both
directions (see ``notes`` below), and the AMS is the system of record.

RETENTION. Trailing-12-month, computed from policy lineage — not from a status
column alone, because ``status='Renewed'`` is only set on 64 of the 148 terms that
demonstrably renewed. A term counts as retained when ANY of:

  * ``status`` is ``Renewed``; or
  * another row points back at it (``renewed_policy == policy_number``) and starts
    on/around when it ended; or
  * the same ``policy_number`` has a later term starting on/around when it ended
    (renewals frequently keep the number — 135 of 294 policy numbers carry
    multiple terms).

Reported two ways, because they answer different questions: **logo** retention
(did we keep the policy) and **premium-weighted** retention (did we keep the
money). Premium-weighted is the one that maps to the agency's revenue goal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_integrations.supabase_client import SupabaseClient
    from hermes_integrations.nowcerts_client import NowCertsClient

log = logging.getLogger(__name__)

SNAPSHOTS_TABLE = "agency_snapshots"
CLIENTS_TABLE = "canonical_clients"
OPPORTUNITIES_TABLE = "opportunities"

# Trailing window for retention, and the slack allowed between a term ending and
# its successor starting (carriers backdate and late-bind constantly).
RETENTION_WINDOW_DAYS = 365
SUCCESSOR_GRACE_DAYS = 45

# Single home: hermes_core.book owns the tombstone vocabulary. Two independent
# copies of this string existed, so a consumer that forgot the check counted
# phantom rows as real book.
from hermes_core.book import TOMBSTONE_PREFIX  # noqa: F401  (re-exported)

STATUS_RENEWED = "renewed"

# Premium fields in resolution order — matches dashboard.kpi_summary.
_PREMIUM_FIELDS = ("annualized_premium", "current_term_amount", "premium_amount")

# LOB buckets. The book carries free-text lines_of_business including typos
# ("personsl auto") and run-together values, so match on normalized substrings.
_PERSONAL_HINTS = (
    "personal auto", "personalauto", "personsl auto", "homeowners", "dwelling fire",
    "condo owners", "renters", "motorcycle", "personal umbrella", "personal",
)
_COMMERCIAL_AUTO_HINTS = ("commercial auto", "garage and dealers")
_WC_HINTS = ("worker", "workers comp")
_GL_BOP_HINTS = (
    "general liability", "business owners", "bop", "commercial package",
    "commercial property", "builders risk",
)

BUCKET_COMMERCIAL_AUTO = "lob_commercial_auto"
BUCKET_GL_BOP = "lob_gl_bop"
BUCKET_WORKERS_COMP = "lob_workers_comp"
BUCKET_PERSONAL = "lob_personal_lines"
BUCKET_OTHER = "lob_other"
BUCKETS = (
    BUCKET_COMMERCIAL_AUTO, BUCKET_GL_BOP, BUCKET_WORKERS_COMP,
    BUCKET_PERSONAL, BUCKET_OTHER,
)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def policy_premium(policy: dict[str, Any]) -> float:
    """Annualized premium, falling back through the term/plain premium fields."""
    for key in _PREMIUM_FIELDS:
        value = policy.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def is_tombstoned(policy: dict[str, Any]) -> bool:
    """True for the phantom 'not in NowCerts' rows the disabled importer wrote."""
    from hermes_core.book import is_tombstoned as _shared

    return _shared(policy)


def lob_bucket(lines_of_business: Any) -> str:
    """Map free-text LOB onto the five agency_snapshots premium columns."""
    text = str(lines_of_business or "").strip().lower()
    if not text:
        return BUCKET_OTHER
    # Order matters: 'commercial auto' must win before the bare 'personal' hints,
    # and workers' comp before the generic commercial buckets.
    if any(h in text for h in _COMMERCIAL_AUTO_HINTS):
        return BUCKET_COMMERCIAL_AUTO
    if any(h in text for h in _WC_HINTS):
        return BUCKET_WORKERS_COMP
    if any(h in text for h in _GL_BOP_HINTS):
        return BUCKET_GL_BOP
    if any(h in text for h in _PERSONAL_HINTS):
        return BUCKET_PERSONAL
    return BUCKET_OTHER


@dataclass
class RetentionResult:
    """Trailing-window retention, both logo and premium-weighted."""

    window_days: int
    denominator: int = 0
    retained: int = 0
    denominator_premium: float = 0.0
    retained_premium: float = 0.0
    excluded_tombstoned: int = 0

    @property
    def logo_rate(self) -> float | None:
        if not self.denominator:
            return None
        return round(100.0 * self.retained / self.denominator, 2)

    @property
    def premium_rate(self) -> float | None:
        if not self.denominator_premium:
            return None
        return round(100.0 * self.retained_premium / self.denominator_premium, 2)

    @property
    def lost(self) -> int:
        return self.denominator - self.retained

    @property
    def lost_premium(self) -> float:
        return self.denominator_premium - self.retained_premium


def _successor_index(policies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """policy_number -> rows that claim it as their predecessor."""
    index: dict[str, list[dict[str, Any]]] = {}
    for row in policies:
        pointer = str(row.get("renewed_policy") or "").strip()
        if pointer:
            index.setdefault(pointer, []).append(row)
    return index


def _by_policy_number(policies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for row in policies:
        number = str(row.get("policy_number") or "").strip()
        if number:
            index.setdefault(number, []).append(row)
    return index


def _same_row(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Identity by policy_guid, which is unique in the book."""
    ga, gb = a.get("policy_guid"), b.get("policy_guid")
    if ga and gb:
        return ga == gb
    return a is b


def _starts_after(candidate: dict[str, Any], expiration: date, grace_days: int) -> bool:
    effective = _parse_date(candidate.get("effective_date"))
    if effective is None:
        return False
    return effective >= expiration - timedelta(days=grace_days)


def was_retained(
    term: dict[str, Any],
    *,
    successors: dict[str, list[dict[str, Any]]],
    by_number: dict[str, list[dict[str, Any]]],
    grace_days: int = SUCCESSOR_GRACE_DAYS,
) -> bool:
    """Did this expiring term continue? See the module docstring for the rule."""
    if str(term.get("status") or "").strip().lower() == STATUS_RENEWED:
        return True

    expiration = _parse_date(term.get("expiration_date"))
    if expiration is None:
        return False
    number = str(term.get("policy_number") or "").strip()
    if not number:
        return False

    for candidate in successors.get(number, ()):
        if _same_row(candidate, term):
            continue
        if _starts_after(candidate, expiration, grace_days):
            return True

    for candidate in by_number.get(number, ()):
        if _same_row(candidate, term):
            continue
        if _starts_after(candidate, expiration, grace_days):
            return True

    return False


def compute_retention(
    policies: list[dict[str, Any]],
    *,
    today: date,
    window_days: int = RETENTION_WINDOW_DAYS,
    grace_days: int = SUCCESSOR_GRACE_DAYS,
) -> RetentionResult:
    """Trailing-window retention over terms that already reached their x-date.

    Pure. Tombstoned rows are excluded from the denominator and counted
    separately — grading a policy the importer wrongly deleted as "lost" would
    manufacture churn that never happened.
    """
    result = RetentionResult(window_days=window_days)
    window_start = today - timedelta(days=window_days)

    successors = _successor_index(policies)
    by_number = _by_policy_number(policies)

    for row in policies:
        expiration = _parse_date(row.get("expiration_date"))
        if expiration is None or not (window_start <= expiration < today):
            continue
        if is_tombstoned(row):
            result.excluded_tombstoned += 1
            continue

        premium = policy_premium(row)
        result.denominator += 1
        result.denominator_premium += premium
        if was_retained(row, successors=successors, by_number=by_number, grace_days=grace_days):
            result.retained += 1
            result.retained_premium += premium

    return result


@dataclass
class BookResult:
    """Point-in-time size of the active book."""

    active_premium: float = 0.0
    active_policy_count: int = 0
    total_policy_count: int = 0
    client_count: int = 0
    tombstoned_count: int = 0
    tombstoned_premium: float = 0.0
    lob_premium: dict[str, float] = field(default_factory=lambda: {b: 0.0 for b in BUCKETS})


def compute_book(policies: list[dict[str, Any]], *, client_count: int = 0) -> BookResult:
    """Active premium, counts, and the per-LOB split. Pure."""
    result = BookResult(client_count=client_count, total_policy_count=len(policies))
    for row in policies:
        if is_tombstoned(row):
            result.tombstoned_count += 1
            result.tombstoned_premium += policy_premium(row)
            continue
        if not row.get("active"):
            continue
        premium = policy_premium(row)
        result.active_policy_count += 1
        result.active_premium += premium
        result.lob_premium[lob_bucket(row.get("lines_of_business"))] += premium
    return result


def _delta(current: float | None, prior: Any) -> float | None:
    if current is None or prior in (None, ""):
        return None
    try:
        return round(current - float(prior), 2)
    except (TypeError, ValueError):
        return None


def build_snapshot(
    policies: list[dict[str, Any]],
    *,
    today: date,
    client_count: int = 0,
    pipeline_value: float | None = None,
    pipeline_count: int | None = None,
    prior: dict[str, Any] | None = None,
    live_reads: bool = False,
    source: str = "auto",
) -> dict[str, Any]:
    """Assemble an ``agency_snapshots`` row. Pure — no I/O, no writes."""
    book = compute_book(policies, client_count=client_count)
    retention = compute_retention(policies, today=today)

    # Premium-weighted retention is the headline: it maps to the revenue goal.
    # Logo retention rides along in the notes so the two are never conflated.
    headline = retention.premium_rate

    notes_parts = [
        f"auto snapshot · source={'live AMS' if live_reads else 'canonical_policies mirror'}",
        (
            f"retention {retention.window_days}d: premium {retention.premium_rate}% "
            f"(${retention.retained_premium:,.0f} of ${retention.denominator_premium:,.0f}), "
            f"logo {retention.logo_rate}% ({retention.retained}/{retention.denominator})"
        ),
    ]
    if retention.excluded_tombstoned:
        notes_parts.append(
            f"{retention.excluded_tombstoned} tombstoned term(s) excluded from the "
            "retention denominator (disabled rsg-import path, not real churn)"
        )
    if book.tombstoned_count:
        notes_parts.append(
            f"{book.tombstoned_count} tombstoned row(s) (${book.tombstoned_premium:,.0f}) "
            "excluded from the book"
        )
    if not live_reads:
        notes_parts.append(
            "WARNING: computed from the mirror, which disagrees with NowCerts in both "
            "directions — set HERMES_AMS_LIVE_READS for authoritative numbers"
        )

    row: dict[str, Any] = {
        "snapshot_date": today.isoformat(),
        "active_premium": round(book.active_premium, 2),
        "policy_count": book.active_policy_count,
        "client_count": book.client_count,
        "retention_rate": headline,
        "pipeline_value": round(pipeline_value, 2) if pipeline_value is not None else None,
        "pipeline_count": pipeline_count,
        "source": source,
        "created_by": "hermes",
        "notes": " · ".join(notes_parts),
    }
    for bucket in BUCKETS:
        row[bucket] = round(book.lob_premium.get(bucket, 0.0), 2)

    if prior:
        row["delta_premium"] = _delta(row["active_premium"], prior.get("active_premium"))
        row["delta_retention"] = _delta(headline, prior.get("retention_rate"))
        prior_policies = prior.get("policy_count")
        if prior_policies not in (None, ""):
            try:
                row["delta_policies"] = row["policy_count"] - int(prior_policies)
            except (TypeError, ValueError):
                pass

    return row


def latest_snapshot(supa: "SupabaseClient") -> dict[str, Any] | None:
    rows = supa.select(
        SNAPSHOTS_TABLE, columns="*", params={"order": "snapshot_date.desc"}, limit=1
    )
    return rows[0] if rows else None


def _pipeline(supa: "SupabaseClient") -> tuple[float, int]:
    """Open pipeline value + count from the opportunities board."""
    try:
        rows = supa.select(
            OPPORTUNITIES_TABLE,
            columns="premium_estimate,status",
            params={"status": "eq.open"},
            limit=5000,
        )
    except Exception:  # noqa: BLE001 — the pipeline card must not fail the snapshot
        log.exception("agency_snapshot: pipeline read failed")
        return 0.0, 0
    total = 0.0
    for row in rows:
        try:
            total += float(row.get("premium_estimate") or 0)
        except (TypeError, ValueError):
            continue
    return total, len(rows)


def run_snapshot(
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
    today: date | None = None,
    dry_run: bool = False,
    source: str = "auto",
) -> dict[str, Any]:
    """Compute and (unless ``dry_run``) write today's snapshot.

    Idempotent per day: re-running replaces the row for ``today`` rather than
    stacking duplicates, so a retry or a manual re-run never corrupts the trend.
    """
    from hermes_core import book as ams_book

    if supa is None:
        from hermes_integrations.supabase_client import SupabaseClient

        supa = SupabaseClient()
    today = today or date.today()

    policies = ams_book.select_policies(
        supa,
        columns=(
            "policy_guid,policy_number,renewed_policy,lines_of_business,status,active,"
            "effective_date,expiration_date,annualized_premium,current_term_amount,premium_amount"
        ),
        limit=20000,
        nowcerts=nowcerts,
    )
    clients = supa.select(CLIENTS_TABLE, columns="nowcerts_insured_guid", limit=20000)
    pipeline_value, pipeline_count = _pipeline(supa)
    prior = latest_snapshot(supa)

    row = build_snapshot(
        policies,
        today=today,
        client_count=len(clients),
        pipeline_value=pipeline_value,
        pipeline_count=pipeline_count,
        prior=prior,
        live_reads=ams_book.live_reads_enabled(),
        source=source,
    )

    summary = {
        "snapshot": row,
        "written": False,
        "replaced": False,
        "policies_read": len(policies),
        "prior_snapshot_date": (prior or {}).get("snapshot_date"),
    }
    if dry_run:
        return summary

    existing = supa.select(
        SNAPSHOTS_TABLE, columns="id", params={"snapshot_date": f"eq.{today.isoformat()}"}, limit=1
    )
    if existing:
        supa.update(SNAPSHOTS_TABLE, existing[0]["id"], row)
        summary["replaced"] = True
    else:
        supa.insert(SNAPSHOTS_TABLE, row)
    summary["written"] = True
    log.info(
        "agency_snapshot %s: premium=%s policies=%s retention=%s%%",
        today, row["active_premium"], row["policy_count"], row["retention_rate"],
    )
    return summary


def format_summary(summary: dict[str, Any]) -> str:
    """One-glance text summary for the CLI."""
    row = summary["snapshot"]
    lines = [
        f"agency snapshot {row['snapshot_date']} "
        f"({'written' if summary['written'] else 'DRY RUN — not written'}"
        f"{', replaced same-day row' if summary.get('replaced') else ''})",
        f"  active premium : ${row['active_premium']:,.0f} across {row['policy_count']} policies",
        f"  clients        : {row['client_count']}",
        f"  retention      : {row['retention_rate']}% (premium-weighted, trailing 12mo)",
        f"  pipeline       : ${(row['pipeline_value'] or 0):,.0f} across {row['pipeline_count'] or 0} open",
        f"  policies read  : {summary['policies_read']}",
    ]
    if row.get("delta_premium") is not None:
        lines.append(
            f"  vs {summary.get('prior_snapshot_date')}: "
            f"premium {row['delta_premium']:+,.0f} · "
            f"policies {row.get('delta_policies', 0):+d} · "
            f"retention {row.get('delta_retention', 0):+.2f} pts"
        )
    lines.append(f"  notes          : {row['notes']}")
    return "\n".join(lines)
