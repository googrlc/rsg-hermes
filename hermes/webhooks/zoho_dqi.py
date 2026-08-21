"""Zoho CRM → Cursor Data Quality Investigator relay."""

from __future__ import annotations

import logging
import os
from typing import Any

from hermes.webhooks.cursor_dqi import CursorDqiTriggerError, trigger_cursor_dqi_investigation

log = logging.getLogger(__name__)

RENEWALS_MODULE = (os.environ.get("ZOHO_RENEWALS_MODULE") or "Renewals").strip()
POLICIES_MODULE = (os.environ.get("ZOHO_POLICIES_MODULE") or "Policies").strip()


def _zoho_client():
    from hermes_integrations.zoho_client import ZohoClientError, get_client

    try:
        return get_client()
    except ZohoClientError as exc:
        raise ValueError(f"Zoho is not configured: {exc}") from exc


def _field(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, dict):
            name = value.get("name")
            if name:
                return str(name).strip()
        return str(value).strip()
    return ""


def _load_renewal_record(renewal_id: str) -> dict[str, Any]:
    zoho = _zoho_client()
    record = zoho.get_record(RENEWALS_MODULE, renewal_id)
    if not record:
        raise ValueError(f"Renewals record not found: {renewal_id}")
    return record


def _load_policy_record(policy_id: str) -> dict[str, Any]:
    zoho = _zoho_client()
    record = zoho.get_record(POLICIES_MODULE, policy_id)
    if not record:
        raise ValueError(f"Policies record not found: {policy_id}")
    return record


def build_cursor_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Normalize Zoho webhook JSON into the Cursor automation payload."""
    renewal_id = str(body.get("renewal_id") or body.get("renewalId") or "").strip()
    policy_id = str(body.get("policy_id") or body.get("policyId") or "").strip()
    policy_number = str(body.get("policy_number") or body.get("policyNumber") or "").strip()
    client_name = str(body.get("client_name") or body.get("clientName") or "").strip()
    line_of_business = str(
        body.get("line_of_business") or body.get("lineOfBusiness") or body.get("lob") or ""
    ).strip()
    zoho_record_id = str(body.get("zoho_record_id") or body.get("zohoRecordId") or "").strip()
    source = str(body.get("source") or "zoho_crm")

    if renewal_id.startswith("${") or policy_number.startswith("${"):
        raise ValueError(
            "Zoho merge field did not resolve — map Record Id via the merge-field picker, "
            "not typed ${Renewals...} text"
        )

    if renewal_id:
        rec = _load_renewal_record(renewal_id)
        policy_number = policy_number or _field(rec, "Policy_Number")
        client_name = client_name or _field(rec, "Client_Name")
        line_of_business = line_of_business or _field(rec, "Line_of_Business")
        zoho_record_id = zoho_record_id or renewal_id
        source = "zoho_renewals"
    elif policy_id:
        rec = _load_policy_record(policy_id)
        policy_number = policy_number or _field(rec, "Policy_Number")
        client_name = client_name or _field(rec, "Account_Name", "Client_Name")
        line_of_business = line_of_business or _field(rec, "Line_of_Business")
        zoho_record_id = zoho_record_id or policy_id
        source = "zoho_policies"
    elif not policy_number:
        raise ValueError("Provide renewal_id, policy_id, or policy_number")

    if not policy_number:
        raise ValueError("policy_number is empty after resolving Zoho record")

    payload: dict[str, Any] = {
        "policy_number": policy_number,
        "source": source,
    }
    if client_name:
        payload["client_name"] = client_name
    if line_of_business:
        payload["line_of_business"] = line_of_business
    if zoho_record_id:
        payload["zoho_record_id"] = zoho_record_id
    return payload


def handle_zoho_dqi_webhook(body: dict[str, Any]) -> dict[str, Any]:
    """Build payload, trigger Cursor, return a small status dict for the caller."""
    payload = build_cursor_payload(body)
    try:
        cursor_response = trigger_cursor_dqi_investigation(payload)
    except CursorDqiTriggerError as exc:
        log.warning("Cursor DQI trigger failed for %s: %s", payload.get("policy_number"), exc)
        raise

    return {
        "ok": True,
        "policy_number": payload["policy_number"],
        "cursor": cursor_response,
    }
