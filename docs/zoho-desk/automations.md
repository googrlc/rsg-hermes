# Desk automations (AUT-01 … AUT-14)

These are configuration proposals. Native Desk workflows should implement them;
Hermes `hermes.desk.routing.apply_event` encodes the same decisions for tests
and future API jobs. Prefer native workflows. Use custom functions only when
conditional or cross-system logic cannot be managed cleanly with standard rules.

## AUT-01 New ticket classification

Trigger: Ticket created. Any channel.

- Set status to **New**
- Stamp received date/time and source
- Attempt to associate contact and account
- Assign **Service Intake** when classification is uncertain
- Internal notification if no account match exists

## AUT-02 Certificate routing

Trigger: Ticket created or category changed. Category = Certificate Request.

- Apply Certificate layout
- Assign Certificates team
- Set priority from required-by date and business impact
- Send certificate intake acknowledgement
- Create a follow-up task if required fields are missing

## AUT-03 Auto and driver routing

Category = Policy Change and subtype is vehicle or driver.

- Apply Auto or Driver Change layout
- Route personal vs commercial line (Personal Lines Service vs Commercial Auto Service)
- Require requested effective date
- Require VIN or driver fields based on transaction type
- Prevent **Ready for Processing** until required information is present

## AUT-04 Cancellation warning

Category = Billing or Cancellation and cancellation warning = Yes.

- Set priority to High or Urgent according to the recorded deadline
- Assign Billing and Retention
- Notify case owner and designated escalation recipient
- Create follow-up before the recorded cancellation date
- Require a documented disposition before resolution

## AUT-05 Waiting on client

Trigger: Status changes to Waiting on Client.

- Require Missing Information
- Require Next Follow-Up Date
- Send missing-information template
- Create follow-up task
- Escalate if the deadline passes without response

## AUT-06 Waiting on carrier

Trigger: Status changes to Waiting on Carrier.

- Require carrier and last-contact date
- Require next follow-up date
- Create follow-up task
- Notify owner when follow-up becomes due
- Escalate aging items based on priority

## AUT-07 Client reply received

Trigger: Customer replies. Status = Waiting on Client.

- Change status to Ready for Processing
- Reassign to previous case owner
- Clear waiting reason
- Notify owner

## AUT-08 Carrier response received

Trigger: Carrier reply or internal update. Status = Waiting on Carrier.

- Change status to In Progress
- Notify owner
- Cancel the obsolete carrier follow-up task where supported

## AUT-09 Required-by reminder

Trigger: Scheduled rule. Required-by date approaching and status not Resolved or Closed.

- Notify owner
- Increase priority at agency thresholds (3 days → High; overdue → escalate)
- Escalate overdue tickets to the service lead

## AUT-10 Closure control

Trigger: Status changes to Resolved or Closed.

Required checks:

- Resolution type completed
- Final customer communication recorded (Closed)
- AMS activity posted
- Final documents stored
- No outstanding task
- No pending carrier or client requirement
- Cancellation-warning cases have a documented disposition

If a required check fails, move the ticket back to **In Progress**.

## AUT-11 Reopened ticket

Trigger: Client replies after resolution.

- Reopen the ticket
- Restore prior owner
- Require reopened reason
- Increment reopen count
- Notify owner

## AUT-12 Duplicate detection

Check open tickets for same contact, same policy number, same request category,
similar subject, and recent creation.

**Flag a possible duplicate for review. Never automatically delete it.**

## AUT-13 CRM handoff

Ticket expresses interest in new coverage, cross-sell, additional location, or
new entity.

- Flag Sales Opportunity = Yes
- Create or update the corresponding CRM opportunity through the approved integration
- Store CRM record ID
- Assign internal sales follow-up
- Keep the original service ticket open until the service issue is addressed

## AUT-14 AMS posting

Trigger: Ticket reaches Ready for Delivery or Resolved.

- Send structured activity data to the Momentum integration (ticket number,
  contact, policy, category, summary, owner, disposition)
- Mark AMS Activity Posted = Yes **only after a successful response**
- Route failures into the Integration Exceptions view
