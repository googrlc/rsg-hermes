"""Morning Policy Sync: 7am cron job for NowCerts → Supabase → EspoCRM.

This job runs daily at 7am to:
1. Fetch updated policies from NowCerts (source of truth for policy data)
2. Sync to Supabase as golden record
3. Push updates to EspoCRM
4. Post summary to Slack
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError
from hermes.sync.pipeline import run_insured_to_account_sync

log = logging.getLogger(__name__)

STATE_DIR = Path(os.environ.get("HERMES_STATE_DIR", "/tmp/hermes"))


@dataclass
class PolicyChange:
    """One policy created or updated in NowCerts."""

    policy_id: str
    insured_name: str
    policy_number: str
    action: str  # "created" | "updated"
    carrier: str
    effective_date: str
    premium: float
    changed_at: str


@dataclass
class MorningSyncResult:
    ok: bool
    posted: bool
    skipped: bool
    message: str
    policies_synced: int = 0
    accounts_synced: int = 0
    policies_created: int = 0
    policies_updated: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run(
    nc: NowCertsClient,
    espo: EspoClient,
    supa: SupabaseClient,
    *,
    notifier: SlackNotifier | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
    lookback_hours: int | None = None,
) -> MorningSyncResult:
    """Execute the morning policy sync pipeline."""
    local_now = _now_in_timezone(now)
    target_day = local_now.date()
    hours = lookback_hours or int(os.environ.get("HERMES_MORNING_SYNC_HOURS", "24"))
    cutoff = local_now - timedelta(hours=hours)

    if not force and _already_sent_today(target_day, prefix="morning_sync"):
        return MorningSyncResult(
            ok=True,
            posted=False,
            skipped=True,
            message=f"Morning sync already posted for {target_day.isoformat()}; skipping.",
        )

    errors: list[str] = []
    warnings: list[str] = []
    policies_synced = 0
    accounts_synced = 0
    policies_created = 0
    policies_updated = 0

    try:
        # Step 1: Sync Insureds → Accounts (existing pipeline)
        log.info("Starting insured → account sync...")
        account_result = run_insured_to_account_sync(
            nc, espo, supa,
            dry_run=dry_run,
            since=cutoff.isoformat() if cutoff else None,
            use_outbound_queue=not dry_run,
        )
        accounts_synced = account_result.records_created + account_result.records_updated
        if account_result.errors:
            errors.extend(account_result.errors)
            warnings.extend(account_result.errors)
        log.info("Account sync complete: %d processed", accounts_synced)

        # Step 2: Fetch and sync policies from NowCerts
        log.info("Fetching policies from NowCerts...")
        raw_policies = nc.fetch_policies(since=cutoff.isoformat() if cutoff else None)
        policies_synced = len(raw_policies)
        log.info("Fetched %d policies from NowCerts", policies_synced)

        if not dry_run:
            # Stage policies in Supabase
            for policy in raw_policies:
                policy_id = str(policy.get("database_id") or policy.get("databaseId") or "")
                if not policy_id:
                    warnings.append(f"Policy missing database_id: {policy}")
                    continue

                try:
                    _stage_policy(supa, policy)
                    is_create = _is_new_policy(supa, policy_id)
                    if is_create:
                        policies_created += 1
                    else:
                        policies_updated += 1
                except Exception as e:
                    errors.append(f"Failed to stage policy {policy_id}: {e}")
                    log.exception("Failed to stage policy %s", policy_id)

            # Step 3: Push policy updates to EspoCRM
            log.info("Pushing policy updates to EspoCRM...")
            _push_policies_to_crm(espo, supa, raw_policies, errors, warnings)

    except Exception as exc:
        log.exception("Morning sync failed: %s", exc)
        errors.append(str(exc))

    # Build summary
    total_changes = policies_created + policies_updated
    message = (
        f"Morning Policy Sync — {target_day.isoformat()}: "
        f"{policies_synced} policies synced ({policies_created} new, {policies_updated} updated), "
        f"{accounts_synced} accounts synced"
    )

    if dry_run:
        return MorningSyncResult(
            ok=len(errors) == 0,
            posted=False,
            skipped=False,
            message=f"DRY RUN: {message}",
            policies_synced=policies_synced,
            accounts_synced=accounts_synced,
            policies_created=policies_created,
            policies_updated=policies_updated,
            errors=errors,
            warnings=warnings,
        )

    # Post to Slack
    active_notifier = notifier or SlackNotifier()
    try:
        text, blocks = _build_slack_payload(
            day=target_day,
            policies_synced=policies_synced,
            accounts_synced=accounts_synced,
            policies_created=policies_created,
            policies_updated=policies_updated,
            errors=errors,
            warnings=warnings,
        )
        active_notifier.post_message(text=text, blocks=blocks)
    except SlackNotifierError as e:
        return MorningSyncResult(
            ok=False,
            posted=False,
            skipped=False,
            message=f"Morning sync Slack post failed: {e}",
            policies_synced=policies_synced,
            accounts_synced=accounts_synced,
            policies_created=policies_created,
            policies_updated=policies_updated,
            errors=errors,
            warnings=warnings,
        )

    _write_state(target_day, prefix="morning_sync")

    # Log to CRM as Task
    _log_to_crm(
        espo, target_day,
        policies_synced, accounts_synced,
        policies_created, policies_updated,
        errors, warnings,
    )

    return MorningSyncResult(
        ok=len(errors) == 0,
        posted=True,
        skipped=False,
        message=message,
        policies_synced=policies_synced,
        accounts_synced=accounts_synced,
        policies_created=policies_created,
        policies_updated=policies_updated,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _stage_policy(supa: SupabaseClient, policy: dict[str, Any]) -> dict[str, Any]:
    """Stage a policy record in Supabase."""
    policy_id = str(policy.get("database_id") or policy.get("databaseId") or "")
    p_hash = hash(json.dumps(policy, sort_keys=True))
    
    return supa.upsert(
        "inbound_sync_staging",
        {
            "source_system": "nowcerts",
            "source_object_type": "Policy",
            "source_object_id": policy_id,
            "raw_payload": policy,
            "payload_hash": str(p_hash),
            "processing_status": "pending",
        },
        on_conflict="source_system,source_object_type,source_object_id",
    )


def _is_new_policy(supa: SupabaseClient, policy_id: str) -> bool:
    """Check if this is a new policy (not previously synced)."""
    # Simple heuristic: check if there's a mapping for this policy
    mappings = supa.select(
        "sync_mappings",
        params={
            "nowcerts_entity_type": "eq.Policy",
            "nowcerts_id": f"eq.{policy_id}",
        },
        limit=1,
    )
    return not mappings


def _push_policies_to_crm(
    espo: EspoClient,
    supa: SupabaseClient,
    policies: list[dict[str, Any]],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Push policy updates from Supabase to EspoCRM."""
    for policy in policies:
        policy_id = str(policy.get("database_id") or policy.get("databaseId") or "")
        if not policy_id:
            continue

        try:
            # Find or create mapping
            mapping = _resolve_policy_mapping(supa, espo, policy)
            
            # Build EspoCRM payload
            espo_payload = _map_policy_to_crm(policy)
            
            if mapping and mapping.get("espocrm_id"):
                # Update existing
                espo.update("Policy", mapping["espocrm_id"], espo_payload)
            elif mapping:
                # Create new
                crm_response = espo.create("Policy", espo_payload)
                if isinstance(crm_response, dict) and crm_response.get("id"):
                    supa.update(
                        "sync_mappings",
                        mapping["id"],
                        {"espocrm_id": crm_response["id"]},
                    )
        except EspoClientError as e:
            errors.append(f"Failed to push policy {policy_id} to CRM: {e}")
            log.exception("Failed to push policy %s to CRM", policy_id)


def _resolve_policy_mapping(
    supa: SupabaseClient,
    espo: EspoClient,
    policy: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve or create a mapping for a policy."""
    policy_id = str(policy.get("database_id") or policy.get("databaseId") or "")
    
    # Check existing mapping
    existing = supa.select(
        "sync_mappings",
        params={
            "nowcerts_entity_type": "eq.Policy",
            "nowcerts_id": f"eq.{policy_id}",
        },
        limit=1,
    )
    if existing:
        return existing[0]
    
    # Try to find by policy number
    policy_number = policy.get("number", "")
    if policy_number:
        espo_match = espo.find_one_by_field("Policy", "policyNumber", policy_number)
        if espo_match:
            return supa.upsert(
                "sync_mappings",
                {
                    "nowcerts_entity_type": "Policy",
                    "nowcerts_id": policy_id,
                    "espocrm_entity_type": "Policy",
                    "espocrm_id": espo_match["id"],
                    "match_method": "policy_number",
                    "match_confidence": 1.0,
                    "active": True,
                },
                on_conflict="nowcerts_entity_type,nowcerts_id",
            )
    
    # No match - create mapping without espocrm_id
    return supa.upsert(
        "sync_mappings",
        {
            "nowcerts_entity_type": "Policy",
            "nowcerts_id": policy_id,
            "espocrm_entity_type": "Policy",
            "espocrm_id": None,
            "match_method": "none",
            "match_confidence": 0.0,
            "active": True,
        },
        on_conflict="nowcerts_entity_type,nowcerts_id",
    )


def _map_policy_to_crm(policy: dict[str, Any]) -> dict[str, Any]:
    """Map NowCerts policy to EspoCRM Policy fields."""
    # Adjust field names based on your EspoCRM schema
    return {
        "policyNumber": policy.get("number", ""),
        "carrier": policy.get("carrierName", ""),
        "effectiveDate": policy.get("effectiveDate", ""),
        "expirationDate": policy.get("expirationDate", ""),
        "premium": float(policy.get("premium", 0) or 0),
        "status": policy.get("status", "Active"),
        "type": policy.get("type", ""),
        "description": f"Synced from NowCerts (ID: {policy.get('database_id', '')})",
    }


def _build_slack_payload(
    *,
    day: date,
    policies_synced: int,
    accounts_synced: int,
    policies_created: int,
    policies_updated: int,
    errors: list[str],
    warnings: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Build Slack Block Kit payload for morning sync."""
    text = (
        f"Morning Policy Sync — {day.isoformat()}: "
        f"{policies_synced} policies ({policies_created} new, {policies_updated} updated), "
        f"{accounts_synced} accounts"
    )

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":sunny: Morning Policy Sync — {day.strftime('%B %d, %Y')}",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{policies_synced} policies* synced from NowCerts\n"
                    f":new: {policies_created} new  |  :pencil2: {policies_updated} updated\n"
                    f":bust_in_silhouette: {accounts_synced} accounts synced"
                ),
            },
        },
    ]

    if errors:
        error_lines = [":x: *Errors:*"]
        for err in errors[:5]:
            error_lines.append(f"  • {err[:100]}")
        if len(errors) > 5:
            error_lines.append(f"  _… +{len(errors) - 5} more_")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(error_lines)},
        })

    if warnings:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f":warning: {len(warnings)} warnings"}],
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"_Generated by Hermes at {datetime.now(timezone.utc).strftime('%H:%M UTC')}_"},
        ],
    })

    return text, blocks


def _log_to_crm(
    espo: EspoClient,
    day: date,
    policies_synced: int,
    accounts_synced: int,
    policies_created: int,
    policies_updated: int,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Create an EspoCRM Task as a sync audit record."""
    description_lines = [
        f"Automated morning policy sync for {day.isoformat()}.",
        f"Policies: {policies_synced} total ({policies_created} new, {policies_updated} updated)",
        f"Accounts: {accounts_synced} synced",
    ]
    
    if errors:
        description_lines.append("\nErrors:")
        for err in errors[:10]:
            description_lines.append(f"  • {err}")
    
    if warnings:
        description_lines.append(f"\nWarnings: {len(warnings)}")

    payload = {
        "name": f"Morning Policy Sync — {day.isoformat()}",
        "status": "Completed" if not errors else "Not Completed",
        "priority": "Normal" if not errors else "High",
        "dateEnd": day.isoformat(),
        "description": "\n".join(description_lines),
    }

    try:
        espo.create("Task", payload)
        log.info("Sync Task created in EspoCRM for %s", day.isoformat())
    except EspoClientError as e:
        log.warning("Failed to create sync Task in EspoCRM: %s", e)


def _now_in_timezone(now: datetime | None) -> datetime:
    tz_name = os.environ.get("HERMES_SENTINEL_TIMEZONE", "America/New_York")
    tz = ZoneInfo(tz_name)
    return (now or datetime.now(timezone.utc)).astimezone(tz)


def _state_path(prefix: str = "") -> Path:
    suffix = f"_{prefix}" if prefix else ""
    return STATE_DIR / f"morning_sync{suffix}_state.json"


def _read_state(prefix: str = "") -> dict[str, Any]:
    path = _state_path(prefix)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(day: date, prefix: str = "") -> None:
    path = _state_path(prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _read_state(prefix)
    state["last_sent_date"] = day.isoformat()
    path.write_text(json.dumps(state))


def _already_sent_today(day: date, prefix: str = "") -> bool:
    state = _read_state(prefix)
    last = state.get("last_sent_date", "")
    return last == day.isoformat()
