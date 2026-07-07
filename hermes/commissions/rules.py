"""Commission-rule lookup and expected-commission math.

The catalog lives in Supabase ``commission_rules``. One row carries both the
new-business rate (``nb_percent``) and the renewal rate (``renewal_percent``),
plus a ``state`` (specific or the ``ALL`` sentinel) and a ``lookup_priority``.

Lookup (per the build spec, hardened): match carrier + LOB + state where state is
the policy's state OR ``ALL``, then prefer an EXACT state match, then the lowest
``lookup_priority``. Prefer-exact-state matters because the live data has at least
one carrier/LOB group where an ``ALL`` row has a lower priority than the
state-specific row — raw ``ORDER BY lookup_priority`` would wrongly pick ALL.
"""

from __future__ import annotations

from typing import Any, Optional

Rule = dict[str, Any]
Policy = dict[str, Any]


def _norm(value: Any) -> str:
    return (str(value).strip().lower()) if value is not None else ""


def _state(value: Any) -> str:
    return (str(value).strip().upper()) if value is not None else ""


def find_rule(
    rules: list[Rule], *, carrier: str, lob: str, state: str
) -> Optional[Rule]:
    """Return the best-matching rule, or None if nothing matches.

    Matching is exact (case-insensitive) on carrier + LOB — we never fuzzy-guess
    a carrier (spec: "Never guess"). A no-match becomes a ``needs_rule`` ledger row.
    """
    c, l, s = _norm(carrier), _norm(lob), _state(state)
    candidates = [
        r
        for r in rules
        if _norm(r.get("carrier_name")) == c
        and _norm(r.get("lob")) == l
        and _state(r.get("state")) in (s, "ALL")
    ]
    if not candidates:
        return None

    def sort_key(r: Rule) -> tuple[int, int]:
        exact = 0 if _state(r.get("state")) == s and s != "" else 1
        prio = r.get("lookup_priority")
        return (exact, prio if isinstance(prio, int) else 9999)

    candidates.sort(key=sort_key)
    return candidates[0]


def _pct_for(rule: Rule, *, is_renewal: bool) -> Optional[float]:
    """Pick the renewal or new-business rate, falling back to whichever exists."""
    primary = rule.get("renewal_percent") if is_renewal else rule.get("nb_percent")
    fallback = rule.get("nb_percent") if is_renewal else rule.get("renewal_percent")
    for pct in (primary, fallback):
        if pct is not None:
            try:
                return float(pct)
            except (TypeError, ValueError):
                return None
    return None


def compute_expected(
    rule: Rule, *, gross_premium: Optional[float], is_renewal: bool
) -> Optional[float]:
    """Expected commission for a matched rule, or None if it can't be computed.

    All live rules are '% of Premium'; flat-fee rules are also supported. Returns
    None (surfaces as needs_rule upstream) when the inputs are insufficient.
    """
    method = (rule.get("commission_method") or "% of Premium")

    if "Premium" in method:
        pct = _pct_for(rule, is_renewal=is_renewal)
        if pct is None or gross_premium is None:
            return None
        return round(float(gross_premium) * pct / 100.0, 2)

    flat = rule.get("flat_fee")
    if flat is not None:
        try:
            return round(float(flat), 2)
        except (TypeError, ValueError):
            return None

    # Percent-based method with a payroll/admin/monthly basis we don't get from the
    # AMS feed — leave uncomputed rather than guess.
    return None
