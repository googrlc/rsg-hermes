"""Deluge for CRM buttons that create Service_Requests records.

None of these functions open Zoho Desk or stamp cf_crm_account_id.
Live IDs (module API name) are interpolated the same way
``hermes/desk/crm_button.py`` interpolates Desk org/layout/team ids — except
the target is CRM ``Service_Requests``.
"""

from __future__ import annotations

from hermes.zoho.service_requests import (
    MODULE_API_NAME,
    MODULE_PLURAL,
    PRIORITIES,
    REQUEST_TYPES,
    suggest_request_type,
)

ACCOUNT_BUTTON = "New Service Request"
CONTACT_BUTTON = "New Service Request"
POLICY_BUTTON = "Service This Policy"
EMAIL_BUTTON = "Create Service Request"

CONNECTION_NAME = "zoho_crm"


def render_account_button(*, module_api_name: str = MODULE_API_NAME) -> str:
    """Accounts → New Service Request. Prefills Account, primary Contact, Owner."""
    return f'''string New_Service_Request_From_Account(accountId)
{{
	acct = zoho.crm.getRecordById("Accounts", accountId.toLong());
	if(acct == null)
	{{
		return "Could not load Account " + accountId.toString();
	}}
	accountName = ifnull(acct.get("Account_Name"), "Unknown client");
	subject = "Service request — " + accountName;
	mp = Map();
	mp.put("Name", subject);
	mp.put("Subject", subject);
	mp.put("Account_Name", accountId.toLong());
	mp.put("Client_Name", accountName);
	mp.put("Named_Insured", accountName);
	mp.put("Status", "New");
	mp.put("Priority", "Standard");
	mp.put("Request_Type", "Other");
	mp.put("Open_Date", zoho.currenttime);
	mp.put("Last_Activity", zoho.currenttime);
	owner = acct.get("Owner");
	if(owner != null)
	{{
		mp.put("Owner", owner.get("id"));
	}}
	primaryId = "";
	related = zoho.crm.getRelatedRecords("Contacts", "Accounts", accountId.toLong());
	if(related != null && related.size() > 0)
	{{
		firstContact = related.get(0);
		primaryId = ifnull(firstContact.get("id"), "");
		for each  c in related
		{{
			flag = ifnull(c.get("Is_Primary_Contact"), false);
			if(flag == true || flag == "true")
			{{
				primaryId = ifnull(c.get("id"), primaryId);
			}}
		}}
	}}
	if(primaryId != "")
	{{
		mp.put("Contact_Name", primaryId.toLong());
	}}
	resp = zoho.crm.create("{module_api_name}", mp);
	rid = ifnull(resp.get("id"), "");
	if(rid == "")
	{{
		details = ifnull(resp.get("details"), resp);
		rid = ifnull(details.get("id"), "");
	}}
	if(rid == "")
	{{
		return "Could not create {MODULE_PLURAL} record: " + resp.toString();
	}}
	openUrl("/crm/tab/{module_api_name}/" + rid, "same window");
	return rid;
}}
'''


def render_contact_button(*, module_api_name: str = MODULE_API_NAME) -> str:
    return f'''string New_Service_Request_From_Contact(contactId)
{{
	con = zoho.crm.getRecordById("Contacts", contactId.toLong());
	if(con == null)
	{{
		return "Could not load Contact " + contactId.toString();
	}}
	account = con.get("Account_Name");
	accountId = "";
	accountName = "";
	if(account != null)
	{{
		accountId = ifnull(account.get("id"), "");
		accountName = ifnull(account.get("name"), "");
	}}
	if(accountId == "")
	{{
		return "This Contact has no Account. Service Requests require an Account.";
	}}
	fullName = ifnull(con.get("Full_Name"), ifnull(con.get("Last_Name"), "Contact"));
	subject = "Service request — " + if(accountName != "", accountName, fullName);
	mp = Map();
	mp.put("Name", subject);
	mp.put("Subject", subject);
	mp.put("Account_Name", accountId.toLong());
	mp.put("Contact_Name", contactId.toLong());
	mp.put("Client_Name", if(accountName != "", accountName, fullName));
	mp.put("Named_Insured", if(accountName != "", accountName, fullName));
	mp.put("Status", "New");
	mp.put("Priority", "Standard");
	mp.put("Request_Type", "Other");
	mp.put("Open_Date", zoho.currenttime);
	mp.put("Last_Activity", zoho.currenttime);
	acct = zoho.crm.getRecordById("Accounts", accountId.toLong());
	if(acct != null && acct.get("Owner") != null)
	{{
		mp.put("Owner", acct.get("Owner").get("id"));
	}}
	resp = zoho.crm.create("{module_api_name}", mp);
	rid = ifnull(resp.get("id"), "");
	if(rid == "")
	{{
		details = ifnull(resp.get("details"), resp);
		rid = ifnull(details.get("id"), "");
	}}
	if(rid == "")
	{{
		return "Could not create {MODULE_PLURAL} record: " + resp.toString();
	}}
	openUrl("/crm/tab/{module_api_name}/" + rid, "same window");
	return rid;
}}
'''


def render_policy_button(*, module_api_name: str = MODULE_API_NAME) -> str:
    return f'''string Service_This_Policy(policyId)
{{
	pol = zoho.crm.getRecordById("Policies", policyId.toLong());
	if(pol == null)
	{{
		return "Could not load Policy " + policyId.toString();
	}}
	account = pol.get("Account_Name");
	accountId = "";
	accountName = "";
	if(account != null)
	{{
		accountId = ifnull(account.get("id"), "");
		accountName = ifnull(account.get("name"), "");
	}}
	if(accountId == "")
	{{
		return "This Policy has no Account. Service Requests require an Account.";
	}}
	policyNumber = ifnull(pol.get("Policy_Number"), "");
	carrier = ifnull(pol.get("Carrier"), "");
	lob = ifnull(pol.get("Line_of_Business"), ifnull(pol.get("Line_Of_Business"), ""));
	effective = pol.get("Effective_Date");
	subject = "Service this policy";
	if(policyNumber != "")
	{{
		subject = subject + " — " + policyNumber;
	}}
	else
	{{
		subject = subject + " — " + accountName;
	}}
	mp = Map();
	mp.put("Name", subject);
	mp.put("Subject", subject);
	mp.put("Account_Name", accountId.toLong());
	mp.put("Policy", policyId.toLong());
	mp.put("Policy_Number", policyNumber);
	mp.put("Carrier", carrier);
	mp.put("Line_Of_Business", lob);
	mp.put("Client_Name", accountName);
	mp.put("Named_Insured", accountName);
	mp.put("Status", "New");
	mp.put("Priority", "Standard");
	mp.put("Request_Type", "Policy Change");
	mp.put("Open_Date", zoho.currenttime);
	mp.put("Last_Activity", zoho.currenttime);
	if(effective != null)
	{{
		mp.put("Effective_Date", effective);
	}}
	acct = zoho.crm.getRecordById("Accounts", accountId.toLong());
	if(acct != null && acct.get("Owner") != null)
	{{
		mp.put("Owner", acct.get("Owner").get("id"));
	}}
	resp = zoho.crm.create("{module_api_name}", mp);
	rid = ifnull(resp.get("id"), "");
	if(rid == "")
	{{
		details = ifnull(resp.get("details"), resp);
		rid = ifnull(details.get("id"), "");
	}}
	if(rid == "")
	{{
		return "Could not create {MODULE_PLURAL} record: " + resp.toString();
	}}
	openUrl("/crm/tab/{module_api_name}/" + rid, "same window");
	return rid;
}}
'''


def render_email_button(*, module_api_name: str = MODULE_API_NAME) -> str:
    """Emails detail view → Create Service Request.

    Prefills Subject, Description from the body, Account/Contact when the
    email is related, Policy_Number via a simple regex, Request_Type via
    keyword hints (same list as suggest_request_type).
    """
    return f'''string Create_Service_Request_From_Email(emailId)
{{
	mail = zoho.crm.getRecordById("Emails", emailId.toLong());
	if(mail == null)
	{{
		return "Could not load Email " + emailId.toString();
	}}
	subject = ifnull(mail.get("Subject"), "Service request from email");
	body = ifnull(mail.get("Description"), ifnull(mail.get("Content"), ""));
	accountId = "";
	contactId = "";
	parent = mail.get("Parent_Id");
	seModule = ifnull(mail.get("se_module"), "");
	if(parent != null)
	{{
		parentId = ifnull(parent.get("id"), parent.toString());
		if(seModule == "Accounts")
		{{
			accountId = parentId;
		}}
		if(seModule == "Contacts")
		{{
			contactId = parentId;
		}}
	}}
	if(accountId == "" && mail.get("Account_Name") != null)
	{{
		accountId = ifnull(mail.get("Account_Name").get("id"), "");
	}}
	if(contactId == "" && mail.get("Contact_Name") != null)
	{{
		contactId = ifnull(mail.get("Contact_Name").get("id"), "");
	}}
	if(accountId == "" && contactId != "")
	{{
		con = zoho.crm.getRecordById("Contacts", contactId.toLong());
		if(con != null && con.get("Account_Name") != null)
		{{
			accountId = ifnull(con.get("Account_Name").get("id"), "");
		}}
	}}
	if(accountId == "")
	{{
		return "Could not resolve an Account from this email. Open the Account and use New Service Request.";
	}}
	acct = zoho.crm.getRecordById("Accounts", accountId.toLong());
	accountName = ifnull(acct.get("Account_Name"), "");
	blob = (subject + " " + body).lower();
	requestType = "Other";
	if(blob.contains("certificate") || blob.contains("coi"))
	{{
		requestType = "Certificate Request";
	}}
	else if(blob.contains("id card"))
	{{
		requestType = "ID Card Request";
	}}
	else if(blob.contains("mortgagee") || blob.contains("lienholder"))
	{{
		requestType = "Mortgagee Change";
	}}
	else if(blob.contains("reinstate"))
	{{
		requestType = "Reinstatement";
	}}
	else if(blob.contains("cancel"))
	{{
		requestType = "Cancellation";
	}}
	else if(blob.contains("renewal"))
	{{
		requestType = "Renewal Service";
	}}
	else if(blob.contains("add vehicle"))
	{{
		requestType = "Add Vehicle";
	}}
	else if(blob.contains("remove vehicle"))
	{{
		requestType = "Remove Vehicle";
	}}
	else if(blob.contains("add driver"))
	{{
		requestType = "Add Driver";
	}}
	else if(blob.contains("remove driver"))
	{{
		requestType = "Remove Driver";
	}}
	else if(blob.contains("billing") || blob.contains("invoice"))
	{{
		requestType = "Billing Question";
	}}
	else if(blob.contains("claim"))
	{{
		requestType = "Claims Question";
	}}
	else if(blob.contains("document") || blob.contains("dec page"))
	{{
		requestType = "Document Request";
	}}
	else if(blob.contains("endorsement") || blob.contains("policy change"))
	{{
		requestType = "Policy Change";
	}}
	policyNumber = "";
	matches = subject.match("[A-Za-z0-9]{{2,}}-[A-Za-z0-9-]{{3,}}");
	if(matches != null && matches.size() > 0)
	{{
		policyNumber = matches.get(0);
	}}
	mp = Map();
	mp.put("Name", subject);
	mp.put("Subject", subject);
	mp.put("Description", body);
	mp.put("Account_Name", accountId.toLong());
	mp.put("Client_Name", accountName);
	mp.put("Named_Insured", accountName);
	mp.put("Status", "New");
	mp.put("Priority", "Standard");
	mp.put("Request_Type", requestType);
	mp.put("Open_Date", zoho.currenttime);
	mp.put("Last_Activity", zoho.currenttime);
	if(contactId != "")
	{{
		mp.put("Contact_Name", contactId.toLong());
	}}
	if(policyNumber != "")
	{{
		mp.put("Policy_Number", policyNumber);
	}}
	if(acct != null && acct.get("Owner") != null)
	{{
		mp.put("Owner", acct.get("Owner").get("id"));
	}}
	resp = zoho.crm.create("{module_api_name}", mp);
	rid = ifnull(resp.get("id"), "");
	if(rid == "")
	{{
		details = ifnull(resp.get("details"), resp);
		rid = ifnull(details.get("id"), "");
	}}
	if(rid == "")
	{{
		return "Could not create {MODULE_PLURAL} record: " + resp.toString();
	}}
	openUrl("/crm/tab/{module_api_name}/" + rid, "same window");
	return rid;
}}
'''


def render_workflow_on_create() -> str:
    return '''void Service_Request_On_Create(srId)
{
	rec = zoho.crm.getRecordById("Service_Requests", srId.toLong());
	if(rec == null)
	{
		return;
	}
	mp = Map();
	status = ifnull(rec.get("Status"), "");
	if(status == "")
	{
		mp.put("Status", "New");
	}
	if(rec.get("Open_Date") == null)
	{
		mp.put("Open_Date", zoho.currenttime);
	}
	mp.put("Last_Activity", zoho.currenttime);
	if(rec.get("Owner") == null && rec.get("Account_Name") != null)
	{
		acctId = rec.get("Account_Name").get("id");
		acct = zoho.crm.getRecordById("Accounts", acctId.toLong());
		if(acct != null && acct.get("Owner") != null)
		{
			mp.put("Owner", acct.get("Owner").get("id"));
		}
	}
	if(mp.size() > 0)
	{
		zoho.crm.updateRecord("Service_Requests", srId.toLong(), mp);
	}
}
'''


def render_workflow_on_complete() -> str:
    return '''void Service_Request_On_Complete(srId)
{
	rec = zoho.crm.getRecordById("Service_Requests", srId.toLong());
	if(rec == null)
	{
		return;
	}
	status = ifnull(rec.get("Status"), "");
	if(status != "Completed")
	{
		return;
	}
	mp = Map();
	if(rec.get("Closed_Date") == null)
	{
		mp.put("Closed_Date", zoho.currenttime);
	}
	mp.put("Last_Activity", zoho.currenttime);
	zoho.crm.updateRecord("Service_Requests", srId.toLong(), mp);
}
'''


def all_deluge(*, module_api_name: str = MODULE_API_NAME) -> str:
    parts = [
        "// Zoho CRM Service_Requests buttons + workflows.",
        "// Do not paste these into Zoho Desk. Do not create Desk tickets.",
        f"// Target module: {module_api_name}",
        "",
        render_account_button(module_api_name=module_api_name),
        render_contact_button(module_api_name=module_api_name),
        render_policy_button(module_api_name=module_api_name),
        render_email_button(module_api_name=module_api_name),
        render_workflow_on_create(),
        render_workflow_on_complete(),
    ]
    return "\n".join(parts)


def button_catalog(*, module_api_name: str = MODULE_API_NAME) -> list[dict[str, str]]:
    return [
        {
            "module": "Accounts",
            "name": ACCOUNT_BUTTON,
            "function": "New_Service_Request_From_Account",
            "argument": "accountId",
            "deluge": render_account_button(module_api_name=module_api_name),
        },
        {
            "module": "Contacts",
            "name": CONTACT_BUTTON,
            "function": "New_Service_Request_From_Contact",
            "argument": "contactId",
            "deluge": render_contact_button(module_api_name=module_api_name),
        },
        {
            "module": "Policies",
            "name": POLICY_BUTTON,
            "function": "Service_This_Policy",
            "argument": "policyId",
            "deluge": render_policy_button(module_api_name=module_api_name),
        },
        {
            "module": "Emails",
            "name": EMAIL_BUTTON,
            "function": "Create_Service_Request_From_Email",
            "argument": "emailId",
            "deluge": render_email_button(module_api_name=module_api_name),
        },
    ]


# Keep unused imports referenced for tests that assert the vocab is wired.
_ = (REQUEST_TYPES, PRIORITIES, suggest_request_type, CONNECTION_NAME)
