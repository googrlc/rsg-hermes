"""NowCerts → canonical book sync — refreshes the Supabase canonical mirror.

The canonical book (``canonical_clients`` + ``canonical_policies`` +
``nowcerts_insured_mirror``) is the deduped NowCerts snapshot the renewal
eligibility engine reads (``hermes/renewals/candidate_refresh.py`` selects
``canonical_policies`` directly). It was bulk-loaded once from CSV on 2026-06-10
and — until this job — had NO code path that refreshed it, so renewal discovery
ran on a frozen book: any policy written/renewed/cancelled after 6/10 was
invisible to ``renewal_candidates`` / ``project_85_renewals``. This job pulls
live NowCerts insureds + policies and reconciles them into the canonical tables
so the nightly ``--renewal-refresh`` runs on a current book.

Design guarantees (all deliberate — see the renewal-eligibility engine contract):

  * **Additive, no destructive truncate.** Reconciles by natural key
    (insured GUID, ``policy_number``). Pre-existing duplicate ``policy_number``
    rows are collapsed to one keeper (status-precedence) so the book converges to
    the 1-row-per-policy invariant without a blind SQL migration.
  * **Preserves ``renewed_policy`` lineage + client linkage.** The NowCerts API
    does not expose the renewal lineage pointer (see ``candidate_refresh``
    docstring); the CSV-loaded ``renewed_policy`` is irreplaceable. A refresh
    updates only volatile fields (status, active, dates, premium, carrier, LOB)
    and never overwrites an existing non-null ``renewed_policy`` or ``client_id``.
  * **Schema-adaptive.** The live canonical tables drifted from the phase-0
    migration (they carry ``policy_guid`` / ``renewed_policy`` the migration does
    not define). Every write is filtered to columns that actually exist on the
    live table (discovered from a sample row), so an unknown-column write can
    never error the run.
  * **dry_run** computes and reports the intended writes with zero side effects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from hermes.renewals import eligibility as elig
from hermes.sync.field_mapper import _strip_date

log = logging.getLogger(__name__)

CLIENTS_TABLE = "canonical_clients"
POLICIES_TABLE = "canonical_policies"
MIRROR_TABLE = "nowcerts_insured_mirror"

# Fallback column sets used only when a table is empty (no sample row to
# introspect). Superset of the phase-0 schema + the live CSV columns; the write
# path intersects these with the columns that actually exist.
_CLIENT_COLS = {
    "nowcerts_insured_guid", "name", "fein", "account_type", "espocrm_account_id", "updated_at",
}
_MIRROR_COLS = {
    "insured_guid", "commercial_name", "first_name", "last_name", "fein",
    "insured_type", "active", "raw_payload", "synced_at",
}
_POLICY_COLS = {
    "policy_number", "policy_guid", "nowcerts_insured_guid", "client_id", "renewed_policy",
    "line_of_business", "carrier", "status", "active", "effective_date", "expiration_date",
    "annualized_premium", "current_term_amount", "premium_amount", "raw_payload",
    "synced_at", "updated_at",
}

# Status precedence for collapsing duplicate policy_number rows — mirrors the
# phase-0 dedup (Active > Renewed > terminal > other).
_STATUS_PRECEDENCE = {
    "active": 1, "in force": 1, "inforce": 1, "bound": 1,
    "renewed": 2,
    "non-renewal": 3, "non-renewed": 3, "non renewed": 3,
    "cancelled": 3, "canceled": 3, "lapsed": 3, "expired": 3,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _status_rank(status: Any) -> int:
    return _STATUS_PRECEDENCE.get(str(status or "").strip().lower(), 4)


def _date_ordinal(value: Any) -> int:
    """Sortable ordinal for a date-ish value (0 when absent/unparseable)."""
    s = _strip_date(value)
    if not s:
        return 0
    try:
        return date.fromisoformat(s[:10]).toordinal()
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# NowCerts field extraction
# ---------------------------------------------------------------------------
def _insured_guid(record: dict[str, Any]) -> str:
    return str(
        record.get("id")
        or record.get("databaseId")
        or record.get("insuredDatabaseId")
        or ""
    ).strip()


def _insured_name(ins: dict[str, Any]) -> str:
    commercial = ins.get("commercialName") or ins.get("insuredCommercialName")
    if commercial:
        return str(commercial).strip()
    parts = [str(ins.get("firstName") or "").strip(), str(ins.get("lastName") or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _policy_number(p: dict[str, Any]) -> str:
    return str(p.get("number") or p.get("policyNumber") or p.get("Number") or "").strip()


def _policy_guid(p: dict[str, Any]) -> str | None:
    return p.get("databaseId") or p.get("DatabaseId") or p.get("id") or None


def _policy_lob(p: dict[str, Any]) -> str:
    lob_list = p.get("lineOfBusinesses")
    if isinstance(lob_list, list) and lob_list and isinstance(lob_list[0], dict):
        name = lob_list[0].get("lineOfBusinessName")
        if name:
            return str(name)
    for key in ("lineOfBusinessName", "lineOfBusiness", "LineOfBusinessName"):
        if p.get(key):
            return str(p[key])
    return ""


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


def _map_client(ins: dict[str, Any]) -> dict[str, Any] | None:
    guid, name = _insured_guid(ins), _insured_name(ins)
    if not guid or not name:
        return None  # canonical_clients.name is NOT NULL; guid is the dedup key
    return {
        "nowcerts_insured_guid": guid,
        "name": name,
        "fein": ins.get("fein") or None,
        "account_type": ins.get("insuredType") or None,
    }


def _map_mirror(ins: dict[str, Any], *, now_iso: str) -> dict[str, Any] | None:
    guid = _insured_guid(ins)
    if not guid:
        return None
    return {
        "insured_guid": guid,
        "commercial_name": ins.get("commercialName") or None,
        "first_name": ins.get("firstName") or None,
        "last_name": ins.get("lastName") or None,
        "fein": ins.get("fein") or None,
        "insured_type": ins.get("insuredType") or None,
        "active": bool(ins.get("active")),
        "raw_payload": ins,
        "synced_at": now_iso,
    }


def _map_policy_volatile(p: dict[str, Any], *, now_iso: str) -> dict[str, Any]:
    """The fields a refresh always overwrites from live NowCerts."""
    status = p.get("status") or p.get("Status")
    premium = _policy_premium(p)
    eff = _strip_date(p.get("effectiveDate") or p.get("EffectiveDate"))
    exp = _strip_date(p.get("expirationDate") or p.get("ExpirationDate"))
    carrier = p.get("carrierName") or p.get("CarrierName") or p.get("carrier") or None
    return {
        "policy_guid": _policy_guid(p),
        "nowcerts_insured_guid": str(p.get("insuredDatabaseId") or p.get("insuredId") or "").strip() or None,
        "line_of_business": _policy_lob(p) or None,
        "carrier": carrier,
        "status": status,
        "active": _policy_active(p, status),
        "effective_date": eff,
        "expiration_date": exp,
        "annualized_premium": premium,
        "current_term_amount": premium,
        "premium_amount": premium,
        "raw_payload": p,
        "synced_at": now_iso,
    }


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class CanonicalSyncResult:
    insureds_fetched: int = 0
    clients_created: int = 0
    clients_updated: int = 0
    mirror_written: int = 0
    policies_fetched: int = 0
    policies_created: int = 0
    policies_updated: int = 0
    dup_rows_collapsed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def message(self) -> str:
        return (
            f"canonical book: insureds={self.insureds_fetched} "
            f"clients(+{self.clients_created}/~{self.clients_updated}) mirror={self.mirror_written} "
            f"policies={self.policies_fetched} (+{self.policies_created}/~{self.policies_updated}) "
            f"dups_collapsed={self.dup_rows_collapsed} errors={len(self.errors)}"
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


def _keeper(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the surviving row for a policy_number: best status, latest expiry/sync."""
    return sorted(
        rows,
        key=lambda r: (
            _status_rank(r.get("status")),
            -_date_ordinal(r.get("expiration_date")),
            -_date_ordinal(r.get("synced_at")),
            str(r.get("id")),
        ),
    )[0]


def _collapse_nowcerts(policies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One NowCerts record per policy_number (best status wins), keyed by number."""
    best: dict[str, dict[str, Any]] = {}
    for p in policies:
        num = _policy_number(p)
        if not num:
            continue
        cur = best.get(num)
        if cur is None or _status_rank(p.get("status") or p.get("Status")) < _status_rank(cur.get("status") or cur.get("Status")):
            best[num] = p
    return best


# ---------------------------------------------------------------------------
# Sync stages
# ---------------------------------------------------------------------------
def _sync_insureds(
    supa: Any, insureds: list[dict[str, Any]], *, now_iso: str, dry_run: bool, result: CanonicalSyncResult
) -> dict[str, str]:
    """Reconcile canonical_clients + nowcerts_insured_mirror. Returns {guid: client_id}."""
    client_cols = _discover_columns(supa, CLIENTS_TABLE, _CLIENT_COLS)
    mirror_cols = _discover_columns(supa, MIRROR_TABLE, _MIRROR_COLS)

    existing_clients = {
        str(r.get("nowcerts_insured_guid")): str(r.get("id"))
        for r in supa.select(CLIENTS_TABLE, columns="id,nowcerts_insured_guid", limit=50000)
        if r.get("nowcerts_insured_guid")
    }
    existing_mirror = {
        str(r.get("insured_guid")): str(r.get("id"))
        for r in supa.select(MIRROR_TABLE, columns="id,insured_guid", limit=50000)
        if r.get("insured_guid")
    }

    guid_to_client: dict[str, str] = dict(existing_clients)
    for ins in insureds:
        client = _map_client(ins)
        if not client:
            continue
        guid = client["nowcerts_insured_guid"]
        try:
            if guid in existing_clients:
                if not dry_run:
                    supa.update(CLIENTS_TABLE, existing_clients[guid], _project({**client, "updated_at": now_iso}, client_cols))
                result.clients_updated += 1
            else:
                if not dry_run:
                    row = supa.insert(CLIENTS_TABLE, _project(client, client_cols))
                    guid_to_client[guid] = str(row.get("id"))
                result.clients_created += 1

            mirror = _map_mirror(ins, now_iso=now_iso)
            if mirror:
                if guid in existing_mirror:
                    if not dry_run:
                        supa.update(MIRROR_TABLE, existing_mirror[guid], _project(mirror, mirror_cols))
                else:
                    if not dry_run:
                        supa.insert(MIRROR_TABLE, _project(mirror, mirror_cols))
                result.mirror_written += 1
        except Exception as exc:  # noqa: BLE001 — one bad insured shouldn't abort the run
            result.errors.append(f"insured {guid}: {exc}")
            log.warning("canonical sync: insured %s failed: %s", guid, exc)
    return guid_to_client


def _sync_policies(
    supa: Any,
    policies: list[dict[str, Any]],
    guid_to_client: dict[str, str],
    *,
    now_iso: str,
    dry_run: bool,
    result: CanonicalSyncResult,
) -> None:
    policy_cols = _discover_columns(supa, POLICIES_TABLE, _POLICY_COLS)

    existing_rows = supa.select(
        POLICIES_TABLE,
        columns="id,policy_number,status,expiration_date,synced_at,renewed_policy,client_id",
        limit=50000,
    ) if {"renewed_policy", "client_id"} <= policy_cols else supa.select(
        POLICIES_TABLE, columns="id,policy_number,status,expiration_date,synced_at", limit=50000,
    )
    existing_by_number: dict[str, list[dict[str, Any]]] = {}
    for r in existing_rows:
        num = str(r.get("policy_number") or "").strip()
        if num:
            existing_by_number.setdefault(num, []).append(r)

    for num, p in _collapse_nowcerts(policies).items():
        volatile = _map_policy_volatile(p, now_iso=now_iso)
        guid = volatile.get("nowcerts_insured_guid") or ""
        resolved_client = guid_to_client.get(guid)
        try:
            if num in existing_by_number:
                rows = existing_by_number[num]
                keep = _keeper(rows)
                # Collapse any duplicate rows for this policy_number to the keeper.
                for loser in rows:
                    if str(loser.get("id")) != str(keep.get("id")):
                        if not dry_run:
                            supa.delete(POLICIES_TABLE, str(loser["id"]))
                        result.dup_rows_collapsed += 1
                # Preserve lineage + client linkage; only fill when currently empty.
                payload = dict(volatile)
                if not keep.get("client_id") and resolved_client:
                    payload["client_id"] = resolved_client
                payload["updated_at"] = now_iso
                if not dry_run:
                    supa.update(POLICIES_TABLE, str(keep["id"]), _project(payload, policy_cols))
                result.policies_updated += 1
            else:
                payload = {
                    "policy_number": num,
                    "client_id": resolved_client,
                    "renewed_policy": None,  # API exposes no lineage; best-effort null for new
                    **volatile,
                }
                if not dry_run:
                    supa.insert(POLICIES_TABLE, _project(payload, policy_cols))
                result.policies_created += 1
        except Exception as exc:  # noqa: BLE001 — one bad policy shouldn't abort the run
            result.errors.append(f"policy {num}: {exc}")
            log.warning("canonical sync: policy %s failed: %s", num, exc)


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
            reconciliation (the nightly default; required to collapse dup rows).
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

    guid_to_client = _sync_insureds(supa, insureds, now_iso=now_iso, dry_run=dry_run, result=result)
    _sync_policies(supa, policies, guid_to_client, now_iso=now_iso, dry_run=dry_run, result=result)

    log.info("canonical book sync done: %s", result.message)
    return result
