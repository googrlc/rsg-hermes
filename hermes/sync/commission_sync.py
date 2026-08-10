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
    RENEWAL_STATUSES,
    _build_rule_index,
    _compute_expected_commission,
    _match_rule,
)
from hermes.renewals import eligibility as elig

from hermes_core import book as ams_book

log = logging.getLogger(__name__)

LEDGER_TABLE = "commission_ledger"
STATEMENT_SOURCE = "canonical_book"

# Only WON, in-force business ledgers a commission: the policy must be Active or
# Renewed. Everything else is excluded — Renewing / Up for Renewal (pending, not
# yet won) and all cancelled/expired/lapsed/non-renewed statuses.
LEDGER_STATUSES = frozenset({"Active", "Renewed"})

# Cancelled book still needs its AMS cancellation_date mirrored onto any ledger
# row that already exists (seeded while Active). Never INSERT a cancelled row —
# only refresh the cancel date (+ leave policy_expiration_date as the original
# term end). Mid-term chargeback estimates in the finance portal depend on this.
CANCEL_DATE_STATUSES = frozenset({"Cancelled", "Flat Cancel", "Pending Cancel"})

# Fallback ledger columns when the table is empty (schema-adaptive otherwise).
_LEDGER_COLS = {
    "policy_number", "nowcerts_policy_id", "carrier_name", "lob", "client_name",
    "statement_date", "policy_effective_date", "policy_expiration_date",
    "cancellation_date", "billing_type", "agency_fee_amount", "admin_fee_amount",
    "is_renewal",
    "gross_premium", "expected_commission", "reconciliation_status", "statement_source",
    "commission_rule_id", "commission_basis", "state", "updated_at",
}


@dataclass
class CommissionSyncResult:
    policies_scanned: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_no_policy_number: int = 0
    skipped_not_commissionable: int = 0
    skipped_no_value: int = 0        # neither premium nor an AMS commission amount
    skipped_out_of_window: int = 0
    skipped_no_date: int = 0         # statement_date is NOT NULL and nothing to seed it
    cancel_dates_updated: int = 0    # Cancelled/Flat/Pending: cancel date onto existing ledger
    no_expected: int = 0             # seeded, but no expected commission could be derived
    overrides_retired: int = 0       # portal corrections the AMS has caught up to
    overrides_conflicted: int = 0    # AMS moved somewhere unexpected — needs a human
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def accounted(self) -> int:
        """Every policy scanned must land in exactly one outcome."""
        return (
            self.inserted
            + self.updated
            + self.skipped_no_policy_number
            + self.skipped_not_commissionable
            + self.skipped_no_value
            + self.skipped_out_of_window
            + self.skipped_no_date
            + self.cancel_dates_updated
        )

    @property
    def balanced(self) -> bool:
        """No policy may fall off the map unexplained.

        ``no_expected`` is deliberately excluded — it counts rows that WERE
        seeded, so adding it would double-count.
        """
        return self.accounted == self.policies_scanned - len(self.errors)

    @property
    def message(self) -> str:
        return (
            f"commission ledger (from book): scanned={self.policies_scanned} "
            f"inserted={self.inserted} updated={self.updated} "
            f"no_policy_number={self.skipped_no_policy_number} "
            f"not_commissionable={self.skipped_not_commissionable} "
            f"no_value={self.skipped_no_value} out_of_window={self.skipped_out_of_window} "
            f"no_date={self.skipped_no_date} cancel_dates={self.cancel_dates_updated} "
            f"no_expected={self.no_expected} "
            f"overrides_retired={self.overrides_retired} "
            f"overrides_conflicted={self.overrides_conflicted} "
            f"balanced={self.balanced} errors={len(self.errors)}"
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


def _direct_commission(policy: dict[str, Any]) -> float | None:
    """NowCerts' own agency commission amount, when it carries one."""
    return _num(policy.get("agency_commission_amount"))


def _has_commissionable_value(policy: dict[str, Any]) -> bool:
    """Is there anything to ledger?

    Premium OR a direct AMS commission amount is enough. Requiring premium alone
    silently dropped live policies whose premium never made it into the book but
    whose agency commission is known — three of them, ~$1,074 of real commission,
    found 2026-07-26. The commission is the thing being tracked; premium is
    context for it.
    """
    prem = _premium(policy)
    if prem and prem > 0:
        return True
    direct = _direct_commission(policy)
    return bool(direct and direct > 0)


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
    since: str | None = None,
) -> CommissionSyncResult:
    """Seed commission_ledger expected values from canonical_policies.

    Only WON, in-window business is ledgered: a policy's ``effective_date`` must
    fall in [since, today]. This excludes future-effective (e.g. 2027) staged
    renewals — not won yet — and pre-``since`` old book, so reconciliation starts
    fresh and clean.

    Args:
        supa: SupabaseClient.
        dry_run: compute + report only, no writes.
        limit: optional cap on policies processed (testing/safety).
        since: earliest effective_date to include (YYYY-MM-DD). Defaults to
            HERMES_COMMISSION_SINCE or 2026-01-01.
    """
    import os as _os
    from datetime import date as _date

    result = CommissionSyncResult()
    now_iso = _utcnow_iso()
    since = (since or _os.environ.get("HERMES_COMMISSION_SINCE") or "2026-01-01").strip()[:10]
    today = _date.today().isoformat()

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

    # Bind -> finance reads the AMS directly: the expected-commission side is
    # derived from live policy facts, not from a nightly mirror.
    policies = ams_book.select_policies(supa, limit=50000)
    if limit:
        policies = policies[:limit]
    result.policies_scanned = len(policies)

    for p in policies:
        pn = str(p.get("policy_number") or "").strip()
        if not pn:
            result.skipped_no_policy_number += 1
            continue
        status = elig.normalize_status(p.get("status"))
        if status not in LEDGER_STATUSES:
            # Cancelled (etc.) never get a NEW ledger row, but an existing row
            # still needs the AMS cancellation_date so the portal can estimate
            # chargebacks without overwriting policy_expiration_date.
            if (
                status in CANCEL_DATE_STATUSES
                and pn in existing
                and p.get("cancellation_date")
            ):
                cancel_refresh = _project({
                    "cancellation_date": p.get("cancellation_date"),
                    # Keep term end current from AMS; never replace it with cancel.
                    "policy_expiration_date": p.get("expiration_date"),
                    "policy_effective_date": p.get("effective_date"),
                    "nowcerts_policy_id": p.get("policy_guid"),
                    "updated_at": now_iso,
                }, ledger_cols)
                try:
                    if not dry_run and cancel_refresh:
                        supa.update(LEDGER_TABLE, existing[pn], cancel_refresh)
                    result.cancel_dates_updated += 1
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"policy {pn}: {exc}")
                    log.warning("commission sync cancel-date error on %s: %s", pn, exc)
            else:
                result.skipped_not_commissionable += 1
            continue
        if not _has_commissionable_value(p):
            result.skipped_no_value += 1
            continue
        prem = _premium(p) or 0.0

        # WON + in-window only: effective in [since, today]. Drops future-effective
        # (2027 staged renewals — not won yet) and pre-since old book.
        eff_s = str(p.get("effective_date") or "")[:10]
        if not eff_s or eff_s < since or eff_s > today:
            result.skipped_out_of_window += 1
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
            # Present when AMS already stamped a cancel (e.g. Pending Cancel).
            "cancellation_date": p.get("cancellation_date"),
            "billing_type": p.get("billing_type"),
            # Agency fee the shop charges; also seed admin_fee_amount for "% of Admin Fee" rules.
            "agency_fee_amount": p.get("agency_fee_amount"),
            "admin_fee_amount": p.get("agency_fee_amount"),
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
                    result.skipped_no_date += 1
                    continue
                if not dry_run:
                    row = supa.insert(LEDGER_TABLE, _project(insert, ledger_cols))
                    existing[pn] = str(row.get("id"))
                result.inserted += 1
        except Exception as exc:  # noqa: BLE001 — one bad policy shouldn't abort the run
            result.errors.append(f"policy {pn}: {exc}")
            log.warning("commission sync error on %s: %s", pn, exc)

    # Portal corrections: retire the ones the AMS has caught up to, flag the ones
    # where it moved somewhere unexpected. Never silently discard a correction —
    # see hermes_core.overrides.core for why the third branch is a conflict, not a
    # retirement. Best-effort: an override hiccup must not fail the seed.
    try:
        from hermes.commissions.surface import OVERRIDABLE_FIELDS
        from hermes_core.overrides.store import reconcile_overrides

        ledger_now = {
            str(r.get("policy_number") or "").strip(): r
            for r in supa.select(LEDGER_TABLE, columns="*", limit=50000)
            if r.get("policy_number")
        }
        source_values = {
            (pn, field_name): row.get(field_name)
            for pn, row in ledger_now.items()
            for field_name in OVERRIDABLE_FIELDS
            if field_name in row
        }
        recon = reconcile_overrides(
            supa, "commission_ledger", source_values,
            actor="commission_sync", dry_run=dry_run,
        )
        result.overrides_retired = recon["retired"]
        result.overrides_conflicted = recon["conflicted"]
    except Exception as exc:  # noqa: BLE001
        log.exception("commission sync: override reconcile failed")
        result.errors.append(f"override reconcile: {exc}")

    log.info("commission sync done: %s", result.message)
    return result
