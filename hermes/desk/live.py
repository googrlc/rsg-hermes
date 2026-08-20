"""Live Zoho Desk identifiers for the RSG CRM Plus portal.

These IDs were read from the Desk MCP on 2026-08-20. Send them as strings —
they exceed JavaScript safe-integer range. Do not create a second department:
the live department is named RSG and is treated as Agency Service.

Custom fields, teams, statuses, Blueprints, and workflows are not in this
module because the Desk MCP cannot create them. See ``docs/zoho-desk/LIVE.md``.
"""

from __future__ import annotations

# Portal https://desk.zoho.com/agent/rsg10761  (org edition CRMPLUS)
ORG_ID = "935382122"
PORTAL_NAME = "rsg10761"
PORTAL_URL = "https://desk.zoho.com/agent/rsg10761"
DEPARTMENT_ID = "1435573000000006907"
DEPARTMENT_NAME = "RSG"

LAYOUT_IDS = {
    "General Service": "1435573000000074011",
    "Certificate Request": "1435573000000460002",
    "Auto or Driver Change": "1435573000000453002",
    "General Policy Change": "1435573000000463001",
    "Billing and Cancellation": "1435573000000464001",
}

NATIVE_FIELD_IDS = {
    "classification": "1435573000000022001",
    "priority": "1435573000000000437",
    "channel": "1435573000000000439",
    "status": "1435573000000000401",
    "dueDate": "1435573000000000435",
    "contactId": "1435573000000000391",
    "accountId": "1435573000000000433",
    "subject": "1435573000000000397",
}

# Until cf_* fields exist, map spec fields onto native Desk fields.
NATIVE_BRIDGES = {
    "cf_request_category": "classification",
    "cf_source": "channel",
    "cf_required_by_date": "dueDate",
}

LAYOUT_CLASSIFICATION_DEFAULTS = {
    "General Service": "General Service",
    "Certificate Request": "Certificate Request",
    "Auto or Driver Change": "Policy Change",
    "General Policy Change": "Policy Change",
    "Billing and Cancellation": "Billing and Payments",
}

# Native Status cannot be extended via the Desk layout APIs.
LIVE_STATUSES = ("Open", "On Hold", "Escalated", "Closed")

PROFILE_IDS = {
    "Support Administrator": "1435573000000008343",
    "Agent": "1435573000000008345",
    "Help Center": "1435573000000008347",
    "Supervisor": "1435573000000055067",
    "Support Manager": "1435573000000055069",
    "Newbie Agent": "1435573000000055071",
    "Light Agent": "1435573000000067003",
}
