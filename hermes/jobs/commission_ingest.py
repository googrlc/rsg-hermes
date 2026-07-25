"""Commission rule matching — carrier/LOB lookup and expected-commission math.

The rule library behind ``commission_ledger``. ``hermes/sync/commission_sync.py``
walks the canonical NowCerts book and calls into here to decide which
``commission_rules`` row applies to a policy and what commission it implies.

Commissionable policy statuses: Active, Renewed, Up for Renewal, Renewing.
Non-commissionable: Expired, Cancelled, Flat Cancel, Pending Cancel.

Carrier matching strategy (in order):
  1. Exact (case-insensitive) carrier_name + lob
  2. Rule carrier is a prefix of the policy carrier (e.g. "PROGRESSIVE MOUNTAIN"
     matches "PROGRESSIVE MOUNTAIN INS CO")
  3. Fuzzy match via rapidfuzz (token_sort ratio >= 85)

The old ``run_ingest`` entry point read the Espo-era ``crm_commissions`` mirror,
which stopped being written when EspoCRM was decommissioned; ``commission_sync``
supersedes it and sources from ``canonical_policies`` instead.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

COMMISSIONABLE_STATUSES = frozenset({
    "Active", "Renewed", "Up for Renewal", "Renewing",
})

RENEWAL_STATUSES = frozenset({
    "Renewed", "Up for Renewal", "Renewing",
})

# Policies cancelled or expired on/after this date are ingested as
# chargeback candidates so Lamar can reconcile carrier clawbacks.
CHARGEBACK_STATUSES = frozenset({
    "Cancelled", "Expired", "Flat Cancel", "Pending Cancel",
})
CHARGEBACK_START_DATE = "2026-07-01"

# Minimum fuzzy match score (0-100) to accept a carrier rule match.
FUZZY_THRESHOLD = 85


def _normalize_carrier(name: str | None) -> str:
    """Uppercase, strip, collapse whitespace for matching."""
    return " ".join((name or "").upper().split())


def _normalize_lob(lob: str | None) -> str:
    """Normalize line of business for matching."""
    val = (lob or "").strip()
    # Common abbreviations the rules table uses.
    aliases = {
        "WORKERS COMP": "Workers Comp",
        "WORKERS' COMP": "Workers Comp",
        "WORK COMP": "Workers Comp",
        "WC": "Workers Comp",
        "GL": "General Liability",
        "BOP": "BOP",
        "COMMERCIAL AUTO": "Commercial Auto",
        "PERSONAL AUTO": "Personal Auto",
        "HOMEOWNERS": "Homeowners",
        "HOMEOWNERS HO-3": "Homeowners",
        "UMBRELLA": "Commercial Umbrella",
        "COMMERCIAL UMBRELLA": "Commercial Umbrella",
    }
    upper = val.upper()
    if upper in aliases:
        return aliases[upper]
    return val


def _build_rule_index(rules: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index rules by (normalized_carrier, normalized_lob) for exact lookup."""
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rules:
        if not r.get("active", True):
            continue
        carrier = _normalize_carrier(r.get("carrier_name"))
        lob = _normalize_lob(r.get("lob"))
        if carrier and lob and carrier != "VARIOUS" and lob != "ALL":
            idx[(carrier, lob)] = r
    return idx


def _match_rule(
    carrier: str,
    lob: str,
    rule_index: dict[tuple[str, str], dict[str, Any]],
    all_rules: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the best commission rule for a carrier + LOB."""
    nc = _normalize_carrier(carrier)
    nl = _normalize_lob(lob)

    # 1. Exact match
    rule = rule_index.get((nc, nl))
    if rule:
        return rule

    # 2. Prefix match — rule carrier is a prefix of the crm carrier
    for (rc, rl), r in rule_index.items():
        if rl == nl and (nc.startswith(rc) or rc.startswith(nc)):
            return r

    # 3. LOB-only fallback (some carriers have one rule per LOB regardless of name)
    for (rc, rl), r in rule_index.items():
        if rl == nl:
            return r

    # 4. Fuzzy match on carrier name
    try:
        from rapidfuzz import fuzz

        best_score = 0
        best_rule: dict[str, Any] | None = None
        for (rc, rl), r in rule_index.items():
            if rl != nl:
                continue
            score = fuzz.token_sort_ratio(nc, rc)
            if score > best_score:
                best_score = score
                best_rule = r
        if best_rule and best_score >= FUZZY_THRESHOLD:
            log.debug("fuzzy match %s -> %s (score=%d)", carrier, best_rule.get("carrier_name"), best_score)
            return best_rule
    except ImportError:
        pass

    return None


def _compute_expected_commission(
    premium: float,
    rule: dict[str, Any],
    is_renewal: bool,
) -> float | None:
    """Compute expected commission from the rule's rate."""
    if premium is None or premium <= 0:
        return None
    rate = rule.get("renewal_percent") if is_renewal else rule.get("nb_percent")
    if rate is None:
        # Fall back to whichever rate is available.
        rate = rule.get("nb_percent") or rule.get("renewal_percent")
    if rate is None:
        return None
    return round(premium * float(rate) / 100.0, 2)


def _is_renewal(policy: dict[str, Any]) -> bool:
    status = (policy.get("policy_status") or "").strip()
    return status in RENEWAL_STATUSES


def _is_chargeback(policy: dict[str, Any]) -> bool:
    """True when a cancelled/expired policy qualifies for chargeback ingest.

    Only policies with an expiration or effective date on/after
    CHARGEBACK_START_DATE are included — earlier cancellations are
    historical and already reconciled (or not worth chasing).
    """
    status = (policy.get("policy_status") or "").strip()
    if status not in CHARGEBACK_STATUSES:
        return False
    date_str = (policy.get("expiration_date") or policy.get("effective_date") or "").strip()
    if not date_str:
        return False
    return date_str >= CHARGEBACK_START_DATE


