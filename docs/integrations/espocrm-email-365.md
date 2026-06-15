# EspoCRM native email ← Microsoft 365 (Outlook)

Connect Outlook to **EspoCRM's built-in email** so messages appear inside the CRM
and auto-thread onto the matching Contact / Lead / Account records. This is
**open-source core** (EspoCRM 9.3.8) — no paid extension, no license.

> This is a *different* lane from the Hermes triage pipeline. Triage
> ([email-triage-365.md](email-triage-365.md)) reads the mailbox with Microsoft
> Graph **app-only** auth and feeds the intake/approval queue. EspoCRM native
> email uses **delegated** OAuth (IMAP/SMTP as the signed-in user) and threads
> mail onto CRM records. They coexist on the same mailbox — see *Coexistence*.

## RSG specifics (filled in from the live instance)

| Thing | Value |
|---|---|
| EspoCRM version | 9.3.8 (open core) |
| EspoCRM site URL | `https://rrespocrm-rsg-u69864.vm.elestio.app` |
| OAuth redirect URI | `https://rrespocrm-rsg-u69864.vm.elestio.app/oauth-callback.php` (confirm the exact value EspoCRM shows on the Microsoft integration page) |
| Mailboxes to connect | `lamar@risksolutionsgroup.net` and `gretchen@risksolutionsgroup.net` — each a Personal Email Account under that user |
| Current state | no inbound accounts, no outbound SMTP — fresh setup |

## Why a NEW Entra app (you can't reuse `hermes-mail-triage`)

The triage app holds **application** permission `Mail.ReadWrite` (client-credentials,
no user). EspoCRM's email connector uses the **authorization-code** flow with
**delegated** permissions — it acts *as the mailbox owner* who clicks "Connect".
Different grant type ⇒ separate registration.

## Step 1 — Register the Entra app (admin, portal.azure.com)

1. **Entra ID → App registrations → New registration.** Name `espocrm-email`.
   Single tenant.
2. **Authentication → Add a platform → Web.** Redirect URI =
   `https://rrespocrm-rsg-u69864.vm.elestio.app/oauth-callback.php`
   (use the exact URI EspoCRM displays in Step 2 — copy it verbatim).
3. **API permissions → Microsoft Graph → Delegated permissions**, add:
   - `offline_access`, `openid`, `profile`, `email`, `User.Read`
   - `IMAP.AccessAsUser.All`  (read the inbox)
   - `SMTP.Send`             (send from EspoCRM)
   - `Mail.ReadWrite`         (delegated — move/flag; optional)
   Then **Grant admin consent**.
4. **Certificates & secrets → New client secret.** Copy the secret *value*.
5. Collect **Application (client) ID**, **Directory (tenant) ID**, **secret value**.

> Microsoft is retiring IMAP/SMTP **basic auth** — OAuth is the supported path.
> Ensure IMAP & authenticated SMTP are enabled for the mailbox in Exchange admin
> (per-mailbox "Manage email apps").

## Step 2 — Wire the provider in EspoCRM (Administration)

1. **Administration → Integrations → Microsoft** (or **External Accounts / OAuth
   Providers → Microsoft**). Enter **Client ID**, **Client Secret**, **Tenant ID**.
2. Copy the **Redirect URI** EspoCRM shows here back into the Entra app (Step 1.2)
   if it differs from the guess above.

## Step 3 — Create the email account

**Chosen path: a Personal Email Account for each user** —
`lamar@risksolutionsgroup.net` (Lamar) and `gretchen@risksolutionsgroup.net`
(Gretchen). Personal accounts are tied to that user, thread onto the records they
can see, and **each person authorizes their own** (no shared-mailbox setup).

Each user, signed into EspoCRM **as themselves**: top-right avatar →
**Preferences → Email Accounts → Add** (or the **Email** view → gear → **Personal
Email Accounts**):
- Email Address = their own mailbox; **Auth Method = Microsoft / OAuth** →
  **Connect** → sign in + consent **as that mailbox's owner**.
- **Monitored folders:** `INBOX` only (so triage-quarantined noise is skipped —
  see *Coexistence*). Enable **Fetch / import**.

> OAuth here is delegated — Gretchen must click **Connect** while logged in as
> herself; Lamar cannot authorize Gretchen's mailbox for her. Both mailboxes use
> the **same** Step-2 Microsoft provider; no second Entra app is needed.

> Later, if an agency-wide `intake@…` shared mailbox is wanted, add it as a
> **Group Email Account** under *Administration → Inbound Emails* (visible to the
> team, with auto-assignment / auto-create-Lead). Same OAuth provider from Step 2.

## Step 4 — Outbound (send from EspoCRM)

Outbound SMTP is currently unset. On the same account (or **Administration →
Outbound Emails**) set **SMTP via Microsoft OAuth** (`smtp.office365.com:587`,
STARTTLS) using the same connected account, so replies send from the real mailbox.

## Coexistence with Hermes triage (important, and actually helpful)

Both lanes read the same mailbox; that's fine — IMAP read doesn't delete, and
triage tags processed mail with the `Hermes Triaged` category. Because triage
**moves noise to the `Hermes Triage` folder**, EspoCRM (monitoring only `INBOX`)
naturally imports the *actionable* mail and skips the newsletters. Keep EspoCRM
pointed at `INBOX` only — don't have it monitor the quarantine folder.

## Verify

1. From an address that matches an existing Contact, send a test email to the
   mailbox.
2. Within one EspoCRM **inbound-email check cycle**, the message appears under
   **Emails** and is **linked to that Contact's** record (and its Account).
3. Reply from EspoCRM → the Contact receives it from the real mailbox (Step 4).
