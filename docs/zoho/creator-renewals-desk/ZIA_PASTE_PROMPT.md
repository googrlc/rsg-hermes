# Zia prompt — DO NOT PASTE (Creator is an empty stub)

Live product is the **Zoho Catalyst SPA** (project `935150771`, function
`renewals_desk_function`). The Creator app `renewals-desk` in workspace
`lamar_risksolutionsgroup668` is an empty stub. Do **not** create a second
application. Do **not** create a new Creator app. Do **not** duplicate.
Do **not** paste this pack into Creator. Evolve [`catalyst/`](catalyst/) instead.
Creator never calls NowCerts.

# Historical pack (do not fill Creator)

Paste this **in the existing application**, Edit mode, Zia / Build Agent.
Do **not** create a new Creator app. Do **not** duplicate this app.

You are filling Zoho Creator application **Renewals Desk**
(workspace `lamar_risksolutionsgroup668`, link name `renewals-desk`,
development environment). This is Gretchen’s live workstation over Zoho CRM.
Hermes remains the only NowCerts writer. Creator never calls NowCerts.

## Hard rules

1. Work **only** in this existing app. Refuse to create a second application.
2. **Do not** create native Creator forms that duplicate CRM modules
   (no local Policies / Renewals / Renewal_Events / AMS_Write_Queue book).
   CRM is the record store. Creator is UI + Deluge guards.
3. First add **Zoho CRM integrations** (integration forms + reports), modules:
   - Accounts
   - Deals
   - Policies
   - Renewal_Events
   - Renewals
   - AMS_Write_Queue
   - Tasks
4. Then create the pages, reports, and workflows below. Use the Deluge
   **verbatim**. Do not paraphrase picklist values.
5. AMS enqueue payload is structured JSON built by Deluge. Operators never
   type JSON. Required keys: `action`, `renewal_id`, `policy_number`,
   `expected_result`. Optional: `note`, `channel`.
6. Allowed executor actions only: `request_terms`, `prepare_options`,
   `client_follow_up`, `update_ams`. Anything else is refused.
7. Stage never skips. Backward Desk_Stage moves require producer confirmation
   (`Producer_Confirmed`). Closed requires Disposition.
8. Dismiss sets `Dismissed=true`. Never delete a Renewal or Renewal_Event.
9. Users: **Gretchen** = CSR (desk, card, worklist, enqueue). **Lamar** =
   producer (all of that + AMS Approve + backward stage confirmation).
   `Approved_By` = `zoho.loginuserid`, never a shared robot user.
10. Do not invent sample policy numbers, premiums, or carrier quotes.

## Phase 0 — CRM integrations

Add Zoho CRM integration (same org) for the seven modules listed above.
If a module is missing in CRM, stop and name it — do not invent a Creator
stand-in form.

## Phase 1 — Integration reports

# Creator Zia paste pack — DO NOT USE

Live product is the Catalyst Renewals Desk SPA (`935150771` /
`renewals_desk_function`). The Creator app `renewals-desk` in workspace
`lamar_risksolutionsgroup668` is an empty stub. Do **not** create a second
application. Do **not** create a new Creator app. Do **not** duplicate.
Do **not** paste this pack into Creator. Evolve the Catalyst client instead
([`catalyst/README.md`](catalyst/README.md)).

The rest of this file is a historical spec pack. Creator never calls NowCerts.

### Worklist (`worklist`)

- Module: Renewals
- Criteria: Dismissed is false or empty **and** Related_Deal or Deal_Id is not empty
  (desk membership = CRM Renewals-pipeline Deal)
- Columns: Client_Name, Policy_Number, Carrier, Line_of_Business,
  Expiration_Date, Days_To_Expiration, Window_Bucket, Premium_Current,
  Premium_Renewal, Increase_Percent, Risk_Status, Desk_Stage,
  Recommended_Action
- Sort: Expiration_Date ascending
- Quick filters: Window_Bucket, Risk_Status, Desk_Stage, Line_of_Business
- Row click: open Page `Card` with that Renewals record id
- Empty text: "No eligible renewals in this filter. Check Needs verification if the book looks short; check Last_Synced on Policies if last night's upsert did not run."

### Needs verification (`needs-verification`)

- Module: Renewal_Events
- Criteria: Eligibility = needs_verification
- Columns: Client_Name, Policy_Number, Renewal_Event_Date, Eligibility_Reason,
  Normalized_Status, Branch, Segment, NowCerts_Insured_GUID, NowCerts_Policy_GUID
- Sort: Renewal_Event_Date ascending

### AMS pending (`ams-pending`)

- Module: AMS_Write_Queue
- Criteria: Object_Type = renewal AND (Status = needs_approval OR
  (Status = queued AND Approved_By is empty))
- Columns: Name, Related_Renewal, Action, Status, Approved_By, Approved_At,
  Last_Error, Created_Time
- Custom button **Approve** → Deluge `approve.dg` below
- Payload is display-only

### AMS failed (`ams-failed`)

- Module: AMS_Write_Queue
- Criteria: Object_Type = renewal AND Status in (failed, dead)
- Columns: Name, Related_Renewal, Action, Status, Attempt_Count, Last_Error,
  Approved_By
- No retry button. Human re-queues from the Card.

### Open tasks (`open-tasks`)

- Module: Tasks
- Criteria: Subject in the five default titles (see task_seed), related to
  this Account / Renewal
- Columns: Subject, Status, Due_Date, Owner

## Phase 2 — Pages

Create two Pages. Application home = **Desk**.

### Page `Desk` (link `desk`)

Paste this HTML. Replace `{{REPORT_*}}` with the published permalinks of the
reports above after they exist.

```html
<section class="desk">
  <header>
    <h1>Renewals Desk</h1>
    <p class="sub">Work the book. Hermes writes the AMS. Gretchen talks to the client.</p>
  </header>
  <div class="kpi-strip" role="navigation">
    <a class="kpi" href="{{REPORT_WORKLIST}}?Window_Bucket=90"><span class="label">90 days</span><span class="value" data-bucket="90">—</span></a>
    <a class="kpi" href="{{REPORT_WORKLIST}}?Window_Bucket=60"><span class="label">60 days</span><span class="value" data-bucket="60">—</span></a>
    <a class="kpi" href="{{REPORT_WORKLIST}}?Window_Bucket=30"><span class="label">30 days</span><span class="value" data-bucket="30">—</span></a>
    <a class="kpi risk" href="{{REPORT_WORKLIST}}?Risk_Status=CRITICAL"><span class="label">CRITICAL</span><span class="value" data-risk="CRITICAL">—</span></a>
    <a class="kpi risk" href="{{REPORT_WORKLIST}}?Risk_Status=AT_RISK"><span class="label">AT_RISK</span><span class="value" data-risk="AT_RISK">—</span></a>
    <a class="kpi risk" href="{{REPORT_WORKLIST}}?Risk_Status=SAFE"><span class="label">SAFE</span><span class="value" data-risk="SAFE">—</span></a>
    <a class="kpi recon" href="{{REPORT_NEEDS_VERIFICATION}}"><span class="label">Needs verification</span><span class="value" data-elig="needs_verification">—</span></a>
    <a class="kpi ams" href="{{REPORT_AMS_PENDING}}"><span class="label">Pending AMS</span><span class="value" data-ams="pending">—</span></a>
    <a class="kpi ams" href="{{REPORT_AMS_FAILED}}"><span class="label">Failed AMS</span><span class="value" data-ams="failed">—</span></a>
  </div>
  <p class="empty-hint">Empty buckets must say why they are empty (no eligible renewals vs sync stale vs the current filter). A blank grid is a lie.</p>
  <div class="worklist">
    <h2>Worklist</h2>
    <p>Default sort: Expiration Date ascending. Hide Dismissed. Hide rows without a Related Deal (desk = Renewals pipeline). Personal rows sit in the personal bucket even inside 30 days.</p>
    <iframe title="Renewals worklist" src="{{REPORT_WORKLIST}}"></iframe>
  </div>
</section>
```

Bind KPI counts with page scripts against those CRM reports. Empty is OK
until `hermes --sync-zoho-renewals` has run.

### Page `Card` (link `card`)

Opened with CRM Renewals record id. Lookups: Policy, Account,
Related_Renewal_Event, Related_Deal.

```html
<article class="card" data-renewal-id="{{RENEWAL_ID}}">
  <header>
    <p class="kicker"><span data-field="Line_of_Business"></span> · <span data-field="Carrier"></span></p>
    <h1 data-field="Client_Name">Client</h1>
    <p class="meta">Policy <strong data-field="Policy_Number"></strong> · x-date <strong data-field="Expiration_Date"></strong> · <span data-field="Days_To_Expiration"></span> days · <span data-field="Window_Bucket"></span> · risk <strong data-field="Risk_Status"></strong></p>
    <p class="money">Current <span data-field="Premium_Current"></span> → renewal <span data-field="Premium_Renewal"></span> (<span data-field="Increase_Percent"></span>%)</p>
  </header>
  <section>
    <h2>Desk</h2>
    <p>Stage never skips. Closed requires Disposition. Producer confirms any backward move.</p>
    <ul>
      <li>Desk Stage — Identified → Outreach Sent → Quote Requested → Proposal Sent → Negotiating → Closed</li>
      <li>Disposition — on Closed only (renewed / rewritten / lost_price / lost_coverage / lost_no_response / do_not_renew)</li>
      <li>Recommended Action — RETAIN_AS_IS / RETAIN_WITH_NEGOTIATION / REMARKET_SAMPLE / REMARKET_FULL / ESCALATE_HUMAN / MOVE_TO_AT_RISK_LIST</li>
      <li>Strategy Notes, Last Contact Date</li>
    </ul>
  </section>
  <section>
    <h2>Related</h2>
    <ul>
      <li>Policy — number, status, effective, carrier, LOB</li>
      <li>Account — name, NowCerts insured GUID</li>
      <li>Renewal Event — eligibility, lineage, segment</li>
      <li>Deal — Renewals pipeline (90 / 60 / 30 day stages)</li>
    </ul>
  </section>
  <section>
    <h2>Tasks</h2>
    <ol>
      <li>Pull renewal declaration &amp; review exposures</li>
      <li>Request renewal terms from carrier</li>
      <li>Prepare renewal options / comparison</li>
      <li>Send renewal review to client</li>
      <li>Update AMS (NowCerts) &amp; file worksheet</li>
    </ol>
    <p>Seeded in the background by task_seed. Gretchen completes checkpoints on the renewal. Creator never emails the client.</p>
  </section>
  <section>
    <h2>AMS actions</h2>
    <p>Each button opens a short form: expected result (required) + note. Deluge writes AMS_Write_Queue. Hermes drains after approval.</p>
    <p>
      <button type="button" data-action="request_terms">Request terms</button>
      <button type="button" data-action="prepare_options">Prepare options</button>
      <button type="button" data-action="client_follow_up">Client follow-up</button>
      <button type="button" data-action="update_ams">Update AMS</button>
    </p>
    <p><button type="button" data-action="dismiss">Dismiss from worklist</button></p>
  </section>
</article>
```

Card AMS buttons collect `expected_result` (required text) and optional note,
then run `ams_enqueue.dg`. Dismiss runs `dismiss.dg`. Card load runs `task_seed.dg`.

Picklists (exact labels, do not retitle):

- Desk_Stage: Identified, Outreach Sent, Quote Requested, Proposal Sent, Negotiating, Closed
- Disposition: renewed, rewritten, lost_price, lost_coverage, lost_no_response, do_not_renew
- Recommended_Action: RETAIN_AS_IS, RETAIN_WITH_NEGOTIATION, REMARKET_SAMPLE, REMARKET_FULL, ESCALATE_HUMAN, MOVE_TO_AT_RISK_LIST
- Window_Bucket: 90, 60, 30, personal, past_due
- Risk_Status: SAFE, AT_RISK, CRITICAL, RENEWED, LAPSED

Correctable fields on the Card (only these): Client_Name, Premium_Current,
Premium_Renewal, Risk_Status, Expiration_Date, Last_Contact_Date, Strategy_Notes.
Policy_Number is the natural key. Increase_Percent is a CRM formula — do not type it.

## Phase 3 — Workflows (Deluge, verbatim)

### 1. Renewals — validate on edit of Desk_Stage (`stage_guard`)

```
deskStages = {"Identified", "Outreach Sent", "Quote Requested", "Proposal Sent", "Negotiating", "Closed"};
here = deskStages.indexOf(ifnull(old_stage, "Identified"));
there = deskStages.indexOf(input.Desk_Stage);
if(there < 0)
{
	return "unknown desk stage";
}
if(there == here)
{
	return null;
}
if(there == here + 1)
{
	if(input.Desk_Stage == "Closed" && (input.Disposition == null || input.Disposition == ""))
	{
		return "Closed requires a Disposition";
	}
	return null;
}
if(there > here + 1)
{
	return "cannot skip desk stages";
}
if(input.Producer_Confirmed != true)
{
	return "moving backward requires producer confirmation";
}
return null;
```

`Producer_Confirmed` is a hidden checkbox the producer sets for backward moves.

### 2. Renewals — on create, and Card page load (`task_seed`)

```
titles = {
	"Pull renewal declaration & review exposures",
	"Request renewal terms from carrier",
	"Prepare renewal options / comparison",
	"Send renewal review to client",
	"Update AMS (NowCerts) & file worksheet"
};
details = {
	"Retrieve the expiring dec page and confirm current exposures on the worksheet.",
	"Request renewal terms from the incumbent carrier (remarket if flagged).",
	"Build the option comparison and premium-change explanation for the client.",
	"Deliver the renewal review and confirm the client's intent.",
	"Stage the approved NowCerts write-back and file the worksheet in the client folder."
};
renewalId = input.get("id");
accountId = ifnull(input.get("Account_Name"), Map()).get("id");
existing = zoho.crm.getRelatedRecords("Tasks", "Renewals", renewalId);
have = List();
for each task in existing
{
	have.add(task.get("Subject"));
}
i = 0;
for each title in titles
{
	if(!have.contains(title))
	{
		taskMap = Map();
		taskMap.put("Subject", title);
		taskMap.put("Description", details.get(i));
		taskMap.put("Status", "Not Started");
		taskMap.put("Priority", "Normal");
		if(accountId != null)
		{
			taskMap.put("What_Id", accountId);
			taskMap.put("$se_module", "Accounts");
		}
		taskMap.put("Renewal_Id", renewalId);
		zoho.crm.createRecord("Tasks", taskMap);
	}
	i = i + 1;
}
```

### 3. Card custom buttons Request terms / Prepare options / Follow up / Update AMS (`ams_enqueue`)

Stateless / button form fields: `action`, `expected_result` (required), `note` (optional).

```
allowed = {"request_terms", "prepare_options", "client_follow_up", "update_ams"};
action = input.action;
if(!allowed.contains(action))
{
	return "unknown renewal action";
}
expected = trim(ifnull(input.expected_result, ""));
if(expected == "")
{
	return "expected_result is required";
}
renewalId = input.get("id");
policyNumber = input.get("Policy_Number");
hermesId = input.get("Hermes_Renewal_ID");
payload = Map();
payload.put("action", action);
payload.put("renewal_id", hermesId);
payload.put("policy_number", policyNumber);
payload.put("expected_result", expected);
payload.put("channel", "task");
if(input.note != null && input.note != "")
{
	payload.put("note", input.note);
}
queueAction = if(action == "update_ams", "update", "create");
row = Map();
row.put("Name", policyNumber + " " + action);
row.put("Object_Type", "renewal");
row.put("Object_ID", policyNumber);
row.put("Destination", "nowcerts");
row.put("Action", queueAction);
row.put("Payload", payload.toString());
row.put("Status", "needs_approval");
row.put("Attempt_Count", 0);
row.put("Related_Renewal", renewalId);
resp = zoho.crm.createRecord("AMS_Write_Queue", row);
return resp;
```

### 4. Card custom button Dismiss (`dismiss`)

```
if(input.get("Policy_Number") == null || input.get("Policy_Number") == "")
{
	return "policy_number is the dismiss key; refuse if missing";
}
upd = Map();
upd.put("Dismissed", true);
zoho.crm.updateRecord("Renewals", input.get("id"), upd);
eventId = ifnull(input.get("Related_Renewal_Event"), Map()).get("id");
if(eventId != null)
{
	ev = Map();
	ev.put("Eligibility", "excluded");
	zoho.crm.updateRecord("Renewal_Events", eventId, ev);
}
return "dismissed";
```

### 5. Renewals — on create/edit of Expiration_Date or Line_of_Business (`window_bucket`)

```
exp = input.Expiration_Date;
lob = ifnull(input.Line_of_Business, "");
if(exp == null)
{
	input.Window_Bucket = null;
	return null;
}
today = zoho.currentdate;
days = daysBetween(today, exp);
personal = (lob.containsIgnoreCase("personal auto") || lob.containsIgnoreCase("homeowner") || lob.containsIgnoreCase("dwelling fire") || lob.containsIgnoreCase("motorcycle") || lob.containsIgnoreCase("personal umbrella") || lob.containsIgnoreCase("condo owners"));
if(days < 0)
{
	input.Window_Bucket = "past_due";
}
else if(personal)
{
	input.Window_Bucket = "personal";
}
else if(days <= 30)
{
	input.Window_Bucket = "30";
}
else if(days <= 60)
{
	input.Window_Bucket = "60";
}
else
{
	input.Window_Bucket = "90";
}
return null;
```

### 6. AMS pending report — custom button Approve (`approve`)

```
if(input.get("Object_Type") != "renewal")
{
	return "approve is only for object_type=renewal";
}
status = ifnull(input.get("Status"), "");
approvedBy = ifnull(input.get("Approved_By"), "");
awaiting = (status == "needs_approval") || (status == "queued" && approvedBy == "");
if(!awaiting)
{
	return "row is not awaiting approval";
}
upd = Map();
upd.put("Approved_By", zoho.loginuserid);
upd.put("Approved_At", zoho.currenttime);
upd.put("Status", "queued");
resp = zoho.crm.updateRecord("AMS_Write_Queue", input.get("id"), upd);
return resp;
```

## Success looks like

- Pages `Desk` and `Card` exist. Desk is the application home.
- Reports: worklist, needs-verification, ams-pending, ams-failed, open-tasks.
- CRM integrations for the seven modules. Zero native duplicate book forms.
- Stage skip Identified → Negotiating is refused. Dismiss never deletes.
- Enqueue writes AMS_Write_Queue `needs_approval` with JSON payload.
- Approve sets Approved_By / Approved_At / Status=queued. No NowCerts call.

Stop after Phase 3. Do not publish to production. Development environment only.
