"""NowCerts → canonical book sync — refreshes the Supabase canonical mirror.

The canonical book (``canonical_clients`` + ``canonical_policies``) is the
NowCerts snapshot the renewal eligibility engine reads
(``hermes/renewals/candidate_refresh.py`` selects ``canonical_policies``
directly; the command-center dashboard reads both). It was bulk-loaded once from
CSV on 2026-06-10 and — until this job — had NO code path that refreshed it, so
renewal discovery ran on a frozen book: any policy written/renewed/cancelled
after 6/10 was invisible to ``renewal_candidates`` / ``project_85_renewals``.
This job pulls live NowCerts insureds + policies and reconciles them into the
canonical tables so the nightly ``--renewal-refresh`` runs on a current book.

Reconciliation keys are the LIVE natural keys (the CSV-loaded tables have no
surrogate ``id`` and differ from the phase-0 migration):
  * ``canonical_clients``  → ``nowcerts_insured_guid``
  * ``canonical_policies`` → ``policy_guid`` (one row per NowCerts policy record)

Design guarantees:
  * **Additive.** Upsert-by-natural-key only; never deletes rows. (The
    ``nowcerts_insured_mirror`` golden/crosswalk table is intentionally left
    alone — it is a legacy crosswalk record the renewal engine does not read.)
  * **Preserves ``renewed_policy`` lineage.** The NowCerts API exposes no renewal
    lineage pointer (see ``candidate_refresh`` docstring); the CSV-loaded value is
    irreplaceable, so an update never overwrites it — only volatile fields
    (status, active, dates, premium, carrier, LOB) refresh from live NowCerts.
  * **Schema-adaptive.** Every write is filtered to columns discovered on a live
    sample row, so a column that exists in one environment but not another can
    never error the run.
  * **dry_run** computes and reports intended writes with zero side effects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from hermes.renewals import eligibility as elig
from hermes.core.field_utils import strip_date

log = logging.getLogger(__name__)

CLIENTS_TABLE = "canonical_clients"
POLICIES_TABLE = "canonical_policies"

# Defined here rather than imported from hermes.ams.book: that module imports from
# this one, so the dependency only runs one way.
OWNER_BOOK_SYNC = "book_sync"

CLIENT_KEY = "nowcerts_insured_guid"
POLICY_KEY = "policy_guid"

# Audit trail. The canonical book sync writes one sync_audit_log row per
# mirrored object so "what did the last sync do to this insured/policy?" is
# answerable and ops-doctor can see the mirror move (#232 — sync_audit_log
# went dead when the Espo bidirectional sync was deleted in slice 4).
# run_id is left NULL: sync_runs.direction is an Espo-only enum
# (nowcerts_to_espocrm / espocrm_to_nowcerts), so we don't create a run
# parent. The audit_action enum (create/update/skip/error) fits the book
# sync's outcomes exactly, and sync_audit_log.run_id is nullable.
AUDIT_TABLE = "sync_audit_log"
AUDIT_OBJECT_CLIENT = "canonical_client"
AUDIT_OBJECT_POLICY = "canonical_policy"

# Fallback column sets, used only when a table is empty (no sample row to
# introspect). Superset of the live CSV schema; writes intersect with these.
_CLIENT_COLS = {
    "nowcerts_insured_guid", "insured_name", "insured_name_normalized",
    "first_name", "last_name", "client_type", "business_type",
    "phone", "cell_phone", "email", "address_line1", "city", "state", "zip", "updated_at",
}
_POLICY_COLS = {
    "policy_guid", "nowcerts_insured_guid", "policy_number", "lines_of_business",
    "business_type", "carrier", "status", "active", "effective_date", "expiration_date",
    "current_term_amount", "premium_amount", "annualized_premium", "renewed_policy", "state",
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(
    supa: Any,
    *,
    object_type: str,
    source_object_id: str | None,
    action: str,
    status: str = "success",
    dest_object_id: str | None = None,
    message: str | None = None,
    dry_run: bool = False,
) -> None:
    """Append one sync_audit_log row for a mirrored object.

    Best-effort: an audit-write failure must never abort the sync. Skipped
    entirely in dry_run (there is no real write to audit).
    """
    if dry_run:
        return
    payload: dict[str, Any] = {
        "object_type": object_type,
        "source_object_id": str(source_object_id) if source_object_id else None,
        "action": action,
        "status": status,
    }
    if dest_object_id is not None:
        payload["dest_object_id"] = str(dest_object_id)
    if message:
        payload["message"] = message[:2000]
    try:
        supa.insert(AUDIT_TABLE, payload)
    except Exception:  # noqa: BLE001
        log.warning("sync_audit_log insert failed for %s %s", object_type, source_object_id)


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# NowCerts field extraction
# ---------------------------------------------------------------------------
def _insured_guid(record: dict[str, Any]) -> str:
    return str(
        record.get("id") or record.get("databaseId") or record.get("insuredDatabaseId") or ""
    ).strip()


def _insured_name(ins: dict[str, Any]) -> str:
    commercial = ins.get("commercialName") or ins.get("insuredCommercialName")
    if commercial:
        return str(commercial).strip()
    parts = [str(ins.get("firstName") or "").strip(), str(ins.get("lastName") or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _policy_number(p: dict[str, Any]) -> str:
    return str(p.get("number") or p.get("policyNumber") or p.get("Number") or "").strip()


def _policy_guid(p: dict[str, Any]) -> str:
    return str(p.get("databaseId") or p.get("DatabaseId") or p.get("id") or "").strip()


def _policy_lob(p: dict[str, Any]) -> str | None:
    lob_list = p.get("lineOfBusinesses")
    if isinstance(lob_list, list) and lob_list and isinstance(lob_list[0], dict):
        name = lob_list[0].get("lineOfBusinessName")
        if name:
            return str(name)
    for key in ("lineOfBusinessName", "lineOfBusiness", "LineOfBusinessName"):
        if p.get(key):
            return str(p[key])
    return None


def _policy_premium(p: dict[str, Any]) -> float | None:
    for key in ("totalPremium", "premium", "Premium", "annualizedPremium"):
        val = _num(p.get(key))
        if val is not None:
            return val
    return None


def _policy_active(p: dict[str, Any], status: Any) -> bool:
    for key in ("active", "isActive", "Active"):
        val = p.get(key)
        if isinstance(val, bool):
            return val
    return elig.normalize_status(status) in elig.CURRENT_STATUSES


def _map_client(ins: dict[str, Any], *, now_iso: str) -> dict[str, Any] | None:
    """Map a NowCerts insured to a canonical_clients row (keyed by guid)."""
    guid, name = _insured_guid(ins), _insured_name(ins)
    if not guid or not name:
        return None  # insured_name is required; guid is the reconcile key
    return {
        CLIENT_KEY: guid,
        "insured_name": name,
        "insured_name_normalized": name.lower(),
        "first_name": ins.get("firstName") or None,
        "last_name": ins.get("lastName") or None,
        "client_type": ins.get("insuredType") or None,
        "business_type": ins.get("typeOfBusiness") or None,
        "email": ins.get("eMail") or ins.get("email") or None,
        "phone": ins.get("phone") or None,
        "cell_phone": ins.get("cellPhone") or None,
        "address_line1": ins.get("addressLine1") or None,
        "city": ins.get("city") or None,
        "state": ins.get("state") or None,
        "zip": ins.get("zipCode") or ins.get("zip") or None,
        "updated_at": now_iso,
    }


def _map_policy_volatile(p: dict[str, Any]) -> dict[str, Any]:
    """Fields a refresh always overwrites from live NowCerts (excludes lineage)."""
    status = p.get("status") or p.get("Status")
    premium = _policy_premium(p)
    return {
        "nowcerts_insured_guid": str(p.get("insuredDatabaseId") or p.get("insuredId") or "").strip() or None,
        "policy_number": _policy_number(p) or None,
        "lines_of_business": _policy_lob(p),
        "business_type": p.get("businessType") or p.get("BusinessType") or None,
        "carrier": p.get("carrierName") or p.get("CarrierName") or p.get("carrier") or None,
        "status": status,
        "active": _policy_active(p, status),
        "effective_date": strip_date(p.get("effectiveDate") or p.get("EffectiveDate")),
        "expiration_date": strip_date(p.get("expirationDate") or p.get("ExpirationDate")),
        "current_term_amount": premium,
        "premium_amount": premium,
        "annualized_premium": premium,
        "state": p.get("state") or p.get("State") or None,
    }


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class CanonicalSyncResult:
    insureds_fetched: int = 0
    clients_created: int = 0
    clients_updated: int = 0
    policies_fetched: int = 0
    policies_created: int = 0
    policies_updated: int = 0
    policies_skipped_no_guid: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        return (
            f"canonical book: insureds={self.insureds_fetched} "
            f"clients(+{self.clients_created}/~{self.clients_updated}) "
            f"policies={self.policies_fetched} (+{self.policies_created}/~{self.policies_updated}) "
            f"skipped_no_guid={self.policies_skipped_no_guid} errors={len(self.errors)}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _discover_columns(supa: Any, table: str, fallback: set[str]) -> set[str]:
    """Columns that exist on the live table, from a sample row (fallback if empty)."""
    try:
        rows = supa.select(table, columns="*", limit=1)
    except Exception:  # noqa: BLE001 — discovery must never abort the sync
        return set(fallback)
    if rows and isinstance(rows[0], dict):
        return set(rows[0].keys())
    return set(fallback)


def _project(payload: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    """Keep only keys that exist as live columns and are non-None."""
    return {k: v for k, v in payload.items() if k in columns and v is not None}


# ---------------------------------------------------------------------------
# Sync stages
# ---------------------------------------------------------------------------
def _sync_clients(
    supa: Any, insureds: list[dict[str, Any]], *, now_iso: str, dry_run: bool, result: CanonicalSyncResult
) -> None:
    cols = _discover_columns(supa, CLIENTS_TABLE, _CLIENT_COLS)
    existing = {
        str(r.get(CLIENT_KEY))
        for r in supa.select(CLIENTS_TABLE, columns=CLIENT_KEY, limit=50000)
        if r.get(CLIENT_KEY)
    }
    for ins in insureds:
        client = _map_client(ins, now_iso=now_iso)
        if not client:
            _audit(supa, object_type=AUDIT_OBJECT_CLIENT,
                   source_object_id=_insured_guid(ins), action="skip",
                   message="no guid or insured_name", dry_run=dry_run)
            continue
        guid = client[CLIENT_KEY]
        try:
            if guid in existing:
                payload = _project({k: v for k, v in client.items() if k != CLIENT_KEY}, cols)
                if not dry_run and payload:
                    supa.update_where(CLIENTS_TABLE, payload, filters={CLIENT_KEY: f"eq.{guid}"})
                result.clients_updated += 1
                _audit(supa, object_type=AUDIT_OBJECT_CLIENT,
                       source_object_id=guid, action="update", dry_run=dry_run)
            else:
                if not dry_run:
                    supa.insert(CLIENTS_TABLE, _project(client, cols))
                result.clients_created += 1
                existing.add(guid)
                _audit(supa, object_type=AUDIT_OBJECT_CLIENT,
                       source_object_id=guid, action="create", dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 — one bad insured shouldn't abort the run
            result.errors.append(f"insured {guid}: {exc}")
            log.warning("canonical sync: insured %s failed: %s", guid, exc)
            _audit(supa, object_type=AUDIT_OBJECT_CLIENT,
                   source_object_id=guid, action="error", status="failed",
                   message=str(exc), dry_run=dry_run)


def _sync_policies(
    supa: Any, policies: list[dict[str, Any]], *, dry_run: bool, result: CanonicalSyncResult
) -> None:
    cols = _discover_columns(supa, POLICIES_TABLE, _POLICY_COLS)
    existing = {
        str(r.get(POLICY_KEY))
        for r in supa.select(POLICIES_TABLE, columns=POLICY_KEY, limit=50000)
        if r.get(POLICY_KEY)
    }
    for p in policies:
        pg = _policy_guid(p)
        if not pg:
            result.policies_skipped_no_guid += 1
            _audit(supa, object_type=AUDIT_OBJECT_POLICY,
                   source_object_id=_policy_number(p), action="skip",
                   message="no policy guid", dry_run=dry_run)
            continue
        volatile = _map_policy_volatile(p)
        try:
            if pg in existing:
                # Update volatile fields only — renewed_policy lineage is never sent,
                # so the CSV-loaded pointer survives the refresh.
                payload = _project(volatile, cols)
                if not dry_run and payload:
                    supa.update_where(POLICIES_TABLE, payload, filters={POLICY_KEY: f"eq.{pg}"})
                result.policies_updated += 1
                _audit(supa, object_type=AUDIT_OBJECT_POLICY,
                       source_object_id=pg, action="update", dry_run=dry_run)
            else:
                # Claim ownership on create. Any writer may refresh a row's volatile
                # fields; only its owner may deactivate it. Stamping here is what
                # lets a future writer tell "mine to retire" from "someone else's
                # row I have no business tombstoning" — the distinction whose
                # absence corrupted 43 rows in July.
                payload = _project(
                    {POLICY_KEY: pg, "renewed_policy": None,
                     "sync_owner": OWNER_BOOK_SYNC, **volatile},
                    cols,
                )
                if not dry_run:
                    supa.insert(POLICIES_TABLE, payload)
                result.policies_created += 1
                existing.add(pg)
                _audit(supa, object_type=AUDIT_OBJECT_POLICY,
                       source_object_id=pg, action="create", dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 — one bad policy shouldn't abort the run
            result.errors.append(f"policy {_policy_number(p) or pg}: {exc}")
            log.warning("canonical sync: policy %s failed: %s", pg, exc)
            _audit(supa, object_type=AUDIT_OBJECT_POLICY,
                   source_object_id=pg, action="error", status="failed",
                   message=str(exc), dry_run=dry_run)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_canonical_book_sync(
    nc: Any,
    supa: Any,
    *,
    since: str | None = None,
    dry_run: bool = False,
    page_size: int = 100,
    limit: int | None = None,
) -> CanonicalSyncResult:
    """Pull live NowCerts insureds + policies and reconcile the canonical book.

    Args:
        nc: NowCertsClient.
        supa: SupabaseClient.
        since: ISO datetime — incremental pull (changeDate >= since). None = full
            reconciliation (the nightly default).
        dry_run: compute + report only, no writes.
        page_size: NowCerts OData page size.
        limit: optional cap on records processed per entity (testing/safety).
    """
    result = CanonicalSyncResult()
    now_iso = _utcnow_iso()

    insureds = nc.fetch_insureds(since=since, page_size=page_size)
    if limit:
        insureds = insureds[:limit]
    result.insureds_fetched = len(insureds)

    policies = nc.fetch_policies(since=since, page_size=page_size)
    if limit:
        policies = policies[:limit]
    result.policies_fetched = len(policies)

    log.info(
        "canonical book sync: %d insureds, %d policies (dry_run=%s, since=%s)",
        len(insureds), len(policies), dry_run, since,
    )

    _sync_clients(supa, insureds, now_iso=now_iso, dry_run=dry_run, result=result)
    _sync_policies(supa, policies, dry_run=dry_run, result=result)

    log.info("canonical book sync done: %s", result.message)
    return result
