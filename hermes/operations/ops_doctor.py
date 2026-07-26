"""Hermes Operations Center health check (``hermes --ops-doctor``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

# Tables the health check probes. Keep this honest: a table listed here that
# does not exist makes --ops-doctor exit 1 forever, which trains people to
# ignore a red health check. crm_write_queue and crm_receipts were listed for
# months and have never existed in this schema — they were designed for the
# Espo-era write path and never built. The real gate is outbound_sync_queue.
HERMES_TABLES = [
    "hermes_ai_roles",
    "commission_ledger",
    "commission_audits",
    "eom_scorecards",
    "project_85_renewals",
    "renewal_actions",
    "guardrail_logs",
    "reporting_schedules",
    "dashboard_kpis",
    # The live NowCerts ↔ Supabase mirror surface. The Espo-era sync control
    # tables (sync_runs / inbound_sync_staging / sync_mappings / sync_audit_log /
    # sync_errors / sync_conflicts) were built for the NowCerts↔EspoCRM pipeline
    # deleted in slice 4 (commit 7ee2787); nothing writes them anymore, so
    # listing them here was reachability theatre — green rows from July that
    # never move. outbound_sync_queue is the one survivor (the executor queue).
    "outbound_sync_queue",
    "canonical_clients",
    "canonical_policies",
]


@dataclass
class OpsCheckResult:
    table: str
    ok: bool
    row_count: int
    error: str | None = None


@dataclass
class OpsDoctorReport:
    checks: list[OpsCheckResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Reachability alone said HEALTHY while the 6am KPI job 404'd for months and
    # the writeback had never once succeeded. Movement is the other half.
    staleness: Any = None

    @property
    def ok(self) -> bool:
        base = all(c.ok for c in self.checks) and not self.errors
        return base and (self.staleness.ok if self.staleness is not None else True)

    def format_lines(self) -> list[str]:
        lines = ["Hermes Operations Center — Health Check", "=" * 48]
        lines.append("Tables reachable:")
        for c in self.checks:
            status = "OK" if c.ok else "FAIL"
            detail = f" ({c.error})" if c.error else ""
            lines.append(f"  [{status}] {c.table}: {c.row_count} rows{detail}")
        if self.staleness is not None:
            lines.extend(self.staleness.format_lines())
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for err in self.errors:
                lines.append(f"  - {err}")
        lines.append("")
        lines.append(f"Overall: {'HEALTHY' if self.ok else 'ISSUES FOUND'}")
        return lines


def run_ops_doctor(supa: SupabaseClient, *, check_movement: bool = True) -> OpsDoctorReport:
    """Verify Supabase connectivity, row counts, AND that pipelines still move.

    ``check_movement=False`` gives the old reachability-only behaviour, which is
    only useful when you already know a pipeline is down and want to confirm the
    database itself is fine.
    """
    report = OpsDoctorReport()

    for table in HERMES_TABLES:
        try:
            count_rows = supa.select(table, columns="id", limit=1000)
            report.checks.append(
                OpsCheckResult(table=table, ok=True, row_count=len(count_rows))
            )
        except SupabaseClientError as exc:
            report.checks.append(
                OpsCheckResult(table=table, ok=False, row_count=0, error=str(exc))
            )
            report.errors.append(f"{table}: {exc}")

    roles = _check_roles(supa)
    if roles:
        report.errors.extend(roles)

    if check_movement:
        from hermes.operations.staleness import check_staleness, validate_rules

        # A monitor that cannot monitor itself is decoration: a rule naming a
        # column that does not exist reports UNKNOWN forever and looks like a
        # gap in the data rather than a bug in the check.
        for problem in validate_rules(supa):
            report.errors.append(f"staleness rule misconfigured — {problem}")
        report.staleness = check_staleness(supa)

    return report


def _check_roles(supa: SupabaseClient) -> list[str]:
    """Verify expected Hermes roles exist."""
    errors: list[str] = []
    expected = {"HermesCommissionAuditor", "HermesRenewalSpecialist", "HermesFinanceOps", "HermesOpsRouter"}
    try:
        rows = supa.select("hermes_ai_roles", columns="role_name", limit=50)
        found = {r.get("role_name") for r in rows}
        missing = expected - found
        if missing:
            errors.append(f"Missing AI roles: {', '.join(sorted(missing))}")
    except SupabaseClientError as exc:
        errors.append(f"Could not check AI roles: {exc}")
    return errors


