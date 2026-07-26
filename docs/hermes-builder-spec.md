# Hermes Builder Spec (RSG)

Builder-ready implementation reference for Hermes routed specialist workflows.

> **Note:** the Open WebUI front end this spec was written against has been
> retired. The command families below are still the routing contract; the
> surface is now the Command Center (`/command-center/`) plus the `rsg-hermes`
> MCP door.

## Guardrails (required)

1. Never invent CRM fields.
2. Never invent Supabase tables.
3. Never invent carrier appetite/rates/table ratings.
4. Never write to CRM/Supabase/tasks without explicit approval token.
5. Always include source links, timestamps, confidence, missing data list.
6. Always separate facts vs assumptions vs recommendations.
7. Always emit legal/compliance caution where required (title-like checks, Medicare, life underwriting).

## Confirm-before-write execution contract

- Draft phase response must contain:
  - `crm_update_draft`
  - `supabase_update_draft`
  - `write_intent.requires_confirmation=true`
- Approval phase accepts only:
  - `APPROVE CRM ONLY`
  - `APPROVE SUPABASE ONLY`
  - `APPROVE TASKS ONLY`
  - `APPROVE ALL`
  - `REVISE`
  - `CANCEL`
- On `CANCEL`: clear pending draft and return no-op confirmation.
- On `REVISE`: preserve pending context and request revised instructions.

## Workflow definitions

## Property workflow
- Normalize address.
- Resolve county/parcel.
- Pull assessor/tax facts.
- Pull recorder clues (pre-check only).
- Estimate rebuild range with confidence band.
- Return risk flags + missing info + client questions + draft updates.

## Business workflow
- Validate business match.
- Resolve website/social profile.
- Classify NAICS/SIC and probable GL/WC mappings.
- Return risk profile + likely coverage needs + draft CRM update.

## Documents workflow
- Identify document type.
- Extract policy/insured/premium/limits/deductibles/forms/locations/vehicles/drivers.
- Attach per-field confidence and source page references.
- Return missing fields + risk flags + drafts.

## Transcript workflow
- Summarize participants, commitments, deadlines, opportunities, service issues.
- Produce CRM note draft + task drafts + optional follow-up email.

## Medicare workflow
- Query only approved Medicare tables.
- Build checklist and compliance-safe summary.
- Flag required missing demographics and eligibility data.

## Life workflow
- Build preliminary underwriting summary from provided facts and table-backed rules.
- Flag uncertainty and prohibit guaranteed class language.
- Include tentative carrier fit and missing-info checklist.

## Commission workflow
- Match commission rule by carrier/LOB/state/effective context.
- Compute expected agency + producer payout.
- Compare expected vs posted where ledger data exists.
- Flag discrepancy reasons and recommended tasks.

## Command coverage

The system should map the provided command families to these handlers:
- Property research commands -> `property.*`
- Business research commands -> `business.*`
- Document extraction commands -> `documents.*`
- Transcript commands -> `transcripts.*`
- Medicare commands -> `medicare.*`
- Life commands -> `life.*`
- Commission commands -> `commissions.*`
- CRM write/draft commands -> `crm.*`
- Missing-info/checklist commands -> `crm.*` + `supabase.*`
- Risk flag commands -> domain specialist + `underwriting_flags`

## Test matrix

- Routing tests:
  - each command family maps to intended domain handler.
- Guardrail tests:
  - write attempts rejected without approval token.
- Draft tests:
  - response includes explicit draft payloads and approval options.
- Evidence tests:
  - source links and confidence are present when facts are returned.
- Compliance tests:
  - required disclaimers included in property/title-like, Medicare, and life responses.
- Commission tests:
  - expected vs posted variance path and missing-rule path both covered.

