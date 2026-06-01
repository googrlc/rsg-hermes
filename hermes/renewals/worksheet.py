"""Worksheet helpers for the renewal flow.

The worksheet Gretchen completes lives on the Renewal record (required fields +
checkbox booleans, enforced by Dynamic Logic). On completion the finished
worksheet is rendered to a Google Doc and filed to the client's Drive folder.

Field reads use the EspoCRM API names, which for the custom Renewal fields are
snake_case (current_premium, renewal_premium, line_of_business, the bool
checkboxes, ...) — NOT camelCase. The {{token}} names exposed for the Drive
template stay camelCase (that's just what goes in the template document).

Two render paths:
  build_worksheet_content(renewal) -> text  (works today via save_document)
  fill_template(renewal, ...)      -> copies a Drive template and merges the
                                      {{tokens}} below (enable once the template's
                                      placeholders + Docs scope are confirmed)
"""
from __future__ import annotations

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


def merge_fields(renewal: dict) -> dict:
    """Placeholder token -> string value for a {{token}} Google Docs template.

    Put these exact tokens in the template doc (double braces), e.g. {{account}}.
    Reads snake_case EspoCRM field names; the four checkbox reads correspond 1:1
    to config.CHECKBOX_FIELDS.
    """
    return {
        "account": renewal.get("accountName") or renewal.get("name") or "",
        "carrier": renewal.get("carrier") or "",
        "lineOfBusiness": renewal.get("line_of_business") or "",
        "expirationDate": renewal.get("expiration_date") or "",
        "renewalEffectiveDate": renewal.get("renewal_effective_date") or "",
        "currentPremium": _money(renewal.get("current_premium")),
        "renewalPremium": _money(renewal.get("renewal_premium")),
        "premiumChange": _pct(renewal.get("premium_change")),
        "stage": renewal.get("stage") or "",
        "lostReason": renewal.get("lost_reason") or "",
        "clientStates": renewal.get("renewal_notes") or "",
        # checkbox booleans (keys = config.CHECKBOX_FIELDS)
        "renewalReviewed": _yn(renewal.get("renewal_reviewed")),
        "accountConfirmed": _yn(renewal.get("account_confirmed")),
        "emailSent": _yn(renewal.get("renewal_email_sent")),
        "amsUpdated": _yn(renewal.get("ams_updated")),
    }


def build_worksheet_content(renewal: dict) -> str:
    """Structured worksheet text — the v1 doc filed to the client folder."""
    f = merge_fields(renewal)
    return f"""# Renewal Worksheet — {f['account']}

**Line of business:** {f['lineOfBusiness']}   **Carrier:** {f['carrier']}
**Expires:** {f['expirationDate']}   **Renewal effective:** {f['renewalEffectiveDate']}

## Premium
- Expiring premium: {f['currentPremium']}
- Renewal premium: {f['renewalPremium']}
- Change: {f['premiumChange']}

## Checklist
- Renewal declaration pulled & reviewed: {f['renewalReviewed']}
- Account details confirmed (units / drivers): {f['accountConfirmed']}
- Renewal email sent to client: {f['emailSent']}
- AMS (NowCerts) updated: {f['amsUpdated']}

## Outcome
- Stage: {f['stage']}
- Lost reason: {f['lostReason']}
- Client states: {f['clientStates']}
"""


def fill_template(renewal: dict, *, drive, template_doc_id: str,
                  dest_folder_id: str | None = None) -> dict:
    """Copy the Drive template, replace {{tokens}}, return the new file metadata.

    `drive` is an authed client exposing Drive copy + Docs batchUpdate. In Hermes,
    reuse hermes/integrations/gdrive_client.py (verify the accessor names + that
    the Docs scope https://www.googleapis.com/auth/documents is granted). Kept as
    an opt-in path; the generated-doc route (build_worksheet_content) ships v1.
    """
    account = renewal.get("accountName") or renewal.get("name") or "Client"
    new_title = f"{account} - Renewal Worksheet"

    copied = drive.copy_file(template_doc_id, name=new_title, parent=dest_folder_id)
    new_id = copied["id"]

    requests = [
        {"replaceAllText": {
            "containsText": {"text": "{{" + token + "}}", "matchCase": True},
            "replaceText": value,
        }}
        for token, value in merge_fields(renewal).items()
    ]
    drive.docs_batch_update(new_id, requests)
    return copied
