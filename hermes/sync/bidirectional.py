"""Bidirectional sync pipeline: EspoCRM ↔ Supabase ↔ NowCerts.

Supabase is the golden record hub. Three flows:
  A. NowCerts → Supabase → EspoCRM  (already built in pipeline.py)
  B. EspoCRM  → Supabase            (mirror new clients + commissions)
  D. Supabase → NowCerts            (push CRM-originated clients + commissions)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.sync.field_mapper import (
    map_account_to_golden,
    map_account_to_insured,
    map_commission_to_nowcerts_policy,
    map_policy_to_commission,
    payload_hash,
)
from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError

log = logging.getLogger(__name__)


@dataclass
class BidiSyncResult:
    """Summary of a bidirectional sync run."""

    run_id: str = ""
    direction: str = ""
    accounts_mirrored: int = 0
    accounts_pushed: int = 0
    commissions_mirrored: int = 0
    commissions_pushed: int = 0
    records_failed: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.records_failed == 0

    @property
    def message(self) -> str:
        prefix = "DRY RUN: " if self.dry_run else ""
        return (
            f"{prefix}{self.direction} sync complete: "
            f"accounts_mirrored={self.accounts_mirrored} "
            f"accounts_pushed={self.accounts_pushed} "
            f"commissions_mirrored={self.commissions_mirrored} "
            f"commissions_pushed={self.commissions_pushed} "
            f"failed={self.records_failed} run_id={self.run_id}"
        )


# ---------------------------------------------------------------------------
# Flow B: EspoCRM → Supabase (mirror)
# ---------------------------------------------------------------------------

def run_crm_to_hub(
    espo: EspoClient,
    supa: SupabaseClient,
    *,
    dry_run: bool = False,
    since_hours: int = 24,
) -> BidiSyncResult:
    """Mirror EspoCRM Accounts + Policies into Supabase golden record."""
    result = BidiSyncResult(direction="crm_to_hub", dry_run=dry_run)

    # Start sync run
    run_row = _start_run(supa, "crm_to_hub", dry_run)
    result.run_id = run_row.get("id", "")

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # ── Mirror Accounts ───────────────────────────────────────────
        accounts = _fetch_modified_accounts(espo, cutoff)
        log.info("CRM→Hub: %d accounts modified since %s", len(accounts), cutoff)

        for account in accounts:
            try:
                golden_row = map_account_to_golden(account)
                espo_id = golden_row["espocrm_id"]
                if not espo_id:
                    continue

                if not dry_run:
                    _upsert_golden_account(supa, golden_row)

                result.accounts_mirrored += 1
                _audit(supa, result.run_id, "Account", espo_id, "mirror", "success", dry_run=dry_run)

            except (SupabaseClientError, Exception) as exc:
                result.records_failed += 1
                result.errors.append(f"Account {account.get('id','?')}: {exc}")
                _audit(supa, result.run_id, "Account", account.get("id", ""), "mirror", "failed", str(exc), dry_run=dry_run)

        # ── Mirror Policies (commission data) ─────────────────────────
        policies = _fetch_modified_policies(espo, cutoff)
        log.info("CRM→Hub: %d policies modified since %s", len(policies), cutoff)

        for policy in policies:
            try:
                account_id = _resolve_golden_account_id(supa, policy, dry_run)
                commission_row = map_policy_to_commission(policy, account_id)
                if not commission_row.get("policy_number"):
                    continue

                if not dry_run:
                    _upsert_golden_commission(supa, commission_row)

                result.commissions_mirrored += 1
            except (SupabaseClientError, Exception) as exc:
                result.records_failed += 1
                result.errors.append(f"Policy {policy.get('id','?')}: {exc}")

    except Exception as exc:
        result.records_failed += 1
        result.errors.append(f"Pipeline error: {exc}")

    _finish_run(supa, result.run_id, result, dry_run)
    return result


# ---------------------------------------------------------------------------
# Flow D: Supabase → NowCerts (push)
# ---------------------------------------------------------------------------

def run_hub_to_nowcerts(
    nc: NowCertsClient,
    supa: SupabaseClient,
    *,
    dry_run: bool = False,
) -> BidiSyncResult:
    """Push CRM-originated clients and commissions from Supabase to NowCerts."""
    result = BidiSyncResult(direction="hub_to_nowcerts", dry_run=dry_run)

    run_row = _start_run(supa, "hub_to_nowcerts", dry_run)
    result.run_id = run_row.get("id", "")

    try:
        # ── Push accounts without NowCerts link ──────────────────────
        unlinked = _fetch_unlinked_accounts(supa)
        log.info("Hub→NowCerts: %d accounts to push", len(unlinked))

        for account in unlinked:
            try:
                nc_payload = map_account_to_insured(
                    account.get("raw_espo_payload") or account,
                    nowcerts_database_id=account.get("nowcerts_id"),
                )

                if not nc_payload.get("CommercialName") and not nc_payload.get("LastName"):
                    log.warning("Skipping account %s: no name", account.get("espocrm_id"))
                    continue

                if not dry_run:
                    resp = nc.create_insured(nc_payload)
                    # Update golden record with NowCerts ID if returned
                    nc_id = resp.get("DatabaseId") or resp.get("databaseId") or resp.get("id")
                    if nc_id:
                        _link_nowcerts_id(supa, account["espocrm_id"], str(nc_id))

                result.accounts_pushed += 1
                _audit(supa, result.run_id, "Insured", account.get("espocrm_id", ""), "push", "success", dry_run=dry_run)

            except (NowCertsClientError, Exception) as exc:
                result.records_failed += 1
                result.errors.append(f"Push account {account.get('espocrm_id','?')}: {exc}")
                _audit(supa, result.run_id, "Insured", account.get("espocrm_id", ""), "push", "failed", str(exc), dry_run=dry_run)

        # ── Push commissions for linked accounts ─────────────────────
        unpushed_commissions = _fetch_unpushed_commissions(supa)
        log.info("Hub→NowCerts: %d commissions to push", len(unpushed_commissions))

        for comm in unpushed_commissions:
            try:
                account_row = _get_golden_account_by_id(supa, comm.get("account_id", ""))
                nc_insured_id = account_row.get("nowcerts_id") if account_row else None
                if not nc_insured_id:
                    continue

                nc_policy_payload = map_commission_to_nowcerts_policy(comm, nc_insured_id)
                if not nc_policy_payload.get("Number") and not nc_policy_payload.get("LineOfBusinessName"):
                    continue

                if not dry_run:
                    nc.insert_policy(nc_policy_payload)
                    _mark_commission_pushed(supa, comm["id"])

                result.commissions_pushed += 1

            except (NowCertsClientError, Exception) as exc:
                result.records_failed += 1
                result.errors.append(f"Push commission {comm.get('id','?')}: {exc}")

    except Exception as exc:
        result.records_failed += 1
        result.errors.append(f"Pipeline error: {exc}")

    _finish_run(supa, result.run_id, result, dry_run)
    return result


# ---------------------------------------------------------------------------
# Full bidirectional orchestrator
# ---------------------------------------------------------------------------

def run_bidirectional(
    nc: NowCertsClient,
    espo: EspoClient,
    supa: SupabaseClient,
    *,
    dry_run: bool = False,
    since_hours: int = 24,
) -> BidiSyncResult:
    """Run all sync directions in sequence.

    1. NowCerts → Supabase → EspoCRM  (existing pipeline)
    2. EspoCRM → Supabase             (mirror)
    3. Supabase → NowCerts            (push)
    """
    combined = BidiSyncResult(direction="bidirectional", dry_run=dry_run)

    # Direction 1: NowCerts → EspoCRM (existing)
    from hermes.sync.pipeline import run_insured_to_account_sync

    nc_result = run_insured_to_account_sync(nc, espo, supa, dry_run=dry_run)
    combined.accounts_mirrored += nc_result.records_created + nc_result.records_updated
    combined.records_failed += nc_result.records_failed
    combined.errors.extend(nc_result.errors)

    # Direction 2: EspoCRM → Supabase
    crm_result = run_crm_to_hub(espo, supa, dry_run=dry_run, since_hours=since_hours)
    combined.accounts_mirrored += crm_result.accounts_mirrored
    combined.commissions_mirrored += crm_result.commissions_mirrored
    combined.records_failed += crm_result.records_failed
    combined.errors.extend(crm_result.errors)

    # Direction 3: Supabase → NowCerts
    push_result = run_hub_to_nowcerts(nc, supa, dry_run=dry_run)
    combined.accounts_pushed += push_result.accounts_pushed
    combined.commissions_pushed += push_result.commissions_pushed
    combined.records_failed += push_result.records_failed
    combined.errors.extend(push_result.errors)

    # Use the last run_id
    combined.run_id = push_result.run_id or crm_result.run_id or nc_result.run_id

    return combined


# ---------------------------------------------------------------------------
# EspoCRM query helpers
# ---------------------------------------------------------------------------

def _fetch_modified_accounts(espo: EspoClient, cutoff: str) -> list[dict[str, Any]]:
    """Fetch Accounts modified after cutoff."""
    try:
        body = espo.get("Account", params={
            "maxSize": 200,
            "orderBy": "modifiedAt",
            "order": "desc",
            "where": [{"type": "after", "attribute": "modifiedAt", "value": cutoff}],
        })
        return body.get("list", []) if isinstance(body, dict) else []
    except EspoClientError as exc:
        log.warning("Failed to fetch modified accounts: %s", exc)
        return []


def _fetch_modified_policies(espo: EspoClient, cutoff: str) -> list[dict[str, Any]]:
    """Fetch Policies modified after cutoff."""
    try:
        body = espo.get("Policy", params={
            "maxSize": 200,
            "orderBy": "modifiedAt",
            "order": "desc",
            "where": [{"type": "after", "attribute": "modifiedAt", "value": cutoff}],
        })
        return body.get("list", []) if isinstance(body, dict) else []
    except EspoClientError as exc:
        log.warning("Failed to fetch modified policies: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Supabase golden record helpers
# ---------------------------------------------------------------------------

def _upsert_golden_account(supa: SupabaseClient, row: dict[str, Any]) -> None:
    """Insert or update a crm_accounts row, keyed on espocrm_id."""
    espo_id = row["espocrm_id"]
    row["last_espo_sync_at"] = datetime.now(timezone.utc).isoformat()
    row["updated_at"] = datetime.now(timezone.utc).isoformat()

    existing = supa.select(
        "crm_accounts", columns="id",
        params={"espocrm_id": f"eq.{espo_id}"}, limit=1,
    )
    if existing:
        supa.update_where(
            "crm_accounts", row,
            filters={"espocrm_id": f"eq.{espo_id}"},
        )
    else:
        supa.insert("crm_accounts", row)


def _upsert_golden_commission(supa: SupabaseClient, row: dict[str, Any]) -> None:
    """Insert or update a crm_commissions row, keyed on espocrm_id."""
    espo_id = row.get("espocrm_id")
    row["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    row["updated_at"] = datetime.now(timezone.utc).isoformat()

    if espo_id:
        existing = supa.select(
            "crm_commissions", columns="id",
            params={"espocrm_id": f"eq.{espo_id}"}, limit=1,
        )
        if existing:
            supa.update_where(
                "crm_commissions", row,
                filters={"espocrm_id": f"eq.{espo_id}"},
            )
            return

    supa.insert("crm_commissions", row)


def _fetch_unlinked_accounts(supa: SupabaseClient) -> list[dict[str, Any]]:
    """Fetch golden accounts that have no NowCerts link."""
    return supa.select(
        "crm_accounts",
        params={"nowcerts_id": "is.null", "source_system": "eq.espocrm"},
        limit=200,
    )


def _fetch_unpushed_commissions(supa: SupabaseClient) -> list[dict[str, Any]]:
    """Fetch commissions from CRM that haven't been pushed to NowCerts."""
    return supa.select(
        "crm_commissions",
        params={"nowcerts_id": "is.null", "source_system": "eq.espocrm"},
        limit=200,
    )


def _link_nowcerts_id(supa: SupabaseClient, espo_id: str, nowcerts_id: str) -> None:
    """Update a golden account with its NowCerts ID after successful push."""
    supa.update_where(
        "crm_accounts",
        {"nowcerts_id": nowcerts_id, "last_nowcerts_sync_at": datetime.now(timezone.utc).isoformat()},
        filters={"espocrm_id": f"eq.{espo_id}"},
    )

    try:
        supa.upsert(
            "sync_mappings",
            {
                "nowcerts_entity_type": "insured",
                "nowcerts_id": nowcerts_id,
                "espocrm_entity_type": "Account",
                "espocrm_id": espo_id,
                "match_method": "crm_originated",
                "match_confidence": 1.0,
                "active": True,
            },
            on_conflict="nowcerts_entity_type,nowcerts_id",
        )
    except SupabaseClientError:
        log.warning("Could not update sync_mappings for %s → %s", espo_id, nowcerts_id)


def _get_golden_account_by_id(supa: SupabaseClient, account_id: str) -> dict[str, Any] | None:
    if not account_id:
        return None
    rows = supa.select("crm_accounts", params={"id": f"eq.{account_id}"}, limit=1)
    return rows[0] if rows else None


def _mark_commission_pushed(supa: SupabaseClient, commission_id: str) -> None:
    supa.update("crm_commissions", commission_id, {
        "nowcerts_id": "pushed",
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    })


def _resolve_golden_account_id(
    supa: SupabaseClient,
    policy: dict[str, Any],
    dry_run: bool,
) -> str | None:
    """Resolve the golden record account_id for a policy's parent account."""
    account_id = policy.get("accountId") or ""
    if not account_id:
        return None

    rows = supa.select(
        "crm_accounts", columns="id",
        params={"espocrm_id": f"eq.{account_id}"}, limit=1,
    )
    return rows[0]["id"] if rows else None


# ---------------------------------------------------------------------------
# Sync run tracking (reuse existing sync_runs table)
# ---------------------------------------------------------------------------

_ACTION_MAP = {"mirror": "create", "push": "update"}


def _start_run(supa: SupabaseClient, workflow: str, dry_run: bool) -> dict[str, Any]:
    try:
        return supa.insert("sync_runs", {
            "workflow_name": workflow,
            "source_system": "espocrm" if "crm" in workflow else "supabase",
            "destination_system": "nowcerts" if "nowcerts" in workflow else "supabase",
            "status": "dry_run" if dry_run else "running",
        })
    except SupabaseClientError as exc:
        log.warning("Failed to start sync run: %s", exc)
        return {}


def _finish_run(supa: SupabaseClient, run_id: str, result: BidiSyncResult, dry_run: bool) -> None:
    if not run_id:
        return
    status = "dry_run" if dry_run else ("success" if result.ok else "failed")
    try:
        supa.update("sync_runs", run_id, {
            "status": status,
            "records_processed": result.accounts_mirrored + result.commissions_mirrored,
            "records_created": result.accounts_pushed + result.commissions_pushed,
            "records_failed": result.records_failed,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
    except SupabaseClientError as exc:
        log.warning("Failed to finish sync run %s: %s", run_id, exc)


def _audit(
    supa: SupabaseClient,
    run_id: str,
    object_type: str,
    object_id: str,
    action: str,
    status: str,
    error: str | None = None,
    *,
    dry_run: bool = False,
) -> None:
    if dry_run or not run_id:
        return
    try:
        row: dict[str, Any] = {
            "run_id": run_id,
            "object_type": object_type,
            "source_object_id": object_id,
            "action": _ACTION_MAP.get(action, action),
            "status": status,
        }
        if error:
            row["message"] = error[:1000]
        supa.insert("sync_audit_log", row)
    except SupabaseClientError:
        log.debug("Failed to write audit row for %s %s", object_type, object_id)
