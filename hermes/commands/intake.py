"""Casual natural-language lead intake via OpenAI extraction.

Handles "parking lot" dictation like:
  "Just met Juan Silva at Peterbilt, 404-555-0199, needs 3-unit fleet quote, Commercial Auto"

Pipeline: OpenAI extract -> EspoCRM upsert (Account + Contact + Opportunity) -> Supabase dual-write.
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


def _write_espo(
    client: "EspoClient",
    data: dict[str, Any],
) -> dict[str, Any]:
    """Upsert Account, Contact, and optionally create an Opportunity."""
    result: dict[str, Any] = {}

    account_id = None
    biz = data.get("businessName")
    if biz:
        account = client.upsert_account({"name": biz})
        account_id = account.get("id") if isinstance(account, dict) else None
        result["account"] = account

    contact_payload: dict[str, Any] = {}
    if data.get("firstName"):
        contact_payload["firstName"] = data["firstName"]
    if data.get("lastName"):
        contact_payload["lastName"] = data["lastName"]
    if data.get("phone"):
        digits = "".join(c for c in data["phone"] if c.isdigit())
        if len(digits) == 10:
            digits = "1" + digits
        contact_payload["phoneNumber"] = f"+{digits}"
    if data.get("email"):
        contact_payload["emailAddress"] = data["email"]
    if account_id:
        contact_payload["accountId"] = account_id

    if contact_payload.get("firstName") or contact_payload.get("lastName"):
        name_parts = [contact_payload.get("firstName", ""), contact_payload.get("lastName", "")]
        contact_payload["name"] = " ".join(p for p in name_parts if p)
        contact = client.upsert_contact(contact_payload)
        result["contact"] = contact

    lob = data.get("lineOfBusiness") or ""
    desc = data.get("businessDescription") or ""
    opp_name = f"{biz or data.get('lastName', 'Lead')} - {lob}" if lob else (biz or "New Lead")
    opp_payload: dict[str, Any] = {
        "name": opp_name,
        "stage": "New",
        "description": f"[Hermes Intake] {desc}".strip(),
    }
    if account_id:
        opp_payload["accountId"] = account_id
    try:
        opp = client.create("Opportunity", opp_payload)
        result["opportunity"] = opp
    except Exception:
        log.exception("Opportunity creation failed (non-fatal)")

    return result


def _write_supabase(
    supa: "SupabaseClient",
    data: dict[str, Any],
    raw_text: str,
    *,
    channel_id: str | None = None,
    user_id: str | None = None,
    message_ts: str | None = None,
) -> None:
    """Dual-write to Supabase leads_staging and stg_slack_intake_notes."""
    try:
        supa.log_lead({
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
        })
    except Exception:
        log.exception("Supabase leads_staging write failed (non-fatal)")

    if channel_id:
        try:
            supa.log_slack_intake(
                channel_id=channel_id,
                user_id=user_id,
                message_text=raw_text,
                message_ts=message_ts,
                parsed_command="intake",
            )
        except Exception:
            log.exception("Supabase stg_slack_intake_notes write failed (non-fatal)")


def _format_confirmation(data: dict[str, Any], espo_result: dict[str, Any]) -> str:
    """Build a Slack-friendly confirmation message."""
    name = " ".join(
        p for p in [data.get("firstName"), data.get("lastName")] if p
    ) or "Unknown"
    biz = data.get("businessName")
    lob = data.get("lineOfBusiness") or "Unspecified"
    phone = data.get("phone") or "N/A"
    desc = data.get("businessDescription") or ""

    lines = [
        "Got it, Lamar. Logged in Momentum Desk:",
        f"*{name}*" + (f" ({biz})" if biz else ""),
        f"LOB: {lob}",
        f"Phone: {phone}",
    ]
    if desc:
        lines.append(f"Notes: {desc}")

    opp = espo_result.get("opportunity")
    if isinstance(opp, dict) and opp.get("id"):
        lines.append(f"Opportunity ID: {opp['id']}")

    lines.append("Want me to set a follow-up for tomorrow?")
    return "\n".join(lines)


def handle(
    client: "EspoClient",
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    channel_id: str | None = None,
    user_id: str | None = None,
    message_ts: str | None = None,
) -> DispatchResult:
    """Parse casual lead text, write to EspoCRM + Supabase, return confirmation."""
    data = _extract_lead(text)
    if not data:
        return DispatchResult(
            False,
            "Couldn't parse that as a lead. Try something like: "
            '"Met Juan Silva at Peterbilt, 404-555-0199, needs fleet quote, Commercial Auto"',
        )

    espo_result = _write_espo(client, data)

    if supa:
        _write_supabase(
            supa, data, text,
            channel_id=channel_id,
            user_id=user_id,
            message_ts=message_ts,
        )

    msg = _format_confirmation(data, espo_result)
    return DispatchResult(True, msg, {"extracted": data, "espo": espo_result})
