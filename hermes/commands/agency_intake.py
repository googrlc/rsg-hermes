"""Agency intake orchestrator — turns raw text into a draft CRM intake payload.

Pipeline:
  1. LLM extraction using the unified `crm-intake-writer` contract.
  2. Validate the JSON shape (per-LOB opps, duplicate_search, facts[]).
  3. Stage in `agency_intake_drafts` with status='pending'.
  4. Return a human-readable approval prompt + draft id.

Approval happens via `hermes.operations.agency_intake_approval.approve_draft`,
which is hit by both the Slack interactive button handler and the
`POST /agency-intake/approve` HTTP endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)


# Canonical Opportunity stage enum (see hermes-training/espocrm/guardrails.md).
ALLOWED_STAGES = {
    "Discovery",
    "Quoting",
    "Markets Out / Shopping",
    "Proposal Presented",
    "Negotiation",
    "Closed Won",
    "Closed Lost",
}

ALLOWED_APPROVAL_TOKENS = {
    "APPROVE ALL",
    "APPROVE CRM ONLY",
    "APPROVE SUPABASE ONLY",
    "APPROVE TASKS ONLY",
    "REVISE",
    "CANCEL",
}


AGENCY_INTAKE_PROMPT = """\
You are Hermes, the RSG Insurance Agency CRM operator.

You will receive a raw insurance-related summary, document, transcript, email,
quote proposal, or Slack note. Your job is to produce a structured intake
payload that follows the `crm-intake-writer` skill contract exactly.

Return ONLY valid JSON with this top-level shape:

{
  "action": "crm_intake_upsert",
  "approval_required": true,
  "source": {
    "type": "slack_summary|document|email|transcript|quote_proposal|manual",
    "submitted_by": "<name or username>",
    "date": "YYYY-MM-DD",
    "source_ref": "optional URL, file name, or message_ts"
  },
  "classification": [
    "Commercial Account" | "Personal Lines Household" | "Life Insurance Prospect" |
    "Group Benefits Prospect" | "Medicare Prospect" | "Renewal" | "Service Request" |
    "Claim" | "Quote Summary" | "Underwriting Submission" | "Carrier Appetite Note"
  ],
  "lines_of_business": ["..."],
  "account": {
    "account_name": "...",
    "legal_name": "...",
    "dba": null,
    "fein": null,
    "entity_type": "Sole Proprietor|LLC|Corporation|S-Corp|Partnership|Non-Profit|Other",
    "industry": "...",
    "address": "...", "city": "...", "state": "..", "zip": "...",
    "phone": "...", "email": "...", "website": null,
    "operations_summary": "...",
    "annual_revenue": null, "estimated_payroll": null, "employee_count": null,
    "account_type": "Prospect|Commercial Lines|Personal Lines|Group Benefits|Medicare|Life Insurance|Carrier|MGA",
    "account_status": "Active|Urgent|Renewing|At Risk|Inactive",
    "tags": []
  },
  "contacts": [
    {
      "full_name": "...", "first_name": "...", "last_name": "...",
      "role": "...", "household_role": null,
      "phone": "...", "email": "...",
      "relationship_to_account": "Principal|Spouse|Decision Maker|Owner|Beneficiary|...",
      "primary_contact": true
    }
  ],
  "opportunities": [
    {
      "opportunity_name": "[Account Name] - [Single LOB] - [MM/DD/YYYY]",
      "line_of_business": "<exactly one LOB>",
      "stage": "Discovery|Quoting|Markets Out / Shopping|Proposal Presented|Negotiation|Closed Won|Closed Lost",
      "quote_number": null,
      "carrier": null,
      "premium": null, "fees": null, "total": null,
      "proposed_effective_date": "YYYY-MM-DD",
      "opportunity_type": "New Business|Renewal|Cross-Sell|Remarket",
      "producer": "...",
      "package_name": null,
      "tags": []
    }
  ],
  "note": {
    "title": "...",
    "note_type": "Underwriting Summary|Quote Summary|Discovery Call|Renewal Review|Service Request|Claim Note|Carrier Appetite Note|Internal Strategy Note|Email Recap|Meeting Summary|Voicemail / No Contact",
    "body": "Structured markdown body — facts vs assumptions separated, source cited.",
    "tags": []
  },
  "facts": [
    {
      "entity": "<display name matching account.account_name or a contact.full_name>",
      "entity_type": "Account|Contact",
      "fact_label": "EIN|Phone|Email|Date of Birth|Annual Revenue|Estimated Payroll|...",
      "fact_value": "...",
      "sensitivity": "standard|restricted",
      "source": "..."
    }
  ],
  "duplicate_search": {
    "account": ["name", "fein", "address", "phone", "email"],
    "contacts": ["full_name", "email", "phone"],
    "opportunities": ["account+lob+effective_date", "quote_number"]
  }
}

HARD RULES (every output must satisfy):
  1. ONE Opportunity per line of business. NEVER bundle GL+WC+Auto+IM+Pollution+Umbrella
     into a single "Commercial Package" opportunity.
  2. Mark EIN, DOB, DL, SSN, banking, health, beneficiary data as sensitivity=restricted.
  3. Never invent EINs, premiums, quote numbers, DOBs, policy numbers, or carriers.
     If the source does not contain it, set the field to null.
  4. Use the canonical stage enum only. When a quote number is present, default to
     "Quoting". When only the LOB is requested with no quote yet, default to "Discovery".
  5. Always populate duplicate_search even if the bundle is empty.
  6. Always emit facts[] entries for retrievable data (EIN, phone, email, DOB,
     payroll, revenue, etc.) — the retrieval layer depends on this.
  7. account_name follows: Commercial=legal entity, Personal Lines="<Last> Household",
     Life="<Name> Life Insurance", Group Benefits="<Company> Benefits", Medicare=individual.

No prose. No code fences. Only the JSON object.
"""


@dataclass
class AgencyIntakeDraft:
    """Result of staging an intake — returned to the caller for approval."""

    draft_id: str
    payload: dict[str, Any]
    approval_prompt: str
    validation_warnings: list[str]


class AgencyIntakeError(Exception):
    """Raised when extraction or validation fails."""


def _extract_payload(raw_text: str) -> dict[str, Any]:
    """Call the configured LLM to produce the unified intake JSON."""
    api_key = os.environ.get("HERMES_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise AgencyIntakeError(
            "No OpenAI key configured (HERMES_OPENAI_API_KEY or OPENAI_API_KEY)."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgencyIntakeError("openai package not installed") from exc

    model = os.environ.get("HERMES_OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": AGENCY_INTAKE_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0,
    )
    raw = (getattr(response, "output_text", "") or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgencyIntakeError(f"LLM did not return valid JSON: {raw[:300]}") from exc


def validate_payload(payload: dict[str, Any]) -> list[str]:
    """Light-touch validation. Returns a list of warning strings (empty = clean)."""
    warnings: list[str] = []
    if payload.get("action") != "crm_intake_upsert":
        warnings.append("action != 'crm_intake_upsert'")
    if not payload.get("approval_required"):
        warnings.append("approval_required is missing or false")

    account = payload.get("account") or {}
    if not account.get("account_name"):
        warnings.append("account.account_name is missing")

    opps = payload.get("opportunities") or []
    seen_lobs: dict[str, int] = {}
    for idx, opp in enumerate(opps):
        lob = opp.get("line_of_business")
        if not lob:
            warnings.append(f"opportunities[{idx}].line_of_business is missing")
            continue
        seen_lobs[lob] = seen_lobs.get(lob, 0) + 1
        stage = opp.get("stage")
        if stage and stage not in ALLOWED_STAGES:
            warnings.append(
                f"opportunities[{idx}].stage={stage!r} is not in the canonical enum"
            )
        if not opp.get("opportunity_name"):
            warnings.append(f"opportunities[{idx}].opportunity_name is missing")
    bundled = [lob for lob, n in seen_lobs.items() if n > 1]
    if bundled:
        warnings.append(
            "multiple opportunities share the same line_of_business "
            f"(bundling forbidden): {bundled}"
        )

    if "duplicate_search" not in payload:
        warnings.append("duplicate_search bundle is missing")
    if "facts" not in payload:
        warnings.append("facts[] is missing — retrieval layer requires this")
    return warnings


def _format_approval_prompt(payload: dict[str, Any], draft_id: str) -> str:
    """Build the human-readable confirmation message."""
    account = payload.get("account") or {}
    contacts = payload.get("contacts") or []
    opps = payload.get("opportunities") or []
    facts = payload.get("facts") or []
    note = payload.get("note") or {}

    contact_names = ", ".join(
        c.get("full_name", "") or f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        for c in contacts
    ) or "(none)"
    lob_lines = [
        f"  - {o.get('line_of_business', '?')}  ({o.get('stage', '?')})"
        + (f"  quote {o.get('quote_number')}" if o.get("quote_number") else "")
        for o in opps
    ]
    restricted = sum(1 for f in facts if f.get("sensitivity") == "restricted")

    dup = payload.get("duplicate_search") or {}
    dup_summary = ", ".join(
        f"{k}={len(v)}" for k, v in dup.items() if isinstance(v, list)
    ) or "(empty)"

    return (
        f"Intake draft ready — NOTHING WRITTEN YET. (draft_id: {draft_id})\n\n"
        f"Account:       {account.get('account_name', '?')}  "
        f"({account.get('entity_type', '?')}, {account.get('industry', '?')})\n"
        f"Contacts:      {contact_names}\n"
        f"Opportunities: {len(opps)}\n"
        + "\n".join(lob_lines)
        + f"\nNote:          {note.get('title', '(none)')}  ({note.get('note_type', '?')})\n"
        f"Facts staged:  {len(facts)} ({restricted} restricted)\n"
        f"Dedup probes:  {dup_summary}\n\n"
        "Reply with one of:\n"
        "  APPROVE ALL · APPROVE CRM ONLY · APPROVE SUPABASE ONLY · "
        "APPROVE TASKS ONLY · REVISE · CANCEL"
    )


def stage_draft(
    supa: "SupabaseClient",
    *,
    raw_text: str,
    submitted_by: str | None = None,
    source_type: str = "manual",
    source_ref: str | None = None,
) -> AgencyIntakeDraft:
    """Run extraction, validate, persist to `agency_intake_drafts`, return prompt."""
    payload = _extract_payload(raw_text)
    warnings = validate_payload(payload)
    if warnings:
        log.warning("Agency intake validation warnings: %s", warnings)

    row = supa.insert(
        "agency_intake_drafts",
        {
            "submitted_by": submitted_by,
            "source_type": source_type,
            "source_ref": source_ref,
            "raw_input": raw_text,
            "payload": payload,
            "classification": payload.get("classification") or [],
            "lines_of_business": payload.get("lines_of_business") or [],
            "duplicate_search": payload.get("duplicate_search") or {},
            "status": "pending",
        },
    )
    draft_id = str(row.get("id") or uuid.uuid4())
    prompt = _format_approval_prompt(payload, draft_id)
    return AgencyIntakeDraft(
        draft_id=draft_id,
        payload=payload,
        approval_prompt=prompt,
        validation_warnings=warnings,
    )
