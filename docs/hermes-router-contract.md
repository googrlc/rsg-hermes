# Hermes Router Contract

This contract defines intent routing and specialist handler interfaces for the RSG expansion.

## Router decision model

1. Parse input mode (`command`, `document_upload`, `transcript_upload`, `approval_token`).
2. Resolve domain (`property`, `business`, `documents`, `transcripts`, `medicare`, `life`, `commissions`, `crm`, `supabase`).
3. Build normalized handler payload.
4. Execute specialist handler.
5. Attach evidence layer output.
6. Return draft update payload(s) with `requires_confirmation=true` when mutating targets are present.

## Unified specialist response envelope

```json
{
  "result_summary": "string",
  "match_confidence": "high|medium|low",
  "key_facts": [
    { "fact": "string", "source": "string", "timestamp": "ISO-8601" }
  ],
  "risk_flags": [
    { "flag": "string", "severity": "low|medium|high", "why_it_matters": "string", "recommended_action": "string" }
  ],
  "missing_information": [
    { "item": "string", "why_needed": "string", "who_should_provide_it": "string" }
  ],
  "recommended_client_questions": ["string"],
  "crm_update_draft": { "entity": "string", "record": "string", "fields": {}, "note": "string", "task": {} },
  "supabase_update_draft": { "table": "string", "record": "string", "fields": {} },
  "write_intent": {
    "requires_confirmation": true,
    "allowed_tokens": [
      "APPROVE CRM ONLY",
      "APPROVE SUPABASE ONLY",
      "APPROVE TASKS ONLY",
      "APPROVE ALL",
      "REVISE",
      "CANCEL"
    ]
  }
}
```

## Specialist function groups

- `property.*` for address/parcel/recorder/rebuild/summary
- `business.*` for lead enrichment and classification
- `documents.*` for extraction and comparison
- `transcripts.*` for summaries, commitments, tasks, follow-up emails
- `medicare.*` for table-backed options/checklists
- `life.*` for underwriting pre-check and carrier fit
- `commissions.*` for rules, calculations, audits
- `crm.*` for metadata inspection, matching, draft prep, post-confirm write
- `supabase.*` for schema inspection, matching, draft prep, post-confirm write, research logging

## Error taxonomy

- `missing_data`: critical required data unavailable
- `no_match`: source search yielded no reliable match
- `low_confidence`: evidence exists but quality is weak
- `policy_blocked`: guardrail/compliance block

