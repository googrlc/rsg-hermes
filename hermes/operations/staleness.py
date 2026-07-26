"""Staleness assertions — does this pipeline still MOVE?

Every silent failure found on 2026-07-26 shared one shape: broken looked exactly
like idle. Every endpoint returned 200 the whole time.

  * The nightly KPI job had been 404ing on a table that never existed, for months.
  * ``--ops-doctor`` was permanently red, which is how a health check becomes
    furniture nobody reads.
  * All 18 tasks were open — not a backlog, an artifact of there being no update
    path in the codebase at all.
  * The opportunity writeback was attempted ONCE, failed 400 on 2026-07-20, and
    nobody knew.

A reachability check cannot see any of that. ``select 1 from t`` succeeds happily
against a pipeline that stopped feeding it in April.

So this module asserts on movement instead. Two distinct failures, because they
mean different things and need different responses:

  NEVER   the pipeline has produced nothing, ever. Usually not-wired or
          never-succeeded — the writeback's single 400 lives here.
  STALE   it produced something once but not lately. Usually a job that died,
          a credential that rotated, or a cron that got disabled.

Thresholds are deliberately generous. A check that cries wolf gets ignored, which
is the exact failure mode this module exists to fix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

VERDICT_OK = "ok"
VERDICT_STALE = "stale"
VERDICT_NEVER = "never"
VERDICT_UNKNOWN = "unknown"   # couldn't read; not the same as "not moving"


@dataclass(frozen=True)
class StalenessRule:
    """One 'has this moved lately?' assertion.

    ``never_ok`` marks a pipeline that legitimately may not have fired yet, so
    emptiness is not an alarm. ``so_what`` is required: a stale check that doesn't
    say what the silence MEANS just becomes another red light to learn to ignore.
    """
    name: str
    table: str
    column: str
    max_age_days: int
    so_what: str
    filters: dict[str, str] = field(default_factory=dict)
    never_ok: bool = False


# Each rule here exists because something actually went quiet. Keep that property:
# a rule with no real failure behind it is a guess about the future.
RULES: tuple[StalenessRule, ...] = (
    StalenessRule(
        name="commission statement ingest",
        table="commission_transactions",
        column="created_at",
        max_age_days=45,
        so_what="No carrier statement line has landed in 45 days. Either statements "
                "have stopped arriving or the ingest path broke — reconciliation is "
                "running on stale money.",
    ),
    StalenessRule(
        name="task completion",
        table="agency_crm_tasks",
        column="completed_at",
        max_age_days=30,
        so_what="No task has been closed in 30 days. Before 2026-07-26 this was "
                "permanently true because no update path existed — 18 tasks sat open "
                "and the queue looked like a backlog instead of a bug.",
    ),
    StalenessRule(
        name="opportunity writeback to NowCerts",
        table="outbound_sync_queue",
        column="updated_at",
        max_age_days=60,
        filters={"object_type": "eq.opportunity_writeback", "status": "eq.completed"},
        never_ok=False,
        so_what="No opportunity writeback has EVER completed. One was attempted "
                "2026-07-20 and failed 400 'Can't assign to Insured/Prospect'. Until "
                "one succeeds, telling anyone a Bound/Won move reaches the AMS is false.",
    ),
    StalenessRule(
        name="renewal candidate refresh",
        table="renewal_candidates",
        column="updated_at",
        max_age_days=4,
        so_what="The nightly renewal refresh (2:30am) hasn't run in 4 days. The "
                "renewals cockpit is showing a stale forward window.",
    ),
    StalenessRule(
        name="KPI snapshots",
        table="dashboard_kpis",
        column="recorded_at",
        max_age_days=3,
        so_what="The nightly 6am KPI job hasn't recorded anything in 3 days. It spent "
                "months 404ing on crm_write_queue, a table that never existed, and "
                "nothing noticed because the endpoint still answered.",
    ),
    StalenessRule(
        name="NowCerts book mirror",
        table="canonical_clients",
        column="updated_at",
        max_age_days=3,
        so_what="The nightly canonical book sync (2:20am ET) hasn't refreshed a "
                "client row in 3 days. canonical_clients / canonical_policies are "
                "the NowCerts mirror the renewals desk reads; if the refresh "
                "stopped, the book is drifting from the AMS and renewals are "
                "working off stale insured/policy data.",
    ),
)


@dataclass
class StalenessCheck:
    rule: StalenessRule
    verdict: str
    last_seen: datetime | None = None
    age_days: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        # UNKNOWN is not a pass, but it is not "stopped" either — it means the
        # check itself is broken, which is its own thing to fix.
        return self.verdict == VERDICT_OK

    @property
    def line(self) -> str:
        label = {
            VERDICT_OK: "OK", VERDICT_STALE: "STALE",
            VERDICT_NEVER: "NEVER", VERDICT_UNKNOWN: "????",
        }[self.verdict]
        if self.verdict == VERDICT_OK:
            detail = f"last {self.age_days:.1f}d ago"
        elif self.verdict == VERDICT_STALE:
            detail = f"last {self.age_days:.1f}d ago, limit {self.rule.max_age_days}d"
        elif self.verdict == VERDICT_NEVER:
            detail = "no rows, ever"
        else:
            detail = self.error or "unreadable"
        return f"  [{label}] {self.rule.name}: {detail}"


@dataclass
class StalenessReport:
    checks: list[StalenessCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def problems(self) -> list[StalenessCheck]:
        return [c for c in self.checks if not c.ok]

    def format_lines(self) -> list[str]:
        lines = ["", "Pipeline movement — is anything still flowing?", "-" * 48]
        lines.extend(c.line for c in self.checks)
        problems = self.problems
        if problems:
            lines.append("")
            lines.append("What the silence means:")
            for c in problems:
                lines.append(f"  * {c.rule.name}: {c.rule.so_what}")
        return lines


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_rule(
    supa: "SupabaseClient", rule: StalenessRule, *, now: datetime | None = None
) -> StalenessCheck:
    """Read the newest timestamp for one rule and judge it."""
    now = now or datetime.now(timezone.utc)
    params = dict(rule.filters)
    # Nulls sort first on a descending order in PostgREST, which would hand us a
    # null and read as "never" on a table that is actually moving.
    params["order"] = f"{rule.column}.desc.nullslast"
    params.setdefault(rule.column, "not.is.null")
    try:
        rows = supa.select(rule.table, columns=rule.column, params=params, limit=1)
    except Exception as exc:  # noqa: BLE001 — an unreadable check is its own verdict
        return StalenessCheck(rule, VERDICT_UNKNOWN, error=str(exc)[:200])

    last = _parse_ts(rows[0].get(rule.column)) if rows else None
    if last is None:
        return StalenessCheck(rule, VERDICT_OK if rule.never_ok else VERDICT_NEVER)

    age = (now - last).total_seconds() / 86400.0
    verdict = VERDICT_OK if age <= rule.max_age_days else VERDICT_STALE
    return StalenessCheck(rule, verdict, last_seen=last, age_days=age)


def validate_rules(
    supa: "SupabaseClient", rules: tuple[StalenessRule, ...] = RULES
) -> list[str]:
    """Confirm every rule points at a table+column that really exists.

    Authoring these, one rule named dashboard_kpis.created_at — the column is
    actually recorded_at. It degraded quietly to UNKNOWN instead of failing, which
    is exactly the phantom-reference defect this module was written to catch. A
    monitor that cannot monitor itself is decoration.
    """
    problems: list[str] = []
    for rule in rules:
        try:
            rows = supa.select(rule.table, columns=rule.column, params={}, limit=1)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{rule.name}: cannot read {rule.table}.{rule.column} ({str(exc)[:120]})")
            continue
        if rows and rule.column not in rows[0]:
            problems.append(
                f"{rule.name}: {rule.table} has no column '{rule.column}' — "
                f"this rule silently reports UNKNOWN forever"
            )
    return problems


def check_staleness(
    supa: "SupabaseClient",
    *,
    now: datetime | None = None,
    rules: tuple[StalenessRule, ...] = RULES,
) -> StalenessReport:
    """Run every movement assertion. Never raises — a broken check is a verdict."""
    now = now or datetime.now(timezone.utc)
    report = StalenessReport(checks=[check_rule(supa, r, now=now) for r in rules])
    for c in report.problems:
        log.warning("staleness: %s -> %s", c.rule.name, c.verdict)
    return report
