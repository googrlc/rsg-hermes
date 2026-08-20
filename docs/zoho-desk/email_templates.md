# Desk email templates

Create at least these reusable templates. Each template must include:

- Ticket number
- Client name
- Policy number when appropriate
- Brief summary
- Exact action needed
- Deadline
- Assigned case owner
- Reply instructions

Tokens: `ticket_number`, `client_name`, `policy_number`, `summary`,
`action_needed`, `deadline`, `case_owner`, `reply_instructions`.

| Template | When |
|---|---|
| Case received | AUT-01 acknowledgement |
| Missing information | AUT-05 |
| Certificate request received | AUT-02 |
| Certificate completed | Certificate Blueprint Send / Confirm |
| Policy change submitted | Policy-change Submit to Carrier |
| Carrier follow-up | AUT-06 |
| Change completed | Policy-change delivery |
| Billing issue acknowledged | Billing intake |
| Cancellation warning outreach | AUT-04 |
| Reinstatement pending | Billing monitoring |
| Claim information received | Claims intake |
| Adjuster details provided | Claims follow-up |
| Renewal information request | Renewal 90/60-day stage |
| Renewal reminder | Renewal 30-day stage |
| Case resolved | AUT-10 |
| Unable to complete request | Resolution type = Unable to complete |
| Client approval needed | Insured approval / special wording |
| Secure-document upload instructions | Sensitive documents — never ask for license/SSN in email body |

Keep carrier credentials, agency codes, and internal escalation paths out of
client-facing templates. Those belong in the **internal** knowledge base.
