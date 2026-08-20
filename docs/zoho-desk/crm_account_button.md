# CRM Account button — Create Service Request in Desk

Adds **Create Service Request** on every Zoho CRM Account so sales can open
a Desk case without leaving the Account. Desk owns the work. CRM stays sales.
Momentum is not written.

Executable spec: `hermes/desk/crm_button.py`. Python twin (same payload):
`hermes/desk/service_request.py`. Live IDs: `hermes/desk/live.py`.

## What the button does

1. Reads the CRM Account (name, email, phone, related Contact if no Account email).
2. Reuses a Desk contact/account when one already exists; otherwise creates a
   thin Desk contact (and name-only Desk account) so the ticket has a requester.
   That is not a second client database — the ticket stamps `cf_crm_account_id`.
3. Creates a ticket in department **RSG** / layout **General Service** /
   team **Service Intake** / channel **CRM** / classification **General Service** /
   priority **Normal**.
4. Title: `Service | {Account Name} | No policy | Service request`.
5. Opens the new Desk ticket in a new window.

Staff then classify, add the policy number, and move it off Intake (AUT-01).

## Place it on every Account

Create **two** buttons with the same function (CRM allows one location per button):

| Location | Why |
|---|---|
| View Page | Account detail |
| List View - Button for each record | Every row in the Accounts list |

Name (≤ 30 characters): `Create Service Request`. Grant every internal profile
that works Accounts.

## One-time CRM setup

### 1. Desk connection

Setup → Developer Hub → Connections → **Add Connection** → Zoho Desk.

- Connection name (link name): `zohodesk`
- Scopes: `Desk.tickets.CREATE`, `Desk.contacts.CREATE`, `Desk.contacts.READ`,
  `Desk.search.READ`

Authorize as a Desk agent who can create tickets in department RSG.

### 2. Button function

Setup → Developer Hub → Functions → **New Function**.

- Function name: `Create_Service_Request`
- Category: **Button**
- Argument: `accountId` = `Accounts.Id` (Edit Arguments)
- Paste the Deluge below. Save.

### 3. Buttons

Setup → Customization → Modules and Fields → **Accounts** → **Links & Buttons**
→ **Create New Button**. Action = the function from step 2. Repeat for both
locations in the table above.

CRM REST cannot create custom buttons with the current Desk-scoped OAuth
token (`settings.custom_buttons` is a different scope). A custom **link** with
the same label is the API fallback (`scripts/zoho_crm_account_desk_button.py`);
it opens the Desk new-ticket form instead of creating the ticket.

## Deluge (paste)

```deluge
string Create_Service_Request(accountId)
{
	orgId = "935382122";
	departmentId = "1435573000000006907";
	layoutId = "1435573000000074011";
	teamId = "1435573000000456002";
	connectionName = "zohodesk";
	accountIdStr = accountId.toString();
	acct = zoho.crm.getRecordById("Accounts", accountId.toLong());
	if(acct == null)
	{
		return "Could not load CRM Account " + accountIdStr;
	}
	accountName = ifnull(acct.get("Account_Name"), "Unknown client");
	email = ifnull(acct.get("Email"), "");
	phone = ifnull(acct.get("Phone"), "");
	contactLast = accountName;
	if(email == "")
	{
		related = zoho.crm.getRelatedRecords("Contacts", "Accounts", accountId.toLong());
		if(related != null && related.size() > 0)
		{
			firstContact = related.get(0);
			email = ifnull(firstContact.get("Email"), "");
			if(phone == "")
			{
				phone = ifnull(firstContact.get("Phone"), "");
			}
			contactLast = ifnull(firstContact.get("Last_Name"), accountName);
		}
	}
	deskAccountId = "";
	try
	{
		acctSearch = zoho.desk.searchRecords(orgId, "accounts", {"accountName":accountName}, 0, 1, connectionName);
		acctData = ifnull(acctSearch.get("data"), acctSearch);
		if(acctData != null && acctData.size() > 0)
		{
			deskAccountId = ifnull(acctData.get(0).get("id"), "");
		}
	}
	catch (acctSearchErr)
	{
		info acctSearchErr;
	}
	if(deskAccountId == "")
	{
		acctMap = Map();
		acctMap.put("accountName", accountName);
		createdAcct = zoho.desk.create(orgId, "accounts", acctMap, connectionName);
		deskAccountId = ifnull(createdAcct.get("id"), "");
	}
	contactId = "";
	if(email != "")
	{
		try
		{
			conSearch = zoho.desk.searchRecords(orgId, "contacts", {"email":email}, 0, 1, connectionName);
			conData = ifnull(conSearch.get("data"), conSearch);
			if(conData != null && conData.size() > 0)
			{
				contactId = ifnull(conData.get(0).get("id"), "");
			}
		}
		catch (conSearchErr)
		{
			info conSearchErr;
		}
	}
	if(contactId == "")
	{
		conMap = Map();
		conMap.put("lastName", contactLast);
		if(email != "")
		{
			conMap.put("email", email);
		}
		if(phone != "")
		{
			conMap.put("phone", phone);
		}
		if(deskAccountId != "")
		{
			conMap.put("accountId", deskAccountId);
		}
		createdCon = zoho.desk.create(orgId, "contacts", conMap, connectionName);
		contactId = ifnull(createdCon.get("id"), "");
	}
	if(contactId == "")
	{
		return "Could not find or create a Desk contact for " + accountName + ". Add an email on the Account or a related Contact, then try again.";
	}
	subject = "Service | " + accountName + " | No policy | Service request";
	ticket = Map();
	ticket.put("subject", subject);
	ticket.put("departmentId", departmentId);
	ticket.put("contactId", contactId);
	ticket.put("layoutId", layoutId);
	ticket.put("teamId", teamId);
	ticket.put("channel", "CRM");
	ticket.put("classification", "General Service");
	ticket.put("priority", "Normal");
	ticket.put("description", "Service request opened from Zoho CRM Account " + accountName + " (id " + accountIdStr + "). Complete the request details in Desk. Do not copy this into Momentum until the work is done.");
	if(deskAccountId != "")
	{
		ticket.put("accountId", deskAccountId);
	}
	cf = Map();
	cf.put("cf_crm_account_id", accountIdStr);
	ticket.put("cf", cf);
	created = zoho.desk.create(orgId, "tickets", ticket, connectionName);
	webUrl = ifnull(created.get("webUrl"), "");
	ticketNumber = ifnull(created.get("ticketNumber"), ifnull(created.get("id"), ""));
	if(webUrl == "")
	{
		info created;
		return "Desk did not return a ticket URL. Response: " + created.toString();
	}
	openUrl(webUrl, "new window");
	return "Opened Desk ticket " + ticketNumber.toString();
}
```

The checked-in copy is generated from live portal IDs. Re-render after an
org/department change:

```bash
python -c "from hermes.desk.crm_button import render_deluge; print(render_deluge())"
```
