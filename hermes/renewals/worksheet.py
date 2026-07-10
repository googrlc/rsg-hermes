"""Worksheet helpers for the renewal flow.

The worksheet Gretchen completes lives on the Renewal record (required fields +
checkbox booleans, enforced by Dynamic Logic). On completion the finished
worksheet is rendered to text via build_worksheet_content() and persisted with
save_document(); client files themselves live in Nextcloud (the file source of
truth).

Field reads use the EspoCRM API names, which for the custom Renewal fields are
snake_case (current_premium, renewal_premium, line_of_business, the bool
checkboxes, ...) — NOT camelCase.
"""
from __future__ import annotations

import re
from typing import Any

from . import config


def _money(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return ""


def _pct(v) -> str:
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return ""


def _yn(v) -> str:
    return "Yes" if v else "No"


def account_url(account_id: str | None) -> str | None:
    base = config.ESPO_BASE_URL
    return f"{base}/#Account/view/{account_id}" if base and account_id else None


def renewal_url(renewal_id: str | None) -> str | None:
    base = config.ESPO_BASE_URL
    return f"{base}/#{config.RENEWAL_ENTITY}/view/{renewal_id}" if base and renewal_id else None


def worksheet_record(renewal: dict[str, Any]) -> dict[str, Any]:
    for key in config.WORKSHEET_LOOKUP_KEYS:
        value = renewal.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _checkbox_value(renewal: dict[str, Any], worksheet_row: dict[str, Any], field: str) -> Any:
    value = renewal.get(field)
    return worksheet_row.get(field) if value is None else value


def _label(key: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key.replace("_", " ")).strip()
    return text[:1].upper() + text[1:]


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return _yn(value)
    if value is None:
        return "—"
    if isinstance(value, float):
        return str(value)
    text = str(value).strip()
    return text or "—"


def worksheet_lines(renewal: dict[str, Any]) -> list[str]:
    row = worksheet_record(renewal)
    lines: list[str] = []
    for key in sorted(row):
        if key in config.WORKSHEET_HIDDEN_FIELDS or key in config.CHECKBOX_FIELDS:
            continue
        value = row.get(key)
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"- {_label(key)}: {_render_value(value)}")
    return lines


def merge_fields(renewal: dict) -> dict:
    """Placeholder token -> string value for the renewal worksheet fields.

    Put these exact tokens in the template doc (double braces), e.g. {{account}}.
    Reads snake_case EspoCRM field names; the four checkbox reads correspond 1:1
    to config.CHECKBOX_FIELDS.
    """
    worksheet_row = worksheet_record(renewal)
    pipeline_stage = _first_present(renewal, "pipeline_stage", "stage") or ""
    disposition = renewal.get("disposition") or ""
    return {
        "account": renewal.get("accountName") or renewal.get("name") or "",
        "carrier": renewal.get("carrier") or "",
        "lineOfBusiness": renewal.get("line_of_business") or "",
        "expirationDate": renewal.get("expiration_date") or "",
        "renewalEffectiveDate": renewal.get("renewal_effective_date") or "",
        "currentPremium": _money(renewal.get("current_premium")),
        "renewalProposedPremium": _money(renewal.get("renewal_proposed_premium")),
        "renewalPremium": _money(renewal.get("renewal_premium")),
        "premiumChange": _pct(renewal.get("premium_change")),
        "stage": pipeline_stage,
        "pipelineStage": pipeline_stage,
        "disposition": disposition,
        "lostReason": renewal.get("lost_reason") or disposition,
        "clientStates": renewal.get("renewal_notes") or "",
        "worksheetVariant": worksheet_row.get("lob_variant") or "",
        # checkbox booleans (keys = config.CHECKBOX_FIELDS)
        "renewalReviewed": _yn(_checkbox_value(renewal, worksheet_row, "renewal_reviewed")),
        "accountConfirmed": _yn(_checkbox_value(renewal, worksheet_row, "account_confirmed")),
        "emailSent": _yn(_checkbox_value(renewal, worksheet_row, "renewal_email_sent")),
        "amsUpdated": _yn(_checkbox_value(renewal, worksheet_row, "ams_updated")),
    }


def build_worksheet_content(renewal: dict) -> str:
    """Structured worksheet text — the v1 doc filed to the client folder."""
    f = merge_fields(renewal)
    variant = f["worksheetVariant"] or "default"
    extra_lines = worksheet_lines(renewal)
    extra = "\n".join(extra_lines) if extra_lines else "- No additional worksheet fields captured"
    return f"""# Renewal Worksheet — {f['account']}

**Line of business:** {f['lineOfBusiness']}   **Carrier:** {f['carrier']}
**Expires:** {f['expirationDate']}   **Renewal effective:** {f['renewalEffectiveDate']}

## Premium
- Expiring premium: {f['currentPremium']}
- Carrier renewal proposal: {f['renewalProposedPremium']}
- Renewal premium: {f['renewalPremium']}
- Change: {f['premiumChange']}

## Checklist
- Renewal declaration pulled & reviewed: {f['renewalReviewed']}
- Account details confirmed (units / drivers): {f['accountConfirmed']}
- Renewal email sent to client: {f['emailSent']}
- AMS (NowCerts) updated: {f['amsUpdated']}

## Worksheet details
- LOB variant: {variant}
{extra}

## Outcome
- Pipeline stage: {f['pipelineStage']}
- Disposition: {f['disposition']}
- Client states: {f['clientStates']}
"""


