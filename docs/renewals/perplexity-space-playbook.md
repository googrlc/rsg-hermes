# RSG Renewals — Perplexity Space Operating Playbook

**For: Gretchen (Personal Lines Specialist) + Perplexity Computer**
**Owner: Lamar Coates · v1.0 · 2026-07-10**

> Paste this into the Perplexity Space **below the AMS/CRM Access Contract**. It
> sits *under* that contract and never overrides it. Where this playbook and the
> contract disagree, the contract wins.

---

## 0. The one rule that governs everything here

**In this Space you READ and DRAFT. You never WRITE to NowCerts, Momentum, or
EspoCRM. Hermes does the writing.**

- Reading is allowed — through the Hermes MCP read tools, or (per Amendment A‑1)
  by viewing the live web apps read-only with Perplexity Computer.
- Every actual change — creating a renewal, updating a task, logging a call,
  touching a policy or premium — is done by **Hermes**, the one sanctioned write
  door. You *ask Hermes*; you don't do it yourself.

Think of it like this:

```
YOU (Perplexity / Gretchen)          HERMES
────────────────────────────         ─────────────────────────
look up who's renewing               makes the changes in NowCerts/Espo
pull the policy facts                creates the renewal + the task
draft the email / call script        logs that you called the client
tell Hermes what you need   ───────► executes it (queued, approved, safe)
```

---

## 1. What you can do here (allowed)

- **See who's up for renewal** and when, with premium and carrier.
- **Read policy facts** for a client (coverage, expiration, prior premium).
- **Prep a renewal packet** — gather what's needed to work the renewal.
- **Draft outreach** — a plain-English email or a call script for the client.
- **Hand the actual work to Hermes** in a clean, copy-paste request.

## 2. What you must NOT do here (forbidden — send it to Hermes instead)

- ❌ Click **Save/Submit/Update** on any NowCerts, Momentum, or EspoCRM screen.
- ❌ Edit a field, change a premium, or touch a policy — ever, on any screen.
- ❌ Create a task, opportunity, or note directly in the system.
- ❌ Send an email or text to a client *through the system's send button*.
- ❌ Log in with a different account, use a different tool, or "open the API."

If a step would change data, **stop and ask Hermes**. Reading is fine; changing is Hermes.

---

## 3. How to work a renewal (the routine)

### Step A — See what's up
Ask for the renewal list (Perplexity will read it via Hermes' tools or by viewing
the app read-only):

> "Show me personal-lines renewals coming up in the next 30 days — client, renewal
> date, premium, carrier. Newest expirations first."

Buckets to sort by (from RSG's renewal rules):
- **0–14 days = do now** · **15–30 = this week** · **31–60 = get ahead** · **61–90 = on the radar**
- Personal lines starts at **30 days out**, commercial at **60 days out**.

### Step B — Prep the packet (per client)
> "For {client}, pull their policy facts: coverage, expiration, current premium,
> and last year's premium if you can see it. Flag if the premium went up more than 10%."

### Step C — Draft the outreach (plain English, no jargon)
> "Draft a friendly renewal email to {client} — their {auto/home} policy renews on
> {date}. Keep it plain, no insurance-speak. If the premium went up, acknowledge it
> and offer to review options."

Review and tweak the draft yourself. **Don't send it from the system.** If you want
it sent officially/logged, that's a Hermes job (Section 4).

### Step D — Hand the work to Hermes
When something needs to actually happen in the systems, tell Hermes (Section 4).

---

## 4. Telling Hermes what to do (the hand-off)

You tell Hermes directly. Keep it short and specific. Use this shape so Hermes
never has to guess which client or what you want:

```
@Hermes RENEWAL ACTION
Client: {client name — or policy number if you have it}
Do:
  - {plain instruction}
  - {plain instruction}
Notes: {anything Hermes should know}
```

**Examples**

```
@Hermes RENEWAL ACTION
Client: Smith (personal auto)
Do:
  - Set up the renewal in the system
  - Log that I called them today and left a voicemail
Notes: Renews May 3. I'll try them again Thursday.
```

```
@Hermes RENEWAL ACTION
Client: Alvarez Landscaping
Do:
  - Create a follow-up task for me to send the renewal quote by Friday
Notes: They asked for a higher liability limit — quote both options.
```

**What Hermes does with it:** finds the right client (asks you if it's unclear),
makes the additive changes, and messages you back in plain English when it's done.
For anything involving **premium, a policy change, filling in a blank system field,
or actually sending something to the client**, Hermes will **ask you (or Lamar) to
confirm first** — that's by design.

**If Hermes replies asking a question,** answer it in the same thread. If Hermes
says a tool is down, it will stop and flag it — it will **not** work around the
system. Don't try to do the write yourself; wait for Hermes.

---

## 5. Tone (for drafts Perplexity writes for Gretchen)

- Plain English. No "bind," "endorsement," "dec page," "COI" — spell it out.
- Start actions with a verb: Call, Email, Send, Follow up, Check.
- Use the client's first name. Include a phone/email if you have it.
- No system field names, no record IDs, no carrier codes in anything Gretchen-facing.

---

## 6. Quick reference

| I want to… | Do it here? | How |
|---|---|---|
| See upcoming renewals | ✅ Read | Ask Perplexity for the list |
| Read a client's policy facts | ✅ Read | Ask Perplexity |
| Draft a renewal email / script | ✅ Draft | Ask Perplexity, then edit |
| Create the renewal / task in the system | ❌ → Hermes | `@Hermes RENEWAL ACTION …` |
| Log a call / note | ❌ → Hermes | `@Hermes RENEWAL ACTION …` |
| Change a premium or policy | ❌ → Hermes (needs approval) | `@Hermes RENEWAL ACTION …` |
| Send the email officially | ❌ → Hermes (needs approval) | `@Hermes RENEWAL ACTION …` |

**Golden rule:** *If it changes data, it's Hermes. If it only shows data, it's you.*
