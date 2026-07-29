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
from typing import Any

from hermes.core.dispatcher import DispatchResult

from hermes.ams import book as ams_book

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI tool definitions — each maps to a CRM operation
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
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
            "name": "carrier_contacts",
            "description": (
                "Who to send a submission to at a carrier — the people on file in "
                "`carrier_contacts`, with role, email, phone, and region. Use for 'who's the "
                "underwriter for X', 'where do I submit this', 'who's our rep at Y', 'new "
                "business contact for Z'. Also answers whether RSG is appointed at all: a "
                "carrier absent from the roster is a carrier we have no appointment with."
            ),
            "parameters": {
                "type": "object",
                "required": ["carrier"],
                "properties": {
                    "carrier": {"type": "string", "description": "Carrier name or partial name."},
                    "role": {"type": "string", "description": "Optional function filter, e.g. 'underwriter', 'new business', 'service'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_class_code",
            "description": (
                "Resolve a WC (NCCI), GL (ISO), NAICS, or SIC class code — by the code itself or "
                "by a trade description ('plumbing contractor'). Returns the code, its "
                "description, and any notes on the row, including DO-NOT-QUOTE flags. Use before "
                "answering any class-code question. WC and GL are different numbering systems "
                "with different answers: pass code_system when you know which, and ask the user "
                "when you don't."
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "A class code, or a trade/operation description."},
                    "code_system": {
                        "type": "string",
                        "enum": ["wc", "gl", "naics", "sic"],
                        "description": "Which numbering system. Omit to search WC, GL, and NAICS together.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "class_code_appetite",
            "description": (
                "The class-code side of appetite, both directions. Given a code, returns the "
                "carriers linked to it with eligibility (eligible/conditional/prohibited), tier, "
                "states, and restrictions — direct links first, then carriers reached by bridging "
                "NAICS through the mapping tables. Given a carrier, returns the codes on that "
                "carrier's rows. Use for 'who writes 5183?', 'can we place a 5537 in AL?', 'what "
                "codes does CNA want?'. An empty result means no code-level link is on file, NOT "
                "that nobody writes it — fall back to match_carrier_appetite by LOB and state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The class code, e.g. '5183'."},
                    "code_system": {
                        "type": "string",
                        "enum": ["wc", "gl", "naics", "sic", "carrier"],
                        "description": "Numbering system of `code`. 'carrier' means a carrier-proprietary code.",
                    },
                    "carrier": {"type": "string", "description": "Carrier name — use instead of `code` to list that carrier's linked codes."},
                    "state": {"type": "string", "description": "Optional 2-letter state to scope the answer."},
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
    {
        "type": "function",
        "function": {
            "name": "find_client",
            "description": (
                "Search the canonical client book (NowCerts insureds) by name. Returns "
                "matching clients with type, location, and contact info. Use for "
                "'look up <client>', 'find <name>', 'contact info for X'."
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Client/business name or partial name."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "client_policies",
            "description": (
                "Show a client's book — their policies (with active count) from the canonical "
                "book. Accepts a client name or a NowCerts insured GUID. Use for "
                "'what does X have', 'X's policies', 'coverage for X'."
            ),
            "parameters": {
                "type": "object",
                "required": ["client"],
                "properties": {
                    "client": {"type": "string", "description": "Client name or nowcerts_insured_guid."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ams_client_snapshot",
            "description": (
                "Live AMS (NowCerts) snapshot for one client, pulled straight from NowCerts "
                "(not the nightly mirror): the insured's current identity/status, in-force "
                "policies (carrier, line, premium, effective/expiration), and open opportunities. "
                "Use for 'what's going on with X in the AMS', 'X's live policies', 'is X active', "
                "'X's pipeline'. Read-only."
            ),
            "parameters": {
                "type": "object",
                "required": ["client"],
                "properties": {
                    "client": {"type": "string", "description": "Client / insured name, or a NowCerts insured GUID."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crm_client_activity",
            "description": (
                "Open cases and their tasks for a client from the agency CRM (the custom cockpit "
                "CRM: agency_crm_cases / agency_crm_tasks) — renewal, service, marketing, and claims "
                "work in flight plus the to-dos on each. Use for 'what's open on X', 'any cases for "
                "X', 'what's the team working on for X'. Read-only."
            ),
            "parameters": {
                "type": "object",
                "required": ["client"],
                "properties": {
                    "client": {"type": "string", "description": "Client / insured name."},
                    "status": {"type": "string", "description": "Optional case status filter, e.g. 'open'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "client_documents",
            "description": (
                "A client's documents in Nextcloud (COIs, policies, proposals, quotes, "
                "correspondence, renewal reviews). Without 'path' it lists what's on file for the "
                "client; with 'path' it reads that one document's text so you can answer from its "
                "contents. Use for 'what documents do we have for X', 'pull X's COI', 'what does X's "
                "renewal review say'. Read-only, scoped to the client's folder."
            ),
            "parameters": {
                "type": "object",
                "required": ["client"],
                "properties": {
                    "client": {"type": "string", "description": "Client name (matches the Nextcloud Clients/<name> folder)."},
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional path under the client's folder to read, e.g. 'COIs/acme-2026.pdf'. "
                            "Omit to list the client's documents."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_intake_submissions",
            "description": (
                "List recent intake submissions and their status (awaiting_approval, failed, "
                "completed). Use for 'what's waiting for approval', 'what intake failed', "
                "'the intake queue'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Optional status filter, e.g. awaiting_approval or failed."},
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
- You sit on top of the agency CRM, a Supabase data hub, and NowCerts policy data.

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

When the user asks about a company or person, use find_client.
When they ask what a client holds, use client_policies (or ams_client_snapshot
when the answer must be live from the AMS).
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

def _exec_report(args: dict[str, Any]) -> DispatchResult:
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
    return _get_report_dispatcher().dispatch(command)


def _exec_list_skills(args: dict[str, Any]) -> DispatchResult:
    from hermes.operations.skills_catalog import render_text

    return DispatchResult(True, render_text())


def _exec_web_research(args: dict[str, Any]) -> DispatchResult:
    from hermes.commands.business_research import handle as research_handle

    business = (args.get("business") or "").strip()
    if not business:
        return DispatchResult(False, "Tell me which business to research (name, and city/state if you have it).")
    return research_handle(f"research business {business}")


def _exec_renewals(args: dict[str, Any]) -> DispatchResult:
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.operations.command_center_qa import renewals_facts

    scope = args.get("scope", "upcoming")
    within = int(args.get("within_days") or 30)
    try:
        supa = SupabaseClient()
    except Exception as exc:
        return DispatchResult(False, f"Renewal data unavailable: {exc}")
    return DispatchResult(True, renewals_facts(supa, scope=scope, within_days=within))


def _exec_list_carriers(args: dict[str, Any]) -> DispatchResult:
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


def _writes_state(row: dict[str, Any], state_upper: str) -> bool:
    """True if an appetite row's states_approved covers `state_upper`.

    states_approved is a text[] (["GA"], ["ALL"]) so it is filtered in Python, not
    PostgREST. An *empty* territory is not a wildcard: 14 rows say, verbatim, that
    their source does not itemize states. Treating those as nationwide would
    manufacture a licensure claim, so they are excluded from a state-scoped answer
    and reported separately as state-unconfirmed.
    """
    arr = row.get("states_approved") or []
    if not isinstance(arr, list):
        arr = [arr]
    up = [str(x).upper() for x in arr if x]
    if not up:
        return False
    return "ALL" in up or any(state_upper == x or state_upper in x or x in state_upper for x in up)


def _appetite_line(r: dict[str, Any]) -> str:
    """One appetite row as a scannable line: tier, territory, premium band, confidence."""
    bits = [f"{r.get('carrier_name')} — {r.get('lob')}", r.get("appetite_level") or "tier unset"]
    states = r.get("states_approved") or []
    if not isinstance(states, list):
        states = [states]
    bits.append("/".join(str(s) for s in states if s) if states else "states not itemized")
    if r.get("min_premium") or r.get("max_premium"):
        hi = f"${_num(r.get('max_premium')):,.0f}" if r.get("max_premium") else "no cap on file"
        bits.append(f"${_num(r.get('min_premium')):,.0f}–{hi}")
    line = " · ".join(str(b) for b in bits)
    if (r.get("confidence") or "unverified") != "verified":
        line += "  ⚠ unverified"
    return line


def _exec_carrier_appetite(args: dict[str, Any]) -> DispatchResult:
    """Carrier hub tool — match carriers to a risk via the carrier_appetite table.

    Reports tier, territory and confidence on every line, because an unverified row
    and a signed-off one are not the same answer and the desk must not present them
    as one. `declined` rows are pulled out as knockouts rather than mixed in with
    the matches — a fast no is an answer, but it is not a market.
    """
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
            columns="id,carrier_name,lob,appetite_level,min_premium,max_premium,states_approved,"
                    "key_requirements,exclusions,notes,confidence,source_document",
            params=params, limit=60,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Appetite lookup failed: {exc}")

    unconfirmed: list[dict[str, Any]] = []
    if state:
        su = state.strip().upper()
        unconfirmed = [r for r in rows if not (r.get("states_approved") or [])]
        rows = [r for r in rows if _writes_state(r, su)]

    narrowed_note = ""
    if cls:
        needle = cls.lower()
        narrowed = [r for r in rows if needle in " ".join(
            str(r.get(k) or "") for k in ("key_requirements", "notes", "exclusions", "lob")).lower()]
        if narrowed:
            rows = narrowed
        elif rows:
            # The class text appears nowhere on these rows. Say so — silently
            # returning the LOB set as if it were a class match is the failure
            # mode that makes a guess look like a lookup.
            narrowed_note = (f"\nNo row mentions '{cls}' — these are LOB/state matches only. "
                             f"Run class_code_appetite for the code-level link.")

    declined = [r for r in rows if (r.get("appetite_level") or "") == "declined"]
    rows = [r for r in rows if (r.get("appetite_level") or "") != "declined"]

    if not rows:
        msg = ("No carriers with matching appetite on file. This is the appetite table only — "
               "it is not a declination. Check class_code_appetite for a code-level link, and "
               "confirm with the carrier before relying on either.")
        if declined:
            msg += f"\n{len(declined)} row(s) are marked declined: " + ", ".join(
                f"{r.get('carrier_name')} ({r.get('lob')})" for r in declined)
        if unconfirmed:
            msg += (f"\n{len(unconfirmed)} row(s) have no itemized territory and were excluded from the "
                    f"{state.upper()} filter — they need the carrier program map, not an assumption.")
        return DispatchResult(True, msg, {"matches": [], "declined": declined, "state_unconfirmed": unconfirmed})

    out = [_appetite_line(r) for r in rows]
    msg = f"{len(rows)} carrier appetite matches:\n" + "\n".join(f"• {x}" for x in out) + narrowed_note
    if declined:
        msg += "\nDeclined on file: " + ", ".join(f"{r.get('carrier_name')} ({r.get('lob')})" for r in declined)
    if unconfirmed:
        msg += (f"\n{len(unconfirmed)} more row(s) have no itemized territory — excluded from the "
                f"{state.upper()} filter rather than assumed nationwide.")
    return DispatchResult(True, msg,
                          {"matches": rows, "declined": declined, "state_unconfirmed": unconfirmed})


def _exec_carrier_contacts(args: dict[str, Any]) -> DispatchResult:
    """Carrier hub tool — who to send a submission to.

    Resolves the carrier against `carriers` (the appointment roster) *before*
    looking for people. An empty contact list and "we have no appointment there"
    look identical otherwise, and they are opposite answers.
    """
    from hermes.integrations.supabase_client import SupabaseClient

    carrier = (args.get("carrier") or "").strip()
    if not carrier:
        return DispatchResult(False, "Which carrier?")
    role = (args.get("role") or "").strip()
    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Carrier book unavailable: {exc}")

    try:
        roster = supa.select(
            "carriers",
            columns="id,name,is_active,general_agent,underwriting_hotline,agent_login",
            params={"name": f"ilike.*{carrier}*", "order": "name.asc"}, limit=10,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Carrier lookup failed: {exc}")
    if not roster:
        return DispatchResult(True, f"No carrier matching '{carrier}' in the appointment roster — "
                                    "on this data we are not appointed there. Worth logging as an "
                                    "appointment gap if the risk keeps coming up.")

    # carrier ids are text slugs — quoted so a comma in one can never split the list
    ids = [f'"{str(r["id"])}"' for r in roster if r.get("id")]
    params: dict[str, str] = {"carrier_id": f"in.({','.join(ids)})",
                              "order": "is_primary.desc,name.asc"}
    if role:
        params["role"] = f"ilike.*{role}*"
    try:
        contacts = supa.select(
            "carrier_contacts",
            columns="carrier_id,name,role,email,phone,region,is_primary,last_interaction",
            params=params, limit=40,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Contact lookup failed: {exc}")

    by_id = {str(r.get("id")): r for r in roster}
    if not contacts:
        scope = f" in a '{role}' role" if role else ""
        fallbacks = []
        for r in roster:
            for label, key in (("underwriting hotline", "underwriting_hotline"), ("GA", "general_agent")):
                if r.get(key):
                    fallbacks.append(f"{r.get('name')} — {label}: {r.get(key)}")
        msg = f"Appointed at {', '.join(str(r.get('name')) for r in roster)}, but no contact on file{scope}."
        if fallbacks:
            msg += "\nGeneral routes on file:\n" + "\n".join(f"• {f}" for f in fallbacks)
        msg += "\nWant me to add the contact once you have a name?"
        return DispatchResult(True, msg, {"carriers": roster, "contacts": []})

    lines = []
    for c in contacts:
        who = c.get("name") or "(unnamed)"
        parent = by_id.get(str(c.get("carrier_id")), {}).get("name") or c.get("carrier_id")
        tail = " · ".join(str(x) for x in (c.get("role"), c.get("email"), c.get("phone"), c.get("region")) if x)
        star = "★ " if c.get("is_primary") else ""
        lines.append(f"{star}{parent} — {who}" + (f" · {tail}" if tail else ""))
    return DispatchResult(True, f"{len(contacts)} contact(s):\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"carriers": roster, "contacts": contacts})


# Classification tables, by numbering system: (table, code column, description column,
# keyword column). WC and GL are different systems with different answers for the same
# trade — which is why code_system is asked for rather than inferred.
_CODE_TABLES: dict[str, tuple[str, str, str, str]] = {
    "wc": ("wc_class_codes", "wc_code", "description", "search_keywords"),
    "gl": ("gl_class_codes", "gl_code", "description", "search_keywords"),
    "naics": ("naics_codes", "naics_code", "naics_title", "common_ops_keywords"),
    "sic": ("sic_codes", "sic_code", "sic_description", "mapped_naics_id"),
}
# Searched together when the caller doesn't say which system. SIC is opt-in — it is
# a legacy system nobody at RSG asks for by default.
_CODE_DEFAULT_SYSTEMS = ("wc", "gl", "naics")


def _exec_resolve_class_code(args: dict[str, Any]) -> DispatchResult:
    """Carrier hub tool — resolve a class code or a trade description.

    Surfaces the row's `notes` verbatim. That column is where the corrections and
    the DO-NOT-QUOTE flags live (WC 5037 is disputed against 5183), so dropping it
    would hand back a code the agency has already decided not to quote.
    """
    from hermes.integrations.supabase_client import SupabaseClient

    query = (args.get("query") or "").strip()
    if not query:
        return DispatchResult(False, "What code or trade should I resolve?")
    system = (args.get("code_system") or "").strip().lower()
    if system and system not in _CODE_TABLES:
        return DispatchResult(False, f"Unknown code system '{system}'. Use wc, gl, naics, or sic.")
    systems = (system,) if system else _CODE_DEFAULT_SYSTEMS

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Classification tables unavailable: {exc}")

    numeric = bool(re.fullmatch(r"[0-9]{2,8}", query))
    found: dict[str, list[dict[str, Any]]] = {}
    for sys_key in systems:
        table, code_col, desc_col, kw_col = _CODE_TABLES[sys_key]
        cols = f"{code_col},{desc_col},notes" if sys_key in ("wc", "gl", "naics") else f"{code_col},{desc_col}"
        if sys_key == "wc":
            cols += ",category,state"
        elif sys_key == "gl":
            cols += ",category,residential_only"
        try:
            if numeric:
                rows = supa.select(table, columns=cols,
                                   params={code_col: f"like.{query}*", "order": f"{code_col}.asc"}, limit=12)
            else:
                rows = supa.select(table, columns=cols,
                                   params={desc_col: f"ilike.*{query}*", "order": f"{code_col}.asc"}, limit=12)
                if not rows and kw_col not in ("mapped_naics_id",):
                    # Keyword columns are barely populated yet; description is the
                    # real path. Try them anyway so this improves as they fill in.
                    rows = supa.select(table, columns=cols,
                                       params={kw_col: f"ilike.*{query}*", "order": f"{code_col}.asc"}, limit=12)
        except Exception as exc:  # noqa: BLE001
            return DispatchResult(False, f"Class code lookup failed on {table}: {exc}")
        if rows:
            found[sys_key] = rows

    if not found:
        scope = system.upper() if system else "WC/GL/NAICS"
        return DispatchResult(True, f"Nothing in {scope} matches '{query}'. Try the trade in other words, "
                                    "or name the numbering system. I won't invent a code.")

    out: list[str] = []
    for sys_key, rows in found.items():
        _, code_col, desc_col, _ = _CODE_TABLES[sys_key]
        out.append(f"{sys_key.upper()}:")
        for r in rows:
            line = f"  {r.get(code_col)} — {r.get(desc_col)}"
            if r.get("category"):
                line += f" ({r['category']})"
            note = str(r.get("notes") or "")
            if note:
                flag = "⛔ " if "DO NOT QUOTE" in note.upper() else "· "
                line += f"\n    {flag}{note}"
            out.append(line)
    ask = ""
    if not system and len({k for k in found if k in ("wc", "gl")}) > 1:
        ask = "\nWC and GL both matched — which one do you need?"
    return DispatchResult(True, "\n".join(out) + ask, {"matches": found, "code_system": system or None})


def _exec_class_code_appetite(args: dict[str, Any]) -> DispatchResult:
    """Carrier hub tool — the class-code side of appetite, both directions.

    Reads the `carrier_appetite_class_codes` bridge (via its resolver views), never
    `carrier_appetite.class_codes[]` — that column is populated on 2 of 74 rows, and
    filtering a risk against it returns zero carriers while looking authoritative.
    An empty result here is reported as "no code-level link on file", which is a
    data gap, not a declination.
    """
    from hermes.integrations.supabase_client import SupabaseClient

    code = (args.get("code") or "").strip()
    carrier = (args.get("carrier") or "").strip()
    system = (args.get("code_system") or "").strip().lower()
    state = (args.get("state") or "").strip().upper()
    if not code and not carrier:
        return DispatchResult(False, "Give me a class code, or a carrier to list codes for.")

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Appetite data unavailable: {exc}")

    params: dict[str, str] = {"active": "eq.true", "order": "carrier_name.asc"}
    if code:
        params["code"] = f"eq.{code}"
    if carrier:
        params["carrier_name"] = f"ilike.*{carrier}*"
    if system and system != "naics":
        params["code_system"] = f"eq.{system}"
    try:
        direct = supa.select("vw_carrier_appetite_class_resolved",
                             columns="carrier_name,lob,appetite_level,appetite_confidence,states_approved,"
                                     "code_system,code,code_description,eligibility,match_method,"
                                     "link_confidence,state_scope,restrictions,resolves_locally",
                             params=params, limit=60)
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Class-code appetite lookup failed: {exc}")

    bridged: list[dict[str, Any]] = []
    if code and not direct:
        # No direct link. Bridge NAICS -> GL/WC -> appetite. Coverage is bounded by
        # the mapping tables, so a miss here still isn't a declination.
        try:
            bridged = supa.select("vw_who_writes_naics",
                                  columns="naics_code,naics_title,carrier_name,lob,appetite_level,"
                                          "appetite_confidence,states_approved,code_system,matched_code,"
                                          "matched_code_description,eligibility,state_scope,restrictions",
                                  params={"naics_code": f"eq.{code}", "order": "carrier_name.asc"}, limit=40)
        except Exception:  # noqa: BLE001
            bridged = []

    def _state_ok(r: dict[str, Any]) -> bool:
        if not state:
            return True
        # A prohibition scoped to this state stays in the answer — it is the answer.
        if (r.get("state_scope") or "").upper() == state:
            return True
        return _writes_state(r, state) or not (r.get("states_approved") or [])

    rows = [r for r in (direct or bridged) if _state_ok(r)]
    kind = "direct code link" if direct else "bridged via NAICS"

    if not rows:
        target = f"code {code}" if code else carrier
        return DispatchResult(True,
                              f"No code-level link on file for {target}"
                              f"{f' in {state}' if state else ''}. That is a gap in the bridge table, "
                              "not a declination — fall back to match_carrier_appetite by LOB and state, "
                              "and offer to link the code once you've worked it out.",
                              {"links": [], "bridged": bool(bridged)})

    lines = []
    for r in rows:
        the_code = r.get("code") or r.get("matched_code")
        desc = r.get("code_description") or r.get("matched_code_description") or ""
        mark = {"eligible": "✔", "conditional": "~", "prohibited": "✘"}.get(str(r.get("eligibility")), "?")
        states = r.get("states_approved") or []
        if not isinstance(states, list):
            states = [states]
        bits = [f"{mark} {r.get('carrier_name')} — {r.get('lob')}",
                str(r.get("appetite_level") or "tier unset"),
                "/".join(str(s) for s in states if s) if states else "states not itemized"]
        line = " · ".join(bits) + f"\n    {the_code} {desc} — {r.get('eligibility')}"
        if r.get("state_scope"):
            line += f" (in {r['state_scope']} only)"
        if r.get("restrictions"):
            line += f"\n    {r['restrictions']}"
        if (r.get("link_confidence") or r.get("appetite_confidence") or "unverified") != "verified":
            line += "\n    ⚠ unverified"
        if r.get("match_method") in ("keyword", "embedding"):
            line += f"\n    ⚠ machine-derived link ({r['match_method']}) — annotation, not carrier truth"
        lines.append(line)

    header = f"{len(rows)} carrier link(s) — {kind}:"
    return DispatchResult(True, header + "\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"links": rows, "match": kind})


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# reconciliation_status values on commission_ledger that mean RSG is still owed money
_OWED_STATUSES = {"underpaid", "missing_statement", "pending"}


def _origin(row: dict[str, Any]) -> str:
    """Provenance of a ledger row: 'statement' (a carrier statement backs it) or
    'seed' (backfilled from NowCerts, expectation computed, nothing matched)."""
    return str(row.get("origin") or "unknown").strip().lower() or "unknown"


def _origin_totals(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Expected/received/row-count split by provenance.

    The Finance Desk may not quote a commission figure without saying whether a
    carrier statement backs it — seed rows are a *statement gap*, not a proven
    shortpay. That distinction is unanswerable unless the tool carries it, so the
    split ships with every summary rather than being left to the model.
    """
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        bucket = out.setdefault(_origin(r), {"expected": 0.0, "received": 0.0, "rows": 0.0})
        bucket["expected"] += _num(r.get("expected_commission"))
        bucket["received"] += _num(r.get("actual_commission"))
        bucket["rows"] += 1
    return out


def _origin_line(rows: list[dict[str, Any]]) -> str:
    """One-line rendering of _origin_totals, statement-backed first."""
    totals = _origin_totals(rows)
    order = sorted(totals, key=lambda k: (k != "statement", k))
    return "; ".join(
        f"{k}: expected ${t['expected']:,.0f}, received ${t['received']:,.0f} ({int(t['rows'])} rows)"
        for k, t in ((k, totals[k]) for k in order)
    )


def _exec_commission_summary(args: dict[str, Any]) -> DispatchResult:
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
            columns="carrier_name,expected_commission,actual_commission,reconciliation_status,payment_received,origin",
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
           f"outstanding ${exp - act:,.0f} across {len(rows)} ledger rows.\nBy status — {status_line}."
           f"\nBy origin — {_origin_line(rows)}")
    return DispatchResult(True, msg,
                          {"expected": exp, "received": act, "outstanding": exp - act, "rows": len(rows),
                           "by_origin": _origin_totals(rows)})


def _exec_commission_shortfalls(args: dict[str, Any]) -> DispatchResult:
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
            columns="client_name,carrier_name,policy_number,expected_commission,actual_commission,reconciliation_status,origin",
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
    # origin travels with every row: a seed shortfall is money we cannot prove was
    # paid, a statement one is money a carrier actually shorted us.
    lines = [f"{r.get('client_name') or r.get('policy_number') or '?'} · {r.get('carrier_name') or ''} — "
             f"${s:,.0f} ({r.get('reconciliation_status')}, origin={_origin(r)})" for s, r in owed]
    seed_total = sum(s for s, r in owed if _origin(r) != "statement")
    return DispatchResult(True, f"{len(owed)} shortfalls, ${total:,.0f} outstanding:\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"total": total, "count": len(owed), "seed_total": seed_total,
                           "statement_total": total - seed_total})


def _exec_find_client(args: dict[str, Any]) -> DispatchResult:
    """CRM hub tool — search the canonical client book (Supabase, NowCerts-sourced)."""
    from hermes.integrations.supabase_client import SupabaseClient

    q = (args.get("query") or "").strip()
    if not q:
        return DispatchResult(False, "Tell me the client name to look up.")
    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Client book unavailable: {exc}")
    try:
        rows = supa.select(
            "canonical_clients",
            columns="nowcerts_insured_guid,insured_name,client_type,city,state,email,phone,active",
            params={"insured_name": f"ilike.*{q}*", "order": "insured_name.asc"}, limit=25,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Client lookup failed: {exc}")
    if not rows:
        return DispatchResult(True, f"No clients matching '{q}'.")
    lines = []
    for r in rows:
        loc = ", ".join(x for x in (r.get("city"), r.get("state")) if x)
        tail = " · ".join(x for x in (r.get("client_type"), loc) if x)
        lines.append(f"{r.get('insured_name')}" + (f" — {tail}" if tail else ""))
    return DispatchResult(True, f"{len(rows)} client(s):\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"clients": rows})


def _exec_client_policies(args: dict[str, Any]) -> DispatchResult:
    """CRM hub tool — a client's policies from the canonical book (Supabase)."""
    from hermes.integrations.supabase_client import SupabaseClient

    who = (args.get("client") or "").strip()
    if not who:
        return DispatchResult(False, "Tell me which client.")
    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Book unavailable: {exc}")
    guid, name = who, who
    looks_like_guid = who.count("-") >= 4 and len(who) >= 30
    if not looks_like_guid:
        try:
            cs = supa.select("canonical_clients", columns="nowcerts_insured_guid,insured_name",
                             params={"insured_name": f"ilike.*{who}*", "order": "insured_name.asc"}, limit=1)
        except Exception as exc:  # noqa: BLE001
            return DispatchResult(False, f"Client lookup failed: {exc}")
        if not cs:
            return DispatchResult(True, f"No client matching '{who}'.")
        guid, name = cs[0].get("nowcerts_insured_guid"), cs[0].get("insured_name")
    try:
        pols = ams_book.select_policies(
            supa,
            columns="policy_number,carrier,lines_of_business,premium_amount,status,expiration_date,active",
            params={"nowcerts_insured_guid": f"eq.{guid}", "order": "expiration_date.desc"}, limit=50,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Policy lookup failed: {exc}")
    if not pols:
        return DispatchResult(True, f"{name}: no policies on file.")
    active = sum(1 for p in pols if p.get("active"))
    lines = [
        f"{p.get('lines_of_business') or '?'} · {p.get('carrier') or ''} — ${_num(p.get('premium_amount')):,.0f} "
        f"({p.get('status') or ''}, exp {str(p.get('expiration_date') or '')[:10]})"
        for p in pols
    ]
    return DispatchResult(True, f"{name} — {active} active of {len(pols)} policies:\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"policies": pols})


def _first(d: dict[str, Any], *keys: str) -> Any:
    """First non-empty value among *keys — NowCerts field names vary by endpoint."""
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _exec_ams_snapshot(args: dict[str, Any]) -> DispatchResult:
    """CRM hub tool — LIVE NowCerts snapshot for one client (insured + policies +
    opportunities), read straight from the AMS rather than the nightly mirror."""
    who = (args.get("client") or "").strip()
    if not who:
        return DispatchResult(False, "Tell me which client to pull from the AMS.")
    try:
        from hermes.sync.nowcerts_client import NowCertsClient
        nc = NowCertsClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"AMS (NowCerts) unavailable: {exc}")

    looks_like_guid = who.count("-") >= 4 and len(who) >= 30
    guid, name, others = who, who, []
    if not looks_like_guid:
        try:
            matches = nc.search_insureds(who, top=5)
        except Exception as exc:  # noqa: BLE001
            return DispatchResult(False, f"AMS lookup failed: {exc}")
        if not matches:
            return DispatchResult(True, f"No AMS insured matching '{who}'.")
        top = matches[0]
        guid = str(_first(top, "databaseId", "id", "insuredDatabaseId") or "")
        name = _first(top, "commercialName", "name") or who
        others = [(_first(m, "commercialName", "name") or "?") for m in matches[1:4]]
    if not guid:
        return DispatchResult(True, f"Found '{name}' in the AMS but it has no insured id to pull the book.")

    try:
        pols = nc.policies_for_insured(guid, top=100)
    except Exception:  # noqa: BLE001
        pols = []
    try:
        opps = nc.opportunities_for_insured(guid)
    except Exception:  # noqa: BLE001
        opps = []

    active = sum(1 for p in pols if _first(p, "active", "isActive"))
    plines = [
        f"{_first(p, 'lineOfBusiness', 'lineOfBusinessName', 'businessLine') or '?'} · "
        f"{_first(p, 'carrierName', 'carrier', 'writingCompanyName') or ''} — "
        f"${_num(_first(p, 'premium', 'totalPremium', 'annualPremium')):,.0f} "
        f"(exp {str(_first(p, 'expirationDate', 'expiration') or '')[:10]})"
        for p in pols
    ]
    olines = [
        f"{_first(o, 'lineOfBusinessName', 'lineOfBusiness') or '?'} — "
        f"{_first(o, 'opportunityStageName', 'stage') or ''}"
        f"{(' (needed ' + str(_first(o, 'neededBy'))[:10] + ')') if _first(o, 'neededBy') else ''}"
        for o in opps
    ]
    parts = [f"{name} (AMS/NowCerts) — {active} active of {len(pols)} policies:"]
    parts += [f"• {ln}" for ln in plines] or ["• no policies on file"]
    if olines:
        parts.append(f"Open opportunities ({len(olines)}):")
        parts += [f"• {ln}" for ln in olines]
    if others:
        parts.append("Other AMS matches: " + ", ".join(others))
    return DispatchResult(True, "\n".join(parts),
                          {"insured": name, "insured_guid": guid, "policies": pols, "opportunities": opps})


def _exec_crm_activity(args: dict[str, Any]) -> DispatchResult:
    """CRM hub tool — a client's open cases + tasks from the custom agency CRM
    (agency_crm_cases / agency_crm_tasks in Supabase)."""
    who = (args.get("client") or "").strip()
    if not who:
        return DispatchResult(False, "Tell me which client.")
    from hermes.integrations.supabase_client import SupabaseClient

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Agency CRM unavailable: {exc}")
    params = {"insured_name": f"ilike.*{who}*", "order": "created_at.desc"}
    status = (args.get("status") or "").strip()
    if status:
        params["status"] = f"eq.{status}"
    try:
        cases = supa.select(
            "agency_crm_cases",
            columns="id,case_number,case_type,title,status,priority,insured_name,created_at",
            params=params, limit=25,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Case lookup failed: {exc}")
    if not cases:
        return DispatchResult(True, f"No agency-CRM cases on file for '{who}'.")

    tasks_by_case: dict[str, list[dict[str, Any]]] = {}
    case_ids = [str(c.get("id")) for c in cases if c.get("id")]
    if case_ids:
        try:
            trows = supa.select(
                "agency_crm_tasks",
                columns="case_id,title,status,due_at,assigned_to_email",
                params={"case_id": f"in.({','.join(case_ids)})", "order": "due_at.asc"}, limit=100,
            )
        except Exception:  # noqa: BLE001
            trows = []
        for t in trows:
            tasks_by_case.setdefault(str(t.get("case_id")), []).append(t)

    lines: list[str] = []
    for c in cases:
        head = f"{c.get('case_type') or 'case'} · {c.get('title') or c.get('case_number') or ''} — {c.get('status') or ''}"
        lines.append(f"• {head}")
        for t in tasks_by_case.get(str(c.get("id")), []):
            due = str(t.get("due_at") or "")[:10]
            lines.append(f"    – {t.get('title') or 'task'} ({t.get('status') or ''}{', due ' + due if due else ''})")
    name = cases[0].get("insured_name") or who
    return DispatchResult(True, f"{name} — {len(cases)} case(s) in the agency CRM:\n" + "\n".join(lines),
                          {"cases": cases})


# Text files we can hand back inline; other extensions get metadata only.
_DOC_MAX_CHARS = 6000


def _exec_client_documents(args: dict[str, Any]) -> DispatchResult:
    """CRM hub tool — list or read a client's Nextcloud documents. Read-only and
    hard-scoped to Clients/{client}/ (no path traversal outside the client)."""
    who = (args.get("client") or "").strip()
    if not who:
        return DispatchResult(False, "Which client's documents?")
    from hermes.integrations.nextcloud_client import NextcloudClient, _sanitize_segment

    nc = NextcloudClient()
    if not nc.is_configured():
        return DispatchResult(False, "Nextcloud isn't configured on this instance.")
    base = f"Clients/{_sanitize_segment(who)}"

    sub = (args.get("path") or "").strip().strip("/")
    if sub:
        # Scope guard: never let a path escape the client's own folder.
        if ".." in sub.split("/") or sub.startswith("/") or "\\" in sub:
            return DispatchResult(False, "That path isn't allowed — I can only read inside the client's folder.")
        rel = f"{base}/{sub}"
        try:
            raw = nc.read_file(rel)
        except Exception as exc:  # noqa: BLE001
            return DispatchResult(True, f"Couldn't read {sub} for {who}: {exc}")
        import os as _os
        import tempfile

        suffix = _os.path.splitext(sub)[1] or ".bin"
        text = ""
        try:
            from hermes.command_center.extract import read_document_text

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
                tmp.write(raw)
                tmp.flush()
                text = read_document_text(tmp.name, ocr=True) or ""
        except Exception:  # noqa: BLE001
            text = ""
        if not text.strip():
            return DispatchResult(True, f"{sub} ({len(raw):,} bytes) — no readable text extracted.",
                                  {"path": rel, "bytes": len(raw)})
        clipped = text.strip()[:_DOC_MAX_CHARS]
        more = "\n…(truncated)" if len(text.strip()) > _DOC_MAX_CHARS else ""
        return DispatchResult(True, f"{sub} for {who}:\n{clipped}{more}", {"path": rel, "text_chars": len(text)})

    # No path → list the client's documents (categories + their files).
    try:
        top = nc.list_dir(base)
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Couldn't list {who}'s documents: {exc}")
    if not top:
        return DispatchResult(True, f"No document folder on file for '{who}'.")
    lines: list[str] = []
    files: list[dict[str, Any]] = []
    for entry in sorted(top, key=lambda e: (not e["is_dir"], e["name"].lower())):
        if entry["is_dir"]:
            try:
                kids = nc.list_dir(entry["path"])
            except Exception:  # noqa: BLE001
                kids = []
            docs = [k for k in kids if not k["is_dir"]]
            if docs:
                lines.append(f"• {entry['name']}/")
                for k in docs:
                    lines.append(f"    – {k['name']}")
                    files.append(k)
        else:
            lines.append(f"• {entry['name']}")
            files.append(entry)
    if not files:
        return DispatchResult(True, f"'{who}' has a folder but no documents on file yet.")
    return DispatchResult(True, f"{who} — documents on file:\n" + "\n".join(lines),
                          {"files": files, "base": base})


def _exec_list_intake(args: dict[str, Any]) -> DispatchResult:
    """Intake hub tool — the intake submission queue and its statuses (Supabase)."""
    from hermes.integrations.supabase_client import SupabaseClient

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Intake data unavailable: {exc}")
    params: dict[str, str] = {"order": "captured_at.desc"}
    status = (args.get("status") or "").strip()
    if status:
        params["status"] = f"eq.{status}"
    try:
        rows = supa.select(
            "intake_submissions",
            columns="intake_kind,client_identifier,lob_code,status,draft_summary,captured_at",
            params=params, limit=int(args.get("limit") or 15),
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Intake lookup failed: {exc}")
    if not rows:
        return DispatchResult(True, "No intake submissions match.")
    lines = [f"{r.get('client_identifier') or '?'} · {r.get('intake_kind') or ''} — {r.get('status')}" for r in rows]
    return DispatchResult(True, f"{len(rows)} submission(s):\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"submissions": rows})


def _exec_intake(args: dict[str, Any], *, confirmed: bool = False) -> DispatchResult:
    raw_text = args["raw_text"]
    if not confirmed:
        return DispatchResult(
            False,
            f"I would process this as a lead intake and create CRM records. Confirm to proceed.\n> {raw_text}",
            {"requires_confirmation": True, "action": "intake", "raw_text": raw_text},
        )
    from hermes.commands.agency_intake import handle as intake_handle
    from hermes.integrations.supabase_client import SupabaseClient

    try:
        supa = SupabaseClient()
    except Exception as exc:
        return DispatchResult(False, f"Intake staging unavailable: {exc}")
    return intake_handle(f"intake {raw_text}", supa=supa)


def _exec_email_search(args: dict[str, Any]) -> DispatchResult:
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
    "run_report": _exec_report,
    "renewals_overview": _exec_renewals,
    "web_research": _exec_web_research,
    "list_skills": _exec_list_skills,
    "email_search": _exec_email_search,
    "intake_lead": _exec_intake,
    "list_carriers": _exec_list_carriers,
    "match_carrier_appetite": _exec_carrier_appetite,
    "carrier_contacts": _exec_carrier_contacts,
    "resolve_class_code": _exec_resolve_class_code,
    "class_code_appetite": _exec_class_code_appetite,
    "commission_summary": _exec_commission_summary,
    "commission_shortfalls": _exec_commission_shortfalls,
    "find_client": _exec_find_client,
    "client_policies": _exec_client_policies,
    "ams_client_snapshot": _exec_ams_snapshot,
    "crm_client_activity": _exec_crm_activity,
    "client_documents": _exec_client_documents,
    "list_intake_submissions": _exec_list_intake,
}

_WRITE_TOOLS = {"intake_lead"}

# ---------------------------------------------------------------------------
# Per-hub AI scoping — each hub gets its own assistant that only carries that
# hub's tools + a hub persona overlay. hub=None → the full CRM assistant.
# Add a hub by adding a tool set here (and, optionally, a persona file).
# ---------------------------------------------------------------------------
_HUB_TOOLS: dict[str, set[str]] = {
    # Carrier / Intake Desk — appetite, and the class codes appetite is keyed by.
    # A class-code question IS an appetite question, so the classification tables
    # and the appetite bridge live in this lane rather than behind a hand-off:
    # `resolve_class_code` turns a trade into a code, `class_code_appetite` turns
    # that code into carriers, and `carrier_contacts` turns a carrier into a
    # submission path. Read-only — enrichment and ingest are proposed here and
    # written in the Carrier Hub.
    "carrier": {
        "list_carriers", "match_carrier_appetite", "carrier_contacts",
        "resolve_class_code", "class_code_appetite", "web_research",
    },
    "commissions": {"commission_summary", "commission_shortfalls"},
    # CRM Desk sees the whole client: the canonical book, live AMS (NowCerts),
    # the custom agency CRM's cases/tasks, and the client's Nextcloud documents —
    # all read-only. Carrier appetite, commissions, and intake stay out of its lane.
    "crm": {
        "find_client", "client_policies", "renewals_overview",
        "ams_client_snapshot", "crm_client_activity", "client_documents",
    },
    # Cases Desk — the service queue. Carries the CRM read tools because you
    # cannot triage a case without knowing who the client is and what they hold;
    # its persona extends the CRM one for the same reason. Note this set is
    # currently read-only: the case triage and write tools (queue by owner,
    # staleness, reassign, close, note) exist in the API but are not yet exposed
    # to the conversational agent, so the desk can reason but not yet act.
    "cases": {
        "find_client", "client_policies", "renewals_overview",
        "ams_client_snapshot", "crm_client_activity", "client_documents",
    },
    "intake": {"list_intake_submissions"},
}
_HUB_PERSONA: dict[str, str] = {
    "carrier": "carrier",
    "commissions": "commissions",
    "crm": "crm",
    "cases": "cases",
    "intake": "intake",
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
    text: str,
    *,
    confirmed: bool = False,
    conversation: list[dict[str, str]] | None = None,
    persona: str | None = None,
    hub: str | None = None,
) -> DispatchResult:
    """Process a natural language CRM request using the OpenAI agent.

    Args:
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
                result = executor(fn_args, confirmed=confirmed)
            else:
                result = executor(fn_args)

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
