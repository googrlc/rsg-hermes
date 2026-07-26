---
name: coi-processing
description: Certificate of Insurance processing playbook for Gretchen — COI SOP, NowCerts steps, additional insured wording rules, waiver of subrogation workflow, certificate holder checklist, compliance warning language, and turnaround expectations. The only skill that should reference NowCerts regularly. Use whenever a COI request comes in or Gretchen asks about certificate processing.
---

# COI Processing

The Certificate of Insurance playbook. NowCerts is the system for COIs
until the data is cleaned up. This is the only workflow where NowCerts
is the primary tool.

## When to use

- A COI request comes in from a client or contractor.
- Gretchen asks "what do I need for this COI?"
- A request includes additional insured or waiver of subrogation
  language.
- You need to check if special wording is required and whether the
  policy supports it.

## What to collect for every COI request

1. Named insured (must match the policy exactly)
2. Policy number (if available — look up in the CRM via find_account or
   lookup)
3. Certificate holder name
4. Certificate holder address
5. Email or fax delivery instructions
6. Job or project description
7. Additional insured requirement? (yes/no, and the specific wording)
8. Waiver of subrogation requirement? (yes/no)
9. Primary and noncontributory wording? (yes/no)
10. Special wording requested? (quote it exactly)
11. Required forms or endorsements (e.g., CG 2037, CG 2038)
12. Due date
13. Is carrier approval needed for the special wording?

## Missing information checklist

If any of the above is missing, draft a response to the client asking
for it. Never guess at holder names, addresses, or special wording.

Common gaps:
- Holder address (often just a name is given)
- Specific additional insured wording (client says "they need AI" but
  does not provide the contract language)
- Whether the policy form actually supports the requested endorsement

## COI processing steps in NowCerts

1. Open NowCerts and search for the insured.
2. If found, open the policy record.
3. Navigate to the certificate section.
4. Enter the certificate holder information.
5. Add any additional insured or waiver of subrogation endorsements.
6. Generate the certificate draft.
7. Review for accuracy — names, addresses, policy numbers, dates.
8. If special wording is required, verify the policy form supports it
   before issuing.
9. Save and deliver to the client (Gretchen reviews and sends).

Note: Policy data in the CRM may have amsLockState = Synced, meaning it
was synced from NowCerts and is locked from manual edits. The CRM Policy
record is for reference — the actual COI is processed in NowCerts.

## Additional insured wording rules

- Additional insured status must be supported by the policy form or a
  specific endorsement already on the policy.
- Common forms: CG 2037 (scheduled), CG 2038 (blanket).
- If the policy has blanket AI, check if the blanket language covers the
  requested holder type (some blankets exclude certain operations).
- If the policy does not have AI coverage, the endorsement must be
  requested from the carrier — this is not instant.

## Waiver of subrogation

- Must be supported by the policy form or endorsement.
- Common form: CG 2404.
- If the policy does not have a waiver of subrogation endorsement, it
  must be requested from the carrier.
- Workers comp waivers require specific state compliance — check the
  state before promising.

## Primary and noncontributory

- Requested when the holder wants the insured's policy to respond first.
- Must be supported by the policy form or endorsement.
- If not on the policy, request from the carrier.

## Compliance warning language

- Do not approve special wording unless confirmed by the policy form or
  carrier approval.
- If the requested wording is not supported, tell Gretchen and draft a
  response to the client explaining what is available and what needs
  carrier approval.
- Never issue a certificate with wording that is not backed by the
  policy form.

## Turnaround expectations

- Standard COI (no special wording): same business day if received
  before 2 PM, next business day otherwise.
- COI with additional insured or waiver: 1-2 business days (carrier
  approval may be needed).
- COI requiring new endorsement: 3-5 business days (carrier dependent).

## the CRM activity note for COI

```
Date:
Client:
Line of Business: COI
Request Type: Certificate of Insurance
Certificate Holder:
Special Wording:
Missing Information:
Action Taken:
Next Step:
Due Date:
```

The note goes through the CRM write queue — Hermes will ask for an
approval token before writing.

## Contractor COI request email template

Hi [Client],

I can get that certificate of insurance processed for you. To make sure
it is accurate, I need a few details:

1. Certificate holder name and address
2. Any special wording required (additional insured, waiver of
   subrogation, primary and noncontributory)
3. The job or project name
4. When you need it by

Once I have that, I will get it turned around for you.

Thanks,
Gretchen
