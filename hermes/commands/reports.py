"""CRM reports: pipeline, KPI, LOB breakdown, commission, stale leads, account list."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient


def _as_money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace("$", "").replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def _pipeline_report(client: "EspoClient") -> DispatchResult:
    """Opportunities grouped by stage with premium totals."""
    premium_field = os.environ.get("HERMES_PREMIUM_FIELD", "amount")
    body = client.get(
        "Opportunity",
        params={
            "maxSize": 200,
            "select": f"id,name,stage,{premium_field},accountName",
            "orderBy": [["stage", "asc"]],
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    if not rows:
        return DispatchResult(True, "Pipeline is empty.", {"stages": {}})

    stages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict):
            stages[row.get("stage", "Unknown")].append(row)

    lines = ["*Pipeline Report*"]
    grand_total = Decimal("0")
    for stage, items in sorted(stages.items()):
        total = sum((_as_money(r.get(premium_field)) for r in items), Decimal("0"))
        grand_total += total
        lines.append(f"\n*{stage}* ({len(items)} deals, ${total:,.0f})")
        for r in items[:5]:
            name = r.get("name", "?")
            acct = r.get("accountName") or ""
            amt = _as_money(r.get(premium_field))
            lines.append(f"  - {name}" + (f" | {acct}" if acct else "") + f" | ${amt:,.0f}")
        if len(items) > 5:
            lines.append(f"  ... +{len(items) - 5} more")

    lines.append(f"\n*Total pipeline:* ${grand_total:,.0f} across {len(rows)} deals")
    return DispatchResult(True, "\n".join(lines), {"stages": dict(stages)})


def _premium_by_lob(client: "EspoClient") -> DispatchResult:
    """Sum premium by line of business (using Opportunity description or custom LOB field)."""
    premium_field = os.environ.get("HERMES_PREMIUM_FIELD", "amount")
    body = client.get(
        "Opportunity",
        params={
            "maxSize": 500,
            "select": f"id,name,{premium_field},stage,description",
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    if not rows:
        return DispatchResult(True, "No opportunities to analyze.", {})

    lob_buckets: dict[str, Decimal] = defaultdict(Decimal)
    lob_counts: dict[str, int] = defaultdict(int)
    known_lobs = [
        "Commercial Auto", "GL/BOP", "Workers Comp", "Personal Lines",
        "Medicare", "Life", "Property", "Umbrella",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        desc = str(row.get("description") or row.get("name") or "")
        matched_lob = "Other"
        for lob in known_lobs:
            if lob.lower() in desc.lower():
                matched_lob = lob
                break
        amt = _as_money(row.get(premium_field))
        lob_buckets[matched_lob] += amt
        lob_counts[matched_lob] += 1

    lines = ["*Premium by Line of Business*"]
    for lob in sorted(lob_buckets, key=lambda k: lob_buckets[k], reverse=True):
        lines.append(f"  {lob}: ${lob_buckets[lob]:,.0f} ({lob_counts[lob]} deals)")
    total = sum(lob_buckets.values(), Decimal("0"))
    lines.append(f"\n*Total:* ${total:,.0f}")
    return DispatchResult(True, "\n".join(lines), {"lob_buckets": {k: str(v) for k, v in lob_buckets.items()}})


def _kpi_dashboard(client: "EspoClient") -> DispatchResult:
    """Quick entity counts + pipeline value."""
    from hermes.core.auditor import count_entity, EspoClientError

    premium_field = os.environ.get("HERMES_PREMIUM_FIELD", "amount")
    lines = ["*KPI Dashboard*"]
    data: dict[str, Any] = {}

    for entity in ("Account", "Contact", "Opportunity"):
        try:
            n = count_entity(client, entity)
            lines.append(f"  {entity}s: {n}")
            data[entity] = n
        except EspoClientError as e:
            lines.append(f"  {entity}s: error ({e})")

    try:
        body = client.get(
            "Opportunity",
            params={
                "maxSize": 500,
                "select": f"id,{premium_field},stage",
            },
        )
        rows = body.get("list", []) if isinstance(body, dict) else []
        open_rows = [r for r in rows if isinstance(r, dict) and r.get("stage") not in ("Closed Won", "Closed Lost")]
        won_rows = [r for r in rows if isinstance(r, dict) and r.get("stage") == "Closed Won"]
        pipeline_val = sum((_as_money(r.get(premium_field)) for r in open_rows), Decimal("0"))
        won_val = sum((_as_money(r.get(premium_field)) for r in won_rows), Decimal("0"))
        total = len(rows)
        win_rate = (len(won_rows) / total * 100) if total else 0

        lines.append(f"  Open pipeline: ${pipeline_val:,.0f} ({len(open_rows)} deals)")
        lines.append(f"  Won revenue: ${won_val:,.0f} ({len(won_rows)} deals)")
        lines.append(f"  Win rate: {win_rate:.0f}%")
        data["pipeline"] = str(pipeline_val)
        data["won"] = str(won_val)
        data["win_rate"] = f"{win_rate:.0f}%"
    except Exception:
        lines.append("  Pipeline: error fetching")

    return DispatchResult(True, "\n".join(lines), data)


def _commission_snapshot(supa: "SupabaseClient | None") -> DispatchResult:
    """Recent commission ledger entries from Supabase."""
    if not supa:
        return DispatchResult(False, "Supabase not configured -- cannot pull commission data.")
    try:
        rows = supa.select(
            "commission_ledger",
            columns="id,client_name,carrier_name,lob,actual_commission,statement_date,reconciliation_status",
            params={"order": "statement_date.desc"},
            limit=15,
        )
    except Exception as e:
        return DispatchResult(False, f"Commission query failed: {e}")

    if not rows:
        return DispatchResult(True, "No commission records yet.", {"rows": []})

    lines = ["*Commission Snapshot* (latest 15)"]
    for r in rows:
        name = r.get("client_name", "?")
        carrier = r.get("carrier_name", "")
        comm = _as_money(r.get("actual_commission"))
        dt = r.get("statement_date", "?")
        status = r.get("reconciliation_status", "?")
        lines.append(f"  {name} | {carrier} | ${comm:,.2f} | {dt} | {status}")
    return DispatchResult(True, "\n".join(lines), {"rows": rows})


def _stale_leads(client: "EspoClient", days: int = 14) -> DispatchResult:
    """Opportunities not modified in the last N days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    body = client.get(
        "Opportunity",
        params={
            "maxSize": 200,
            "select": "id,name,stage,accountName,modifiedAt",
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    rows = [
        r
        for r in rows
        if isinstance(r, dict)
        and str(r.get("modifiedAt") or "")[:10] < cutoff
        and r.get("stage") not in ("Closed Won", "Closed Lost")
    ]
    rows.sort(key=lambda r: str(r.get("modifiedAt") or ""))
    rows = rows[:25]
    if not rows:
        return DispatchResult(True, f"No stale leads (>{days} days untouched). Nice.", {"rows": []})

    lines = [f"*Stale Leads* (not touched in {days}+ days)"]
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = r.get("name", "?")
        acct = r.get("accountName") or ""
        stage = r.get("stage", "?")
        mod = str(r.get("modifiedAt", "?"))[:10]
        lines.append(f"  - {name}" + (f" | {acct}" if acct else "") + f" | {stage} | last touch {mod}")
    return DispatchResult(True, "\n".join(lines), {"rows": rows})


def _account_list(client: "EspoClient") -> DispatchResult:
    """Paginated account listing."""
    body = client.get(
        "Account",
        params={
            "maxSize": 25,
            "select": "id,name,phoneNumber,website",
            "orderBy": [["name", "asc"]],
        },
    )
    rows = body.get("list", []) if isinstance(body, dict) else []
    total = body.get("total", len(rows)) if isinstance(body, dict) else len(rows)
    if not rows:
        return DispatchResult(True, "No accounts in CRM yet.", {"rows": []})

    lines = [f"*Accounts* (showing {len(rows)} of {total})"]
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = r.get("name", "?")
        phone = r.get("phoneNumber") or ""
        web = r.get("website") or ""
        extras = " | ".join(p for p in [phone, web] if p)
        lines.append(f"  - {name}" + (f" | {extras}" if extras else ""))
    return DispatchResult(True, "\n".join(lines), {"rows": rows, "total": total})


def _looks_non_canonical(name: str) -> bool:
    letters = [c for c in name if c.isalpha()]
    if not letters:
        return False
    # SHOUT_CASE, all lower, or obvious spacing anomalies.
    return (
        name.isupper()
        or name.islower()
        or "  " in name
        or name != name.strip()
    )


def _canonicalize_name(name: str) -> str:
    # Keep common business suffixes uppercase.
    keep_upper = {"LLC", "INC", "LTD", "CO", "LP", "LLP", "USA"}
    words = [w for w in name.strip().split() if w]
    normalized: list[str] = []
    for w in words:
        up = w.upper().strip(",.")
        if up in keep_upper:
            normalized.append(up)
            continue
        normalized.append(w.capitalize())
    return " ".join(normalized)


def _report_personal(client: "EspoClient") -> DispatchResult:
    """Report suspect name rows for manual cleanup (report-first workflow)."""
    account_body = client.get(
        "Account",
        params={"maxSize": 500, "select": "id,name,accountType"},
    )
    contact_body = client.get(
        "Contact",
        params={"maxSize": 500, "select": "id,name,firstName,lastName,emailAddress"},
    )

    accounts = account_body.get("list", []) if isinstance(account_body, dict) else []
    contacts = contact_body.get("list", []) if isinstance(contact_body, dict) else []

    account_issues: list[dict[str, Any]] = []
    contact_issues: list[dict[str, Any]] = []

    for row in accounts:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name or not _looks_non_canonical(name):
            continue
        account_issues.append(
            {
                "id": row.get("id"),
                "name": name,
                "suggested": _canonicalize_name(name),
                "accountType": row.get("accountType"),
            }
        )

    for row in contacts:
        if not isinstance(row, dict):
            continue
        full_name = str(row.get("name") or "").strip()
        if not full_name:
            first = str(row.get("firstName") or "").strip()
            last = str(row.get("lastName") or "").strip()
            full_name = " ".join([p for p in [first, last] if p]).strip()
        if not full_name or not _looks_non_canonical(full_name):
            continue
        contact_issues.append(
            {
                "id": row.get("id"),
                "name": full_name,
                "suggested": _canonicalize_name(full_name),
                "email": row.get("emailAddress"),
            }
        )

    lines = ["*Personal Data Cleanup Report*"]
    lines.append(f"Accounts scanned: {len(accounts)} | suspect rows: {len(account_issues)}")
    for r in account_issues[:20]:
        lines.append(f"  - Account: {r['name']} -> {r['suggested']} (id: {r['id']})")
    if len(account_issues) > 20:
        lines.append(f"  ... +{len(account_issues) - 20} more account rows")

    lines.append("")
    lines.append(f"Contacts scanned: {len(contacts)} | suspect rows: {len(contact_issues)}")
    for r in contact_issues[:20]:
        email = r.get("email") or "no email"
        lines.append(f"  - Contact: {r['name']} -> {r['suggested']} ({email})")
    if len(contact_issues) > 20:
        lines.append(f"  ... +{len(contact_issues) - 20} more contact rows")

    lines.append("")
    lines.append("Recommended workflow:")
    lines.append("1) Run `report personal`")
    lines.append("2) Fix rows deliberately in UI/export using canonical spelling")
    lines.append("3) Run `bulk normalize` (dry-run preview) before any apply step")
    return DispatchResult(
        True,
        "\n".join(lines),
        {"accounts": account_issues, "contacts": contact_issues},
    )


def _bulk_normalize_preview(client: "EspoClient") -> DispatchResult:
    """Dry-run normalization preview only (no writes)."""
    report = _report_personal(client)
    if not report.ok:
        return report
    lines = ["*Bulk Normalize (Dry Run)*", "No changes were applied.", ""]
    lines.append("Preview generated from `report personal` candidates.")
    lines.append("If this looks right, apply manually in UI/export first.")
    lines.append("")
    lines.append(report.message)
    return DispatchResult(True, "\n".join(lines), report.data)


def handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
) -> DispatchResult:
    """Route to the appropriate report sub-handler."""
    t = text.lower()
    if re.search(r"\bpipeline\b", t):
        return _pipeline_report(client)
    if re.search(r"\b(lob|line.of.business)\s*(break|report|summary)\b", t) or "premium by lob" in t:
        return _premium_by_lob(client)
    if re.search(r"\b(kpi|dashboard)\b", t):
        return _kpi_dashboard(client)
    if re.search(r"\bcommission\s*(snap|report|ledger)\b", t):
        return _commission_snapshot(supa)
    if re.search(r"\bstale\b", t):
        return _stale_leads(client)
    if re.search(r"\b(account\s*list|my\s*accounts)\b", t):
        return _account_list(client)
    if re.search(r"\b(report\s*[- ]?personal|personal\s*report|cleanup\s*report)\b", t):
        return _report_personal(client)
    if re.search(r"\b(bulk\s*[- ]?normalize|normalize\s*preview)\b", t):
        return _bulk_normalize_preview(client)
    return DispatchResult(
        False,
        "Unknown report. Try: *pipeline*, *premium by lob*, *kpi*, *dashboard*, "
        "*commission snapshot*, *stale leads*, *my accounts*, *report personal*, or *bulk normalize*.",
    )
