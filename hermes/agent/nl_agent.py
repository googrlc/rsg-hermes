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

from hermes.core.dispatch import DispatchResult

from hermes import carriers as _carriers
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
                "by name or line of business. Returns each carrier's lines, general agent, "
                "underwriting contacts (name/role/email/phone), hotline, agency code, "
                "website and agent portal login. Use for 'which carriers do we work with', "
                "'who's our GA for X', 'who's our underwriter at X', 'what's the agent "
                "portal URL for X', or 'what's our agency code with X'."
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
                    "class_or_naics": {"type": "string", "description": "Class code, NAICS, or an operations keyword. Accepts 'ISO 91341' or '91341'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_class_code",
            "description": (
                "The class-code reference (gl_class_codes / wc_class_codes) — what a code "
                "MEANS, not who writes it. Two directions: a code in ('ISO 91341', '5645') "
                "returns its manual description, scope and category; a business description "
                "in ('finish carpentry, cabinets and countertops') returns ranked candidate "
                "codes. Use for 'what is the scope of X', 'what code applies to this "
                "operation', '91341 or 91340?'. Pair with match_carrier_appetite, which "
                "answers who will actually write it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A class code, or a description of the operation."},
                    "code_system": {"type": "string", "description": "Optional filter: 'gl' or 'wc'."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "appointments_by_line",
            "description": (
                "The panel grouped by line of business — which carriers RSG can place each "
                "line with, and whether the appointment is direct or through a general agent. "
                "Use for 'who can write this?', 'appointments by line', 'what markets do we "
                "have for work comp', 'do we have anyone for X'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "line_of_business": {"type": "string", "description": "Optional LOB filter; omit for the whole panel."},
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
#   _DEFAULT_PERSONA  — identity, audience, and voice. A persona file
#                       (HERMES_PERSONA_FILE) or a per-request bundled persona
#                       replaces this, so one instance can speak as Gretchen's
#                       assistant or Lamar's without running a second container.
#   _PLATFORM_GUIDE   — capabilities, field aliases, tool-routing, and write-safety
#                       rules. Persona-agnostic; shared by every request.
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
    from hermes.agent.skills_catalog import render_text

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
        # Embed carrier_contacts: this tool is advertised as answering "who's our
        # underwriter at X", but the old column list carried no contact data at all,
        # so those questions could only be answered by guessing. Credentials (agency
        # code, portal login) ride along for "what's the agent portal URL for X".
        rows = supa.select(
            "carriers",
            columns=(
                "id,name,segment,lines_of_business,general_agent,appetite_notes,"
                "underwriting_hotline,agency_code,website,agent_login,"
                "carrier_contacts(name,role,email,phone,region)"
            ),
            params=params, limit=60,
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Carrier lookup failed: {exc}")
    if not rows:
        return DispatchResult(True, "No carriers matched that filter.")
    lines = []
    for r in rows:
        bits = [r.get("name") or "?"]
        tail = " · ".join(x for x in (_join(r.get("segment")), _join(r.get("lines_of_business"))) if x)
        if tail:
            bits.append(tail)
        if r.get("general_agent"):
            bits.append(f"via {r['general_agent']}")
        lines.append(" — ".join(bits))
        for c in r.get("carrier_contacts") or []:
            detail = " · ".join(
                x for x in (c.get("role"), c.get("email"), c.get("phone"), c.get("region")) if x
            )
            lines.append(f"    contact: {c.get('name') or '?'}" + (f" — {detail}" if detail else ""))
        if r.get("underwriting_hotline"):
            lines.append(f"    hotline: {r['underwriting_hotline']}")
        if r.get("agent_login"):
            lines.append(f"    agent portal / login: {r['agent_login']}")
        if r.get("website"):
            lines.append(f"    website: {r['website']}")
        if r.get("agency_code"):
            lines.append(f"    agency code: {r['agency_code']}")
    return DispatchResult(True, f"{len(rows)} carriers:\n" + "\n".join(f"• {ln}" for ln in lines),
                          {"carriers": rows})


def _join(v: Any) -> str:
    """Render a Postgres text[] (or a scalar) as a readable comma list."""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x)
    return str(v or "")


# Shared with the /api/carriers endpoint so both answer "who writes this?" alike.
_norm_code = _carriers.norm_code


def _exec_lookup_class_code(args: dict[str, Any]) -> DispatchResult:
    """Carrier hub tool — the class-code reference over gl_class_codes (1,154) and
    wc_class_codes (499): what a code MEANS, plus the reverse lookup from a business
    description to candidate codes.

    Deliberately separate from appetite: `match_carrier_appetite` answers who WRITES
    a code, this answers what the code IS. A correctly classified risk on a carrier
    with no appetite is still a dead submission, and vice versa.
    """
    from hermes.integrations.supabase_client import SupabaseClient

    query = (args.get("query") or "").strip()
    system = (args.get("code_system") or "").strip().lower()
    if not query:
        return DispatchResult(False, "Give a class code or a description of the operation.")
    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Class-code reference unavailable: {exc}")

    rows: list[dict[str, Any]] = []
    try:
        if system in ("", "gl"):
            for r in supa.select(
                "gl_class_codes",
                columns="id,gl_code,description,category,subcategory,search_keywords,typical_businesses,notes,max_stories",
                params={"order": "gl_code.asc"}, limit=2000,
            ):
                rows.append({**r, "system": "gl", "code": r.get("gl_code"),
                             "typical": r.get("typical_businesses")})
        if system in ("", "wc"):
            for r in supa.select(
                "wc_class_codes",
                columns="id,wc_code,description,category,subcategory,search_keywords,typical_duties,notes,state",
                params={"order": "wc_code.asc"}, limit=2000,
            ):
                rows.append({**r, "system": "wc", "code": r.get("wc_code"),
                             "typical": r.get("typical_duties")})
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Class-code lookup failed: {exc}")
    if not rows:
        return DispatchResult(True, "The class-code tables came back empty.")

    wanted = _norm_code(query)
    terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 2]
    scored: list[tuple[int, dict[str, Any]]] = []
    for r in rows:
        score = 100 if wanted and _norm_code(r.get("code")) == wanted else 0
        fields = (
            (str(r.get("description") or "").lower(), 4),
            (str(r.get("search_keywords") or "").lower(), 3),
            (str(r.get("typical") or "").lower(), 3),
            (str(r.get("notes") or "").lower(), 2),
            (f"{r.get('category') or ''} {r.get('subcategory') or ''}".lower(), 1),
        )
        for t in terms:
            for hay, weight in fields:
                if hay and t in hay:
                    score += weight
                    break
        if score:
            scored.append((score, r))
    if not scored:
        return DispatchResult(True, f"No manual class code matches '{query}'. Most codes have no "
                                    f"keywords recorded yet, so only the official description is "
                                    f"searchable — try the code number, or fewer words.")
    scored.sort(key=lambda p: -p[0])

    out = []
    for _, r in scored[:6]:
        out.append(f"{str(r['system']).upper()} {r.get('code')} — {r.get('description')}")
        if r.get("notes"):
            out.append(f"    scope: {r['notes']}")
        if r.get("typical"):
            out.append(f"    typical: {r['typical']}")
        # Keywords carry the operation vocabulary ("countertops", "trim") that the
        # official manual description leaves out — it's often the only place the
        # actual covered work is spelled out.
        if r.get("search_keywords"):
            out.append(f"    covers: {r['search_keywords']}")
        if r.get("category") or r.get("subcategory"):
            out.append(f"    category: {' / '.join(x for x in (r.get('category'), r.get('subcategory')) if x)}")
        if r.get("max_stories"):
            out.append(f"    max stories: {r['max_stories']}")
        if not (r.get("notes") or r.get("search_keywords") or r.get("typical")):
            out.append("    (manual description only — no scope detail recorded for this code yet)")
    return DispatchResult(True, "\n".join(out), {"codes": [r for _, r in scored[:6]]})


def _exec_appointments_by_line(args: dict[str, Any]) -> DispatchResult:
    """Carrier hub tool — the panel inverted: for each line of business, which
    carriers RSG can actually place it with. "Who can write this?" is usually a
    question about the appointment list, not about one carrier."""
    from hermes.integrations.supabase_client import SupabaseClient

    try:
        supa = SupabaseClient()
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Carrier book unavailable: {exc}")
    want = (args.get("line_of_business") or "").strip().lower()
    try:
        carriers = supa.select("carriers", columns="id,name,general_agent,lines_of_business",
                               params={"order": "name.asc", "is_active": "eq.true"}, limit=200)
        appetite = supa.select("carrier_appetite", columns="carrier_id,lob,appetite_level",
                               params={"active": "eq.true"}, limit=400)
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(False, f"Appointment lookup failed: {exc}")

    levels = {(a.get("carrier_id"), str(a.get("lob") or "").lower()): a.get("appetite_level")
              for a in appetite}
    by_line: dict[str, list[str]] = {}
    for c in carriers:
        lobs = c.get("lines_of_business") or []
        if not isinstance(lobs, list):
            lobs = [lobs]
        # An appetite row is itself evidence of an appointment on that line, even
        # when lines_of_business on the carrier record was never filled in.
        for a in appetite:
            if a.get("carrier_id") == c.get("id") and a.get("lob") and a["lob"] not in lobs:
                lobs.append(a["lob"])
        for lob in lobs:
            if not lob or (want and want not in str(lob).lower()):
                continue
            label = c.get("name") or "?"
            label += f" (via {c['general_agent']})" if c.get("general_agent") else " (direct)"
            level = levels.get((c.get("id"), str(lob).lower()))
            if level:
                label += f" — {level}"
            by_line.setdefault(str(lob), []).append(label)

    if not by_line:
        return DispatchResult(True, "No appointments on file for that line. This reflects only the "
                                    "carrier book — confirm directly before relying on it.")
    out = []
    for lob in sorted(by_line):
        out.append(f"{lob} ({len(by_line[lob])}):")
        out.extend(f"  • {name}" for name in sorted(by_line[lob]))
    return DispatchResult(True, "Appointments by line:\n" + "\n".join(out), {"by_line": by_line})


def _exec_carrier_appetite(args: dict[str, Any]) -> DispatchResult:
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
    # states_approved is a text[] (e.g. ["GA"] or ["ALL"]) — filter in Python.
    rows = _carriers.filter_by_state(rows, state)
    bridge_note = ""
    if cls:
        # The bridge (carrier_appetite_class_codes) is the authoritative carrier↔code
        # link and carries eligibility. Check it first — a code explicitly written in
        # the carrier's own source beats any amount of keyword matching on notes.
        code = _norm_code(cls)
        try:
            linked = supa.select(
                "vw_carrier_appetite_class_resolved",
                columns="carrier_name,lob,eligibility,match_method,state_scope,restrictions,code",
                params={}, limit=200,
            )
        except Exception:  # noqa: BLE001
            linked = []
        hits = [b for b in linked if code and _norm_code(b.get("code")) == code]
        if hits:
            names = {(h.get("carrier_name"), h.get("lob")) for h in hits
                     if h.get("eligibility") != "prohibited"}
            banned = [h for h in hits if h.get("eligibility") == "prohibited"]
            bridge_note = "\n".join(
                f"• {h.get('carrier_name')} — {h.get('lob')}: {h.get('eligibility')}"
                + (f" [{h['state_scope']} only]" if h.get("state_scope") else "")
                + (f" — {h['restrictions']}" if h.get("restrictions") else "")
                + f" ({h.get('match_method')})"
                for h in hits)
            matched = [r for r in rows
                       if (r.get("carrier_name"), r.get("lob")) in names]
            if matched:
                rows = matched
            if banned:
                bridge_note += "\n(prohibited links above are knockouts — do not submit)"
        else:
            # Token-based, not whole-phrase: a producer asks about "interior carpentry"
            # while the record says "Carpentry — interior", and a substring match misses.
            tokens = [t for t in re.split(r"[^a-z0-9]+", cls.lower()) if len(t) > 2]

            def _hay(r: dict[str, Any]) -> str:
                return " ".join(str(r.get(k) or "") for k in
                                ("key_requirements", "notes", "exclusions", "lob", "class_codes")).lower()

            narrowed = [r for r in rows if tokens and all(t in _hay(r) for t in tokens)]
            if not narrowed:
                narrowed = [r for r in rows if tokens and any(t in _hay(r) for t in tokens)]
            rows = narrowed or rows  # fall back to the LOB/state set if the filter is too tight
    if not rows:
        return DispatchResult(True, "No carriers with matching appetite on file. This reflects only the "
                                    "appetite table — confirm directly with the carrier before relying on it.")
    out = []
    for r in rows:
        prem = ""
        if r.get("min_premium") or r.get("max_premium"):
            prem = f" · ${r.get('min_premium') or 0}–${r.get('max_premium') or '?'}"
        out.append(f"{r.get('carrier_name')} — {r.get('lob')} ({r.get('appetite_level') or 'appetite'}){prem}")
    msg = f"{len(rows)} carrier appetite matches:\n" + "\n".join(f"• {x}" for x in out)
    if bridge_note:
        msg += f"\n\nExplicit class-code links for this code:\n{bridge_note}"
    return DispatchResult(True, msg, {"matches": rows})


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
        from hermes.integrations.nowcerts_client import NowCertsClient
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
    "lookup_class_code": _exec_lookup_class_code,
    "appointments_by_line": _exec_appointments_by_line,
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
    "carrier": {"list_carriers", "match_carrier_appetite", "lookup_class_code",
                "appointments_by_line", "web_research"},
    "commissions": {"commission_summary", "commission_shortfalls"},
    # CRM Desk sees the whole client: the canonical book, live AMS (NowCerts),
    # the custom agency CRM's cases/tasks, and the client's Nextcloud documents —
    # all read-only. Carrier appetite, commissions, and intake stay out of its lane.
    "crm": {
        "find_client", "client_policies", "renewals_overview",
        "ams_client_snapshot", "crm_client_activity", "client_documents",
    },
    # Renewals Desk — the retention worklist. The portal has had a Renewals
    # screen all along and this file had no hub for it, so every renewal question
    # arrived as an unknown hub. Client context comes along because "should we
    # remarket this?" is unanswerable without knowing what else they hold.
    "renewals": {
        "renewals_overview", "find_client", "client_policies",
        "ams_client_snapshot", "crm_client_activity",
    },
    # Cases Desk — the service queue. Carries the CRM read tools because you
    # cannot triage a case without knowing who the client is and what they hold;
    # its persona extends the CRM one for the same reason. Read-only for now: the
    # case write tools exist in the API but are not exposed to the agent yet.
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
    "renewals": "renewals",
    "intake": "intake",
}

# The hub name arrives from the portal as its screen name, and one of them does
# not match: its Finance screen is this file's "commissions" desk. That single
# mismatch meant the Finance desk resolved to no persona and — because an
# unknown hub falls through to the FULL tool list — answered money questions
# with every carrier and CRM tool loaded and no instruction about provenance.
# Alias rather than rename: "commissions" is what the tables, the skill and the
# persona file are all called.
_HUB_ALIASES: dict[str, str] = {"finance": "commissions"}


def _hub_key(hub: str | None) -> str:
    """Normalise an incoming hub name to the key this module uses."""
    key = (hub or "").strip().lower()
    return _HUB_ALIASES.get(key, key)


# Which model group each desk gets. Anything not listed runs on the default
# group, which is where every desk ran until now.
#
# The split is by cost of being wrong, not prestige. A CRM lookup is "read a row
# back to me" — a small model does that as well as a large one. A class code, an
# appetite call and a commission shortfall are judgments the agency acts on:
# quote the wrong WC code and the premium is wrong; call a seed row a shortpay
# and someone chases a carrier for money they were never owed.
#
# The volume argument that would normally favour the cheap model does not apply:
# these desks answer a handful of questions a day, so this saves fractions of a
# cent on exactly the answers that matter.
_HUB_MODEL: dict[str, str] = {
    "carrier": "hard_judgment_escalation",
    "commissions": "hard_judgment_escalation",
    "renewals": "hard_judgment_escalation",
}


def _scoped_tools(tools: list[dict[str, Any]], hub: str | None) -> list[dict[str, Any]]:
    """Filter a tool list to a hub's allowed set. Unknown/None hub → unchanged.

    Unchanged means every tool, which is right for the portal's Home screen
    ("All desks") and wrong for a desk whose name simply failed to match — so the
    names have to agree. ``_hub_key`` is what makes them.
    """
    allowed = _HUB_TOOLS.get(_hub_key(hub))
    if allowed is None:
        return tools
    return [t for t in tools if t["function"]["name"] in allowed]

_report_dispatcher: Any = None


def _get_report_dispatcher() -> Any:
    """Return a cached Dispatcher(use_openai=False) for running reports."""
    global _report_dispatcher
    if _report_dispatcher is None:
        from hermes.agent.dispatcher import Dispatcher
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

    model = resolve_model(_HUB_MODEL.get(_hub_key(hub)))

    persona_key = persona or _HUB_PERSONA.get(_hub_key(hub))
    messages: list[dict[str, Any]] = [{"role": "system", "content": _compose_system_prompt(persona_key)}]
    if conversation:
        messages.extend(conversation)
    messages.append({"role": "user", "content": text})

    active_tools = _scoped_tools(_TOOLS, hub)

    def _complete(with_model: str):
        return oai.chat.completions.create(
            model=with_model,
            messages=messages,
            tools=active_tools,
            tool_choice="auto",
            temperature=0,
        )

    try:
        response = _complete(model)
    except Exception as exc:
        fallback = resolve_model(None)
        if model == fallback:
            log.exception("OpenAI agent call failed")
            return DispatchResult(False, f"AI agent error: {exc}")
        # The desk asked for a stronger model group and the proxy would not serve
        # it — a group that isn't configured, a budget cap, a model without
        # tool-calling. A degraded answer beats no answer, so drop to the default
        # and say so in the log; doing it silently is how a desk ends up running
        # on the wrong model for a month.
        log.warning("hub model %r failed (%s); falling back to %r", model, exc, fallback)
        model = fallback
        try:
            response = _complete(model)
        except Exception as exc2:
            log.exception("OpenAI agent call failed on fallback model")
            return DispatchResult(False, f"AI agent error: {exc2}")

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
