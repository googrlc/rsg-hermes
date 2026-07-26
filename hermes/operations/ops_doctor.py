"""Hermes Operations Center health check (``hermes --ops-doctor``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)

HERMES_TABLES = [
    "hermes_ai_roles",
    "crm_write_queue",
    "crm_receipts",
    "commission_audits",
    "eom_scorecards",
    "project_85_renewals",
    "renewal_actions",
    "guardrail_logs",
    "reporting_schedules",
    "dashboard_kpis",
    # Sync control tables (NowCerts ↔ canonical book pipeline)
    "sync_runs",
    "inbound_sync_staging",
    "sync_mappings",
    "outbound_sync_queue",
    "sync_audit_log",
    "sync_errors",
    "sync_conflicts",
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

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks) and not self.errors

    def format_lines(self) -> list[str]:
        lines = ["Hermes Operations Center — Health Check", "=" * 48]
        for c in self.checks:
            status = "OK" if c.ok else "FAIL"
            detail = f" ({c.error})" if c.error else ""
            lines.append(f"  [{status}] {c.table}: {c.row_count} rows{detail}")
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for err in self.errors:
                lines.append(f"  - {err}")
        lines.append("")
        lines.append(f"Overall: {'HEALTHY' if self.ok else 'ISSUES FOUND'}")
        return lines


def run_ops_doctor(supa: SupabaseClient) -> OpsDoctorReport:
    """Verify Supabase connectivity and row counts for all Hermes tables."""
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


