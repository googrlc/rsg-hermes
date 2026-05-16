"""
RSG-Hermes Hermes Agent Plugin
Chief CRM Officer — EspoCRM via natural language
"""
from __future__ import annotations

import os
from typing import Any


def _make_clients():
    """Build EspoCRM and Supabase clients from env vars."""
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient

    espo = EspoClient(
        base_url=os.environ["ESPO_URL"],
        api_key=os.environ["ESPO_API_KEY"],
    )
    supa = SupabaseClient(
        url=os.environ.get("SUPABASE_URL", ""),
        key=os.environ.get("SUPABASE_KEY", ""),
    )
    return espo, supa


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def crm_lookup(text: str) -> dict[str, Any]:
    """Look up any contact, account, policy, or field value in EspoCRM."""
    from hermes.commands import lookup
    espo, _ = _make_clients()
    result = await lookup.handle(espo, text)
    return {"output": result.text}


async def crm_write(text: str) -> dict[str, Any]:
    """Create or update any CRM record using natural language (e.g. 'add contact John Smith email john@acme.com')."""
    from hermes.commands import data_entry
    espo, _ = _make_clients()
    result = await data_entry.handle(espo, text)
    return {"output": result.text}


async def crm_pipeline_report() -> dict[str, Any]:
    """Return the current sales pipeline broken down by stage with premium totals."""
    from hermes.commands import reports
    espo, supa = _make_clients()
    result = await reports.handle(espo, "pipeline", supa)
    return {"output": result.text}


async def crm_kpi_dashboard() -> dict[str, Any]:
    """Return a KPI snapshot: entity counts, open pipeline value, won revenue, win rate."""
    from hermes.commands import reports
    espo, supa = _make_clients()
    result = await reports.handle(espo, "kpi", supa)
    return {"output": result.text}


async def crm_premium_by_lob() -> dict[str, Any]:
    """Break down total premium by Line of Business (Auto, GL/BOP, Workers Comp, etc.)."""
    from hermes.commands import reports
    espo, supa = _make_clients()
    result = await reports.handle(espo, "lob", supa)
    return {"output": result.text}


async def crm_stale_leads(days: int = 14) -> dict[str, Any]:
    """List open opportunities not touched in N days (default 14). Flags at-risk pipeline."""
    from hermes.commands import reports
    espo, supa = _make_clients()
    result = await reports.handle(espo, f"stale {days}", supa)
    return {"output": result.text}


async def crm_account_list() -> dict[str, Any]:
    """Return a list of all accounts with contact details (phone, website)."""
    from hermes.commands import reports
    espo, supa = _make_clients()
    result = await reports.handle(espo, "accounts", supa)
    return {"output": result.text}


async def crm_changelog() -> dict[str, Any]:
    """Return the nightly CRM changelog digest (recent changes across all records)."""
    from hermes.commands import changelog
    espo, supa = _make_clients()
    result = await changelog.handle(espo, supa, "changelog")
    return {"output": result.text}


async def crm_renewal_audit(days: int = 90) -> dict[str, Any]:
    """Run the renewal sentinel: find policies expiring within N days lacking a renewal review task."""
    from hermes.commands import revenue
    espo, _ = _make_clients()
    result = await revenue.renewal_audit(espo, days)
    return {"output": result.text}


async def crm_revenue_view(text: str = "renewal") -> dict[str, Any]:
    """Show cross-sell / renewal pipeline views. Text can be 'renewal', 'cross-sell', or 'audit'."""
    from hermes.commands import revenue
    espo, _ = _make_clients()
    result = await revenue.handle(espo, text)
    return {"output": result.text}


async def crm_merge_records(text: str) -> dict[str, Any]:
    """Merge duplicate CRM records (e.g. 'merge contact <id1> into <id2>')."""
    from hermes.commands import merge
    espo, _ = _make_clients()
    result = await merge.handle(espo, text)
    return {"output": result.text}


async def crm_data_quality() -> dict[str, Any]:
    """Scan for data quality issues: bad name formatting, missing fields, duplicates."""
    from hermes.commands import data_quality
    espo, _ = _make_clients()
    result = await data_quality.handle(espo, "report")
    return {"output": result.text}


async def crm_sync_run(direction: str = "full") -> dict[str, Any]:
    """Trigger a sync. Direction: 'full' (bidirectional), 'nowcerts', 'crm-to-hub', or 'hub-to-nowcerts'."""
    from hermes.commands import sync
    espo, supa = _make_clients()
    result = await sync.handle(espo, supa, f"sync {direction}")
    return {"output": result.text}


async def crm_sync_status() -> dict[str, Any]:
    """Check recent sync execution history: status, records processed, failures."""
    from hermes.commands import sync
    espo, supa = _make_clients()
    result = await sync.handle(espo, supa, "sync status")
    return {"output": result.text}


async def crm_sync_conflicts() -> dict[str, Any]:
    """List unresolved data conflicts between NowCerts and EspoCRM."""
    from hermes.commands import sync
    espo, supa = _make_clients()
    result = await sync.handle(espo, supa, "sync conflicts")
    return {"output": result.text}


async def crm_sync_errors() -> dict[str, Any]:
    """List recent synchronization errors from the pipeline."""
    from hermes.commands import sync
    espo, supa = _make_clients()
    result = await sync.handle(espo, supa, "sync errors")
    return {"output": result.text}


async def crm_intake(text: str) -> dict[str, Any]:
    """Process a new client intake: parse lead details and create records in EspoCRM."""
    from hermes.commands import intake
    espo, _ = _make_clients()
    result = await intake.handle(espo, text)
    return {"output": result.text}


async def crm_business_research(text: str) -> dict[str, Any]:
    """Research a business: look up company info and enrich the CRM record."""
    from hermes.commands import business_research
    espo, _ = _make_clients()
    result = await business_research.handle(espo, text)
    return {"output": result.text}


# ---------------------------------------------------------------------------
# Dashboard & Analytics tool handlers
# ---------------------------------------------------------------------------

async def crm_dashboard_snapshot() -> dict[str, Any]:
    """Take a full dashboard snapshot: writes system health, finance, and renewal KPIs to Supabase dashboard_kpis table."""
    from hermes.operations.kpi_writer import snapshot_system_health, snapshot_finance, snapshot_renewals
    _, supa = _make_clients()
    await snapshot_system_health(supa)
    await snapshot_finance(supa)
    await snapshot_renewals(supa)
    return {"output": "Dashboard KPI snapshot complete: system health, finance, and renewal metrics written to Supabase."}


async def crm_ops_health() -> dict[str, Any]:
    """Run the Ops Doctor health check: verify all Supabase tables are reachable, check roles and Slack channels, return a full status report."""
    from hermes.operations.ops_doctor import run_ops_doctor
    _, supa = _make_clients()
    report = await run_ops_doctor(supa)
    lines = report.format_lines()
    return {"output": "\n".join(lines)}


async def crm_renewal_dashboard() -> dict[str, Any]:
    """Return a full renewal dashboard: all Project 85 renewals grouped by risk status (SAFE, AT_RISK, CRITICAL, RENEWED, LAPSED) with premium totals."""
    from hermes.operations.renewal_tracker import get_renewals_by_risk
    _, supa = _make_clients()
    results = {}
    for status in ("AT_RISK", "CRITICAL", "SAFE", "RENEWED", "LAPSED"):
        results[status] = await get_renewals_by_risk(supa, status)
    summary = "\n".join([f"{k}: {len(v)} records" for k, v in results.items()])
    return {"output": summary, "data": results}


async def crm_renewals_by_risk(status: str = "AT_RISK") -> dict[str, Any]:
    """Get all renewals filtered by risk status. Status options: SAFE, AT_RISK, CRITICAL, RENEWED, LAPSED."""
    from hermes.operations.renewal_tracker import get_renewals_by_risk
    _, supa = _make_clients()
    records = await get_renewals_by_risk(supa, status.upper())
    return {"output": f"{len(records)} renewals with status {status.upper()}", "data": records}


async def crm_renewals_expiring(days: int = 30) -> dict[str, Any]:
    """Get all renewals expiring within N days (default 30). Shows policy name, expiry date, premium, and risk status."""
    from hermes.operations.renewal_tracker import get_renewals_expiring_within
    _, supa = _make_clients()
    records = await get_renewals_expiring_within(supa, days)
    return {"output": f"{len(records)} renewals expiring within {days} days", "data": records}


async def crm_escalate_renewal(policy_id: str, new_status: str = "CRITICAL") -> dict[str, Any]:
    """Escalate a renewal record's risk status (e.g. AT_RISK -> CRITICAL) and log the escalation action automatically."""
    from hermes.operations.renewal_tracker import escalate_risk
    _, supa = _make_clients()
    await escalate_risk(supa, policy_id, new_status.upper())
    return {"output": f"Policy {policy_id} escalated to {new_status.upper()} and action logged."}


async def crm_log_renewal_action(policy_id: str, action_type: str, note: str = "") -> dict[str, Any]:
    """Log a renewal touchpoint action for a policy. Action types: EMAIL_SENT, PHONE_CALL, MEETING_SCHEDULED, QUOTE_GENERATED, PROPOSAL_SENT, BOUND, SLACK_ALERT, MANUAL_NOTE."""
    from hermes.operations.renewal_tracker import log_renewal_action
    _, supa = _make_clients()
    await log_renewal_action(supa, policy_id, action_type.upper(), note)
    return {"output": f"Action {action_type.upper()} logged for policy {policy_id}."}


async def crm_kpi_write(metric_name: str, metric_value: float, category: str) -> dict[str, Any]:
    """Write a single custom KPI data point to the Supabase dashboard_kpis table. Use category to group metrics (e.g. 'pipeline', 'renewals', 'ops')."""
    from hermes.operations.kpi_writer import record_kpi
    _, supa = _make_clients()
    await record_kpi(supa, metric_name=metric_name, metric_value=metric_value, category=category)
    return {"output": f"KPI '{metric_name}' = {metric_value} written to dashboard under category '{category}'."}


async def crm_commission_snapshot() -> dict[str, Any]:
    """Pull the latest commission ledger entries from Supabase: client, carrier, LOB, amount, and reconciliation status."""
    from hermes.commands import reports
    espo, supa = _make_clients()
    result = await reports.handle(espo, "commission", supa)
    return {"output": result.text}


async def crm_eom_scorecard() -> dict[str, Any]:
    """Retrieve end-of-month scorecards from Supabase: production targets vs actuals, close rates, and LOB breakdown."""
    from hermes.integrations.supabase_client import SupabaseClient
    _, supa = _make_clients()
    rows = supa.select("eom_scorecards", limit=10, order="created_at.desc")
    if not rows:
        return {"output": "No EOM scorecards found."}
    lines = []
    for r in rows:
        lines.append(f"{r.get('period','?')} | Target: {r.get('target')} | Actual: {r.get('actual')} | Rate: {r.get('close_rate')}")
    return {"output": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Hermes plugin registration
# ---------------------------------------------------------------------------

def register(registry):
    """Register all Chief CRM Officer tools with Hermes."""
    tools = [
        crm_lookup,
        crm_write,
        crm_pipeline_report,
        crm_kpi_dashboard,
        crm_premium_by_lob,
        crm_stale_leads,
        crm_account_list,
        crm_changelog,
        crm_renewal_audit,
        crm_revenue_view,
        crm_merge_records,
        crm_data_quality,
        crm_sync_run,
        crm_sync_status,
        crm_sync_conflicts,
        crm_sync_errors,
        crm_intake,
        crm_business_research,
        # --- DASHBOARDS & ANALYTICS ---
        crm_dashboard_snapshot,
        crm_ops_health,
        crm_renewal_dashboard,
        crm_renewals_by_risk,
        crm_renewals_expiring,
        crm_escalate_renewal,
        crm_log_renewal_action,
        crm_kpi_write,
        crm_commission_snapshot,
        crm_eom_scorecard,
    ]
    for tool_fn in tools:
        registry.register_tool(
            name=tool_fn.__name__,
            handler=tool_fn,
            schema={
                "name": tool_fn.__name__,
                "description": tool_fn.__doc__ or "",
                "parameters": {},
            },
        )
