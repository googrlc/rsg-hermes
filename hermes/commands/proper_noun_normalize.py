"""Proper noun normalization via staging table with AI-assisted reasoning.

This module handles edge cases for entity names (accounts, contacts) by:
1. Detecting non-canonical proper nouns (SHOUTING, all lower, typos, spacing issues)
2. Staging proposed normalizations in Supabase for review
3. Using OpenAI to reason about ambiguous cases
4. Providing clear explanations before applying changes
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)


@dataclass
class NormalizationCandidate:
    """A record that may need proper noun normalization."""
    entity_type: str
    record_id: str
    current_name: str
    suggested_name: str
    confidence: float  # 0.0 - 1.0
    reasons: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


# Common business suffixes that should stay uppercase
BUSINESS_SUFFIXES = frozenset({
    "LLC", "INC", "LTD", "CO", "LP", "LLP", "USA", "CORP", "PC", "PLLC",
    "DBA", "HQ", "NA", "US", "NY", "CA", "TX", "FL"
})

# Common name prefixes that need special handling
NAME_PREFIXES = frozenset({"Mr", "Mrs", "Ms", "Dr", "Prof", "Jr", "Sr", "II", "III", "IV"})


def _is_shouting(text: str) -> bool:
    """Check if text is ALL CAPS (excluding suffixes)."""
    words = [w for w in text.split() if w.isalpha()]
    if not words:
        return False
    return all(w.isupper() for w in words)


def _is_all_lower(text: str) -> bool:
    """Check if text is all lowercase."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) > 0 and all(c.islower() for c in letters)


def _has_spacing_issues(text: str) -> bool:
    """Detect double spaces, leading/trailing spaces, or missing spaces after punctuation."""
    return (
        "  " in text
        or text != text.strip()
        or bool(re.search(r"[,.](?!\s)\w", text))
    )


def _has_mixed_case_anomalies(text: str) -> bool:
    """Detect weird capitalization like 'jOhN dOe' or 'ACME inc'."""
    words = text.split()
    for word in words:
        if word.upper() in BUSINESS_SUFFIXES:
            continue
        alpha_chars = [c for c in word if c.isalpha()]
        if not alpha_chars:
            continue
        # Check for random capitalization patterns
        upper_count = sum(1 for c in alpha_chars if c.isupper())
        lower_count = sum(1 for c in alpha_chars if c.islower())
        if upper_count > 0 and lower_count > 0:
            # Mixed case - check if it's title case (acceptable)
            if word != word.capitalize() and word != word.title():
                if word.lower() not in {"of", "the", "and", "for"}:
                    return True
    return False


def _canonicalize_business_name(name: str) -> str:
    """Convert business name to proper title case with correct suffix handling."""
    words = [w for w in name.strip().split() if w]
    result: list[str] = []
    
    for i, word in enumerate(words):
        # Clean punctuation
        clean = word.strip(",.")
        upper = clean.upper()
        
        # Keep business suffixes uppercase
        if upper in BUSINESS_SUFFIXES:
            result.append(upper)
            continue
        
        # Keep state codes uppercase (2 letters)
        if len(clean) == 2 and clean.isalpha():
            # Could be a state code - keep uppercase
            result.append(upper)
            continue
        
        # First word always capitalized
        if i == 0:
            result.append(clean.capitalize())
            continue
        
        # Minor words in middle stay lowercase (unless first)
        if clean.lower() in {"of", "the", "and", "for", "in", "on", "at", "to"}:
            result.append(clean.lower())
            continue
        
        # Default: title case
        result.append(clean.capitalize())
    
    return " ".join(result)


def _canonicalize_person_name(name: str) -> str:
    """Convert person name to proper format."""
    parts = name.strip().split()
    result: list[str] = []
    
    for part in parts:
        clean = part.strip(",.")
        
        # Handle prefixes
        if clean.capitalize() in NAME_PREFIXES:
            result.append(clean.capitalize())
            continue
        
        # Handle roman numerals
        if clean.upper() in {"II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}:
            result.append(clean.upper())
            continue
        
        # Default: capitalize
        result.append(clean.capitalize())
    
    return " ".join(result)


def detect_normalization_candidates(
    client: EspoClient,
    *,
    limit: int = 100,
) -> list[NormalizationCandidate]:
    """Scan CRM for records with non-canonical proper nouns."""
    candidates: list[NormalizationCandidate] = []
    
    # Scan Accounts
    try:
        body = client.get("Account", params={
            "maxSize": limit,
            "select": "id,name,accountType",
        })
        accounts = body.get("list", []) if isinstance(body, dict) else []
        
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            name = str(acc.get("name") or "").strip()
            if not name:
                continue
            
            reasons: list[str] = []
            confidence = 0.0
            
            if _is_shouting(name):
                reasons.append("ALL_CAPS")
                confidence = max(confidence, 0.9)
            
            if _is_all_lower(name):
                reasons.append("all_lowercase")
                confidence = max(confidence, 0.85)
            
            if _has_spacing_issues(name):
                reasons.append("spacing_issues")
                confidence = max(confidence, 0.7)
            
            if _has_mixed_case_anomalies(name):
                reasons.append("mixed_case_anomaly")
                confidence = max(confidence, 0.8)
            
            if reasons:
                suggested = _canonicalize_business_name(name)
                candidates.append(NormalizationCandidate(
                    entity_type="Account",
                    record_id=acc.get("id", ""),
                    current_name=name,
                    suggested_name=suggested,
                    confidence=confidence,
                    reasons=reasons,
                    context={"accountType": acc.get("accountType")},
                ))
    except Exception as exc:
        log.exception("Failed to scan accounts for normalization")
    
    # Scan Contacts
    try:
        body = client.get("Contact", params={
            "maxSize": limit,
            "select": "id,name,firstName,lastName,emailAddress",
        })
        contacts = body.get("list", []) if isinstance(body, dict) else []
        
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            
            # Build full name
            full_name = str(contact.get("name") or "").strip()
            if not full_name:
                first = str(contact.get("firstName") or "").strip()
                last = str(contact.get("lastName") or "").strip()
                full_name = " ".join(p for p in [first, last] if p).strip()
            
            if not full_name:
                continue
            
            reasons: list[str] = []
            confidence = 0.0
            
            if _is_shouting(full_name):
                reasons.append("ALL_CAPS")
                confidence = max(confidence, 0.9)
            
            if _is_all_lower(full_name):
                reasons.append("all_lowercase")
                confidence = max(confidence, 0.85)
            
            if _has_mixed_case_anomalies(full_name):
                reasons.append("mixed_case_anomaly")
                confidence = max(confidence, 0.8)
            
            if reasons:
                suggested = _canonicalize_person_name(full_name)
                candidates.append(NormalizationCandidate(
                    entity_type="Contact",
                    record_id=contact.get("id", ""),
                    current_name=full_name,
                    suggested_name=suggested,
                    confidence=confidence,
                    reasons=reasons,
                    context={"email": contact.get("emailAddress")},
                ))
    except Exception as exc:
        log.exception("Failed to scan contacts for normalization")
    
    return candidates


def stage_normalizations(
    supa: SupabaseClient,
    candidates: list[NormalizationCandidate],
    *,
    batch_id: str | None = None,
) -> list[dict[str, Any]]:
    """Insert normalization candidates into staging table for review."""
    import uuid
    from datetime import datetime
    
    if batch_id is None:
        batch_id = str(uuid.uuid4())[:8]
    
    staged_records: list[dict[str, Any]] = []
    timestamp = datetime.utcnow().isoformat()
    
    for candidate in candidates:
        payload = {
            "batch_id": batch_id,
            "entity_type": candidate.entity_type,
            "record_id": candidate.record_id,
            "current_value": candidate.current_name,
            "proposed_value": candidate.suggested_name,
            "confidence_score": candidate.confidence,
            "detection_reasons": json.dumps(candidate.reasons),
            "context_data": json.dumps(candidate.context),
            "status": "pending_review",
            "created_at": timestamp,
            "processing_status": "queued",
        }
        
        try:
            result = supa.insert("proper_noun_staging", payload)
            if isinstance(result, dict):
                payload["staging_id"] = result.get("id")
            staged_records.append(payload)
        except Exception as exc:
            log.exception("Failed to stage normalization for %s", candidate.record_id)
    
    return staged_records


def reason_about_edge_case(
    candidate: NormalizationCandidate,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Use OpenAI to reason about ambiguous normalization cases."""
    if api_key is None:
        api_key = os.environ.get("HERMES_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    
    if not api_key:
        return {
            "recommendation": "no_ai_available",
            "explanation": "OpenAI API key not configured. Using rule-based suggestion.",
            "approved": False,
        }
    
    try:
        from openai import OpenAI
    except ImportError:
        return {
            "recommendation": "no_ai_sdk",
            "explanation": "OpenAI SDK not installed. Using rule-based suggestion.",
            "approved": False,
        }
    
    model = model or os.environ.get("HERMES_OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)
    
    prompt = f"""You are an expert data quality assistant for an insurance agency CRM.

Analyze this proper noun normalization case:

**Entity Type:** {candidate.entity_type}
**Current Name:** "{candidate.current_name}"
**Suggested Name:** "{candidate.suggested_name}"
**Detection Reasons:** {", ".join(candidate.reasons)}
**Context:** {json.dumps(candidate.context)}

Your task:
1. Determine if the suggested normalization is correct
2. Identify any edge cases (e.g., brand names that should stay unusual, people who prefer unique capitalization)
3. Provide a clear explanation

Respond with ONLY valid JSON:
{{
    "approved": true/false,
    "confidence": 0.0-1.0,
    "explanation": "brief explanation",
    "alternative_suggestion": "optional alternative or null",
    "edge_case_detected": true/false,
    "edge_case_notes": "notes about any edge case or null"
}}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a data quality expert. Respond with JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception as exc:
        log.exception("AI reasoning failed for %s", candidate.record_id)
        return {
            "approved": False,
            "confidence": 0.0,
            "explanation": f"AI analysis failed: {exc}",
            "alternative_suggestion": None,
            "edge_case_detected": False,
            "edge_case_notes": None,
        }


def apply_normalizations(
    client: EspoClient,
    candidates: list[NormalizationCandidate],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply normalizations to CRM records.
    
    Args:
        client: EspoCRM client
        candidates: List of candidates to normalize
        dry_run: If True, only preview changes without writing
    
    Returns:
        Summary of applied/pending changes
    """
    results = {
        "applied": [],
        "failed": [],
        "skipped": [],
        "dry_run": dry_run,
    }
    
    for candidate in candidates:
        if candidate.confidence < 0.5:
            results["skipped"].append({
                "record_id": candidate.record_id,
                "reason": "low_confidence",
                "confidence": candidate.confidence,
            })
            continue
        
        if dry_run:
            results["applied"].append({
                "record_id": candidate.record_id,
                "entity_type": candidate.entity_type,
                "old_name": candidate.current_name,
                "new_name": candidate.suggested_name,
                "confidence": candidate.confidence,
                "status": "preview",
            })
            continue
        
        try:
            updated = client.update(candidate.entity_type, candidate.record_id, {
                "name": candidate.suggested_name,
            })
            results["applied"].append({
                "record_id": candidate.record_id,
                "entity_type": candidate.entity_type,
                "old_name": candidate.current_name,
                "new_name": candidate.suggested_name,
                "confidence": candidate.confidence,
                "status": "applied",
                "updated_record": updated if isinstance(updated, dict) else None,
            })
        except Exception as exc:
            results["failed"].append({
                "record_id": candidate.record_id,
                "error": str(exc),
            })
    
    return results


def handle_normalize_command(
    client: EspoClient,
    text: str,
    *,
    supa: SupabaseClient | None = None,
) -> DispatchResult:
    """Handle natural language normalization commands.
    
    Examples:
        - "normalize proper nouns"
        - "find bad names in CRM"
        - "preview name fixes"
        - "apply name normalizations"
    """
    from hermes.core.dispatcher import DispatchResult
    
    t = text.lower()
    
    # Detect intent
    wants_preview = any(kw in t for kw in ["preview", "show", "list", "find", "detect"])
    wants_apply = any(kw in t for kw in ["apply", "fix", "update", "correct"])
    wants_ai_reasoning = any(kw in t for kw in ["reason", "analyze", "explain", "ai"])
    
    # Step 1: Detect candidates
    candidates = detect_normalization_candidates(client)
    
    if not candidates:
        return DispatchResult(
            True,
            "No proper noun normalization issues detected. Your CRM names look clean!",
            {"candidates_found": 0},
        )
    
    # Step 2: Optionally use AI reasoning for edge cases
    ai_analysis: list[dict[str, Any]] = []
    if wants_ai_reasoning or not wants_apply:
        api_key = os.environ.get("HERMES_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        for candidate in candidates[:10]:  # Limit to top 10 for speed
            analysis = reason_about_edge_case(candidate, api_key=api_key)
            ai_analysis.append({
                "record_id": candidate.record_id,
                "current": candidate.current_name,
                "suggested": candidate.suggested_name,
                **analysis,
            })
    
    # Step 3: Stage to Supabase if available
    staged_count = 0
    if supa:
        try:
            staged = stage_normalizations(supa, candidates)
            staged_count = len(staged)
        except Exception as exc:
            log.warning("Failed to stage normalizations: %s", exc)
    
    # Step 4: Build response
    lines = [f"*Proper Noun Normalization Report*"]
    lines.append(f"Found {len(candidates)} records with potential issues:\n")
    
    # Group by entity type
    by_entity: dict[str, list[NormalizationCandidate]] = {}
    for c in candidates:
        by_entity.setdefault(c.entity_type, []).append(c)
    
    for entity_type, entity_candidates in sorted(by_entity.items()):
        lines.append(f"*{entity_type}s* ({len(entity_candidates)} issues)")
        for c in entity_candidates[:10]:  # Show top 10 per entity
            reasons_str = ", ".join(c.reasons)
            lines.append(f"  - `{c.current_name}` → `{c.suggested_name}`")
            lines.append(f"    Confidence: {c.confidence:.0%} | Reasons: {reasons_str}")
        
        if len(entity_candidates) > 10:
            lines.append(f"    ... and {len(entity_candidates) - 10} more")
        lines.append("")
    
    # Add AI analysis if available
    if ai_analysis:
        lines.append("*AI Analysis (top cases):*")
        for analysis in ai_analysis[:5]:
            status = "✓" if analysis.get("approved") else "⚠"
            lines.append(f"  {status} `{analysis['current']}` → `{analysis['suggested']}`")
            lines.append(f"    {analysis.get('explanation', 'No explanation')}")
        lines.append("")
    
    # Add staging info
    if staged_count > 0:
        lines.append(f"*Staging:* {staged_count} candidates logged to `proper_noun_staging` table")
        lines.append("")
    
    # Next steps
    lines.append("*Recommended Actions:*")
    if wants_preview or not wants_apply:
        lines.append("  • Review the suggestions above")
        lines.append("  • Reply 'apply normalizations' to fix these automatically")
        lines.append("  • Or fix manually in CRM UI for sensitive cases")
    else:
        lines.append("  • Run 'preview normalizations' first to review")
        lines.append("  • Then 'apply normalizations' when ready")
    
    message = "\n".join(lines)
    
    return DispatchResult(
        True,
        message,
        {
            "candidates": [
                {
                    "entity_type": c.entity_type,
                    "record_id": c.record_id,
                    "current_name": c.current_name,
                    "suggested_name": c.suggested_name,
                    "confidence": c.confidence,
                    "reasons": c.reasons,
                }
                for c in candidates
            ],
            "ai_analysis": ai_analysis,
            "staged_count": staged_count,
            "total_found": len(candidates),
        },
    )


# Import at end to avoid circular dependency
from hermes.core.dispatcher import DispatchResult
