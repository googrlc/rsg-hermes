"""Fuzzy dedup helpers — search-before-insert for the sync pipeline.

Replaces the exact-name fallback that caused blind inserts (the same class
of bug behind the May 29-Jun 1 2026 duplicate-insured incident). Uses
rapidfuzz when available (fast C extension) and falls back to the stdlib
``difflib`` so the module imports anywhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

log = logging.getLogger(__name__)

try:  # optional, preferred
    from rapidfuzz import fuzz  # type: ignore

    _HAS_RAPIDFUZZ = True
except Exception:  # pragma: no cover - exercised only without rapidfuzz
    import difflib

    _HAS_RAPIDFUZZ = False


def name_score(a: str, b: str) -> float:
    """Return a 0..1 similarity score between two names."""
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return 0.0
    if _HAS_RAPIDFUZZ:
        # token_sort handles "Coates, Lamar" vs "Lamar Coates".
        return fuzz.token_sort_ratio(a, b) / 100.0
    # difflib fallback: normalize + sort tokens to approximate token_sort_ratio.
    import re
    toks_a = sorted(re.findall(r"[a-z0-9]+", a))
    toks_b = sorted(re.findall(r"[a-z0-9]+", b))
    if not toks_a or not toks_b:
        return 0.0
    return difflib.SequenceMatcher(None, " ".join(toks_a), " ".join(toks_b)).ratio()


@dataclass
class NameMatch:
    record: dict[str, Any]
    score: float
    method: str = "fuzzy_name"


def best_name_match(
    query: str,
    candidates: Iterable[dict[str, Any]],
    *,
    name_key: str = "name",
    threshold: float = 0.90,
) -> NameMatch | None:
    """Return the best candidate above ``threshold``, else None."""
    best: NameMatch | None = None
    for cand in candidates:
        cand_name = str(cand.get(name_key) or cand.get("CommercialName") or "")
        score = name_score(query, cand_name)
        if score >= threshold and (best is None or score > best.score):
            best = NameMatch(record=cand, score=score)
    return best


# Confidence gates per match method (shared standards §3.5 / §3.7).
# Below these the sync treats it as "no match" and stages for human review.
DEFAULT_THRESHOLDS = {
    "dedup_key": 1.0,   # momentumClientId exact
    "fein": 0.95,
    "email": 0.90,
    "fuzzy_name_commercial": 0.90,
    "fuzzy_name_personal": 0.88,
    "exact_name": 0.70,  # legacy fallback — superseded by fuzzy gates
}


def passes_gate(score: float, method: str) -> bool:
    return score >= DEFAULT_THRESHOLDS.get(method, 0.90)
