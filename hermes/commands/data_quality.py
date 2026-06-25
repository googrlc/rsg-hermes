"""Periodic data quality audit across all CRM modules.

Scans EspoCRM for missing/empty required fields, logs issues to Supabase
`data_quality_issues`, and returns a Slack-friendly summary.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from hermes.core.auditor import count_entity, EspoClientError
from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

AUDIT_RULES: dict[str, list[dict[str, Any]]] = {
    "Account": [
        {"field": "fein", "label": "FEIN", "severity": "high"},
        {"field": "phoneNumber", "label": "Phone", "severity": "medium"},
        {"field": "billingAddressStreet", "label": "Address", "severity": "medium"},
        {"field": "account_status", "label": "Account Status", "severity": "low"},
        {"field": "industry", "label": "Industry", "severity": "low"},
    ],
    "Contact": [
        {"field": "lastName", "label": "Last Name", "severity": "high"},
        {"field": "phoneNumber", "label": "Phone", "severity": "medium"},
        {"field": "emailAddress", "label": "Email", "severity": "medium"},
    ],
    "Opportunity": [
        {"field": "lineOfBusiness", "label": "Line of Business", "severity": "high"},
        {"field": "stage", "label": "Stage", "severity": "high"},
        {"field": "amount", "label": "Premium Amount", "severity": "medium"},
        {"field": "accountId", "label": "Linked Account", "severity": "high"},
    ],
    "Policy": [
        {"field": "policy_number", "label": "Policy Number", "severity": "high", "fallbacks": ["policyNumber"]},
        {"field": "accountId", "label": "Linked Account", "severity": "high"},
        {"field": "carrier", "label": "Carrier", "severity": "high"},
        {"field": "effective_date", "label": "Effective Date", "severity": "high", "fallbacks": ["effectiveDate"]},
        {"field": "premium_amount", "label": "Premium", "severity": "medium", "fallbacks": ["premiumAmount", "amount"]},
    ],
    "Lead": [
        {"field": "firstName", "label": "First Name", "severity": "medium"},
        {"field": "phoneNumber", "label": "Phone", "severity": "medium"},
        {"field": "emailAddress", "label": "Email", "severity": "medium"},
    ],
}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _scan_missing_records(
    client: "EspoClient",
    entity: str,
    field_name: str,
    *,
    fallbacks: list[str] | None = None,
    max_scan: int = 200,
) -> list[dict[str, Any]]:
    """Fetch all records in safe pages and count missing values locally.

    We intentionally avoid field-specific `select` clauses because some Espo setups
    reject them for custom fields even when the fields are readable.

    *fallbacks* lists alternate field names to check when *field_name* is absent
    (handles the camelCase ↔ snake_case transition in EspoCRM).
    """
    all_keys = [field_name] + (fallbacks or [])
    missing: list[dict[str, Any]] = []
    page_size = min(max_scan, 200)
    offset = 0
    total: int | None = None

    while True:
        body = client.get(
            entity,
            params={
                "maxSize": page_size,
                "offset": offset,
            },
        )
        rows = body.get("list", []) if isinstance(body, dict) else []
        if not isinstance(rows, list) or rows == []:
            break

        for row in rows:
            if not isinstance(row, dict):
                continue
            if all(_is_missing(row.get(k)) for k in all_keys):
                missing.append(row)

        if isinstance(body, dict) and total is None and body.get("total") is not None:
            try:
                total = int(body["total"])
            except (TypeError, ValueError):
                total = None

        offset += len(rows)
        if total is not None and offset >= total:
            break
        if len(rows) < page_size:
            break

    return missing


def _run_audit(client: "EspoClient") -> dict[str, Any]:
    """Run all audit rules and return structured results."""
    results: dict[str, Any] = {}
    total_records = 0
    total_issues = 0
    scan_errors = 0

    for entity, rules in AUDIT_RULES.items():
        entity_result: dict[str, Any] = {"rules": [], "record_count": 0}
        try:
            entity_result["record_count"] = count_entity(client, entity)
            total_records += entity_result["record_count"]
        except EspoClientError:
            entity_result["record_count"] = -1

        for rule in rules:
            try:
                violations = _scan_missing_records(
                    client, entity, rule["field"],
                    fallbacks=rule.get("fallbacks"),
                    max_scan=200,
                )
                count = len(violations)
            except EspoClientError:
                count = -1
                violations = []
                scan_errors += 1

            if count > 0:
                total_issues += count
            entity_result["rules"].append({
                "field": rule["field"],
                "label": rule["label"],
                "severity": rule["severity"],
                "violation_count": count,
                "sample_names": [
                    v.get("name") or v.get("firstName", "") + " " + v.get("lastName", "")
                    for v in violations[:3]
                ],
            })
        results[entity] = entity_result

    max_possible = sum(
        er["record_count"] * len(er["rules"])
        for er in results.values()
        if er["record_count"] > 0
    )
    if scan_errors > 0:
        score: int | None = None
    else:
        score = round((1 - total_issues / max_possible) * 100) if max_possible > 0 else 100

    return {
        "entities": results,
        "total_records": total_records,
        "total_issues": total_issues,
        "score": score,
        "scan_errors": scan_errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _log_to_supabase(supa: "SupabaseClient", audit: dict[str, Any]) -> None:
    """Write issues to data_quality_issues table."""
    for entity, entity_result in audit["entities"].items():
        for rule in entity_result["rules"]:
            if rule["violation_count"] <= 0:
                continue
            try:
                supa.insert("data_quality_issues", {
                    "domain": "crm",
                    "severity": rule["severity"],
                    "issue_type": f"missing_{rule['field']}",
                    "issue_detail": (
                        f"{entity}.{rule['field']}: {rule['violation_count']} records missing. "
                        f"Samples: {', '.join(rule['sample_names'][:3])}"
                    ),
                    "owner": "hermes",
                    "resolution_status": "open",
                })
            except Exception:
                log.exception("Failed to log DQ issue for %s.%s", entity, rule["field"])


def _format_report(audit: dict[str, Any]) -> str:
    """Format audit results as Slack message."""
    lines = ["*Data Quality Report*", ""]

    for entity, er in audit["entities"].items():
        rc = er["record_count"]
        count_str = str(rc) if rc >= 0 else "error"
        lines.append(f"*{entity}* ({count_str} records)")
        for rule in er["rules"]:
            vc = rule["violation_count"]
            if vc < 0:
                lines.append(f"  {rule['label']}: error scanning")
            elif vc == 0:
                lines.append(f"  {rule['label']}: all good")
            else:
                severity_icon = ":red_circle:" if rule["severity"] == "high" else ":large_yellow_circle:"
                lines.append(f"  {severity_icon} {rule['label']}: {vc} missing")
                if rule["sample_names"]:
                    samples = ", ".join(s.strip() for s in rule["sample_names"] if s.strip())
                    if samples:
                        lines.append(f"    e.g. {samples}")
        lines.append("")

    if audit.get("score") is None:
        lines.append("*Overall score: N/A (scan errors present)*")
    else:
        lines.append(f"*Overall score: {audit['score']}/100*")
    lines.append(f"Total: {audit['total_issues']} issues across {audit['total_records']} records")
    if audit.get("scan_errors"):
        lines.append(f"Scan errors: {audit['scan_errors']} field checks")
    return "\n".join(lines)


def handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
) -> DispatchResult:
    """Run data quality audit, log to Supabase, return Slack report."""
    audit = _run_audit(client)

    if supa:
        _log_to_supabase(supa, audit)

    report = _format_report(audit)
    return DispatchResult(True, report, audit)
