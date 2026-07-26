"""Dashboard KPI writer for Hermes operations metrics."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)


def record_kpi(
    supa: SupabaseClient,
    *,
    metric_name: str,
    metric_value: float | Decimal,
    category: str,
) -> dict[str, Any]:
    """Write a single KPI data point to ``dashboard_kpis``."""
    return supa.insert(
        "dashboard_kpis",
        {
            "metric_name": metric_name,
            "metric_value": float(metric_value),
            "category": category,
        },
    )


def snapshot_system_health(supa: SupabaseClient) -> list[dict[str, Any]]:
    """Compute and record system health KPIs from live Supabase data."""
    results: list[dict[str, Any]] = []

    # Was crm_write_queue with UPPERCASE statuses — a table that has never
    # existed in this schema, so this KPI 404'd on every nightly run. The real
    # write gate is outbound_sync_queue, whose statuses are lowercase.
    open_queue = supa.select(
        "outbound_sync_queue",
        params={"status": "in.(queued,processing,failed)"},
        limit=1000,
    )
    results.append(
        record_kpi(
            supa,
            metric_name="crm_queue_open_items",
            metric_value=len(open_queue),
            category="SYSTEM_HEALTH",
        )
    )

    guardrails_recent = supa.select(
        "guardrail_logs",
        columns="id",
        params={"created_at": f"gte.{(datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()}"},
        limit=1000,
    )
    results.append(
        record_kpi(
            supa,
            metric_name="guardrail_events_24h",
            metric_value=len(guardrails_recent),
            category="SYSTEM_HEALTH",
        )
    )

    active_channels = supa.select(
        "slack_registry",
        columns="id",
        params={"is_active": "eq.true"},
        limit=100,
    )
    results.append(
        record_kpi(
            supa,
            metric_name="slack_registry_channels_active",
            metric_value=len(active_channels),
            category="SYSTEM_HEALTH",
        )
    )

    log.info("System health snapshot recorded: %d KPIs", len(results))
    return results


def snapshot_finance(supa: SupabaseClient) -> list[dict[str, Any]]:
    """Compute and record finance-related KPIs."""
    results: list[dict[str, Any]] = []

    open_discrepancies = supa.select(
        "commission_audits",
        columns="id",
        params={"status": "in.(DISCREPANCY,ESCALATED)"},
        limit=1000,
    )
    results.append(
        record_kpi(
            supa,
            metric_name="open_commission_audit_exceptions",
            metric_value=len(open_discrepancies),
            category="FINANCE",
        )
    )

    log.info("Finance snapshot recorded: %d KPIs", len(results))
    return results


def snapshot_renewals(supa: SupabaseClient) -> list[dict[str, Any]]:
    """Compute and record renewal-related KPIs."""
    results: list[dict[str, Any]] = []

    all_renewals = supa.select(
        "project_85_renewals",
        columns="id,risk_status",
        limit=1000,
    )
    at_risk = [r for r in all_renewals if r.get("risk_status") in ("AT_RISK", "CRITICAL")]
    pct = (len(at_risk) / len(all_renewals) * 100) if all_renewals else 0

    results.append(
        record_kpi(
            supa,
            metric_name="project85_renewals_at_risk_pct",
            metric_value=round(pct, 1),
            category="RENEWALS",
        )
    )

    log.info("Renewals snapshot recorded: %d KPIs", len(results))
    return results
