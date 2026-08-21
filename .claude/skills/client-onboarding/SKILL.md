---
name: client-onboarding
description: RSG's new-client setup playbook. Use this skill ANY time a client is signed, bound, or needs to be stood up in the system — creating the Hermes account and contacts, opening one opportunity per line of business, flagging cross-sell gaps, building the NextCloud folder structure, drafting the welcome email, and setting first-touch follow-up tasks. Triggers include "set up this new client," "we just bound [client]," "new client onboarding," "create the folders for [client]," "draft the welcome email," "what's left to do on this new account," or any mention of a newly signed or newly bound piece of business. When in doubt, use it — a half-onboarded client is how retention leaks.
---

# Client Onboarding

Everything that has to happen when a client signs, in the order that
protects revenue first and does paperwork second.

**Owner:** Gretchen runs this for personal lines. Lamar runs it for
commercial. Terrance never runs this — he does relationship-only
outreach and gives no coverage advice.

---

## The one-minute version

If you only get through four steps, get through these:

1. **Opportunities per LOB** — every line, its own record, correct premium.
2. **Cross-sell gaps flagged** — this is the money. Do it while you're
   already looking at the LOB list.
3. **Renewal date registered in Walker** — no renewal date, no cadence,
   no retention.
4. **Welcome email sent** — the client should hear from a human within
   24 hours of binding.

Everything else (folders, contacts, tasks) can be caught up the next
morning. These four cannot.

---

## Step 1 — Hermes account

All CRM writes go through the **Hermes write queue**. Hermes will ask
for an approval token before anything is written. There is no other
system; do not go looking for one.

- Search for duplicates **first**. Name, phone, and email.
- Create the account: Personal Household or Commercial Business.
- Fields: `account_type`, `account_status` (Active), `assignedUserName`
  (Gretchen for personal lines, Lamar for commercial), `annual_premium`.

**Premium rule — do not improvise.**
`annual_premium` on the account is the **rolled-up sum of all bound
opportunity amounts**. It is a derived number, not a typed one. The
authoritative premium always lives on the **opportunity**. If the two
disagree, the opportunity wins and the account gets corrected.

This rule exists because bad premium data has already corrupted the
scoreboard once. Do not let it happen twice.

---

## Step 2 — Hermes contacts

- Primary client contact.
- Spouse, business owner, office manager, anyone who will actually call
  us.
- Link each to the account.
- Fields: `emailAddress`, `phoneNumber`, `householdRole`.

For commercial: capture the person who signs and the person who calls.
They are rarely the same human and confusing them costs a renewal.

---

## Step 3 — Opportunities, one per line of business

Naming convention:

```
[Client Name] - [LOB] - New Business - [Year]
```

Examples:

- `Marty Richards - Personal Auto - New Business - 2026`
- `Marty Richards - Home - New Business - 2026`
- `Acme Hauling - Commercial Auto - New Business - 2026`

Fields: `lineOfBusiness`, `amount` (the authoritative premium),
`closeDate`, `assignedUserName`, `effectiveDate`, `expirationDate`.

Stage: **Closed Won** if bound. **Discovery** if still in process.

New business stages, in order, no skipping:

```
Discovery → Quoting → Markets Out / Shopping → Proposal Presented →
Negotiation → Closed Won | Closed Lost
```

### Step 3b — Cross-sell gaps (do this now, not later)

You are already staring at the complete list of what this client has.
That is the single best moment in the entire relationship to notice
what they don't have. Do not defer this.

| They have | They're missing | Open |
|---|---|---|
| Auto | Home | Home cross-sell |
| Home | Umbrella | Umbrella cross-sell |
| Auto + Home | Life | Life cross-sell |
| Commercial Auto | General Liability | GL cross-sell |
| Commercial Auto | Workers Comp | WC cross-sell |
| Any commercial | Umbrella / Excess | Umbrella cross-sell |
| Business owner, personal lines only | Commercial | Commercial review |

For each gap, open an opportunity at stage **Discovery** with a realistic
`amount` estimate and a `closeDate` 30–60 days out. An unrecorded
cross-sell is not a cross-sell — it's a thought you had once.

Commercial cross-sell opportunities route to Lamar regardless of who
onboarded the client.

---

## Step 4 — Register the renewal in Walker

**This is the step that protects the book.** RSG's retention is the
number that matters most, and clients don't churn at renewal — they
churn because nobody talked to them for eleven months and then a
renewal notice showed up.

For each bound policy:

- Register the policy in Walker with its expiration date.
- Confirm the Day-14 / Day-7 / Day-4 follow-up ladder is scheduled.
- Assign a renewal owner explicitly. **Never leave a renewal
  unassigned** — an unowned renewal is a lost renewal with extra steps.

If Walker registration fails or the policy doesn't appear in the
cadence, escalate immediately. Do not mark onboarding complete.

---

## Step 5 — NextCloud folders

NextCloud is the file source of truth. Nothing lives on a desktop.

```
[Agency Documents/]Clients/{Client Name}/
  Intake/
  Quotes/
  Proposals/
  Policies/
  COIs/
  Claims/
  Correspondence/
  Renewal Reviews/
```

Hermes `ensure_client_folders` creates this tree. Document Registry upload
does the same as a side effect — do not pre-build a second Commercial
Lines/{name} tree. One `[category]` folder per type the client actually
uses is enough; empty year/LOB trees are not required.

---

## Step 6 — Welcome email

Send within 24 hours of binding. Fill in every bracket; a template with
a visible `[Carrier]` in it is worse than no email at all.

```
Hi [Client],

Welcome to Risk Solutions Group — glad to have you with us.

Here's what happens next:

1. Your [LOB] coverage is bound with [Carrier], effective [Date].
2. Your renewal date is [Date]. We'll start reviewing it well before
   then — you won't get a surprise.
3. I'm your point of contact for questions, changes, and certificates
   of insurance.
4. Reach me directly at [phone] or [email].

One thing worth mentioning: based on what we set up, [brief cross-sell
observation — e.g., "you don't currently carry umbrella coverage, and
with your auto and home limits it's usually inexpensive to add"]. No
rush, but I'd like to walk you through it when you have ten minutes.

Thanks,
[Name]
Risk Solutions Group
```

That cross-sell line is not filler. It sets up the follow-up call and
costs one sentence.

---

## Step 7 — Follow-up tasks

All task writes go through the Hermes write queue (approval token:
`APPROVE ALL` or `APPROVE TASKS ONLY`).

| Task | Due | Owner |
|---|---|---|
| Welcome call to [Client] | 3 business days | Onboarding owner |
| Confirm policy bind / verify docs received | 5 business days | Onboarding owner |
| Cross-sell conversation: [gap] | 14 days | Gretchen (personal) / Lamar (commercial) |
| 30-day check-in | 30 days | Onboarding owner |
| Renewal owner confirmed in Walker | 45 days | Lamar |

---

## Completion check

Onboarding is **not done** until all of these are true:

- [ ] Account created, no duplicate left behind
- [ ] Every LOB has its own opportunity with correct premium
- [ ] `annual_premium` matches the sum of bound opportunities
- [ ] Cross-sell gaps reviewed and opportunities opened
- [ ] Every bound policy registered in Walker with an assigned owner
- [ ] NextCloud folders created
- [ ] Welcome email sent (not drafted — sent)
- [ ] All five follow-up tasks queued

If any box is unchecked, say which one and why. "Mostly done" is how
clients fall through the floor.

---

## Common failure modes

- **One opportunity for a multi-line client.** Kills LOB reporting and
  hides cross-sell. One record per line, always.
- **Premium typed in two places with two different numbers.** See the
  premium rule in Step 1.
- **Renewal date left blank.** The cadence never fires. This is the
  expensive one.
- **Cross-sell "noted" verbally.** If it isn't an opportunity record,
  it doesn't exist.
- **Welcome email drafted and never sent.** Check the sent folder, not
  the drafts folder.