"""Casual natural-language lead intake via OpenAI extraction.

Handles "parking lot" dictation like:
  "Just met Juan Silva at Peterbilt, 404-555-0199, needs 3-unit fleet quote, Commercial Auto"

Pipeline: OpenAI extraction -> draft payloads -> explicit confirmation request.
No CRM/Supabase writes are executed from this handler.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

INTAKE_SYSTEM_PROMPT = """\
You are Hermes, the RSG Insurance Agency CRM operator.
Extract structured data from a casual message about an insurance lead or client meeting.

Return ONLY valid JSON with these fields:
- firstName (string or null)
- lastName (string or null)
- businessName (string or null)
- phone (string or null)
- email (string or null)
- address (string or null)
- city (string or null)
- state (2-letter US state code or null)
- lineOfBusiness (one of: Commercial Auto, GL/BOP, Workers Comp, Personal Lines, Medicare, Life, Property, Umbrella, or null)
- businessDescription (brief summary of what they need)
- referredBy (string or null)
- aiScore (integer 1-10, lead quality estimate based on detail and urgency)

No conversational text. Only JSON."""


def _extract_lead(text: str) -> dict[str, Any] | None:
    """Call OpenAI to parse casual text into structured lead fields."""
    api_key = os.environ.get("HERMES_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    model = os.environ.get("HERMES_OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": INTAKE_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0,
        )
    except Exception:
        log.exception("OpenAI intake extraction failed")
        return None

    raw = getattr(response, "output_text", "") or ""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("OpenAI returned non-JSON for intake: %s", raw[:200])
        return None


def _normalize_phone(raw_phone: str) -> str:
    digits = "".join(c for c in raw_phone if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}" if digits else raw_phone


def _build_espo_drafts(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Prepare draft Espo payloads without writing."""
    biz = data.get("businessName")
    account_payload = {"name": biz} if biz else {}

    contact_payload: dict[str, Any] = {}
    if data.get("firstName"):
        contact_payload["firstName"] = data["firstName"]
    if data.get("lastName"):
        contact_payload["lastName"] = data["lastName"]
    if data.get("phone"):
        contact_payload["phoneNumber"] = _normalize_phone(data["phone"])
    if data.get("email"):
        contact_payload["emailAddress"] = data["email"]
    if contact_payload.get("firstName") or contact_payload.get("lastName"):
        name_parts = [contact_payload.get("firstName", ""), contact_payload.get("lastName", "")]
        contact_payload["name"] = " ".join(p for p in name_parts if p)

    lob = data.get("lineOfBusiness") or ""
    desc = data.get("businessDescription") or ""
    opp_name = f"{biz or data.get('lastName', 'Lead')} - {lob}" if lob else (biz or "New Lead")
    opportunity_payload: dict[str, Any] = {
        "name": opp_name,
        "stage": "New",
        "description": f"[Hermes Intake] {desc}".strip(),
    }
    return {
        "account": account_payload,
        "contact": contact_payload,
        "opportunity": opportunity_payload,
    }


def _build_supabase_drafts(
    data: dict[str, Any],
    raw_text: str,
    *,
    channel_id: str | None = None,
    user_id: str | None = None,
    message_ts: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Prepare draft Supabase payloads without writing."""
    lead_payload = {
        "source": "hermes_slack",
        "status": "new",
        "contact_first_name": data.get("firstName"),
        "contact_last_name": data.get("lastName"),
        "business_name": data.get("businessName"),
        "phone": data.get("phone"),
        "email": data.get("email"),
        "address": data.get("address"),
        "city": data.get("city"),
        "state": data.get("state"),
        "business_description": data.get("businessDescription"),
        "referred_by": data.get("referredBy"),
        "raw_input": raw_text,
        "ai_score": data.get("aiScore"),
        "ai_notes": f"LOB: {data.get('lineOfBusiness', 'unknown')}",
    }
    slack_payload = {
        "channel_id": channel_id,
        "user_id": user_id,
        "message_text": raw_text,
        "message_ts": message_ts,
        "parsed_command": "intake",
        "source_record_id": message_ts,
        "payload": {},
    }
    return {
        "leads_staging": lead_payload,
        "stg_slack_intake_notes": slack_payload if channel_id else {},
    }


def _format_confirmation_request(
    data: dict[str, Any],
    espo_drafts: dict[str, dict[str, Any]],
    supabase_drafts: dict[str, dict[str, Any]],
) -> str:
    """Build confirm-before-write response with explicit approval options."""
    lines = [
        "I found the following updates and have not written anything yet.",
        "Proposed CRM Updates:",
        f"- Account draft: {json.dumps(espo_drafts.get('account', {}), default=str)}",
        f"- Contact draft: {json.dumps(espo_drafts.get('contact', {}), default=str)}",
        f"- Opportunity draft: {json.dumps(espo_drafts.get('opportunity', {}), default=str)}",
        "Proposed Supabase Updates:",
        f"- leads_staging: {json.dumps(supabase_drafts.get('leads_staging', {}), default=str)}",
    ]
    slack_draft = supabase_drafts.get("stg_slack_intake_notes", {})
    if slack_draft:
        lines.append(f"- stg_slack_intake_notes: {json.dumps(slack_draft, default=str)}")
    lines.extend(
        [
            "Proposed Tasks:",
            "- None",
            "Source Links:",
            "- None (intake text only)",
            f"Confidence: aiScore={data.get('aiScore', 'unknown')}",
            "Reply with one of the following:",
            "- APPROVE CRM ONLY",
            "- APPROVE SUPABASE ONLY",
            "- APPROVE TASKS ONLY",
            "- APPROVE ALL",
            "- REVISE",
            "- CANCEL",
            "Write Status:",
            "Not written. Awaiting confirmation.",
        ]
    )
    return "\n".join(lines)


def execute_approved_drafts(
    client: "EspoClient",
    supa: "SupabaseClient | None",
    *,
    espo_drafts: dict[str, dict[str, Any]],
    supabase_drafts: dict[str, dict[str, Any]],
    approve_crm: bool,
    approve_supabase: bool,
) -> dict[str, Any]:
    """Execute approved draft payloads for intake."""
    out: dict[str, Any] = {"crm": {}, "supabase": {}}
    account_id: str | None = None

    if approve_crm:
        account_payload = espo_drafts.get("account") or {}
        if account_payload:
            account = client.upsert_account(account_payload)
            out["crm"]["account"] = account
            if isinstance(account, dict) and account.get("id"):
                account_id = str(account["id"])

        contact_payload = dict(espo_drafts.get("contact") or {})
        if contact_payload:
            if account_id and not contact_payload.get("accountId"):
                contact_payload["accountId"] = account_id
            out["crm"]["contact"] = client.upsert_contact(contact_payload)

        opp_payload = dict(espo_drafts.get("opportunity") or {})
        if opp_payload:
            if account_id and not opp_payload.get("accountId"):
                opp_payload["accountId"] = account_id
            out["crm"]["opportunity"] = client.create("Opportunity", opp_payload)

    if approve_supabase and supa:
        lead_payload = supabase_drafts.get("leads_staging") or {}
        if lead_payload:
            out["supabase"]["leads_staging"] = supa.log_lead(lead_payload)
        slack_payload = supabase_drafts.get("stg_slack_intake_notes") or {}
        if slack_payload:
            out["supabase"]["stg_slack_intake_notes"] = supa.insert("stg_slack_intake_notes", slack_payload)

    return out


def handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    channel_id: str | None = None,
    user_id: str | None = None,
    message_ts: str | None = None,
) -> DispatchResult:
    """Parse lead text and return draft updates pending explicit approval."""
    _ = client  # Direct writes intentionally disabled for confirm-before-write.
    _ = supa
    data = _extract_lead(text)
    if not data:
        return DispatchResult(
            False,
            "Couldn't parse that as a lead. Try something like: "
            '"Met Juan Silva at Peterbilt, 404-555-0199, needs fleet quote, Commercial Auto"',
        )

    espo_drafts = _build_espo_drafts(data)
    supabase_drafts = _build_supabase_drafts(
        data,
        text,
        channel_id=channel_id,
        user_id=user_id,
        message_ts=message_ts,
    )
    msg = _format_confirmation_request(data, espo_drafts, supabase_drafts)
    return DispatchResult(
        True,
        msg,
        {
            "extracted": data,
            "espo_drafts": espo_drafts,
            "supabase_drafts": supabase_drafts,
            "write_status": "NOT_WRITTEN_AWAITING_CONFIRMATION",
        },
    )
