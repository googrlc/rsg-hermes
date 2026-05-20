---
name: crm-note-structurer
description: Convert raw insurance-related text (transcripts, emails, Slack threads, fact-finders, underwriting summaries, claims notes) into a clean, structured CRM ClientNote body — consistent sections, scannable bullets, separated facts vs assumptions, source attribution, and a one-line headline. Use whenever the user asks for "a note for the file," "write this up for CRM," "summarize this conversation for the account," or when another skill (e.g. crm-intake-writer) needs the `note.body` field populated.
---

# CRM Note Structurer

Produces the canonical CRM ClientNote body. Other skills call this when they
need a well-formed note; humans call it directly to write up a call, email,
or meeting for the file.

## When to use

- "Write a CRM note for this call."
- "Summarize this email thread for the account."
- "Note for the file on the Jarod conversation."
- Anywhere `crm-intake-writer` needs `note.body`.
- Service team logging a request, complaint, or change.
- Producer logging an outcome after a meeting.

Do **not** use this for retrieval, intake payloads, or proposal building —
delegate to the right skill.

## Note types (use one)

| `note_type` | When |
|-------------|------|
| `Underwriting Summary` | New submission / risk profile |
| `Quote Summary` | Quotes received, side-by-side, recommendation |
| `Discovery Call` | Initial fact-finding conversation |
| `Renewal Review` | Renewal triage outcome |
| `Service Request` | Endorsement, change, COI, billing question |
| `Claim Note` | Claim intake or update |
| `Carrier Appetite Note` | Carrier feedback on a risk |
| `Internal Strategy Note` | Producer-only thinking, not client-safe |
| `Email Recap` | Summary of inbound/outbound email |
| `Meeting Summary` | In-person or video meeting |
| `Voicemail / No Contact` | Outreach attempts without response |

## Output shape

Return both the note metadata block and the rendered body. The body is what
goes into Espo `ClientNote.description` (or `note.body` in an intake payload).

```json
{
  "title": "3D Pumps LLC - Underwriting Summary",
  "note_type": "Underwriting Summary",
  "date": "2026-05-19",
  "author": "Lamar Coates",
  "audience": "internal | client_safe",
  "sensitivity": "standard | restricted",
  "tags": ["underwriting", "prospect", "contractor"],
  "linked_entities": {
    "account": "3D Pumps LLC",
    "contacts": ["Jarod Denero Mattison"],
    "opportunities": ["3D Pumps LLC - General Liability - 05/19/2026"]
  },
  "body": "...rendered markdown body..."
}
```

## Body template

Use this skeleton. Omit any section that has no content; never pad with
filler. Keep bullets tight — full sentences only when nuance matters.

```
# {Title}
{ISO date} · {Author} · {Note type}

## Headline
One sentence on what this is and why it matters.

## Account / Contact
- Account: {name} ({entity_type}, {industry})
- Primary contact: {name}, {role}, {phone}, {email}
- Other contacts: {list}

## Lines of Business In Scope
- {LOB} — {status / quote # / carrier if known}

## Key Facts
- {fact} — {source}
- {fact} — {source}

## Assumptions
- {assumption} — flag if it needs validation

## Risk / Underwriting Flags
- {flag} — severity: low|medium|high — why it matters

## Quotes / Premium
| LOB | Carrier | Quote # | Premium | Fees | Total | Effective |
|-----|---------|---------|---------|------|-------|-----------|
| ... | ...     | ...     | ...     | ...  | ...   | ...       |

## Missing Information
- {item} — who should provide it

## Next Actions
- [ ] {action} — owner — due

## Source
- {source link / file name / email subject / message_ts}
```

## Hard rules

1. **Separate facts from assumptions.** A fact has a source. An assumption
   does not. Never blend them.
2. **Cite the source for every fact.** "underwriting summary," "client
   email 2026-05-18," "carrier portal screenshot," etc.
3. **Never invent numbers.** No EINs, premiums, quote numbers, DOBs,
   policy numbers, or limits that aren't in the source.
4. **Restricted data is opt-in.** EIN, DOB, DL #, SSN, banking, beneficiary,
   health info → only include in the body when `audience: "internal"` and
   `sensitivity: "restricted"`. Otherwise reference by label only
   ("EIN on file") and let `crm-fact-retriever` answer when asked.
5. **No therapy language.** No "exciting opportunity," no "great
   conversation," no client-flattering filler. Notes are operational
   artifacts, not marketing copy.
6. **One headline sentence, max.** It's the line scanners read in list
   views.
7. **Markdown only.** No HTML, no emojis (unless the user explicitly asks),
   no images.
8. **Mirror the canonical LOB vocabulary** from
   `hermes-training/espocrm/workflows.md` — don't paraphrase ("Commercial
   Auto" not "company vehicles coverage").

## Companion outputs

If the source contains facts that should be retrievable later, also emit
the corresponding `facts[]` block so `crm-intake-writer` can stage them
into `client_facts`. The note body is for humans; facts are for the
retrieval layer.

## References

- `docs/agency-memory-plan.md` — the agency memory architecture
- `hermes-training/espocrm/workflows.md` — canonical LOB vocabulary
- `crm-intake-writer` — receives `note.body` as part of the unified payload
- `crm-fact-retriever` — consumes `facts[]` produced alongside the note
