"""Zoho CRM Account → Zoho Desk service-request button.

Puts a **Create Service Request** action on every CRM Account (record view
and each list-view row). The button opens a Desk ticket in department RSG
(Agency Service). It does not write to Momentum and does not treat Desk as
a second client database — the ticket only stores the CRM Account id.
"""

from __future__ import annotations

from typing import Any

from hermes.desk.live import (
    DEPARTMENT_ID,
    LAYOUT_IDS,
    ORG_ID,
    PORTAL_NAME,
    PORTAL_URL,
    TEAM_IDS,
)
from hermes.desk.titles import case_title

BUTTON_NAME = "Create Service Request"
BUTTON_DESCRIPTION = (
    "Open a Zoho Desk service ticket for this CRM Account. Desk owns the "
    "work; this does not write to Momentum."
)
MODULE = "Accounts"
# CRM UI labels for Setup → Modules → Accounts → Links & Buttons.
POSITIONS = (
    "View Page",
    "List View - Button for each record",
)
CONNECTION_NAME = "zohodesk"
FUNCTION_NAME = "Create_Service_Request"
FUNCTION_API_NAME = "create_service_request"
ARGUMENT_NAME = "accountId"
ARGUMENT_VALUE = "Accounts.Id"
DEFAULT_CATEGORY = "General Service"
DEFAULT_SHORT_REQUEST = "Service request"
DEFAULT_CHANNEL = "CRM"
DEFAULT_PRIORITY = "Normal"
DEFAULT_TEAM = "Service Intake"
DEFAULT_LAYOUT = "General Service"

# Custom-link fallback when a true custom button cannot be created via API.
CUSTOM_LINK_NAME = "Create Service Request"


def _text(value: Any, *, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def crm_account_identity(account: dict[str, Any]) -> dict[str, str]:
    """Normalize a CRM Account record (or a loosely keyed dict) for Desk."""
    name = _text(
        account.get("Account_Name")
        or account.get("account_name")
        or account.get("name"),
        fallback="Unknown client",
    )
    crm_id = _text(account.get("id") or account.get("Id") or account.get("crm_account_id"))
    email = _text(account.get("Email") or account.get("email"))
    phone = _text(account.get("Phone") or account.get("phone") or account.get("Mobile"))
    last_name = _text(account.get("Last_Name") or account.get("last_name"), fallback=name)
    return {
        "crm_account_id": crm_id,
        "account_name": name,
        "email": email,
        "phone": phone,
        "contact_last_name": last_name,
    }


def new_ticket_form_url() -> str:
    """Agent UI new-ticket form for department RSG (not prefilled)."""
    return f"{PORTAL_URL}/rsg/tickets/new"


def custom_link_url() -> str:
    """CRM custom-link URL. Merge fields fill the CRM Account id and name."""
    return (
        f"{PORTAL_URL}/rsg/tickets/new"
        f"?crmAccountId=${{Accounts.Id}}"
        f"&crmAccountName=${{Accounts.Account_Name}}"
    )


def build_ticket_payload(
    account: dict[str, Any],
    *,
    contact_id: str | None = None,
    desk_account_id: str | None = None,
    category: str | None = None,
    policy_number: str | None = None,
    short_request: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Desk POST /tickets body for a CRM Account service request."""
    ident = crm_account_identity(account)
    chosen_category = category or DEFAULT_CATEGORY
    title = case_title(
        chosen_category,
        ident["account_name"],
        policy_number,
        short_request or DEFAULT_SHORT_REQUEST,
    )
    desc = description or (
        f"Service request opened from Zoho CRM Account "
        f"{ident['account_name']} (id {ident['crm_account_id'] or 'unknown'}). "
        "Complete the request details in Desk. Do not copy this into Momentum "
        "until the work is done."
    )
    payload: dict[str, Any] = {
        "subject": title,
        "departmentId": DEPARTMENT_ID,
        "layoutId": LAYOUT_IDS[DEFAULT_LAYOUT],
        "teamId": TEAM_IDS[DEFAULT_TEAM],
        "channel": DEFAULT_CHANNEL,
        "classification": chosen_category,
        "priority": DEFAULT_PRIORITY,
        "description": desc,
        "cf": {},
    }
    if ident["crm_account_id"]:
        payload["cf"]["cf_crm_account_id"] = ident["crm_account_id"]
    if contact_id:
        payload["contactId"] = str(contact_id)
    else:
        contact: dict[str, str] = {"lastName": ident["contact_last_name"]}
        if ident["email"]:
            contact["email"] = ident["email"]
        if ident["phone"]:
            contact["phone"] = ident["phone"]
        payload["contact"] = contact
    if desk_account_id:
        payload["accountId"] = str(desk_account_id)
    if ident["email"] and "contactId" not in payload:
        payload["email"] = ident["email"]
    return payload


def render_deluge() -> str:
    """CRM button function. Argument ``accountId`` = Accounts.Id."""
    layout_id = LAYOUT_IDS[DEFAULT_LAYOUT]
    team_id = TEAM_IDS[DEFAULT_TEAM]
    return f'''string {FUNCTION_NAME}({ARGUMENT_NAME})
{{
	orgId = "{ORG_ID}";
	departmentId = "{DEPARTMENT_ID}";
	layoutId = "{layout_id}";
	teamId = "{team_id}";
	connectionName = "{CONNECTION_NAME}";
	accountIdStr = {ARGUMENT_NAME}.toString();
	acct = zoho.crm.getRecordById("{MODULE}", {ARGUMENT_NAME}.toLong());
	if(acct == null)
	{{
		return "Could not load CRM Account " + accountIdStr;
	}}
	accountName = ifnull(acct.get("Account_Name"), "Unknown client");
	email = ifnull(acct.get("Email"), "");
	phone = ifnull(acct.get("Phone"), "");
	contactLast = accountName;
	if(email == "")
	{{
		related = zoho.crm.getRelatedRecords("Contacts", "{MODULE}", {ARGUMENT_NAME}.toLong());
		if(related != null && related.size() > 0)
		{{
			firstContact = related.get(0);
			email = ifnull(firstContact.get("Email"), "");
			if(phone == "")
			{{
				phone = ifnull(firstContact.get("Phone"), "");
			}}
			contactLast = ifnull(firstContact.get("Last_Name"), accountName);
		}}
	}}
	deskAccountId = "";
	try
	{{
		acctSearch = zoho.desk.searchRecords(orgId, "accounts", {{"accountName":accountName}}, 0, 1, connectionName);
		acctData = ifnull(acctSearch.get("data"), acctSearch);
		if(acctData != null && acctData.size() > 0)
		{{
			deskAccountId = ifnull(acctData.get(0).get("id"), "");
		}}
	}}
	catch (acctSearchErr)
	{{
		info acctSearchErr;
	}}
	if(deskAccountId == "")
	{{
		acctMap = Map();
		acctMap.put("accountName", accountName);
		createdAcct = zoho.desk.create(orgId, "accounts", acctMap, connectionName);
		deskAccountId = ifnull(createdAcct.get("id"), "");
	}}
	contactId = "";
	if(email != "")
	{{
		try
		{{
			conSearch = zoho.desk.searchRecords(orgId, "contacts", {{"email":email}}, 0, 1, connectionName);
			conData = ifnull(conSearch.get("data"), conSearch);
			if(conData != null && conData.size() > 0)
			{{
				contactId = ifnull(conData.get(0).get("id"), "");
			}}
		}}
		catch (conSearchErr)
		{{
			info conSearchErr;
		}}
	}}
	if(contactId == "")
	{{
		conMap = Map();
		conMap.put("lastName", contactLast);
		if(email != "")
		{{
			conMap.put("email", email);
		}}
		if(phone != "")
		{{
			conMap.put("phone", phone);
		}}
		if(deskAccountId != "")
		{{
			conMap.put("accountId", deskAccountId);
		}}
		createdCon = zoho.desk.create(orgId, "contacts", conMap, connectionName);
		contactId = ifnull(createdCon.get("id"), "");
	}}
	if(contactId == "")
	{{
		return "Could not find or create a Desk contact for " + accountName + ". Add an email on the Account or a related Contact, then try again.";
	}}
	subject = "Service | " + accountName + " | No policy | Service request";
	ticket = Map();
	ticket.put("subject", subject);
	ticket.put("departmentId", departmentId);
	ticket.put("contactId", contactId);
	ticket.put("layoutId", layoutId);
	ticket.put("teamId", teamId);
	ticket.put("channel", "{DEFAULT_CHANNEL}");
	ticket.put("classification", "{DEFAULT_CATEGORY}");
	ticket.put("priority", "{DEFAULT_PRIORITY}");
	ticket.put("description", "Service request opened from Zoho CRM Account " + accountName + " (id " + accountIdStr + "). Complete the request details in Desk. Do not copy this into Momentum until the work is done.");
	if(deskAccountId != "")
	{{
		ticket.put("accountId", deskAccountId);
	}}
	cf = Map();
	cf.put("cf_crm_account_id", accountIdStr);
	ticket.put("cf", cf);
	created = zoho.desk.create(orgId, "tickets", ticket, connectionName);
	webUrl = ifnull(created.get("webUrl"), "");
	ticketNumber = ifnull(created.get("ticketNumber"), ifnull(created.get("id"), ""));
	if(webUrl == "")
	{{
		info created;
		return "Desk did not return a ticket URL. Response: " + created.toString();
	}}
	openUrl(webUrl, "new window");
	return "Opened Desk ticket " + ticketNumber.toString();
}}
'''


def button_setup_steps() -> tuple[str, ...]:
    return (
        "CRM Setup → Developer Hub → Connections → create Zoho Desk "
        f"connection named {CONNECTION_NAME} with Desk.tickets.CREATE, "
        "Desk.contacts.CREATE, Desk.contacts.READ, Desk.search.READ.",
        "CRM Setup → Developer Hub → Functions → New Function → Category "
        f"Button. Name {FUNCTION_NAME}. Paste the Deluge from "
        "hermes.desk.crm_button.render_deluge. Argument "
        f"{ARGUMENT_NAME} = {ARGUMENT_VALUE}.",
        "CRM Setup → Customization → Modules and Fields → Accounts → "
        "Links & Buttons → Create New Button.",
        f"Button name: {BUTTON_NAME}. Action: the {FUNCTION_NAME} function. "
        "Profiles: every internal profile that works Accounts.",
        "Create the button twice: View Page, and List View - Button for "
        "each record, so it appears on every Account.",
    )
