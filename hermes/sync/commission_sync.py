"""Canonical book → commission_ledger EXPECTED-commission seeding.

Re-sources the expected side of ``commission_ledger`` from the fresh
``canonical_policies`` book (NowCerts) instead of the Espo-era ``crm_commissions``
table that ``hermes/jobs/commission_ingest.py`` reads. Two wins over that path:

  * ``canonical_policies`` carries ``agency_commission_amount`` straight from
    NowCerts, so expected commission is the AMS's own number when present (rule
    lookup is only the fallback).
  * ``policy_guid`` gives a real policy-level ``nowcerts_policy_id`` (the crm_commissions
    path could only ever set it to None — its nowcerts id is account-level).

Money-data safety: this writes only the EXPECTED side (gross_premium,
expected_commission, carrier/LOB/client, policy dates). It NEVER overwrites the
statement-sourced actuals — ``actual_commission``, ``reconciliation_status``,
``payment_received``, ``delta``, ``statement_date``/``statement_source`` are left
untouched on existing rows (those change only through the approval-gated
commission-statement ingest). One ledger row per ``policy_number``; additive;
``dry_run`` reports counts with zero writes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.jobs.commission_ingest import (
    COMMISSIONABLE_STATUSES,
    RENEWAL_STATUSES,
    _build_rule_index,
    _compute_expected_commission,
    _match_rule,
)
from hermes.renewals import eligibility as elig

log = logging.getLogger(__name__)

LEDGER_TABLE = "commission_ledger"
STATEMENT_SOURCE = "canonical_book"

# Fallback ledger columns when the table is empty (schema-adaptive otherwise).
_LEDGER_COLS = {
    "policy_number", "nowcerts_policy_id", "carrier_name", "lob", "client_name",
    "statement_date", "policy_effective_date", "policy_expiration_date", "is_renewal",
    "gross_premium", "expected_commission", "reconciliation_status", "statement_source",
    "commission_rule_id", "commission_basis", "state", "updated_at",
}


@dataclass
class CommissionSyncResult:
    policies_scanned: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_not_commissionable: int = 0
    skipped_no_premium: int = 0
    no_expected: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        return (
            f"commission ledger (from book): scanned={self.policies_scanned} "
            f"inserted={self.inserted} updated={self.updated} "
            f"not_commissionable={self.skipped_not_commissionable} "
            f"no_premium={self.skipped_no_premium} no_expected={self.no_expected} "
            f"errors={len(self.errors)}"
        )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _premium(policy: dict[str, Any]) -> float | None:
    return _num(policy.get("premium_amount")) or _num(policy.get("annualized_premium"))


def _expected(policy: dict[str, Any], rule: dict[str, Any] | None, is_renewal: bool) -> float | None:
    """Prefer NowCerts' own agency commission; fall back to the rule rate."""
    direct = _num(policy.get("agency_commission_amount"))
    if direct and direct > 0:
        return round(direct, 2)
    prem = _premium(policy)
    if rule and prem:
        return _compute_expected_commission(prem, rule, is_renewal)
    return None


def _discover_columns(supa: Any, table: str, fallback: set[str]) -> set[str]:
    try:
        rows = supa.select(table, columns="*", limit=1)
    except Exception:  # noqa: BLE001
        return set(fallback)
    if rows and isinstance(rows[0], dict):
        return set(rows[0].keys())
    return set(fallback)


def _project(payload: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k in columns and v is not None}


def run_commission_sync(
    supa: Any,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> CommissionSyncResult:
    """Seed commission_ledger expected values from canonical_policies.

    Args:
        supa: SupabaseClient.
        dry_run: compute + report only, no writes.
        limit: optional cap on policies processed (testing/safety).
    """
    result = CommissionSyncResult()
    now_iso = _utcnow_iso()

    rules = supa.select("commission_rules", columns="*", limit=1000)
    rule_index = _build_rule_index(rules)

    clients = {
        str(c.get("nowcerts_insured_guid")): c.get("insured_name")
        for c in supa.select("canonical_clients", columns="nowcerts_insured_guid,insured_name", limit=50000)
        if c.get("nowcerts_insured_guid")
    }

    ledger_cols = _discover_columns(supa, LEDGER_TABLE, _LEDGER_COLS)
    existing = {
        str(r.get("policy_number")): str(r.get("id"))
        for r in supa.select(LEDGER_TABLE, columns="id,policy_number", limit=50000)
        if r.get("policy_number")
    }

    policies = supa.select("canonical_policies", columns="*", limit=50000)
    if limit:
        policies = policies[:limit]
    result.policies_scanned = len(policies)

    for p in policies:
        pn = str(p.get("policy_number") or "").strip()
        if not pn:
            continue
        status = elig.normalize_status(p.get("status"))
        if status not in COMMISSIONABLE_STATUSES:
            result.skipped_not_commissionable += 1
            continue
        prem = _premium(p)
        if not prem or prem <= 0:
            result.skipped_no_premium += 1
            continue

        is_renewal = status in RENEWAL_STATUSES
        carrier = p.get("carrier") or ""
        lob = p.get("lines_of_business") or ""
        rule = _match_rule(carrier, lob, rule_index, rules)
        expected = _expected(p, rule, is_renewal)
        if expected is None:
            result.no_expected += 1

        guid = str(p.get("nowcerts_insured_guid") or "")
        eff = p.get("effective_date")
        exp = p.get("expiration_date")

        # Expected-side facts refreshed on every run.
        refresh = {
            "nowcerts_policy_id": p.get("policy_guid"),
            "carrier_name": carrier or None,
            "lob": lob or None,
            "client_name": clients.get(guid) or None,
            "policy_effective_date": eff,
            "policy_expiration_date": exp,
            "is_renewal": is_renewal,
            "gross_premium": prem,
            "expected_commission": expected,
            "commission_rule_id": (rule or {}).get("id"),
            "commission_basis": (rule or {}).get("commission_basis"),
            "state": p.get("state") or None,
            "updated_at": now_iso,
        }
        try:
            if pn in existing:
                payload = _project(refresh, ledger_cols)
                if not dry_run and payload:
                    supa.update(LEDGER_TABLE, existing[pn], payload)
                result.updated += 1
            else:
                # statement_date is NOT NULL; seed from the policy's own dates.
                insert = {
                    **refresh,
                    "policy_number": pn,
                    "statement_date": eff or exp,
                    "statement_source": STATEMENT_SOURCE,
                    "reconciliation_status": "pending",
                }
                if not (eff or exp):
                    # No date at all — cannot satisfy NOT NULL statement_date.
                    result.skipped_no_premium += 1
                    continue
                if not dry_run:
                    row = supa.insert(LEDGER_TABLE, _project(insert, ledger_cols))
                    existing[pn] = str(row.get("id"))
                result.inserted += 1
        except Exception as exc:  # noqa: BLE001 — one bad policy shouldn't abort the run
            result.errors.append(f"policy {pn}: {exc}")
            log.warning("commission sync error on %s: %s", pn, exc)

    log.info("commission sync done: %s", result.message)
    return result
