"""Bidirectional sync pipeline: EspoCRM ↔ Supabase ↔ NowCerts.

Supabase is the golden record hub. Three flows:
  A. NowCerts → Supabase → EspoCRM  (already built in pipeline.py)
  B. EspoCRM  → Supabase            (mirror new clients + commissions)
  D. Supabase → NowCerts            (push CRM-originated clients + commissions)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.operations.renewal_tracker import upsert_renewal
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
                espo_id = str(account.get("id") or "").strip()
                if espo_id:
                    _stage_inbound_espo(
                        supa, result.run_id, "Account", espo_id, account, dry_run=dry_run,
                    )

                golden_row = map_account_to_golden(account)
                espo_id = golden_row["espocrm_id"]
                if not espo_id:
                    continue

                if not dry_run:
                    _upsert_golden_account(supa, golden_row)

                result.accounts_mirrored += 1
                _audit(
                    supa,
                    result.run_id,
                    workflow_name="crm_to_hub",
                    source_system="espocrm",
                    destination_system="supabase",
                    object_type="Account",
                    object_id=espo_id,
                    action="mirror",
                    status="success",
                    dry_run=dry_run,
                )

            except (SupabaseClientError, Exception) as exc:
                result.records_failed += 1
                result.errors.append(f"Account {account.get('id','?')}: {exc}")
                _audit(
                    supa,
                    result.run_id,
                    workflow_name="crm_to_hub",
                    source_system="espocrm",
                    destination_system="supabase",
                    object_type="Account",
                    object_id=str(account.get("id", "")),
                    action="mirror",
                    status="failed",
                    error=str(exc),
                    dry_run=dry_run,
                )

        # ── Mirror Policies (commission data) ─────────────────────────
        policies = _fetch_modified_policies(espo, cutoff)
        log.info("CRM→Hub: %d policies modified since %s", len(policies), cutoff)

        for policy in policies:
            try:
                pol_id = str(policy.get("id") or "").strip()
                if pol_id:
                    _stage_inbound_espo(
                        supa, result.run_id, "Policy", pol_id, policy, dry_run=dry_run,
                    )

                account_id = _resolve_golden_account_id(supa, policy, dry_run)
                commission_row = map_policy_to_commission(policy, account_id)
                if not commission_row.get("policy_number"):
                    continue

                if not dry_run:
                    _upsert_golden_commission(supa, commission_row)
                    _maybe_upsert_renewal_watchlist(supa, policy, dry_run=dry_run)

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
                    # NowCerts /api/Insured/Insert returns the id under "insuredDatabaseId".
                    # Other keys kept as defensive fallbacks.
                    nc_id = (
                        resp.get("insuredDatabaseId")
                        or resp.get("DatabaseId")
                        or resp.get("databaseId")
                        or resp.get("id")
                    )
                    if nc_id:
                        _link_nowcerts_id(supa, account["espocrm_id"], str(nc_id))

                result.accounts_pushed += 1
                _audit(
                    supa,
                    result.run_id,
                    workflow_name="hub_to_nowcerts",
                    source_system="supabase",
                    destination_system="nowcerts",
                    object_type="Insured",
                    object_id=str(account.get("espocrm_id", "")),
                    action="push",
                    status="success",
                    dry_run=dry_run,
                )

            except (NowCertsClientError, Exception) as exc:
                result.records_failed += 1
                result.errors.append(f"Push account {account.get('espocrm_id','?')}: {exc}")
                _audit(
                    supa,
                    result.run_id,
                    workflow_name="hub_to_nowcerts",
                    source_system="supabase",
                    destination_system="nowcerts",
                    object_type="Insured",
                    object_id=str(account.get("espocrm_id", "")),
                    action="push",
                    status="failed",
                    error=str(exc),
                    dry_run=dry_run,
                )

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
                    policy_resp = nc.insert_policy(nc_policy_payload)
                    nc_policy_id = (
                        policy_resp.get("policyDatabaseId")
                        or policy_resp.get("insuredDatabaseId")
                        or policy_resp.get("DatabaseId")
                        or policy_resp.get("databaseId")
                        or policy_resp.get("id")
                        or "pushed"
                    ) if isinstance(policy_resp, dict) else "pushed"
                    _mark_commission_pushed(supa, comm["id"], str(nc_policy_id))

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


def run_writeback(
    nc: NowCertsClient,
    espo: EspoClient,
    supa: SupabaseClient,
    *,
    dry_run: bool = False,
    since_hours: int = 24,
) -> BidiSyncResult:
    """Run ONLY the EspoCRM → NowCerts writeback direction.

    This is ``run_bidirectional`` minus the inbound NowCerts → EspoCRM leg, so
    the two directions can be scheduled at different times and never run in the
    same pass. Running both directions together lets a value synced one way
    immediately echo back the other way; separating them removes that race.

    1. EspoCRM → Supabase   (crm_to_hub — mirror CRM fields into the hub)
    2. Supabase → NowCerts  (hub_to_nowcerts — push CRM fields to NowCerts)
    """
    combined = BidiSyncResult(direction="writeback", dry_run=dry_run)

    crm_result = run_crm_to_hub(espo, supa, dry_run=dry_run, since_hours=since_hours)
    combined.accounts_mirrored += crm_result.accounts_mirrored
    combined.commissions_mirrored += crm_result.commissions_mirrored
    combined.records_failed += crm_result.records_failed
    combined.errors.extend(crm_result.errors)

    push_result = run_hub_to_nowcerts(nc, supa, dry_run=dry_run)
    combined.accounts_pushed += push_result.accounts_pushed
    combined.commissions_pushed += push_result.commissions_pushed
    combined.records_failed += push_result.records_failed
    combined.errors.extend(push_result.errors)

    combined.run_id = push_result.run_id or crm_result.run_id

    return combined


# ---------------------------------------------------------------------------
# Inbound staging + renewal watchlist (CRM → hub contract)
# ---------------------------------------------------------------------------

def _stage_inbound_espo(
    supa: SupabaseClient,
    run_id: str,
    object_type: str,
    object_id: str,
    payload: dict[str, Any],
    *,
    dry_run: bool,
) -> None:
    """Snapshot raw Espo payloads into inbound_sync_staging for the run."""
    if dry_run or not run_id or not object_id:
        return
    try:
        supa.upsert(
            "inbound_sync_staging",
            {
                "run_id": run_id,
                "source_system": "espocrm",
                "source_object_type": object_type,
                "source_object_id": object_id,
                "raw_payload": payload,
                "processing_status": "mapped",
                "payload_hash": payload_hash(payload),
            },
            on_conflict="run_id,source_system,source_object_type,source_object_id",
        )
    except SupabaseClientError:
        log.debug("Staging skipped for %s %s", object_type, object_id)


def _maybe_upsert_renewal_watchlist(
    supa: SupabaseClient,
    policy: dict[str, Any],
    *,
    dry_run: bool,
) -> None:
    """Drive project_85_renewals from Espo Policy rows when enough fields exist."""
    if dry_run:
        return
    commission_row = map_policy_to_commission(policy, None)
    policy_number = commission_row.get("policy_number")
    expiration_date = commission_row.get("expiration_date")
    if not policy_number or not expiration_date:
        return
    client_name = (
        policy.get("accountName")
        or policy.get("account_name")
        or "Unknown client"
    )
    premium = commission_row.get("premium")
    try:
        upsert_renewal(
            supa,
            policy_number=str(policy_number),
            client_name=str(client_name),
            expiration_date=str(expiration_date),
            premium_current=float(premium) if premium is not None else None,
        )
    except (SupabaseClientError, ValueError) as exc:
        log.debug("project_85_renewals upsert skipped for %s: %s", policy_number, exc)


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
        items = body.get("list") if isinstance(body, dict) else None
        return items if isinstance(items, list) else []
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
        items = body.get("list") if isinstance(body, dict) else None
        return items if isinstance(items, list) else []
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


def _hub_push_account_limit() -> int:
    """Cap Hub→NowCerts account batch (default 200). Set HERMES_HUB_TO_NOWCERTS_ACCOUNT_LIMIT for tests."""
    raw = os.environ.get("HERMES_HUB_TO_NOWCERTS_ACCOUNT_LIMIT", "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), 500))
    return 200


def _fetch_unlinked_accounts(supa: SupabaseClient) -> list[dict[str, Any]]:
    """Fetch golden accounts that have no NowCerts link."""
    return supa.select(
        "crm_accounts",
        params={"nowcerts_id": "is.null", "source_system": "eq.espocrm"},
        limit=_hub_push_account_limit(),
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
                "nowcerts_entity_type": "Insured",
                "nowcerts_id": nowcerts_id,
                "espocrm_entity_type": "Account",
                "espocrm_id": espo_id,
                "match_method": "manual",
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


def _mark_commission_pushed(supa: SupabaseClient, commission_id: str, nowcerts_id: str = "pushed") -> None:
    supa.update("crm_commissions", commission_id, {
        "nowcerts_id": nowcerts_id,
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
    wf_name = f"dry_run:{workflow}" if dry_run else workflow
    try:
        return supa.insert("sync_runs", {
            "workflow_name": wf_name,
            "source_system": "espocrm" if "crm" in workflow else "supabase",
            "destination_system": "nowcerts" if "nowcerts" in workflow else "supabase",
            "status": "running",
        })
    except SupabaseClientError as exc:
        log.warning("Failed to start sync run: %s", exc)
        return {}


def _finish_run(supa: SupabaseClient, run_id: str, result: BidiSyncResult, dry_run: bool) -> None:
    if not run_id:
        return
    status = "success" if result.ok else "failed"
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
    *,
    workflow_name: str,
    source_system: str,
    destination_system: str,
    object_type: str,
    object_id: str,
    action: str,
    status: str,
    error: str | None = None,
    dry_run: bool = False,
) -> None:
    if dry_run or not run_id:
        return
    oid = (object_id or "").strip() or "unknown"
    try:
        row: dict[str, Any] = {
            "workflow_name": workflow_name,
            "run_id": run_id,
            "object_type": object_type,
            "object_id": oid,
            "source_system": source_system,
            "destination_system": destination_system,
            "source_object_id": oid,
            "action": _ACTION_MAP.get(action, action),
            "status": status,
        }
        if error:
            row["message"] = error[:1000]
        supa.insert("sync_audit_log", row)
    except SupabaseClientError:
        log.debug("Failed to write audit row for %s %s", object_type, object_id)
