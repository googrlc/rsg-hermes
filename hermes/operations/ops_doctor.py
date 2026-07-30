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

# The reachability probe below selects one column to count rows, and most tables
# have a surrogate `id`. The canonical book does not: it is keyed by the NowCerts
# guids, so probing `id` there 400s ("column canonical_clients.id does not
# exist") and pins --ops-doctor at ISSUES FOUND forever — the same
# permanently-red trap the HERMES_TABLES comment above warns about.
TABLE_KEY_COLUMNS = {
    "canonical_clients": "nowcerts_insured_guid",
    "canonical_policies": "policy_guid",
}
DEFAULT_KEY_COLUMN = "id"


@dataclass
class OpsCheckResult:
    table: str
    ok: bool
    row_count: int
    error: str | None = None


@dataclass
class LLMCheckResult:
    """Whether the configured LLM gateway accepts our key."""

    ok: bool
    endpoint: str
    key_tail: str
    model_group: str
    error: str | None = None

    def format_lines(self) -> list[str]:
        status = "OK" if self.ok else "FAIL"
        detail = f" ({self.error})" if self.error else ""
        return [
            "LLM gateway:",
            f"  [{status}] {self.endpoint} — key {self.key_tail}, "
            f"model {self.model_group}{detail}",
        ]


def _key_tail(key: str) -> str:
    """A safe-to-print tail of the key, so a key/proxy mismatch is diagnosable
    from a health check without leaking the secret."""
    if not key:
        return "(none)"
    return f"...{key[-4:]}" if len(key) >= 4 else "(set)"


def _classify_llm_error(exc: Exception) -> str:
    """Turn an SDK/proxy exception into a short, actionable line.

    The failure that motivated this check was a LiteLLM 401
    ``token_not_found_in_db`` — a rotated/expired virtual key. Name that case
    explicitly so a red health check points straight at the credential, not at
    a vague network error.
    """
    msg = str(exc).strip()
    low = msg.lower()
    if isinstance(exc, ModuleNotFoundError) or "no module named 'openai'" in low:
        return "openai SDK not installed"
    if (
        "token_not_found" in low
        or "invalid proxy server token" in low
        or "authentication error" in low
        or " 401" in f" {low}"
        or low.startswith("401")
    ):
        return f"key rejected by gateway (401) — {msg}"
    return msg or exc.__class__.__name__


def check_llm_gateway() -> LLMCheckResult:
    """Verify the LLM gateway accepts our key with a cheap authenticated call.

    Every Hermes LLM path routes through ``hermes.core.llm_client``, so a
    rejected key takes down all AI features (Ask Hermes, intake synthesis, OCR)
    at once. This probe surfaces that here instead of only at agent runtime. It
    resolves the endpoint/key through ``llm_client`` (the one source of truth for
    resolution order) and lists models — an authenticated call that costs no
    tokens — rather than running a completion.
    """
    from hermes.core import llm_client

    base = llm_client._resolve_base_url()
    endpoint = base or "OpenAI public API (no LITELLM_BASE_URL)"
    key = llm_client._resolve_api_key()
    model_group = llm_client.default_model()
    tail = _key_tail(key)

    if not key:
        return LLMCheckResult(
            ok=False,
            endpoint=endpoint,
            key_tail=tail,
            model_group=model_group,
            error="no API key configured (set LITELLM_API_KEY)",
        )
    try:
        llm_client.get_client().models.list()
    except Exception as exc:  # noqa: BLE001 — a health check must never raise
        return LLMCheckResult(
            ok=False,
            endpoint=endpoint,
            key_tail=tail,
            model_group=model_group,
            error=_classify_llm_error(exc),
        )
    return LLMCheckResult(ok=True, endpoint=endpoint, key_tail=tail, model_group=model_group)


@dataclass
class OpsDoctorReport:
    checks: list[OpsCheckResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Reachability alone said HEALTHY while the 6am KPI job 404'd for months and
    # the writeback had never once succeeded. Movement is the other half.
    staleness: Any = None
    # The LLM gateway is the other silent single point of failure: a rotated
    # LiteLLM key 401s every AI feature at once, and reachability/movement say
    # nothing about it.
    llm: LLMCheckResult | None = None

    @property
    def ok(self) -> bool:
        base = all(c.ok for c in self.checks) and not self.errors
        base = base and (self.staleness.ok if self.staleness is not None else True)
        return base and (self.llm.ok if self.llm is not None else True)

    def format_lines(self) -> list[str]:
        lines = ["Hermes Operations Center — Health Check", "=" * 48]
        lines.append("Tables reachable:")
        for c in self.checks:
            status = "OK" if c.ok else "FAIL"
            detail = f" ({c.error})" if c.error else ""
            lines.append(f"  [{status}] {c.table}: {c.row_count} rows{detail}")
        if self.llm is not None:
            lines.append("")
            lines.extend(self.llm.format_lines())
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


def run_ops_doctor(
    supa: SupabaseClient, *, check_movement: bool = True, check_llm: bool = True
) -> OpsDoctorReport:
    """Verify Supabase connectivity, row counts, AND that pipelines still move.

    ``check_movement=False`` gives the old reachability-only behaviour, which is
    only useful when you already know a pipeline is down and want to confirm the
    database itself is fine. ``check_llm=False`` skips the LLM-gateway probe (a
    live authenticated call), which is what tests and offline runs want.
    """
    report = OpsDoctorReport()

    for table in HERMES_TABLES:
        try:
            key = TABLE_KEY_COLUMNS.get(table, DEFAULT_KEY_COLUMN)
            count_rows = supa.select(table, columns=key, limit=1000)
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

    if check_llm:
        report.llm = check_llm_gateway()

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


