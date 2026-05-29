"""Business web research and CRM enrichment."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from hermes.core.dispatcher import DispatchResult
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

if TYPE_CHECKING:
    from hermes.core.client import EspoClient

log = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """\
You are Hermes, the RSG Insurance Agency business research officer.
Research the business named by the user for insurance sales and underwriting context.

Return ONLY valid JSON with these keys:
- business_name: string or null
- claimed_services: list of strings
- short_summary: string
- website_url: string or null
- facebook_url: string or null
- linkedin_company_url: string or null
- owner_profiles: list of objects with name, title, linkedin_url, source_url
- phone: string or null
- address: string or null
- city: string or null
- state: string or null
- naics: string or null
- sic: string or null
- insurance_notes: list of strings
- confidence: one of low, medium, high
- sources: list of objects with title, url, note

Rules:
- Treat the provided internal Supabase classification candidates as preferred options when they fit the business evidence.
- Prefer the business' own website and public profile pages over directories.
- Distinguish what the business claims from what third-party directories say.
- Do not invent owner names, LinkedIn URLs, NAICS, SIC, addresses, or phone numbers.
- If sources conflict, say so in insurance_notes.
- Keep short_summary under 90 words.
"""


def _wants_save(text: str) -> bool:
    return bool(re.search(r"\b(save|write|update|put|log|store)\b.*\b(crm|account|espo)\b|\b(save|write|update|put|log|store)\b", text, re.I))


def _clean_query(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:research|enrich|investigate|look\s+up|web\s+research)\s+(?:business|account|company)?\s*",
        "",
        text,
        flags=re.I,
    )
    cleaned = re.sub(r"\b(?:and\s+)?(?:save|write|update|put|log|store)(?:\s+(?:to|in)\s+(?:crm|account|espo))?\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned


def _extract_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _research_business(query: str) -> dict[str, Any] | None:
    api_key = os.environ.get("HERMES_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    model = os.environ.get("HERMES_RESEARCH_MODEL") or os.environ.get("HERMES_OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)
    spine = _classification_spine(query)
    user_prompt = query
    if spine:
        user_prompt = (
            f"{query}\n\nInternal Supabase classification candidates:\n"
            f"{json.dumps(spine, indent=2, sort_keys=True)}"
        )
    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            tools=[{"type": "web_search_preview"}],
            temperature=0,
        )
    except Exception:
        log.exception("Business research failed")
        return None
    research = _extract_json(getattr(response, "output_text", "") or "")
    if research is not None:
        research["classification_spine"] = spine
        _apply_spine_defaults(research, spine)
    return research


def _keywords(query: str) -> list[str]:
    stop = {
        "the", "and", "for", "with", "company", "business", "llc", "inc", "corp", "co",
        "atlanta", "georgia", "ga", "research", "service", "services",
    }
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9&-]{2,}", query.lower())
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        token = token.strip("-")
        if token in stop or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result[:8]


def _ilike_any(columns: list[str], keyword: str) -> str:
    escaped = keyword.replace("*", "").replace(",", " ").replace("(", "").replace(")", "")
    return "(" + ",".join(f"{column}.ilike.*{escaped}*" for column in columns) + ")"


def _classification_spine(query: str) -> dict[str, Any]:
    try:
        supa = SupabaseClient()
    except SupabaseClientError:
        return {}

    candidates: dict[str, Any] = {"naics": [], "sic": []}
    for keyword in _keywords(query):
        if len(candidates["naics"]) < 5:
            try:
                rows = supa.select(
                    "naics_codes",
                    columns="id,naics_code,naics_title,description,industry_group,allowed,notes",
                    params={"or": _ilike_any(["naics_code", "naics_title", "description", "industry_group", "notes"], keyword)},
                    limit=5,
                )
                _extend_unique(candidates["naics"], rows, "naics_code")
            except SupabaseClientError:
                log.info("Supabase NAICS candidate lookup failed", exc_info=True)
        if len(candidates["sic"]) < 5:
            try:
                rows = supa.select(
                    "sic_codes",
                    columns="id,sic_code,sic_description,subcategory,subcategory_2,level_3_term,mapped_naics_id",
                    params={"or": _ilike_any(["sic_code", "sic_description", "subcategory", "subcategory_2", "level_3_term"], keyword)},
                    limit=5,
                )
                _extend_unique(candidates["sic"], rows, "sic_code")
            except SupabaseClientError:
                log.info("Supabase SIC candidate lookup failed", exc_info=True)
    return {k: v for k, v in candidates.items() if v}


def _extend_unique(target: list[dict[str, Any]], rows: list[dict[str, Any]], key: str) -> None:
    seen = {str(row.get(key)) for row in target}
    for row in rows:
        value = str(row.get(key))
        if value and value not in seen:
            target.append(row)
            seen.add(value)


def _apply_spine_defaults(research: dict[str, Any], spine: dict[str, Any]) -> None:
    if not research.get("naics"):
        naics = (spine.get("naics") or [{}])[0]
        if isinstance(naics, dict) and naics.get("naics_code"):
            research["naics"] = naics["naics_code"]
    if not research.get("sic"):
        sic = (spine.get("sic") or [{}])[0]
        if isinstance(sic, dict) and sic.get("sic_code"):
            research["sic"] = sic["sic_code"]


def _entity_fields(client: "EspoClient", entity: str) -> dict[str, Any]:
    metadata = client.get_metadata()
    if not isinstance(metadata, dict):
        return {}
    entity_def = metadata.get("entityDefs", {}).get(entity, {})
    fields = entity_def.get("fields", {}) if isinstance(entity_def, dict) else {}
    return fields if isinstance(fields, dict) else {}


def _first_existing(fields: dict[str, Any], *names: str) -> str | None:
    for name in names:
        if name in fields:
            return name
    return None


def _find_or_create_account(client: "EspoClient", business_name: str, *, create_if_missing: bool) -> dict[str, Any] | None:
    hits = client.search("Account", business_name, max_size=1, select="id,name,website,intel_website,intel_linkedin_url")
    if hits:
        return hits[0]
    if not create_if_missing:
        return None
    record = client.create("Account", {"name": business_name})
    return record if isinstance(record, dict) else None


def _source_lines(research: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for source in research.get("sources") or []:
        if not isinstance(source, dict):
            continue
        title = source.get("title") or "Source"
        url = source.get("url") or ""
        note = source.get("note") or ""
        line = f"- {title}: {url}"
        if note:
            line += f" ({note})"
        lines.append(line)
    return lines


def _note_body(research: dict[str, Any], query: str) -> str:
    lines = [
        "[Hermes Business Research]",
        f"Query: {query}",
        f"Business: {research.get('business_name') or 'Unknown'}",
        f"Confidence: {research.get('confidence') or 'unknown'}",
        "",
        "Summary:",
        research.get("short_summary") or "No summary returned.",
        "",
        "Claimed services:",
    ]
    services = research.get("claimed_services") or []
    lines.extend(f"- {service}" for service in services if service)
    lines.extend(["", "Public URLs:"])
    for label, key in [
        ("Website", "website_url"),
        ("Facebook", "facebook_url"),
        ("LinkedIn company", "linkedin_company_url"),
    ]:
        if research.get(key):
            lines.append(f"- {label}: {research[key]}")
    owner_profiles = research.get("owner_profiles") or []
    if owner_profiles:
        lines.extend(["", "Owner / leadership profiles:"])
        for profile in owner_profiles:
            if isinstance(profile, dict):
                name = profile.get("name") or "Unknown"
                title = profile.get("title") or ""
                url = profile.get("linkedin_url") or profile.get("source_url") or ""
                lines.append(f"- {name}" + (f", {title}" if title else "") + (f": {url}" if url else ""))
    notes = research.get("insurance_notes") or []
    if notes:
        lines.extend(["", "Insurance notes:"])
        lines.extend(f"- {note}" for note in notes if note)
    sources = _source_lines(research)
    if sources:
        lines.extend(["", "Sources:"])
        lines.extend(sources)
    spine = research.get("classification_spine") or {}
    if spine:
        lines.extend(["", "Supabase classification spine candidates:"])
        for row in spine.get("naics") or []:
            lines.append(f"- NAICS {row.get('naics_code')}: {row.get('naics_title')}")
        for row in spine.get("sic") or []:
            lines.append(f"- SIC {row.get('sic_code')}: {row.get('sic_description')}")
    lines.extend(["", "Raw research JSON:", json.dumps(research, indent=2, sort_keys=True)])
    return "\n".join(lines)


def _write_account_research(client: "EspoClient", research: dict[str, Any], query: str) -> dict[str, Any]:
    fields = _entity_fields(client, "Account")
    business_name = str(research.get("business_name") or query).strip()
    account = _find_or_create_account(client, business_name, create_if_missing=True)
    if not account or not account.get("id"):
        raise RuntimeError("Could not find or create Account for research result.")

    payload: dict[str, Any] = {}
    website_field = _first_existing(fields, "intel_website", "website", "websiteUrl")
    if website_field and research.get("website_url"):
        payload[website_field] = research["website_url"]
    linkedin_field = _first_existing(fields, "intel_linkedin_url", "linkedin_url")
    if linkedin_field and research.get("linkedin_company_url"):
        payload[linkedin_field] = research["linkedin_company_url"]
    notes_field = _first_existing(fields, "intel_website_notes", "intel_linkedin_notes", "description")
    if notes_field:
        services = ", ".join(research.get("claimed_services") or [])
        summary = research.get("short_summary") or ""
        payload[notes_field] = f"{summary}\n\nClaimed services: {services}".strip()
    if "intel_naics" in fields and research.get("naics"):
        payload["intel_naics"] = research["naics"]
    if "intel_sic" in fields and research.get("sic"):
        payload["intel_sic"] = research["sic"]

    updated = client.update("Account", str(account["id"]), payload) if payload else account
    note_payload = {
        "post": _note_body(research, query),
        "parentType": "Account",
        "parentId": str(account["id"]),
    }
    note = client.create("Note", note_payload)
    return {
        "account": updated if isinstance(updated, dict) else account,
        "note": note if isinstance(note, dict) else {"result": note},
        "fields_updated": sorted(payload.keys()),
    }


def _format_result(research: dict[str, Any], crm_result: dict[str, Any] | None = None) -> str:
    lines = [
        f"*Business Research: {research.get('business_name') or 'Unknown'}*",
        research.get("short_summary") or "No summary returned.",
        f"Confidence: {research.get('confidence') or 'unknown'}",
    ]
    services = research.get("claimed_services") or []
    if services:
        lines.append("Services: " + ", ".join(str(s) for s in services[:8]))
    urls = []
    for label, key in [("Website", "website_url"), ("Facebook", "facebook_url"), ("LinkedIn", "linkedin_company_url")]:
        if research.get(key):
            urls.append(f"{label}: {research[key]}")
    if urls:
        lines.extend(urls)
    owner_profiles = research.get("owner_profiles") or []
    if owner_profiles:
        lines.append("Owner/leadership profiles:")
        for profile in owner_profiles[:5]:
            if isinstance(profile, dict):
                lines.append(f"- {profile.get('name') or 'Unknown'}" + (f": {profile.get('linkedin_url')}" if profile.get("linkedin_url") else ""))
    sources = _source_lines(research)
    if sources:
        lines.append("Sources:")
        lines.extend(sources[:8])
    spine = research.get("classification_spine") or {}
    if spine.get("naics"):
        first = spine["naics"][0]
        lines.append(f"Supabase NAICS candidate: {first.get('naics_code')} - {first.get('naics_title')}")
    if spine.get("sic"):
        first = spine["sic"][0]
        lines.append(f"Supabase SIC candidate: {first.get('sic_code')} - {first.get('sic_description')}")
    if crm_result:
        account = crm_result.get("account") or {}
        lines.append(
            f"CRM updated: Account {account.get('name') or account.get('id')} | fields: "
            + (", ".join(crm_result.get("fields_updated") or []) or "note only")
        )
    return "\n".join(lines)


def handle(client: "EspoClient", text: str) -> DispatchResult:
    query = _clean_query(text)
    if len(query) < 2:
        return DispatchResult(False, "Tell me the business to research. Example: research business Acme Plumbing Atlanta")
    research = _research_business(query)
    if not research:
        return DispatchResult(
            False,
            "Business research needs an OpenAI key with web search support. Set OPENAI_API_KEY/HERMES_OPENAI_API_KEY and try again.",
        )
    save = _wants_save(text)
    crm_result = _write_account_research(client, research, query) if save else None
    return DispatchResult(
        True,
        _format_result(research, crm_result),
        {"research": research, "crm": crm_result or {}, "saved": save},
    )
