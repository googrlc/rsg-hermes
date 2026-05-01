"""Project 85 Sentinel: daily revenue guardrail briefing."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError

STALE_STATUSES = ("Prospecting", "Quoting", "Gathering Info")
WHALE_MARKER = "🐳"
DEFAULT_RENEWAL_CHECKPOINTS = (90, 60, 30)


@dataclass
class SentinelQueryResult:
    label: str
    rows: list[dict[str, Any]]
    error: str | None = None


@dataclass
class SentinelRunResult:
    ok: bool
    posted: bool
    skipped: bool
    message: str
    sections: dict[str, list[dict[str, Any]]]
    warnings: list[str]


@dataclass
class SentinelHealthStatus:
    ok: bool
    summary: str
    details: dict[str, Any]


def run(
    client: EspoClient,
    *,
    notifier: SlackNotifier | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> SentinelRunResult:
    local_now = _now_in_timezone(now)
    target_day = local_now.date()
    stale = _query_stale_opportunities(client=client, now_local=local_now)
    renewals = _query_renewals(client=client, now_local=local_now)
    x_dates = _query_x_dates(client=client, now_local=local_now)
    results = [stale, renewals, x_dates]
    warnings = [f"{r.label}: {r.error}" for r in results if r.error]
    sections = {
        "stale_leads": _prioritize_whales(stale.rows),
        "upcoming_renewals": _prioritize_whales(renewals.rows),
        "xdate_opportunities": _prioritize_whales(x_dates.rows),
    }
    text, blocks = _build_slack_payload(day=target_day, sections=sections, warnings=warnings)
    if dry_run:
        return SentinelRunResult(
            ok=True,
            posted=False,
            skipped=False,
            message=text,
            sections=sections,
            warnings=warnings,
        )
    if not force and _already_sent_today(target_day):
        return SentinelRunResult(
            ok=True,
            posted=False,
            skipped=True,
            message=f"Sentinel already posted for {target_day.isoformat()}; skipping duplicate post.",
            sections=sections,
            warnings=warnings,
        )
    active_notifier = notifier or SlackNotifier()
    try:
        active_notifier.post_message(text=text, blocks=blocks)
    except SlackNotifierError as e:
        return SentinelRunResult(
            ok=False,
            posted=False,
            skipped=False,
            message=f"Revenue sentinel Slack post failed: {e}",
            sections=sections,
            warnings=warnings,
        )
    _write_state(target_day)
    return SentinelRunResult(
        ok=True,
        posted=True,
        skipped=False,
        message=f"Revenue sentinel posted for {target_day.isoformat()}",
        sections=sections,
        warnings=warnings,
    )


def health_status(now: datetime | None = None) -> SentinelHealthStatus:
    local_now = _now_in_timezone(now)
    state = _read_state()
    last_sent_raw = str(state.get("last_sent_date") or "").strip()
    last_sent = _parse_iso_date(last_sent_raw)
    expected_date = _expected_latest_business_day(local_now.date())
    missing_config = _missing_required_env()
    stale_days = None
    if last_sent and expected_date:
        stale_days = (expected_date - last_sent).days
    is_fresh = bool(last_sent and expected_date and last_sent >= expected_date)
    details = {
        "timezone": os.environ.get("HERMES_SENTINEL_TIMEZONE", "America/New_York"),
        "now_local": local_now.isoformat(timespec="seconds"),
        "expected_latest_business_day": expected_date.isoformat() if expected_date else None,
        "last_sent_date": last_sent.isoformat() if last_sent else None,
        "is_fresh": is_fresh,
        "stale_business_days": stale_days,
        "missing_required_env": missing_config,
        "state_file": str(_state_path()),
    }
    if missing_config:
        return SentinelHealthStatus(
            ok=False,
            summary="Revenue sentinel health is NOT ready: required environment variables are missing.",
            details=details,
        )
    if not last_sent:
        return SentinelHealthStatus(
            ok=False,
            summary="Revenue sentinel has not posted yet (no last_sent_date found).",
            details=details,
        )
    if not is_fresh:
        return SentinelHealthStatus(
            ok=False,
            summary=(
                "Revenue sentinel is stale: last successful post is older than the latest expected business day."
            ),
            details=details,
        )
    return SentinelHealthStatus(
        ok=True,
        summary="Revenue sentinel is healthy and up to date.",
        details=details,
    )


def build_action_value(*, entity: str, record_id: str, name: str, category: str) -> str:
    payload = {
        "entity": entity,
        "record_id": record_id,
        "name": name,
        "category": category,
    }
    return json.dumps(payload, separators=(",", ":"))


def parse_action_value(raw_value: str) -> dict[str, str]:
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError as e:
        raise ValueError("Invalid sentinel action payload.") from e
    if not isinstance(data, dict):
        raise ValueError("Invalid sentinel action payload shape.")
    entity = str(data.get("entity") or "").strip()
    record_id = str(data.get("record_id") or "").strip()
    name = str(data.get("name") or "").strip()
    category = str(data.get("category") or "").strip()
    if not entity or not record_id:
        raise ValueError("Sentinel action payload missing required fields.")
    return {"entity": entity, "record_id": record_id, "name": name, "category": category}


def handle_slack_action(
    *,
    client: EspoClient,
    action: str,
    action_value: str,
) -> str:
    item = parse_action_value(action_value)
    now_local = _now_in_timezone(None)
    if action == "sentinel_remind":
        due = (now_local.date() + timedelta(days=2)).isoformat()
        payload = {
            "name": f"Follow up: {item['name'] or item['record_id']} ({item['category']})",
            "status": "Not Started",
            "dateEnd": due,
            "description": f"Hermes sentinel reminder for {item['entity']}:{item['record_id']}",
        }
        client.create("Task", payload)
        return f"Reminder created for {due}."
    if action == "sentinel_assign_gretchen":
        gretchen_user_id = os.environ.get("HERMES_SENTINEL_GRETCHEN_USER_ID", "").strip()
        if not gretchen_user_id:
            return "Set HERMES_SENTINEL_GRETCHEN_USER_ID to assign this action."
        payload = {
            "name": f"Project 85 follow-up: {item['name'] or item['record_id']}",
            "status": "Not Started",
            "assignedUserId": gretchen_user_id,
            "description": (
                f"Assigned from Hermes sentinel ({item['category']}) for "
                f"{item['entity']}:{item['record_id']}"
            ),
        }
        client.create("Task", payload)
        return "Assigned to Gretchen."
    if action == "sentinel_dismiss":
        return "Dismissed."
    return "Unknown sentinel action."


def _query_stale_opportunities(*, client: EspoClient, now_local: datetime) -> SentinelQueryResult:
    cutoff = now_local - timedelta(days=int(os.environ.get("HERMES_SENTINEL_STALE_DAYS", "14")))
    cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    select = (
        "id,name,accountName,lineOfBusiness,line_of_business,amount,premium_amount,"
        "stage,status,modifiedAt"
    )
    try:
        body = client.get(
            "Opportunity",
            params={
                "maxSize": 200,
                "select": select,
                "orderBy": [["modifiedAt", "asc"]],
                "where": [{"type": "lessThan", "attribute": "modifiedAt", "value": cutoff_text}],
            },
        )
    except EspoClientError as e:
        return SentinelQueryResult(label="STALE LEADS", rows=[], error=str(e))
    rows = _list_rows(body)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        pipeline_state = _pick(row, "status", "stage")
        if pipeline_state not in STALE_STATUSES:
            continue
        filtered.append(
            {
                "entity": "Opportunity",
                "record_id": str(row.get("id") or ""),
                "name": str(row.get("name") or "Unknown"),
                "lob": _pick(row, "lineOfBusiness", "line_of_business") or "Unknown",
                "date_label": _format_datetime(_pick(row, "modifiedAt")),
                "date_prefix": "Last touched",
                "category": "STALE LEADS",
                "premium": _as_money(_pick(row, "premium_amount", "amount")),
            }
        )
    return SentinelQueryResult(label="STALE LEADS", rows=filtered)


def _query_renewals(*, client: EspoClient, now_local: datetime) -> SentinelQueryResult:
    checkpoints = _renewal_checkpoints()
    select = (
        "id,name,accountId,accountName,line_of_business,lineOfBusiness,premium_amount,amount,"
        "expiration_date,expirationDate,status"
    )
    try:
        body = client.get(
            "Policy",
            params={
                "maxSize": 200,
                "select": select,
                "orderBy": [["expirationDate", "asc"]],
                "where": [
                    {"type": "equals", "attribute": "status", "value": "Active"},
                ],
            },
        )
    except EspoClientError as e:
        return SentinelQueryResult(label="PROJECT 85 RENEWALS", rows=[], error=str(e))
    rows = _list_rows(body)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        expiration = _parse_iso_date(str(_pick(row, "expirationDate", "expiration_date") or ""))
        if not expiration:
            continue
        days_left = (expiration - now_local.date()).days
        if days_left not in checkpoints:
            continue
        filtered.append(
            {
                **row,
                "_expiration": expiration.isoformat(),
                "_days_left": days_left,
            }
        )
    account_stage = _account_renewal_stages(client=client, account_ids=[str(r.get("accountId") or "") for r in filtered])
    mapped = []
    for row in filtered:
        account_id = str(row.get("accountId") or "")
        pipeline_stage = account_stage.get(account_id, "")
        in_pipeline = _is_in_pipeline(pipeline_stage)
        checkpoint_days = int(row.get("_days_left") or 0)
        mapped.append(
            {
            "entity": "Policy",
            "record_id": str(row.get("id") or ""),
            "name": str(row.get("accountName") or row.get("name") or "Unknown"),
            "lob": _pick(row, "lineOfBusiness", "line_of_business") or "Unknown",
            "date_label": _format_date(_pick(row, "expirationDate", "expiration_date")),
            "date_prefix": "Exp",
            "category": "PROJECT 85 RENEWALS",
            "premium": _as_money(_pick(row, "premium_amount", "amount")),
            "action": _renewal_action_for_checkpoint(checkpoint_days, in_pipeline=in_pipeline),
            "checkpoint_days": checkpoint_days,
            "pipeline_stage": pipeline_stage or "Not in pipeline",
            "in_pipeline": in_pipeline,
        }
        )
    mapped.sort(key=lambda row: (-int(row.get("checkpoint_days") or 0), not bool(row.get("in_pipeline"))))
    return SentinelQueryResult(label="PROJECT 85 RENEWALS", rows=mapped)


def _query_x_dates(*, client: EspoClient, now_local: datetime) -> SentinelQueryResult:
    x_days = int(os.environ.get("HERMES_SENTINEL_XDATE_DAYS", "60"))
    target = (now_local.date() + timedelta(days=x_days)).isoformat()
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    lead_rows, lead_err = _query_x_date_entity(client=client, entity="Lead", target_date=target)
    rows.extend(lead_rows)
    if lead_err:
        errors.append(f"Lead {lead_err}")
    opportunity_rows, opp_err = _query_x_date_entity(client=client, entity="Opportunity", target_date=target)
    for row in opportunity_rows:
        stage = str(_pick(row, "stage", "status")).lower()
        if "closed" in stage and "lost" in stage:
            rows.append(row)
    if opp_err:
        errors.append(f"Opportunity {opp_err}")
    return SentinelQueryResult(
        label="X-DATE OPPORTUNITIES",
        rows=rows,
        error="; ".join(errors) if errors else None,
    )


def _query_x_date_entity(
    *,
    client: EspoClient,
    entity: str,
    target_date: str,
) -> tuple[list[dict[str, Any]], str | None]:
    select = (
        "id,name,lineOfBusiness,line_of_business,xDate,x_date,modifiedAt,lostToCarrier,carrier,amount,premium_amount,stage,status"
    )
    where = [
        {
            "type": "or",
            "value": [
                {"type": "equals", "attribute": "xDate", "value": target_date},
                {"type": "equals", "attribute": "x_date", "value": target_date},
            ],
        }
    ]
    try:
        body = client.get(entity, params={"maxSize": 200, "select": select, "where": where})
    except EspoClientError as e:
        return [], str(e)
    mapped = [
        {
            "entity": entity,
            "record_id": str(row.get("id") or ""),
            "name": str(row.get("name") or "Unknown"),
            "lob": _pick(row, "lineOfBusiness", "line_of_business") or "Unknown",
            "date_label": _format_date(_pick(row, "xDate", "x_date")),
            "date_prefix": "X-Date",
            "category": "X-DATE OPPORTUNITIES",
            "premium": _as_money(_pick(row, "premium_amount", "amount")),
            "action": "Re-quote",
            "carrier": _pick(row, "lostToCarrier", "carrier"),
            "stage": _pick(row, "stage", "status"),
        }
        for row in _list_rows(body)
    ]
    return mapped, None


def _build_slack_payload(
    *,
    day: date,
    sections: dict[str, list[dict[str, Any]]],
    warnings: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    stale = sections["stale_leads"]
    renewals = sections["upcoming_renewals"]
    x_dates = sections["xdate_opportunities"]
    renewal_buckets = _bucket_renewals_by_checkpoint(renewals)
    text_lines = [
        "GOOD MORNING, CAPTAIN. 🫡",
        f"Here is your Revenue Guardrail for {day.isoformat()}:",
        "",
        f"⚠️ STALE LEADS (14+ Days No Contact) — {len(stale)}",
        *_format_section_lines(stale),
        "",
        f"🔄 PROJECT 85: RENEWALS (90/60/30 Day Checkpoints) — {len(renewals)}",
        *_format_renewal_checkpoint_lines(renewal_buckets),
        "",
        f"📅 X-DATE PIPELINE (60 Days Out) — {len(x_dates)}",
        *_format_section_lines(x_dates),
    ]
    if warnings:
        text_lines.extend(["", "Warnings:", *[f"- {w}" for w in warnings]])
    text_lines.extend(["", "Which of these should I create a task for?"])
    text = "\n".join(text_lines)

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*GOOD MORNING, CAPTAIN.* 🫡\nHere is your Revenue Guardrail for *{day.isoformat()}*.",
            },
        }
    ]
    blocks.extend(_section_block("⚠️ STALE LEADS (14+ Days No Contact)", stale))
    blocks.extend(_renewal_section_blocks(renewal_buckets))
    blocks.extend(_section_block("📅 X-DATE PIPELINE (60 Days Out)", x_dates))
    if warnings:
        warning_text = "\n".join(f"• {w}" for w in warnings)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f":warning: *Warnings*\n{warning_text}"}})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "_Which of these should I create a task for?_"}})
    return text, blocks


def _section_block(title: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}}]
    if not rows:
        output.append({"type": "section", "text": {"type": "mrkdwn", "text": "• None found."}})
        return output
    for row in rows[:8]:
        item_line = _format_line(row)
        output.append({"type": "section", "text": {"type": "mrkdwn", "text": item_line}})
        action_value = build_action_value(
            entity=row.get("entity", ""),
            record_id=row.get("record_id", ""),
            name=row.get("name", ""),
            category=row.get("category", ""),
        )
        output.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Remind me in 2 days"},
                        "action_id": "sentinel_remind",
                        "value": action_value,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Assign to Gretchen"},
                        "action_id": "sentinel_assign_gretchen",
                        "value": action_value,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Dismiss"},
                        "action_id": "sentinel_dismiss",
                        "value": action_value,
                    },
                ],
            }
        )
    if len(rows) > 8:
        output.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"... +{len(rows) - 8} more"}]})
    return output


def _format_section_lines(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["• None found."]
    return [f"• {_format_line(row)}" for row in rows[:8]] + ([f"• ... +{len(rows) - 8} more"] if len(rows) > 8 else [])


def _format_renewal_checkpoint_lines(buckets: dict[int, list[dict[str, Any]]]) -> list[str]:
    lines: list[str] = []
    for checkpoint in _renewal_checkpoints():
        rows = buckets.get(checkpoint, [])
        lines.append(f"{checkpoint}d checkpoint — {len(rows)}")
        lines.extend(_format_section_lines(rows))
    return lines


def _format_line(row: dict[str, Any]) -> str:
    whale = f"{WHALE_MARKER} " if row.get("_is_whale") else ""
    name = str(row.get("name") or "Unknown")
    lob = str(row.get("lob") or "Unknown")
    date_prefix = str(row.get("date_prefix") or "Date")
    date_label = str(row.get("date_label") or "?")
    action = str(row.get("action") or "").strip()
    carrier = str(row.get("carrier") or "").strip()
    checkpoint = row.get("checkpoint_days")
    checkpoint_note = f" [{checkpoint}d]" if checkpoint is not None else ""
    pipeline_stage = str(row.get("pipeline_stage") or "").strip()
    pipeline_note = f" Pipeline: {pipeline_stage}." if pipeline_stage else ""
    carrier_note = f" - Lost to {carrier} last year." if carrier else ""
    action_note = f" **Action: {action}.**" if action else ""
    return f"{whale}*{name}* ({lob}) - {date_prefix}: {date_label}{checkpoint_note}.{pipeline_note}{carrier_note}{action_note}"


def _renewal_section_blocks(buckets: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output = [{"type": "section", "text": {"type": "mrkdwn", "text": "*🔄 PROJECT 85: RENEWALS (90/60/30 Day Checkpoints)*"}}]
    for checkpoint in _renewal_checkpoints():
        rows = buckets.get(checkpoint, [])
        output.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{checkpoint}d checkpoint* — {len(rows)}"},
            }
        )
        if not rows:
            output.append({"type": "section", "text": {"type": "mrkdwn", "text": "• None found."}})
            continue
        for row in rows[:8]:
            item_line = _format_line(row)
            output.append({"type": "section", "text": {"type": "mrkdwn", "text": item_line}})
            action_value = build_action_value(
                entity=row.get("entity", ""),
                record_id=row.get("record_id", ""),
                name=row.get("name", ""),
                category=row.get("category", ""),
            )
            output.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Remind me in 2 days"},
                            "action_id": "sentinel_remind",
                            "value": action_value,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Assign to Gretchen"},
                            "action_id": "sentinel_assign_gretchen",
                            "value": action_value,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Dismiss"},
                            "action_id": "sentinel_dismiss",
                            "value": action_value,
                        },
                    ],
                }
            )
        if len(rows) > 8:
            output.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"... +{len(rows) - 8} more"}]})
    return output


def _prioritize_whales(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    threshold = _as_money(os.environ.get("HERMES_SENTINEL_WHALE_PREMIUM", "20000"))
    prepared: list[dict[str, Any]] = []
    for row in rows:
        premium = _as_money(row.get("premium"))
        prepared.append({**row, "_is_whale": premium >= threshold, "_premium": premium})
    return sorted(prepared, key=lambda r: (not bool(r.get("_is_whale")), -_as_money(r.get("_premium"))))


def _bucket_renewals_by_checkpoint(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    buckets: dict[int, list[dict[str, Any]]] = {checkpoint: [] for checkpoint in _renewal_checkpoints()}
    for row in rows:
        checkpoint = int(row.get("checkpoint_days") or 0)
        if checkpoint not in buckets:
            continue
        buckets[checkpoint].append(row)
    return buckets


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _list_rows(body: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [x for x in body["list"] if isinstance(x, dict)]
    return []


def _as_money(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _format_datetime(value: Any) -> str:
    if not value:
        return "Unknown"
    raw = str(value).replace("T", " ")
    if len(raw) >= 10:
        return raw[:10]
    return raw


def _format_date(value: Any) -> str:
    if not value:
        return "Unknown"
    raw = str(value)
    return raw[:10] if len(raw) >= 10 else raw


def _now_in_timezone(now: datetime | None) -> datetime:
    timezone_name = os.environ.get("HERMES_SENTINEL_TIMEZONE", "America/New_York")
    tz = ZoneInfo(timezone_name)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc).astimezone(tz)
    return now.astimezone(tz)


def _already_sent_today(day: date) -> bool:
    path = _state_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    return str(data.get("last_sent_date")) == day.isoformat()


def _write_state(day: date) -> None:
    path = _state_path()
    payload = {"last_sent_date": day.isoformat()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except OSError:
        return


def _state_path() -> Path:
    raw = os.environ.get("HERMES_SENTINEL_STATE_FILE", ".hermes/sentinel_state.json").strip()
    return Path(raw).expanduser()


def _read_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_iso_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _expected_latest_business_day(today_local: date) -> date:
    weekday = today_local.weekday()
    if weekday <= 4:
        return today_local
    if weekday == 5:
        return today_local - timedelta(days=1)
    return today_local - timedelta(days=2)


def _missing_required_env() -> list[str]:
    required = ["SLACK_BOT_TOKEN"]
    missing: list[str] = []
    for key in required:
        if not os.environ.get(key, "").strip():
            missing.append(key)
    return missing


def _renewal_checkpoints() -> list[int]:
    raw = os.environ.get("HERMES_SENTINEL_RENEWAL_CHECKPOINTS", "")
    values: list[int] = []
    if raw.strip():
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                value = int(token)
            except ValueError:
                continue
            if value > 0:
                values.append(value)
    if not values:
        values = list(DEFAULT_RENEWAL_CHECKPOINTS)
    return sorted(set(values), reverse=True)


def _account_renewal_stages(*, client: EspoClient, account_ids: list[str]) -> dict[str, str]:
    unique_ids = sorted({account_id for account_id in account_ids if account_id})
    if not unique_ids:
        return {}
    where = [
        {
            "type": "or",
            "value": [{"type": "equals", "attribute": "id", "value": account_id} for account_id in unique_ids],
        }
    ]
    try:
        body = client.get(
            "Account",
            params={
                "maxSize": min(len(unique_ids), 200),
                "select": "id,renewalOutreachStage,renewalDate,nextRenewalDate",
                "where": where,
            },
        )
    except EspoClientError:
        return {}
    rows = _list_rows(body)
    result: dict[str, str] = {}
    for row in rows:
        account_id = str(row.get("id") or "")
        if not account_id:
            continue
        result[account_id] = str(row.get("renewalOutreachStage") or "").strip()
    return result


def _is_in_pipeline(stage: str) -> bool:
    normalized = stage.strip().lower()
    if not normalized:
        return False
    return normalized not in {"none", "not started", "n/a", "na"}


def _renewal_action_for_checkpoint(checkpoint_days: int, *, in_pipeline: bool) -> str:
    if not in_pipeline:
        return "Add to Renewal Pipeline"
    if checkpoint_days >= 90:
        return "Start Review"
    if checkpoint_days >= 60:
        return "Carrier Marketing"
    return "Finalize Terms"

