# Desk Blueprints

Desk distinguishes workflows from Blueprints: workflows automate alerts,
assignments, and field updates when a rule fires; Blueprints enforce the
sequence of states and required actions through resolution.

Executable copies live in `hermes/desk/blueprints.py`.

## Blueprint 1 — Certificate request

```
New
  -> Validate Request
Information Needed
  -> Receive Missing Information
Ready for Processing
  -> Prepare Certificate
Pending Internal Approval
  -> Approve or Return
Ready for Delivery
  -> Send Certificate
Delivered
  -> Confirm and Document
Closed
```

### Transition requirements

**Validate Request**

- Client matched (AMS Client ID)
- Policy matched
- Holder name and address completed
- Required-by date entered
- Special wording identified (Yes or No is an answer)
- Contract attached when special wording or additional interest applies

**Prepare Certificate**

- Coverage verified
- Wording reviewed
- Additional interest requirements reviewed
- No unapproved coverage representation

**Approve or Return**

Required when special wording, unusual holder requirements, or a coverage
exception exists.

**Send Certificate**

- Recipient recorded
- Delivery method recorded
- Final copy attached or linked

## Blueprint 2 — Policy change

```
New
  -> Triage
Information Needed
  -> Complete Intake
Ready for Processing
  -> Submit to Carrier
Waiting on Carrier
  -> Receive Carrier Response
In Progress
  -> Review Change
Ready for Delivery
  -> Deliver Confirmation
Monitoring
  -> Verify Issued Documents
Closed
```

The important feature is the **Monitoring** stage. A carrier acknowledgement
does not necessarily mean the endorsement or corrected policy document has
been issued.

Vehicle and driver subtypes use the Auto or Driver Change layout on this
Blueprint.

## Blueprint 3 — Billing and cancellation

```
New
  -> Assess Deadline
In Progress
  -> Contact Client or Carrier
Waiting on Client / Waiting on Carrier
  -> Receive Response
Pending Internal Approval
  -> Approve Retention Action
Monitoring
  -> Verify Payment or Reinstatement
Resolved
  -> Document Outcome
Closed
```

Cancellation-warning cases cannot resolve without a documented disposition
(AUT-04 / AUT-10).

## Blueprint 4 — Claims assistance

```
New
  -> Verify Claim Information
Information Needed
  -> Complete Claim Intake
In Progress
  -> Coordinate with Carrier
Waiting on Carrier / Waiting on Client
  -> Follow Up
Monitoring
  -> Confirm Service Outcome
Resolved
  -> Post Final Note
Closed
```

Desk coordinates communication and follow-up. The carrier or AMS claim record
remains the authoritative claim record.
