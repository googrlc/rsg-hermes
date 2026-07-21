"""Conversational CRM agent powered by OpenAI function calling.

Replaces the rigid intent mapper with a full agent that understands all Hermes
CRM operations and can answer natural language questions, run lookups, create
records, and generate reports — all via structured tool calls.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult

if TYPE_CHECKING:
    from hermes.core.client import EspoClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI tool definitions — each maps to a CRM operation
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_records",
            "description": (
                "Search CRM records by name across any entity type. "
                "Use this for questions like 'find Acme', 'who is John Smith', "
                "'look up Atlas Protection Service'."
            ),
            "parameters": {
                "type": "object",
                "required": ["entity", "query"],
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": ["Account", "Contact", "Lead", "Opportunity", "Policy", "Task"],
                        "description": "CRM entity type to search.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search term (name or partial name).",
                    },
                    "fields": {
                        "type": "string",
                        "description": (
                            "Comma-separated extra fields to return beyond id,name. "
                            "Examples: phoneNumber,emailAddress,fein,website,amount,stage"
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_field_value",
            "description": (
                "Look up a specific field value for a CRM record. "
                "Use for questions like 'what is Acme's FEIN', "
                "'what is the DOT number for Trucking Inc', "
                "'show me Atlas Protection's email'."
            ),
            "parameters": {
                "type": "object",
                "required": ["entity", "name_query", "field"],
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": ["Account", "Contact", "Lead", "Opportunity", "Policy"],
                        "description": "CRM entity to search in.",
                    },
                    "name_query": {
                        "type": "string",
                        "description": "Name or partial name to search for.",
                    },
                    "field": {
                        "type": "string",
                        "description": (
                            "CRM field name to retrieve. Common fields: "
                            "fein, caDotNumber (DOT), caMcNumber (MC), phoneNumber, "
                            "emailAddress, website, billingAddressStreet, industry, "
                            "amount, stage, policyNumber, carrier, effectiveDate, "
                            "expirationDate, lineOfBusiness, status, description"
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_report",
            "description": (
                "Run a CRM report or dashboard view. "
                "Use for requests like 'show pipeline', 'give me KPIs', "
                "'stale leads', 'premium by line of business', "
                "'renewal audit', 'data quality report', 'my accounts'."
            ),
            "parameters": {
                "type": "object",
                "required": ["report_type"],
                "properties": {
                    "report_type": {
                        "type": "string",
                        "enum": [
                            "pipeline",
                            "kpi",
                            "premium_by_lob",
                            "stale_leads",
                            "my_accounts",
                            "account_list",
                            "renewal_audit",
                            "cross_sell",
                            "data_quality",
                            "commission_snapshot",
                        ],
                        "description": "Which report to generate.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "total_premium",
            "description": (
                "Calculate total premium for a specific account. "
                "Use for 'total premium for Acme', 'how much premium does Trucking Inc have'."
            ),
            "parameters": {
                "type": "object",
                "required": ["account_name"],
                "properties": {
                    "account_name": {
                        "type": "string",
                        "description": "Account name to look up.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": (
                "List Hermes's own capabilities — the live tools it can run plus the domain "
                "playbooks available. Use when asked 'what can you do', 'what are your skills', "
                "'list your capabilities', or 'how can you help'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_research",
            "description": (
                "Research a business/client on the public web — website, owners, "
                "NAICS/SIC, phone, address, insurance context. Use when asked to look a "
                "company up online, find info on a prospect, or 'go to the web' for client "
                "data. Provide the business name (plus city/state if known)."
            ),
            "parameters": {
                "type": "object",
                "required": ["business"],
                "properties": {
                    "business": {
                        "type": "string",
                        "description": "Business name, optionally with city/state.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_search",
            "description": (
                "Search the connected agency mailbox (Microsoft 365) for recent emails. Use "
                "when asked about emails, messages, or the inbox — e.g. 'any emails from JB "
                "Noble?', 'what did the carrier send about that renewal?', 'anything new in my "
                "inbox today?'. Matches sender, subject, and preview across recent inbox mail."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords, a sender name/email, or subject terms to match. Omit for the latest mail.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "How many days back to search (default 14).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renewals_overview",
            "description": (
                "Upcoming policy renewals and at-risk/retention clients from the Project 85 "
                "renewal watchlist (classified risk + premium). Use for 'who renews this week/"
                "month', 'what's coming up for renewal', 'who's at risk of leaving', 'retention', "
                "'who should I save', 'the save-list'. Returns clients with premium, days until "
                "renewal, and risk status."
            ),
            "parameters": {
                "type": "object",
                "required": ["scope"],
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["upcoming", "at_risk"],
                        "description": "upcoming = renewals due soon; at_risk = CRITICAL/AT_RISK clients.",
                    },
                    "within_days": {
                        "type": "integer",
                        "description": "Window for upcoming renewals (7=this week, 30=this month). Default 30.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_record",
            "description": (
                "Create a new CRM record (Contact, Lead, Account, Opportunity, Task). "
                "Use for 'add contact John Smith', 'create a lead for Jane Doe', "
                "'add account Acme Corp'. Only include fields that were explicitly mentioned."
            ),
            "parameters": {
                "type": "object",
                "required": ["entity", "fields"],
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": ["Account", "Contact", "Lead", "Opportunity", "Task"],
                        "description": "CRM entity type to create.",
                    },
                    "fields": {
                        "type": "object",
                        "description": (
                            "Key-value pairs for the record. Common keys: "
                            "name, firstName, lastName, phoneNumber, emailAddress, "
                            "accountId, stage, status, description, amount"
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_record",
            "description": (
                "Update an existing CRM record by ID. "
                "Use for 'update opportunity X stage to Closed Won', "
                "'change the phone number for contact abc123'."
            ),
            "parameters": {
                "type": "object",
                "required": ["entity", "record_id", "fields"],
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": ["Account", "Contact", "Lead", "Opportunity", "Task", "Policy"],
                        "description": "CRM entity type.",
                    },
                    "record_id": {
                        "type": "string",
                        "description": "The record ID to update.",
                    },
                    "fields": {
                        "type": "object",
                        "description": "Key-value pairs of fields to update.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "intake_lead",
            "description": (
                "Process a casual lead intake message. Use when someone describes "
                "meeting a potential client, dictates lead info, or provides "
                "unstructured client details. Example: 'Just met Juan Silva at "
                "Peterbilt, needs fleet quote for 3 trucks, Commercial Auto'."
            ),
            "parameters": {
                "type": "object",
                "required": ["raw_text"],
                "properties": {
                    "raw_text": {
                        "type": "string",
                        "description": "The original unstructured lead description.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "merge_records",
            "description": (
                "Merge duplicate CRM records. The source is merged into the target "
                "and then deleted. Use for 'merge contact abc into def', "
                "'these two accounts are duplicates'."
            ),
            "parameters": {
                "type": "object",
                "required": ["entity", "source_id", "target_id"],
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": ["Contact", "Account", "Lead", "Opportunity"],
                        "description": "Entity type of both records.",
                    },
                    "source_id": {
                        "type": "string",
                        "description": "ID of the record to merge FROM (will be deleted).",
                    },
                    "target_id": {
                        "type": "string",
                        "description": "ID of the record to merge INTO (will be kept).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_carriers",
            "description": (
                "List the carriers RSG has appointments/data on, optionally filtered "
                "by name or line of business. Use for 'which carriers do we work with', "
                "'who's our GA for X', a carrier's lines, or its underwriting contact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional carrier name / text filter."},
                    "line_of_business": {"type": "string", "description": "Optional LOB filter, e.g. 'Commercial Auto'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "match_carrier_appetite",
            "description": (
                "Find carriers whose appetite matches a risk — by line of business, state, "
                "and/or class/NAICS. Use for 'who writes this?', 'carrier fit for X', "
                "'where do we submit this risk?'. Returns candidates with premium bands, "
                "requirements, and exclusions. Never invents appetite — only what's on file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "line_of_business": {"type": "string", "description": "Line of business, e.g. 'General Liability'."},
                    "state": {"type": "string", "description": "2-letter or full state name."},
                    "class_or_naics": {"type": "string", "description": "Class code, NAICS, or an operations keyword."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commission_summary",
            "description": (
                "Summarize RSG's commissions from the reconciled ledger — total expected vs "
                "received and what's still outstanding, optionally for one carrier. Use for "
                "'how are commissions', 'what are we owed', 'commission shortfall'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "carrier": {"type": "string", "description": "Optional carrier name filter."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commission_shortfalls",
            "description": (
                "List the specific policies where RSG was underpaid or is missing a carrier "
                "statement — ranked by dollars outstanding. Use for 'what are we chasing', "
                "'which carriers underpaid us', 'commission discrepancies'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "carrier": {"type": "string", "description": "Optional carrier name filter."},
                    "limit": {"type": "integer", "description": "Max rows (default 15)."},
                },
            },
        },
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

# The system prompt is two parts:
#   _DEFAULT_PERSONA  — identity, audience, and voice. This is what a persona file
#                       (HERMES_PERSONA_FILE) replaces, so Gretchen's instance speaks
#                       as her assistant while Lamar's speaks as his.
#   _PLATFORM_GUIDE   — capabilities, field aliases, tool-routing, and write-safety
#                       rules. Persona-agnostic; shared by every instance.
_DEFAULT_PERSONA = """\
You are Hermes — the AI right hand and chief of staff for Risk Solutions Group (RSG),
an independent insurance agency in Georgia. You are talking with Lamar Coates, RSG's
owner and operator. Address him by name and speak like a sharp, warm, proactive
account manager who knows the agency — not a generic chatbot or a database.

About RSG (use this context; don't ask Lamar to re-explain it):
- Independent agency writing commercial, personal, benefits, life, and Medicare lines.
- The #1 priority is RETENTION and protecting the book — client retention has been
  ~55% vs an ~84% industry benchmark, so renewals and at-risk clients matter most.
- You sit on top of EspoCRM, a Supabase data hub, and NowCerts policy data.

Voice: conversational, concrete, and brief. Lead with the answer and the next action.
Never reply "I don't know who you are" — you know it's Lamar at RSG. If you truly lack
a data point, say what you'd look up and offer to fetch it."""


_PLATFORM_GUIDE = """\
Your capabilities (use the tools — never guess at CRM data):
- Search and look up any CRM record (Account, Contact, Lead, Opportunity, Policy, Task)
- Retrieve specific field values (FEIN, DOT number, phone, email, premium, etc.)
- Run reports (pipeline, KPIs, stale leads, renewals, data quality, commissions)
- Show upcoming renewals and at-risk/retention clients (Project 85 watchlist) via renewals_overview
- Research a business/client on the public web via web_research (website, owners, NAICS/SIC, contact info)
- Create new records (contacts, leads, accounts, opportunities, tasks)
- Update existing records
- Process casual lead intake (dictated meeting notes → structured CRM entries)
- Merge duplicate records
- List your own capabilities via list_skills when asked what you can do

Field aliases you should know:
- FEIN / EIN / tax ID → field "fein" on Account
- DOT / DOT number → field "caDotNumber" on Account
- MC number → field "caMcNumber" on Account
- SIC → field "sicCode", NAICS → field "naicsCode"
- LOB / line of business → field "lineOfBusiness"
- premium → field "amount" on Opportunity

When the user asks about a company or person, search the appropriate entity.
When they ask for a specific data point, use get_field_value.
When they ask for a report or overview, use run_report.
When they ask who renews soon, who's at risk, retention, or the save-list, use renewals_overview.
When they ask to look a business up online, research a prospect, or "go to the web" for client data, use web_research.
When they describe meeting someone or dictate lead info, use intake_lead.
For questions you can answer from CRM data, always use a tool — never guess.
If a search returns multiple matches, present them clearly.
Be concise and direct in your responses.

IMPORTANT: For write operations (create, update, merge, intake), if the caller
has not confirmed the action, describe what you WOULD do and ask for confirmation.
Only execute writes when the caller has set confirmed=true."""


# Backwards-compatible default (Lamar's identity + the shared platform guide).
_SYSTEM_PROMPT = _DEFAULT_PERSONA + "\n\n" + _PLATFORM_GUIDE


def _compose_system_prompt(persona_key: str | None = None) -> str:
    """System prompt for this request: persona overlay + platform guide.

    ``persona_key`` selects a bundled persona (hermes/personas/{key}.md) per
    request — e.g. Lamar's owner/revenue voice on the same box that defaults to
    Gretchen. Falls back to the instance persona (HERMES_PERSONA_FILE) and then
    the built-in default. The shared platform guide (tools, field aliases,
    write-safety) is always appended so capabilities/guardrails never change.
    """
    from hermes.core.identity import load_named_persona, load_persona

    persona = ""
    if persona_key:
        persona = load_named_persona(persona_key)
    if not persona:
        persona = load_persona() or _DEFAULT_PERSONA
    return persona + "\n\n" + _PLATFORM_GUIDE


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _exec_search(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    entity = args["entity"]
    query = args["query"]
    extra = args.get("fields", "")
    select = "id,name"
    if extra:
        select = f"{select},{extra}"

    try:
        hits = client.search(entity, query, max_size=10, select=select)
    except Exception as exc:
        return DispatchResult(False, f"Search failed: {exc}")

    if not hits:
        return DispatchResult(True, f'No {entity} records matching "{query}".')

    lines = [f"*{entity} search: \"{query}\"* ({len(hits)} result{'s' if len(hits) != 1 else ''})"]
    for rec in hits:
        name = rec.get("name", "?")
        rec_id = rec.get("id", "?")
        details = []
        for key, val in rec.items():
            if key in ("id", "name", "deleted") or val is None or val == "":
                continue
            details.append(f"{key}: {val}")
        detail_str = " | ".join(details[:6])
        lines.append(f"  *{name}* (id: {rec_id})" + (f" — {detail_str}" if detail_str else ""))
    return DispatchResult(True, "\n".join(lines), {"results": hits})


def _exec_get_field(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    from hermes.commands.lookup import _field_lookup, _resolve_field_name
    field_hint = args["field"]
    name_query = args["name_query"]
    entity_hint = args.get("entity")
    field_name = _resolve_field_name(field_hint)
    return _field_lookup(client, field_name, name_query, entity_hint)


def _exec_report(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    report_type = args["report_type"]
    report_commands = {
        "pipeline": "pipeline",
        "kpi": "kpi",
        "premium_by_lob": "premium by lob",
        "stale_leads": "stale leads",
        "my_accounts": "my accounts",
        "account_list": "account list",
        "renewal_audit": "renewal audit",
        "cross_sell": "cross-sell opportunities",
        "data_quality": "data quality",
        "commission_snapshot": "commission snapshot",
    }
    command = report_commands.get(report_type, report_type)
    return _get_report_dispatcher().dispatch(client, command)


def _exec_total_premium(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    from hermes.commands.lookup import handle
    return handle(client, f"total premium for {args['account_name']}")


def _exec_list_skills(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    from hermes.operations.skills_catalog import render_text

    return DispatchResult(True, render_text())


def _exec_web_research(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    from hermes.commands.business_research import handle as research_handle

    business = (args.get("business") or "").strip()
    if not business:
        return DispatchResult(False, "Tell me which business to research (name, and city/state if you have it).")
    return research_handle(client, f"research business {business}")


def _exec_renewals(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.operations.command_center_qa import renewals_facts

    scope = args.get("scope", "upcoming")
    within = int(args.get("within_days") or 30)
    try:
        supa = SupabaseClient()
    except Exception as exc:
        return DispatchResult(False, f"Renewal data unavailable: {exc}")
    return DispatchResult(True, renewals_facts(supa, scope=scope, within_days=within))


def _exec_list_carriers(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    """Carrier hub tool — list carriers from the Supabase carrier book (read-only)."""
    from hermes.integrations.supabase_client import SupabaseClient

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Carrier book unavailable: {exc}")
    params: dict[str, str] = {"order": "name.asc", "is_active": "eq.true"}
    q = (args.get("query") or "").strip()
    lob = (args.get("line_of_business") or "").strip()
    if q:
        params["name"] = f"ilike.*{q}*"
    if lob:
        params["lines_of_business"] = f"ilike.*{lob}*"
    try:
        rows = supa.select(
            "carriers",
            columns="name,segment,lines_of_business,general_agent,appetite_notes,underwriting_hotline",
            params=params, limit=60,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Carrier lookup failed: {exc}")
    if not rows:
        return DispatchResult(True, "No carriers matched that filter.")
    lines = []
    for r in rows:
        bits = [r.get("name") or "?"]
        tail = " · ".join(x for x in (r.get("segment"), r.get("lines_of_business")) if x)
        if tail:
            bits.append(tail)
        lines.append(" — ".join(bits))
    return DispatchResult(True, f"{len(rows)} carriers:\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"carriers": rows})


def _exec_carrier_appetite(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    """Carrier hub tool — match carriers to a risk via the carrier_appetite table."""
    from hermes.integrations.supabase_client import SupabaseClient

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Appetite data unavailable: {exc}")
    params: dict[str, str] = {"order": "carrier_name.asc", "active": "eq.true"}
    lob = (args.get("line_of_business") or "").strip()
    state = (args.get("state") or "").strip()
    cls = (args.get("class_or_naics") or "").strip()
    if lob:
        params["lob"] = f"ilike.*{lob}*"
    try:
        rows = supa.select(
            "carrier_appetite",
            columns="carrier_name,lob,appetite_level,min_premium,max_premium,states_approved,key_requirements,exclusions,notes",
            params=params, limit=40,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Appetite lookup failed: {exc}")
    if state:
        # states_approved is a text[] (e.g. ["GA"] or ["ALL"]) — filter in Python.
        su = state.strip().upper()

        def _writes_state(r: dict[str, Any]) -> bool:
            arr = r.get("states_approved") or []
            if not isinstance(arr, list):
                arr = [arr]
            up = [str(x).upper() for x in arr if x]
            return "ALL" in up or any(su == x or su in x or x in su for x in up)

        rows = [r for r in rows if _writes_state(r)]
    if cls:
        needle = cls.lower()
        narrowed = [r for r in rows if needle in " ".join(
            str(r.get(k) or "") for k in ("key_requirements", "notes", "exclusions", "lob")).lower()]
        rows = narrowed or rows  # fall back to the LOB/state set if the class filter is too tight
    if not rows:
        return DispatchResult(True, "No carriers with matching appetite on file. This reflects only the "
                                    "appetite table — confirm directly with the carrier before relying on it.")
    out = []
    for r in rows:
        prem = ""
        if r.get("min_premium") or r.get("max_premium"):
            prem = f" · ${r.get('min_premium') or 0}–${r.get('max_premium') or '?'}"
        out.append(f"{r.get('carrier_name')} — {r.get('lob')} ({r.get('appetite_level') or 'appetite'}){prem}")
    return DispatchResult(True, f"{len(rows)} carrier appetite matches:\n" + "\n".join(f"• {x}" for x in out),
                          {"matches": rows})


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# reconciliation_status values on commission_ledger that mean RSG is still owed money
_OWED_STATUSES = {"underpaid", "missing_statement", "pending"}


def _exec_commission_summary(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    """Commissions hub tool — expected vs received vs outstanding from commission_ledger."""
    from collections import Counter

    from hermes.integrations.supabase_client import SupabaseClient

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Commission data unavailable: {exc}")
    params: dict[str, str] = {"order": "statement_date.desc"}
    carrier = (args.get("carrier") or "").strip()
    if carrier:
        params["carrier_name"] = f"ilike.*{carrier}*"
    try:
        rows = supa.select(
            "commission_ledger",
            columns="carrier_name,expected_commission,actual_commission,reconciliation_status,payment_received",
            params=params, limit=1000,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Commission lookup failed: {exc}")
    if not rows:
        return DispatchResult(True, "No commission ledger rows for that filter.")
    exp = sum(_num(r.get("expected_commission")) for r in rows)
    act = sum(_num(r.get("actual_commission")) for r in rows)
    by_status = Counter(str(r.get("reconciliation_status") or "unknown") for r in rows)
    scope = f" for {carrier}" if carrier else ""
    status_line = ", ".join(f"{k}: {v}" for k, v in by_status.most_common())
    msg = (f"Commissions{scope}: expected ${exp:,.0f}, received ${act:,.0f}, "
           f"outstanding ${exp - act:,.0f} across {len(rows)} ledger rows.\nBy status — {status_line}.")
    return DispatchResult(True, msg,
                          {"expected": exp, "received": act, "outstanding": exp - act, "rows": len(rows)})


def _exec_commission_shortfalls(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    """Commissions hub tool — the specific underpaid/missing-statement policies RSG is chasing."""
    from hermes.integrations.supabase_client import SupabaseClient

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Commission data unavailable: {exc}")
    params: dict[str, str] = {"order": "statement_date.desc"}
    carrier = (args.get("carrier") or "").strip()
    if carrier:
        params["carrier_name"] = f"ilike.*{carrier}*"
    try:
        rows = supa.select(
            "commission_ledger",
            columns="client_name,carrier_name,policy_number,expected_commission,actual_commission,reconciliation_status",
            params=params, limit=1000,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Commission lookup failed: {exc}")
    limit = int(args.get("limit") or 15)
    owed: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        status = str(r.get("reconciliation_status") or "").lower()
        short = _num(r.get("expected_commission")) - _num(r.get("actual_commission"))
        if status in _OWED_STATUSES and short > 0.5:
            owed.append((short, r))
    owed.sort(key=lambda x: x[0], reverse=True)
    owed = owed[:limit]
    if not owed:
        return DispatchResult(True, "No outstanding commission shortfalls on file — everything reconciled or paid.")
    total = sum(s for s, _ in owed)
    lines = [f"{r.get('client_name') or r.get('policy_number') or '?'} · {r.get('carrier_name') or ''} — "
             f"${s:,.0f} ({r.get('reconciliation_status')})" for s, r in owed]
    return DispatchResult(True, f"{len(owed)} shortfalls, ${total:,.0f} outstanding:\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"total": total, "count": len(owed)})


def _exec_create(client: "EspoClient", args: dict[str, Any], *, confirmed: bool = False) -> DispatchResult:
    entity = args["entity"]
    fields = args.get("fields", {})
    if not confirmed:
        field_summary = ", ".join(f"{k}={v}" for k, v in fields.items())
        return DispatchResult(
            False,
            f"I would create a new {entity} with: {field_summary}. Confirm to proceed.",
            {"requires_confirmation": True, "action": "create", "entity": entity, "fields": fields},
        )
    from hermes.commands.data_entry import _apply_workflow_defaults, _find_existing
    existing = _find_existing(client, entity, fields)
    if existing:
        existing_id = str(existing["id"])
        try:
            record = client.update(entity, existing_id, fields)
        except Exception as exc:
            return DispatchResult(False, f"Failed to update existing {entity} {existing_id}: {exc}")
        return DispatchResult(
            True,
            f"Found existing {entity} *{existing.get('name', existing_id)}* — updated instead of creating a duplicate.",
            {"record": record if isinstance(record, dict) else {"result": record}, "dedupe": existing},
        )
    payload = _apply_workflow_defaults(client, entity, fields)
    try:
        result = client.create(entity, payload)
    except Exception as exc:
        return DispatchResult(False, f"Failed to create {entity}: {exc}")
    rec_id = result.get("id", "?") if isinstance(result, dict) else "?"
    name = result.get("name", "") if isinstance(result, dict) else ""
    return DispatchResult(True, f"Created {entity}: *{name}* (id: {rec_id})", {"record": result})


def _exec_update(client: "EspoClient", args: dict[str, Any], *, confirmed: bool = False) -> DispatchResult:
    entity = args["entity"]
    record_id = args["record_id"]
    fields = args.get("fields", {})
    if not confirmed:
        field_summary = ", ".join(f"{k}={v}" for k, v in fields.items())
        return DispatchResult(
            False,
            f"I would update {entity} {record_id} with: {field_summary}. Confirm to proceed.",
            {"requires_confirmation": True, "action": "update", "entity": entity, "record_id": record_id, "fields": fields},
        )
    try:
        result = client.update(entity, record_id, fields)
    except Exception as exc:
        return DispatchResult(False, f"Failed to update {entity} {record_id}: {exc}")
    name = result.get("name", record_id) if isinstance(result, dict) else record_id
    return DispatchResult(True, f"Updated {entity}: *{name}*", {"record": result})


def _exec_intake(client: "EspoClient", args: dict[str, Any], *, confirmed: bool = False) -> DispatchResult:
    raw_text = args["raw_text"]
    if not confirmed:
        return DispatchResult(
            False,
            f"I would process this as a lead intake and create CRM records. Confirm to proceed.\n> {raw_text}",
            {"requires_confirmation": True, "action": "intake", "raw_text": raw_text},
        )
    from hermes.commands.intake import handle as intake_handle
    return intake_handle(client, f"intake {raw_text}")


def _exec_merge(client: "EspoClient", args: dict[str, Any], *, confirmed: bool = False) -> DispatchResult:
    entity = args["entity"]
    source_id = args["source_id"]
    target_id = args["target_id"]
    if not confirmed:
        return DispatchResult(
            False,
            f"I would merge {entity} {source_id} into {target_id} (source will be deleted). Confirm to proceed.",
            {"requires_confirmation": True, "action": "merge", "entity": entity, "source_id": source_id, "target_id": target_id},
        )
    from hermes.commands.merge import handle as merge_handle
    return merge_handle(client, f"merge {entity.lower()} {source_id} into {target_id}")


def _exec_email_search(client: "EspoClient", args: dict[str, Any]) -> DispatchResult:
    """Search the connected Microsoft 365 mailbox for recent matching emails.

    Reuses the proven MS365 inbox read (same path the triage lane uses); filters
    recent inbox mail by sender/subject/preview in Python. Read-only.
    """
    import os
    from datetime import datetime, timedelta, timezone

    query = (args.get("query") or "").strip()
    try:
        days = max(1, min(int(args.get("days") or 14), 90))
    except (TypeError, ValueError):
        days = 14

    mailbox = (os.environ.get("HERMES_ASK_MAILBOX")
               or os.environ.get("MS365_MAILBOXES", "").split(",")[0]).strip()
    if not mailbox:
        return DispatchResult(False, "No mailbox is connected (set HERMES_ASK_MAILBOX or MS365_MAILBOXES). Email search is unavailable.")

    try:
        from hermes.integrations.ms365_client import MS365Client

        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        msgs = MS365Client().list_inbox_messages(mailbox, since_iso=since, top=50)
    except Exception as exc:  # noqa: BLE001
        log.exception("email_search failed")
        return DispatchResult(False, f"Couldn't reach the mailbox: {exc}")

    terms = query.lower().split()

    def _match(m: dict[str, Any]) -> bool:
        if not terms:
            return True
        frm = (m.get("from") or {}).get("emailAddress") or {}
        hay = " ".join([str(m.get("subject") or ""), str(m.get("bodyPreview") or ""),
                        str(frm.get("name") or ""), str(frm.get("address") or "")]).lower()
        return all(t in hay for t in terms)

    hits = [m for m in msgs if _match(m)][:15]
    label = f' matching "{query}"' if query else ""
    if not hits:
        return DispatchResult(True, f"No emails in the last {days} days{label}.")
    lines = []
    for m in hits:
        frm = (m.get("from") or {}).get("emailAddress") or {}
        who = frm.get("name") or frm.get("address") or "?"
        when = str(m.get("receivedDateTime") or "")[:10]
        lines.append(f"- {when} · {who}: {m.get('subject') or '(no subject)'}")
    return DispatchResult(True, f"{len(hits)} email(s){label} (last {days}d):\n" + "\n".join(lines), {"emails": hits})


_EXECUTORS: dict[str, Any] = {
    "search_records": _exec_search,
    "get_field_value": _exec_get_field,
    "run_report": _exec_report,
    "total_premium": _exec_total_premium,
    "renewals_overview": _exec_renewals,
    "web_research": _exec_web_research,
    "list_skills": _exec_list_skills,
    "email_search": _exec_email_search,
    "create_record": _exec_create,
    "update_record": _exec_update,
    "intake_lead": _exec_intake,
    "merge_records": _exec_merge,
    "list_carriers": _exec_list_carriers,
    "match_carrier_appetite": _exec_carrier_appetite,
    "commission_summary": _exec_commission_summary,
    "commission_shortfalls": _exec_commission_shortfalls,
}

_WRITE_TOOLS = {"create_record", "update_record", "intake_lead", "merge_records"}

# ---------------------------------------------------------------------------
# Per-hub AI scoping — each hub gets its own assistant that only carries that
# hub's tools + a hub persona overlay. hub=None → the full CRM assistant.
# Add a hub by adding a tool set here (and, optionally, a persona file).
# ---------------------------------------------------------------------------
_HUB_TOOLS: dict[str, set[str]] = {
    "carrier": {"list_carriers", "match_carrier_appetite", "web_research"},
    "commissions": {"commission_summary", "commission_shortfalls"},
}
_HUB_PERSONA: dict[str, str] = {
    "carrier": "carrier",
    "commissions": "commissions",
}


def _scoped_tools(tools: list[dict[str, Any]], hub: str | None) -> list[dict[str, Any]]:
    """Filter a tool list to a hub's allowed set. Unknown/None hub → unchanged."""
    allowed = _HUB_TOOLS.get(hub or "")
    if allowed is None:
        return tools
    return [t for t in tools if t["function"]["name"] in allowed]

_report_dispatcher: Any = None


def _get_report_dispatcher() -> Any:
    """Return a cached Dispatcher(use_openai=False) for running reports."""
    global _report_dispatcher
    if _report_dispatcher is None:
        from hermes.core.dispatcher import Dispatcher
        _report_dispatcher = Dispatcher(use_openai=False)
    return _report_dispatcher


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

def ask(
    client: "EspoClient",
    text: str,
    *,
    confirmed: bool = False,
    conversation: list[dict[str, str]] | None = None,
    persona: str | None = None,
    hub: str | None = None,
) -> DispatchResult:
    """Process a natural language CRM request using the OpenAI agent.

    Args:
        client: EspoCRM client for executing operations.
        text: The user's natural language input.
        confirmed: Whether write operations should be executed (vs. previewed).
        conversation: Optional prior conversation messages for multi-turn context.

    Returns:
        DispatchResult with the agent's response.
    """
    from hermes.core.llm_client import get_client, resolve_model, LLMConfigError

    try:
        oai = get_client()
    except LLMConfigError:
        return DispatchResult(False, "LLM API key not configured. Set LITELLM_API_KEY or HERMES_OPENAI_API_KEY.")
    except ImportError:
        return DispatchResult(False, "OpenAI SDK not installed.")

    model = resolve_model(None)

    persona_key = persona or _HUB_PERSONA.get(hub or "")
    messages: list[dict[str, Any]] = [{"role": "system", "content": _compose_system_prompt(persona_key)}]
    if conversation:
        messages.extend(conversation)
    messages.append({"role": "user", "content": text})

    # Per-instance tool scoping: Gretchen's CRM-only instance disables web_research.
    from hermes.core.identity import disabled_tools

    disabled = disabled_tools()
    active_tools = _scoped_tools([t for t in _TOOLS if t["function"]["name"] not in disabled], hub)

    try:
        response = oai.chat.completions.create(
            model=model,
            messages=messages,
            tools=active_tools,
            tool_choice="auto",
            temperature=0,
        )
    except Exception as exc:
        log.exception("OpenAI agent call failed")
        return DispatchResult(False, f"AI agent error: {exc}")

    choice = response.choices[0] if response.choices else None
    if not choice:
        return DispatchResult(False, "No response from AI agent.")

    msg = choice.message

    # If the model wants to call tools, execute them
    if msg.tool_calls:
        tool_results = []
        final_result: DispatchResult | None = None

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_results.append({"tool_call_id": tc.id, "content": "Invalid arguments."})
                continue

            if fn_name in disabled:
                tool_results.append({"tool_call_id": tc.id,
                                     "content": f"The {fn_name} capability is not available on this instance."})
                continue

            executor = _EXECUTORS.get(fn_name)
            if not executor:
                tool_results.append({"tool_call_id": tc.id, "content": f"Unknown tool: {fn_name}"})
                continue

            is_write = fn_name in _WRITE_TOOLS
            if is_write:
                result = executor(client, fn_args, confirmed=confirmed)
            else:
                result = executor(client, fn_args)

            final_result = result
            tool_results.append({
                "tool_call_id": tc.id,
                "content": result.message,
            })

        # Send tool results back to get a natural language summary
        followup_messages = messages + [
            msg.model_dump(),
            *[{"role": "tool", **tr} for tr in tool_results],
        ]

        try:
            summary_response = oai.chat.completions.create(
                model=model,
                messages=followup_messages,
                temperature=0,
            )
            summary_text = summary_response.choices[0].message.content or ""
        except Exception:
            summary_text = ""

        if summary_text.strip():
            ok = final_result.ok if final_result else True
            data = final_result.data if final_result else None
            return DispatchResult(ok, summary_text.strip(), data)

        if final_result:
            return final_result
        return DispatchResult(True, "Done.")

    # No tool calls — the model responded directly (e.g. for greetings or general questions)
    content = msg.content or "I'm not sure how to help with that. Try asking about a CRM record, report, or operation."
    return DispatchResult(True, content)
