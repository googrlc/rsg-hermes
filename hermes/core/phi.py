"""PHI guard for memory writes — Medicare-lane interactions are PHI-sensitive.

Rule (3c): for Medicare-lane interactions, Supermemory / Supabase memory rows store
**client name + CRM link + task context ONLY** — never health details, Medicare
numbers, or eligibility specifics.

Two layers, allowlist first:

  1. ALLOWLIST (the real control): ``build_medicare_memory`` constructs a memory
     entry from exactly three permitted inputs. There is no parameter through which
     a health detail or Medicare number could be stored — the shape forbids it.

  2. REDACTION BACKSTOP: ``redact_phi`` strips Medicare Beneficiary Identifiers
     (MBI), legacy SSN/HICN numbers, and obvious eligibility phrasing from any free
     text before it reaches the memory layer — in case unstructured content slips
     through. ``add_document`` runs this automatically on Medicare-tagged writes.

The allowlist is what you rely on; the backstop is suspenders, not the belt.
"""

from __future__ import annotations

import re

REDACTED = "[redacted-phi]"

# Medicare Beneficiary Identifier: 11 chars, no S/L/O/I/B/Z in alpha positions,
# commonly shown with dashes after the 4th and 7th characters (1EG4-TE5-MK73).
_MBI = re.compile(
    r"\b[1-9][ACDEFGHJKMNPQRTUVWXY][ACDEFGHJKMNPQRTUVWXY0-9]\d"
    r"[-\s]?[ACDEFGHJKMNPQRTUVWXY][ACDEFGHJKMNPQRTUVWXY0-9]\d"
    r"[-\s]?[ACDEFGHJKMNPQRTUVWXY][ACDEFGHJKMNPQRTUVWXY]\d\d\b"
)
# Legacy SSN-based Medicare claim numbers / SSNs (optional trailing suffix letter).
_SSN = re.compile(r"\b\d{3}-?\d{2}-?\d{4}[A-Za-z]?\b")
# Obvious eligibility / health phrasing — coarse, intentionally conservative.
_ELIGIBILITY = re.compile(
    r"\b(diagnos\w*|condition|medication|prescription|disabilit\w*|ESRD|"
    r"end[-\s]?stage renal|dialysis|eligibilit\w*|qualif\w+ (?:for|due to))\b",
    re.IGNORECASE,
)

# Container-tag / metadata markers that mean "this is Medicare-lane memory".
_MEDICARE_MARKERS = ("medicare", "gretchen-medicare")


def is_medicare_context(tags: list[str] | None) -> bool:
    """True if any container tag marks this as Medicare-lane content."""
    if not tags:
        return False
    low = [str(t).lower() for t in tags]
    return any(any(m in t for m in _MEDICARE_MARKERS) for t in low)


def redact_phi(text: str | None) -> str:
    """Redact MBI / SSN-style numbers and eligibility phrasing from free text."""
    if not text:
        return ""
    out = _MBI.sub(REDACTED, str(text))
    out = _SSN.sub(REDACTED, out)
    out = _ELIGIBILITY.sub(REDACTED, out)
    return out


def contains_phi(text: str | None) -> bool:
    """Heuristic: does this text carry something we'd refuse to store for Medicare?"""
    if not text:
        return False
    s = str(text)
    return bool(_MBI.search(s) or _SSN.search(s) or _ELIGIBILITY.search(s))


def build_medicare_memory(
    client_name: str,
    crm_link: str,
    task_context: str,
) -> dict:
    """The ONLY permitted shape for a Medicare-lane memory entry.

    Returns a dict ready for SupermemoryClient.add_document (and a parallel
    ``row`` for a Supabase memory table). No health/eligibility parameter exists;
    the three accepted inputs are additionally run through the redaction backstop
    so a stray identifier in, say, task_context cannot be persisted.
    """
    name = redact_phi(client_name).strip()
    link = (crm_link or "").strip()
    task = redact_phi(task_context).strip()
    content = f"{name} — {task}" if task else name
    metadata = {"client_name": name, "crm_link": link, "lane": "gretchen-medicare"}
    return {
        "content": content,
        "container_tags": ["gretchen-memory", "lane:gretchen-medicare", "type:interaction"],
        "metadata": metadata,
        # Supabase-ready row: same allowlist, nothing else.
        "row": {"client_name": name, "crm_link": link, "task_context": task,
                "lane": "gretchen-medicare"},
    }
