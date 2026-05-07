"""Nightly CRM Changelog: structured Slack digest of daily CRM changes.

Queries EspoCRM for records created or modified in the last 24 hours,
posts a Slack Block Kit summary, and logs the report in EspoCRM as a
Task for audit trail.
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

log = logging.getLogger(__name__)

TRACKED_ENTITIES = ("Account", "Contact", "Lead", "Opportunity", "Policy", "Task")

_ENTITY_EMOJI: dict[str, str] = {
    "Account": ":office:",
    "Contact": ":bust_in_silhouette:",
    "Lead": ":mag:",
    "Opportunity": ":moneybag:",
    "Policy": ":page_facing_up:",
    "Task": ":ballot_box_with_check:",
}

_STATE_DIR = Path(os.environ.get("HERMES_STATE_DIR", "/tmp/hermes"))


@dataclass
class EntityChange:
    """One created or updated CRM record."""

    entity_type: str
    record_id: str
    name: str
    action: str  # "created" | "updated"
    modified_by: str
    modified_at: str
    created_at: str


@dataclass
class ChangelogRunResult:
    ok: bool
    posted: bool
    skipped: bool
    message: str
    changes: dict[str, list[EntityChange]] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def run(
    client: EspoClient,
    *,
    notifier: SlackNotifier | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
    lookback_hours: int | None = None,
) -> ChangelogRunResult:
    """Execute the nightly changelog pipeline."""
    local_now = _now_in_timezone(now)
    target_day = local_now.date()
    hours = lookback_hours or int(os.environ.get("HERMES_CHANGELOG_LOOKBACK_HOURS", "24"))
    cutoff = local_now - timedelta(hours=hours)

    if not force and _already_sent_today(target_day):
        return ChangelogRunResult(
            ok=True,
            posted=False,
            skipped=True,
            message=f"Changelog already posted for {target_day.isoformat()}; skipping.",
        )

    changes: dict[str, list[EntityChange]] = {}
    warnings: list[str] = []

    for entity_type in TRACKED_ENTITIES:
        entity_changes, warning = _query_entity_changes(client, entity_type, cutoff)
        if entity_changes:
            changes[entity_type] = entity_changes
        if warning:
            warnings.append(warning)

    totals = {et: len(cl) for et, cl in changes.items()}
    total_all = sum(totals.values())
    created_count = sum(
        1 for cl in changes.values() for c in cl if c.action == "created"
    )
    updated_count = total_all - created_count

    text, blocks = _build_slack_payload(
        day=target_day,
        changes=changes,
        totals=totals,
        created_count=created_count,
        updated_count=updated_count,
        warnings=warnings,
        lookback_hours=hours,
    )

    if dry_run:
        return ChangelogRunResult(
            ok=True,
            posted=False,
            skipped=False,
            message=text,
            changes=changes,
            totals=totals,
            warnings=warnings,
        )

    # Log the report in EspoCRM as a Task for tracking
    _log_to_crm(client, target_day, changes, totals, created_count, updated_count)

    # Post to Slack
    active_notifier = notifier or SlackNotifier()
    try:
        active_notifier.post_message(text=text, blocks=blocks)
    except SlackNotifierError as e:
        return ChangelogRunResult(
            ok=False,
            posted=False,
            skipped=False,
            message=f"Changelog Slack post failed: {e}",
            changes=changes,
            totals=totals,
            warnings=warnings,
        )

    _write_state(target_day)
    return ChangelogRunResult(
        ok=True,
        posted=True,
        skipped=False,
        message=f"Nightly changelog posted for {target_day.isoformat()} — {total_all} changes ({created_count} new, {updated_count} updated)",
        changes=changes,
        totals=totals,
        warnings=warnings,
    )


def run_on_demand(
    client: EspoClient,
    *,
    lookback_hours: int = 24,
) -> ChangelogRunResult:
    """On-demand version for chat commands — no Slack post, no state."""
    local_now = _now_in_timezone(None)
    cutoff = local_now - timedelta(hours=lookback_hours)

    changes: dict[str, list[EntityChange]] = {}
    warnings: list[str] = []

    for entity_type in TRACKED_ENTITIES:
        entity_changes, warning = _query_entity_changes(client, entity_type, cutoff)
        if entity_changes:
            changes[entity_type] = entity_changes
        if warning:
            warnings.append(warning)

    totals = {et: len(cl) for et, cl in changes.items()}
    total_all = sum(totals.values())
    created_count = sum(
        1 for cl in changes.values() for c in cl if c.action == "created"
    )
    updated_count = total_all - created_count

    if total_all == 0:
        return ChangelogRunResult(
            ok=True,
            posted=False,
            skipped=False,
            message=f"No CRM changes in the last {lookback_hours} hours.",
            changes=changes,
            totals=totals,
            warnings=warnings,
        )

    lines = [f"*CRM Changes (last {lookback_hours}h):* {total_all} total — {created_count} new, {updated_count} updated"]
    for entity_type, entity_changes in changes.items():
        emoji = _ENTITY_EMOJI.get(entity_type, ":small_blue_diamond:")
        creates = [c for c in entity_changes if c.action == "created"]
        updates = [c for c in entity_changes if c.action == "updated"]
        lines.append(f"\n{emoji} *{entity_type}* ({len(entity_changes)})")
        if creates:
            lines.append("  _New:_")
            for c in creates[:10]:
                lines.append(f"  • {c.name} (by {c.modified_by})")
        if updates:
            lines.append("  _Updated:_")
            for c in updates[:10]:
                lines.append(f"  • {c.name} (by {c.modified_by})")

    if warnings:
        lines.append("\n:warning: " + "; ".join(warnings))

    return ChangelogRunResult(
        ok=True,
        posted=False,
        skipped=False,
        message="\n".join(lines),
        changes=changes,
        totals=totals,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _query_entity_changes(
    client: EspoClient,
    entity_type: str,
    cutoff: datetime,
) -> tuple[list[EntityChange], str | None]:
    """Query EspoCRM for records modified after cutoff."""
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    try:
        body = client.get(
            entity_type,
            params={
                "maxSize": 200,
                "orderBy": "modifiedAt",
                "order": "desc",
                "select": "id,name,modifiedAt,createdAt,modifiedByName,createdByName",
                "where": [
                    {"type": "after", "attribute": "modifiedAt", "value": cutoff_str},
                ],
            },
        )
    except EspoClientError as e:
        return [], f"{entity_type}: {e}"

    rows = body.get("list", []) if isinstance(body, dict) else []
    result: list[EntityChange] = []

    for row in rows:
        record_id = str(row.get("id") or "")
        name = str(row.get("name") or "Unnamed")
        modified_at = str(row.get("modifiedAt") or "")
        created_at = str(row.get("createdAt") or "")
        modified_by = str(row.get("modifiedByName") or row.get("createdByName") or "System")

        action = _classify_action(created_at, modified_at, cutoff)

        result.append(EntityChange(
            entity_type=entity_type,
            record_id=record_id,
            name=name,
            action=action,
            modified_by=modified_by,
            modified_at=modified_at,
            created_at=created_at,
        ))

    return result, None


def _classify_action(created_at: str, modified_at: str, cutoff: datetime) -> str:
    """Determine if a record was created or updated in the window."""
    try:
        created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if created >= cutoff:
            return "created"
    except (ValueError, TypeError):
        pass
    return "updated"


def _build_slack_payload(
    *,
    day: date,
    changes: dict[str, list[EntityChange]],
    totals: dict[str, int],
    created_count: int,
    updated_count: int,
    warnings: list[str],
    lookback_hours: int,
) -> tuple[str, list[dict[str, Any]]]:
    """Build Slack Block Kit payload for the nightly changelog."""
    total_all = sum(totals.values())
    text = f"Nightly CRM Changelog — {day.isoformat()}: {total_all} changes ({created_count} new, {updated_count} updated)"

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":clipboard: Nightly CRM Changelog — {day.strftime('%B %d, %Y')}",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{total_all} changes* in the last {lookback_hours} hours\n"
                    f":new: {created_count} new records  |  :pencil2: {updated_count} updated records"
                ),
            },
        },
        {"type": "divider"},
    ]

    if total_all == 0:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_No CRM changes recorded in the last {lookback_hours} hours._"},
        })
    else:
        for entity_type, entity_changes in changes.items():
            emoji = _ENTITY_EMOJI.get(entity_type, ":small_blue_diamond:")
            creates = [c for c in entity_changes if c.action == "created"]
            updates = [c for c in entity_changes if c.action == "updated"]

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} *{entity_type}* — {len(entity_changes)} change(s)",
                },
            })

            if creates:
                create_lines = [f":new: *New {entity_type}s:*"]
                for c in creates[:8]:
                    create_lines.append(f"  • *{c.name}* — by {c.modified_by}")
                if len(creates) > 8:
                    create_lines.append(f"  _… +{len(creates) - 8} more_")
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(create_lines)},
                })

            if updates:
                update_lines = [f":pencil2: *Updated {entity_type}s:*"]
                for c in updates[:8]:
                    update_lines.append(f"  • *{c.name}* — by {c.modified_by}")
                if len(updates) > 8:
                    update_lines.append(f"  _… +{len(updates) - 8} more_")
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n".join(update_lines)},
                })

            blocks.append({"type": "divider"})

    if warnings:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f":warning: {'; '.join(warnings)}"}],
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"_Generated by Hermes at {datetime.now(timezone.utc).strftime('%H:%M UTC')}_"},
        ],
    })

    return text, blocks


def _log_to_crm(
    client: EspoClient,
    day: date,
    changes: dict[str, list[EntityChange]],
    totals: dict[str, int],
    created_count: int,
    updated_count: int,
) -> None:
    """Create an EspoCRM Task as a changelog audit record."""
    total_all = sum(totals.values())

    summary_parts = []
    for entity_type, entity_changes in changes.items():
        creates = sum(1 for c in entity_changes if c.action == "created")
        updates = len(entity_changes) - creates
        parts = []
        if creates:
            parts.append(f"{creates} new")
        if updates:
            parts.append(f"{updates} updated")
        if parts:
            summary_parts.append(f"{entity_type}: {', '.join(parts)}")

    description_lines = [
        f"Automated nightly CRM changelog for {day.isoformat()}.",
        f"Total changes: {total_all} ({created_count} new, {updated_count} updated)",
        "",
        "Breakdown:",
    ]
    description_lines.extend(f"  • {s}" for s in summary_parts)

    # Add detail for each entity type
    for entity_type, entity_changes in changes.items():
        description_lines.append(f"\n{entity_type}:")
        for c in entity_changes[:20]:
            description_lines.append(f"  [{c.action}] {c.name} (by {c.modified_by}, {c.modified_at})")
        if len(entity_changes) > 20:
            description_lines.append(f"  … +{len(entity_changes) - 20} more")

    payload = {
        "name": f"Nightly CRM Changelog — {day.isoformat()}",
        "status": "Completed",
        "priority": "Normal",
        "dateEnd": day.isoformat(),
        "description": "\n".join(description_lines),
    }

    try:
        client.create("Task", payload)
        log.info("Changelog Task created in EspoCRM for %s", day.isoformat())
    except EspoClientError as e:
        log.warning("Failed to create changelog Task in EspoCRM: %s", e)


# ---------------------------------------------------------------------------
# Timezone / state helpers
# ---------------------------------------------------------------------------

def _now_in_timezone(now: datetime | None) -> datetime:
    tz_name = os.environ.get("HERMES_SENTINEL_TIMEZONE", "America/New_York")
    tz = ZoneInfo(tz_name)
    return (now or datetime.now(timezone.utc)).astimezone(tz)


def _state_path() -> Path:
    return _STATE_DIR / "changelog_state.json"


def _read_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(day: date) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _read_state()
    state["last_sent_date"] = day.isoformat()
    path.write_text(json.dumps(state))


def _already_sent_today(day: date) -> bool:
    state = _read_state()
    last = state.get("last_sent_date", "")
    return last == day.isoformat()
