---
name: quote-rescue
description: Stale quote recovery playbook for Lamar — classification (Healthy/Needs Follow-Up/Stale/Urgent/Dead), stale thresholds, recovery steps, revenue-at-risk calculation, and client follow-up templates. Use when Lamar asks about stale quotes, quote status, or quote recovery. Complements revenue-sentinel (which flags stale opps) with the recovery workflow.
---

# Quote Rescue

The stale quote recovery playbook. Stale quotes are money leaking out
of a tiny hole while everyone debates software architecture. This
skill exists to stop that leak.

## When to use

- "Show my stale quotes."
- "Review this quote and tell me the next action."
- "Mark this quote as stale and create a recovery plan."
- When `revenue-sentinel` flags stale opportunities.
- A quote or opportunity has been sitting with no activity.

## Stale classification

| Status | Trigger |
|---|---|
| **Healthy** | Active within 3 business days, on track, no blockers. |
| **Needs Follow-Up** | Quoted opp with no activity for 3-5 business days, OR waiting on carrier with no update for 5 business days. |
| **Stale** | No activity for 3+ business days (quoted), 5+ business days (submitted to carrier), or 7+ calendar days (waiting on client). |
| **Urgent** | Effective date within 7 days and not yet bound. |
| **Dead / Archive** | No activity for 30+ days, client unresponsive after 3+ follow-ups, or opportunity explicitly lost. Never close without giving a recovery step first. |

## For every quote review, check

- Account name
- Line of business
- Stage (Discovery, Quoting, Markets Out / Shopping, Proposal Presented, Negotiation)
- Estimated premium (amount field on Opportunity)
- Estimated revenue (commission)
- Last activity date
- Quote due date
- Effective date
- Missing information
- Client responsiveness
- Carrier status
- Next follow-up date

## Always output

1. **Status** — Healthy, Needs Follow-Up, Stale, Urgent, or Dead
2. **Reason** — why it is in this status
3. **Revenue at risk** — estimated commission if lost
4. **Next action** — one clear step
5. **Client message** — draft follow-up (if needed)
6. **EspoCRM note** — activity note for the file
7. **Follow-up task** — task with due date
8. **Recommended stage update** — if the stage should change

## Stale thresholds (reference)

- A quoted opportunity (stage: Proposal Presented) with no activity for
  3 business days is stale.
- A submitted-to-carrier opportunity (stage: Quoting or Markets Out) with
  no update for 5 business days needs follow-up.
- A waiting-on-client opportunity (task status: Waiting on Client) with
  no client response for 7 calendar days is stale.
- A quote with an effective date within 7 days is urgent — escalate
  immediately.
- Never close an opportunity without giving a recovery step first.

## Recovery playbook by status

### Needs Follow-Up

1. Identify what is blocking progress (carrier response, client
   response, missing info).
2. Draft a follow-up message to the right party.
3. Create a follow-up task due in 1 business day.
4. Update the opportunity's next follow-up date.

### Stale

1. Check if the client is still responsive (last contact date).
2. Draft a re-engagement message: "I wanted to make sure we didn't let
   this slip — here's where we left off and what I need to move
   forward."
3. Create a recovery task due in 1 business day.
4. If the client responds, re-energize the quote. If no response after
   2 attempts, flag for Dead/Archive consideration.

### Urgent

1. Call the client or carrier immediately — this is revenue at risk.
2. Determine what is needed to bind or decline.
3. If binding: coordinate effective date, payment, and confirmation.
4. If declining: document the reason and update stage to Closed Lost.
5. Escalate to Lamar if the premium is over $10K.

### Dead / Archive

1. Document the reason for closing (client unresponsive, lost to
   competitor, withdrew, etc.).
2. Update the opportunity stage to Closed Lost.
3. Set the reason lost field if available.
4. Create a task to revisit in 90 days (re-marketing opportunity).
5. Never close without at least one recovery attempt logged.

## Quote follow-up email template

```
Hi [Client],

I wanted to follow up on the [Line of Business] quote I sent on [Date].
Here is where we left off:

- [Current status]
- [What is needed to move forward]

Is this still something you want to move forward with? If so, I can have
the updated quote to you by [Date]. If your situation has changed, let
me know — I would rather know now than chase a dead quote.

Thanks,
Lamar
Risk Solutions Group
```
