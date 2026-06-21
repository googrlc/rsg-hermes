---
name: renewal-playbook
description: Renewal workflow playbook for Gretchen — 90/60/30 day process, Project 85 workflow, renewal email templates, retention scripts, remarketing checklist, premium increase explanations, and cross-sell checklist. Complements renewal-review (which triages) with the process and templates. Use when Gretchen needs the full renewal workflow, templates, or scripts.
---

# Renewal Playbook

The full renewal process — from 90 days out through bind. This
complements the `renewal-review` skill (which triages and recommends)
with the step-by-step workflow, templates, and scripts Gretchen uses.

## When to use

- "What is the 90/60/30 renewal workflow?"
- "Draft a renewal email for this client."
- "How do I explain a premium increase?"
- "What is the remarketing checklist?"
- "Create a 30-day renewal follow-up message."
- Pair with `renewal-review` when you need the triage and recommendation.

## The 90/60/30 workflow

### 90 days out (commercial) / 60 days out (personal)

1. Pull the renewal list from EspoCRM (renewals due in 90 days).
2. For each renewal, identify: client, line of business, current carrier,
   current premium, expiration date.
3. Create an EspoCRM opportunity (if not already created):
   `[Client Name] - [LOB] Renewal - [Year]`
4. Set stage to Identified.
5. Create a task: "Review renewal for [Client]" due in 7 days.

### 60 days out

1. Check if the renewal offer has arrived from the carrier.
2. If yes: compare current premium to renewal premium. Calculate the
   increase percentage.
3. If no: follow up with the carrier.
4. Create a task: "Request renewal offer from [Carrier]" if not received.
5. If the increase is over 10%, flag for review.
6. If the increase is over 20%, flag for remarketing consideration.

### 30 days out

1. Review the renewal offer with the client.
2. Recommend: renew as-is, review with adjustments, or remarket.
3. Draft the client communication (see templates below).
4. Update the opportunity stage: Outreach Sent.
5. Create a task: "Confirm renewal decision with [Client]" due in 5 days.

### 14 days out

1. Confirm the client has decided.
2. If renewing: confirm payment method and bind.
3. If remarketing: quotes should be in process.
4. If no response: escalate to Lamar (retention risk).

### 7 days out

1. Final confirmation.
2. If still no decision: escalate immediately.
3. Update EspoCRM opportunity to Renewed-Won or Lost.

## Renewal email templates

### Standard renewal (no increase)

```
Hi [Client],

Your [Line of Business] policy with [Carrier] is renewing on [Date].
The premium is [Amount], which is the same as last year.

No action is needed on your part — the policy will renew automatically.

Let me know if you have any questions.

Thanks,
Gretchen
```

### Small increase (under 10%)

```
Hi [Client],

Your [Line of Business] policy with [Carrier] is renewing on [Date].
The new premium is [Amount], up from [Old Amount] — an increase of
about [X]%.

This is a normal market adjustment. I have reviewed the coverage and
everything looks good.

If you would like to talk about options to bring it down, I am happy
to look. Otherwise, the policy will renew automatically.

Thanks,
Gretchen
```

### Larger increase (10% or more)

```
Hi [Client],

Your [Line of Business] policy with [Carrier] is renewing on [Date].
The new premium is [Amount], up from [Old Amount] — an increase of
about [X]%.

I want to make sure we look at this together. Here is what I recommend:

1. Let me review the policy for any discounts or adjustments.
2. If the increase is driven by a specific factor, I will explain it.
3. If you would like, I can shop this with other carriers to compare.

Would you like me to go ahead and review, or do you want to look at the
renewal first?

Thanks,
Gretchen
```

## Retention scripts

### When a client mentions shopping

"I completely understand. Before you make any changes, let me get you
a few options so you can compare side by side. That way you know you
are getting the best deal, not just leaving. Can I put that together
for you this week?"

### When a client is unhappy with service

"I hear you, and I am sorry it has been frustrating. Let me fix this
right now — what specifically needs to happen? I will take care of it
today and follow up to make sure it is done."

### When a client is cancelling

"Before you cancel, can we talk about what is driving this? If it is
price, I can shop it. If it is coverage, I can adjust it. If it is
service, I will fix it. I would rather earn your renewal than lose you
over something fixable."

## Premium increase explanations

Common reasons for increases, in plain English:

- **Rebuilding cost up** — "The cost to rebuild your home has gone up,
  so the insured value and premium adjusted with it."
- **Claims surcharge** — "You had a claim last year, and the carrier
  applied a surcharge. It typically falls off after 3 years."
- **Market-wide rate adjustment** — "The carrier filed a rate increase
  with the state that applies to all policies, not just yours."
- **Coverage change** — "We added [coverage] at your last review, which
  increased the premium."
- **Inflation guard** — "Your policy has an automatic inflation
  adjustment that increases the insured value each year."

## Remarketing checklist

1. Pull the current policy declarations page.
2. Identify the coverages and limits.
3. Check carrier appetite (use the `carrier-appetite` skill).
4. Submit to 2-3 alternative carriers.
5. Compare quotes side by side (use the `proposal-builder` skill).
6. Present options to the client.
7. If the client switches, set up the new policy and cancel the old one
   (coordinate effective dates).
8. Update EspoCRM with the new carrier and policy number.

## Cross-sell checklist

After every renewal review, check:

- [ ] Has auto but no home? Offer home quote.
- [ ] Has home but no auto? Offer auto quote.
- [ ] Has auto + home but no umbrella? Offer umbrella quote.
- [ ] Has personal lines but no life? Offer life quote.
- [ ] Commercial client with no commercial auto? Offer commercial auto.
- [ ] Commercial client with no workers comp? Offer workers comp.

If any apply, create a cross-sell opportunity in EspoCRM.
