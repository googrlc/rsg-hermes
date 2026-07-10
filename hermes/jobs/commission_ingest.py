"""Commission ingest — bridge crm_commissions → commission_ledger.

Reads commissionable policies from ``crm_commissions`` (the golden record table
populated by the bidirectional sync), matches each to a ``commission_rules`` row
by carrier + LOB, computes expected commission, and upserts into
``commission_ledger`` — the table the Commission Tracker app renders.

Commissionable policy statuses: Active, Renewed, Up for Renewal, Renewing.
Non-commissionable: Expired, Cancelled, Flat Cancel, Pending Cancel.

Carrier matching strategy (in order):
  1. Exact (case-insensitive) carrier_name + lob
  2. Rule carrier is a prefix of the crm carrier (e.g. "PROGRESSIVE MOUNTAIN"
     matches "PROGRESSIVE MOUNTAIN INS CO")
  3. Fuzzy match via rapidfuzz (token_sort ratio >= 85)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

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


@dataclass
class IngestResult:
    total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_no_rule: int = 0
    skipped_no_premium: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @property
    def message(self) -> str:
        return (
            f"Commission ingest: total={self.total} inserted={self.inserted} "
            f"updated={self.updated} skipped_no_rule={self.skipped_no_rule} "
            f"skipped_no_premium={self.skipped_no_premium} failed={self.failed}"
        )


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


def run_ingest(
    supa: SupabaseClient | None = None,
    *,
    dry_run: bool = False,
) -> IngestResult:
    """Main entry point — ingest commissionable policies into commission_ledger."""
    supa = supa or SupabaseClient()
    result = IngestResult()

    # 1. Load commission rules and build the lookup index.
    rules = supa.select("commission_rules", columns="*", limit=1000)
    rule_index = _build_rule_index(rules)
    log.info("Loaded %d active commission rules (%d indexed)", len(rules), len(rule_index))

    # 2. Load all crm_accounts for client name lookup.
    accounts_raw = supa.select("crm_accounts", columns="id,name", limit=2000)
    accounts = {a["id"]: a.get("name") for a in accounts_raw if a.get("id")}
    log.info("Loaded %d crm_accounts for name lookup", len(accounts))

    # 3. Load existing commission_ledger policy numbers to avoid duplicates.
    ledger_raw = supa.select("commission_ledger", columns="id,policy_number", limit=2000)
    existing = {r.get("policy_number") for r in ledger_raw if r.get("policy_number")}
    log.info("commission_ledger has %d existing policy numbers", len(existing))

    # 4. Load commissionable policies from crm_commissions.
    policies = supa.select("crm_commissions", columns="*", limit=2000)
    commissionable = [
        p for p in policies
        if (p.get("policy_status") or "").strip() in COMMISSIONABLE_STATUSES
        or _is_chargeback(p)
    ]
    result.total = len(commissionable)
    chargeback_count = sum(1 for p in commissionable if _is_chargeback(p))
    log.info(
        "crm_commissions: %d total, %d commissionable (%d chargeback candidates)",
        len(policies), result.total, chargeback_count,
    )

    for policy in commissionable:
        try:
            pn = (policy.get("policy_number") or "").strip()
            if not pn:
                result.skipped_no_premium += 1
                continue

            premium = policy.get("premium")
            if premium is None or float(premium) <= 0:
                result.skipped_no_premium += 1
                continue

            carrier = policy.get("carrier") or ""
            lob = policy.get("line_of_business") or ""
            rule = _match_rule(carrier, lob, rule_index, rules)

            if not rule:
                result.skipped_no_rule += 1
                log.debug("no rule for %s / %s (policy %s)", carrier, lob, pn)
                continue

            is_renewal = _is_renewal(policy)
            is_cb = _is_chargeback(policy)
            expected = _compute_expected_commission(float(premium), rule, is_renewal)

            client_name = accounts.get(policy.get("account_id")) or ""

            if is_cb:
                # Chargeback: negative expected commission signals a clawback.
                # The actual chargeback amount comes from the carrier statement;
                # this entry flags the policy so Lamar can reconcile it.
                expected = -abs(expected) if expected else None
                recon_status = "chargeback"
                source = "ams_ingest_chargeback"
                notes = f"Policy {policy.get('policy_status')} — potential carrier clawback"
            else:
                recon_status = "pending"
                source = "ams_ingest"
                notes = ""

            # statement_date is NOT NULL in commission_ledger — fall back to
            # expiration_date or last_synced_at if effective_date is missing.
            eff_date = policy.get("effective_date") or policy.get("expiration_date")
            if not eff_date:
                # Skip policies with no date at all — can't ledger them.
                result.skipped_no_premium += 1
                continue

            row = {
                "policy_number": pn,
                "carrier_name": carrier,
                "lob": lob,
                "client_name": client_name,
                "statement_date": eff_date,
                "policy_effective_date": policy.get("effective_date") or eff_date,
                "policy_expiration_date": policy.get("expiration_date"),
                "is_renewal": is_renewal,
                "gross_premium": float(premium),
                "expected_commission": expected,
                "commission_rule_id": rule.get("id"),
                "commission_basis": rule.get("commission_basis"),
                "reconciliation_status": recon_status,
                "statement_source": source,
                "espocrm_policy_id": policy.get("espocrm_id"),
                "nowcerts_policy_id": None,  # crm_commissions.nowcerts_id is account-level, not policy-level
                "notes": notes,
            }

            if dry_run:
                log.info("[DRY RUN] would upsert %s: %s %s $%s exp=$%s",
                         pn, carrier, lob, premium, expected)
                continue

            if pn in existing:
                # Update existing row.
                ledger_rows = supa.select(
                    "commission_ledger", columns="id",
                    params={"policy_number": f"eq.{pn}"}, limit=1,
                )
                if ledger_rows:
                    ledger_id = ledger_rows[0]["id"]
                    supa.update("commission_ledger", ledger_id, row)
                    result.updated += 1
                else:
                    inserted = supa.insert("commission_ledger", row)
                    ledger_id = inserted.get("id", "")
                    result.inserted += 1
                    existing.add(pn)
            else:
                inserted = supa.insert("commission_ledger", row)
                ledger_id = inserted.get("id", "")
                result.inserted += 1
                existing.add(pn)

            # ── Write-back: flag the EspoCRM Opportunity as ledger-synced ──
            # Belt-and-suspenders: the ledger has espocrm_policy_id, and the
            # EspoCRM Opportunity gets commissionLogged=True so you can see at
            # a glance which policies have been synced to the commission ledger.
            espo_id = policy.get("espocrm_id")
            if espo_id and not dry_run:
                try:
                    from hermes.core.client import EspoClient
                    espo = EspoClient()
                    espo.patch("Opportunity", espo_id, {"commissionLogged": True})
                    log.debug("write-back: Opportunity %s commissionLogged=True", espo_id)
                except Exception as exc:
                    log.warning("write-back failed for %s: %s", espo_id, exc)

        except SupabaseClientError as exc:
            result.failed += 1
            result.errors.append(f"{policy.get('policy_number', '?')}: {exc}")
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"{policy.get('policy_number', '?')}: {exc}")

    log.info(result.message)
    return result
