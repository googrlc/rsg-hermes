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
- Base NAICS/SIC classification on the business' OPERATIONS — what it actually does
  (its services and how it earns revenue) — not on its name or location. Read the
  operations from, in priority order: an explicit "operations:" clause in the
  request, the business' own website, then claimed_services. State the operations
  you relied on in insurance_notes.
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
    from hermes.core.llm_client import get_client, resolve_model, LLMConfigError

    try:
        client = get_client()
    except (LLMConfigError, ImportError):
        return None

    model = resolve_model(os.environ.get("HERMES_RESEARCH_MODEL"))
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
        # Re-rank the spine now that we know what the business actually does — the
        # researched operations are stronger class-code signal than the raw name.
        operations = _operations_text(query, research)
        if operations:
            spine = _classification_spine(query, operations)
        research["classification_spine"] = spine
        _apply_spine_defaults(research, spine)
    return research


def _keywords(*texts: str) -> list[str]:
    """Extract ranked keywords from one or more text sources, in the order given.
    Callers pass operations text FIRST so what the business *does* takes priority
    over its name/location when only the first 8 keywords survive the cap."""
    stop = {
        "the", "and", "for", "with", "company", "business", "llc", "inc", "corp", "co",
        "atlanta", "georgia", "ga", "research", "service", "services",
    }
    seen: set[str] = set()
    result: list[str] = []
    for text in texts:
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9&-]{2,}", (text or "").lower()):
            token = token.strip("-")
            if token in stop or token in seen:
                continue
            seen.add(token)
            result.append(token)
    return result[:8]


def _ilike_any(columns: list[str], keyword: str) -> str:
    escaped = keyword.replace("*", "").replace(",", " ").replace("(", "").replace(")", "")
    return "(" + ",".join(f"{column}.ilike.*{escaped}*" for column in columns) + ")"


# Text columns searched (recall) and their scoring weight (precision). Higher weight
# = a match there is stronger evidence the row fits the business.
_NAICS_FIELDS: list[tuple[str, int]] = [
    ("naics_title", 3), ("description", 2), ("industry_group", 1), ("notes", 1),
]
_SIC_FIELDS: list[tuple[str, int]] = [
    ("sic_description", 3), ("subcategory", 2), ("subcategory_2", 2), ("level_3_term", 1),
]
# Minimum relevance for a candidate to be auto-written as the default code. One
# whole-word match on a title (weight 3) clears it; loose/notes-only matches do not.
_CONFIDENT_SCORE = 3

_WORD_RE = re.compile(r"[a-z0-9&]+")

# The designated place operations keywords are read from. Operations describe what
# the business DOES ("installs and services HVAC systems"), which is what actually
# drives class-code selection — far more than the company name or city. Priority:
#   1. an explicit `operations:` / `ops:` / `does:` clause in the request, then
#   2. the researched claimed_services, then
#   3. the researched short_summary.
_OPERATIONS_RE = re.compile(r"\b(?:operations|ops|does|business\s+is)\s*[:\-]\s*(.+)$", re.I | re.S)


def _operations_text(query: str, research: dict[str, Any] | None = None) -> str:
    parts: list[str] = []
    explicit = _OPERATIONS_RE.search(query or "")
    if explicit:
        parts.append(explicit.group(1))
    if research:
        parts.extend(str(s) for s in (research.get("claimed_services") or []))
        if research.get("short_summary"):
            parts.append(str(research["short_summary"]))
    return " ".join(parts).strip()


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _score_row(row: dict[str, Any], keywords: list[str], weighted_fields: list[tuple[str, int]]) -> tuple[int, set[str]]:
    """Rank a candidate by how many distinct query keywords it matches, weighted by
    which field they land in. Whole-word matching (not substring) so 'air' matches
    'Air-Conditioning' but a token can't ride along inside an unrelated word."""
    kws = [k for k in keywords if len(k) >= 3] or keywords
    total = 0
    matched: set[str] = set()
    for column, weight in weighted_fields:
        field_tokens = _tokens(str(row.get(column) or ""))
        if not field_tokens:
            continue
        for kw in kws:
            if kw in field_tokens:
                total += weight
                matched.add(kw)
    # Phrase bonus: adjacent query keywords appearing together (e.g. "air conditioning")
    # are far stronger evidence than the same two words scattered apart.
    full = " ".join(str(row.get(c) or "") for c, _ in weighted_fields).lower()
    for a, b in zip(kws, kws[1:]):
        if f"{a} {b}" in full or f"{a}-{b}" in full:
            total += 2
    return total, matched


def _fetch_pool(supa: SupabaseClient, table: str, columns: str, search_cols: list[str], code_col: str, keywords: list[str]) -> list[dict[str, Any]]:
    """Gather a de-duplicated candidate pool via broad substring recall. A numeric
    keyword is matched against the code column; word keywords against text columns."""
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for keyword in keywords:
        cols = [code_col] if keyword.isdigit() else search_cols
        try:
            rows = supa.select(table, columns=columns, params={"or": _ilike_any(cols, keyword)}, limit=8)
        except SupabaseClientError:
            log.info("Supabase %s candidate lookup failed", table, exc_info=True)
            continue
        for row in rows:
            key = str(row.get(code_col))
            if key and key not in seen:
                seen.add(key)
                pool.append(row)
    return pool


def _rank(pool: list[dict[str, Any]], keywords: list[str], weighted_fields: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """Score the pool and return the top candidates. Rows that match no query
    keyword on any text field (score 0) are dropped rather than surfaced as noise."""
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for row in pool:
        score, matched = _score_row(row, keywords, weighted_fields)
        if score <= 0:
            continue
        enriched = dict(row)
        enriched["_relevance"] = score
        enriched["_matched_keywords"] = sorted(matched)
        scored.append((score, len(matched), enriched))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [row for _, _, row in scored[:5]]


def _classification_spine(query: str, operations: str = "") -> dict[str, Any]:
    try:
        supa = SupabaseClient()
    except SupabaseClientError:
        return {}

    # Operations keywords lead so what the business does drives the ranking.
    keywords = _keywords(operations, query)
    naics_pool = _fetch_pool(
        supa, "naics_codes",
        "id,naics_code,naics_title,description,industry_group,allowed,notes",
        [col for col, _ in _NAICS_FIELDS], "naics_code", keywords,
    )
    sic_pool = _fetch_pool(
        supa, "sic_codes",
        "id,sic_code,sic_description,subcategory,subcategory_2,level_3_term,mapped_naics_id",
        [col for col, _ in _SIC_FIELDS], "sic_code", keywords,
    )
    candidates = {
        "naics": _rank(naics_pool, keywords, _NAICS_FIELDS),
        "sic": _rank(sic_pool, keywords, _SIC_FIELDS),
    }
    return {k: v for k, v in candidates.items() if v}


def _apply_spine_defaults(research: dict[str, Any], spine: dict[str, Any]) -> None:
    """Fill a missing code from the top candidate only when it is a confident match.
    A weak/loose top candidate is left for human confirmation, not silently written."""
    if not research.get("naics"):
        naics = (spine.get("naics") or [{}])[0]
        if isinstance(naics, dict) and naics.get("naics_code") and naics.get("_relevance", 0) >= _CONFIDENT_SCORE:
            research["naics"] = naics["naics_code"]
    if not research.get("sic"):
        sic = (spine.get("sic") or [{}])[0]
        if isinstance(sic, dict) and sic.get("sic_code") and sic.get("_relevance", 0) >= _CONFIDENT_SCORE:
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
