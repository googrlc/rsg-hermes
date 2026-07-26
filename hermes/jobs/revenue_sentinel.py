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

from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

# Data source: the custom CRM (Command Center) — read directly from its Supabase
# tables via SupabaseClient, the same in-process path renewal-refresh /
# canonical-book use.
STALE_STATUSES = ("Prospecting", "Quoting", "Gathering Info")
# Owner-facing briefing → #the-boss. team_notify.resolve_room maps this id to
# HERMES_TALK_ROOM_BOSS (Nextcloud Talk). Override via HERMES_SENTINEL_REPORT_CHANNEL.
SENTINEL_REPORT_CHANNEL = "C0ANQUENX4P"
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
    *,
    supa: SupabaseClient | None = None,
    notifier: SlackNotifier | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> SentinelRunResult:
    local_now = _now_in_timezone(now)
    target_day = local_now.date()
    supa = supa or SupabaseClient()
    stale = _query_stale_opportunities(supa=supa, now_local=local_now)
    renewals = _query_renewals(supa=supa, now_local=local_now)
    x_dates = _query_x_dates(supa=supa, now_local=local_now)
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
    active_notifier = notifier or SlackNotifier(
        channel=os.environ.get("HERMES_SENTINEL_REPORT_CHANNEL", SENTINEL_REPORT_CHANNEL)
    )
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


def _query_stale_opportunities(*, supa: SupabaseClient, now_local: datetime) -> SentinelQueryResult:
    """Open opportunities in the custom CRM not touched in HERMES_SENTINEL_STALE_DAYS."""
    stale_days = int(os.environ.get("HERMES_SENTINEL_STALE_DAYS", "14"))
    cutoff = now_local.date() - timedelta(days=stale_days)
    try:
        rows = supa.select(
            "opportunities",
            params={"status": "eq.open", "order": "updated_at.asc"},
            limit=200,
        )
    except SupabaseClientError as e:
        return SentinelQueryResult(label="STALE LEADS", rows=[], error=str(e))
    filtered: list[dict[str, Any]] = []
    for row in rows:
        touched = _parse_iso_date(str(_pick(row, "updated_at", "synced_at") or ""))
        # No timestamp -> treat as stale (open but never worked); else stale if older than cutoff.
        if touched and touched >= cutoff:
            continue
        filtered.append(
            {
                "entity": "Opportunity",
                "record_id": str(row.get("id") or ""),
                "name": str(_pick(row, "insured_name") or "Unknown"),
                "lob": _pick(row, "line_of_business") or "Unknown",
                "date_label": _format_datetime(_pick(row, "updated_at", "synced_at")),
                "date_prefix": "Last touched",
                "category": "STALE LEADS",
                "premium": _as_money(_pick(row, "premium_estimate", "premium_actual")),
            }
        )
    return SentinelQueryResult(label="STALE LEADS", rows=filtered)


def _query_renewals(*, supa: SupabaseClient, now_local: datetime) -> SentinelQueryResult:
    """Active, eligible renewal candidates expiring within the checkpoint window.

    Each upcoming renewal is bucketed into the tightest checkpoint tier it falls in
    (≤30d / 31-60d / 61-90d), so every renewal inside the window surfaces, ranked by
    urgency — not only ones landing exactly on a checkpoint day.
    """
    checkpoints = _renewal_checkpoints()
    max_checkpoint = max(checkpoints)
    try:
        rows = supa.select(
            "renewal_candidates",
            params={
                "policy_active": "eq.true",
                "eligibility_state": "neq.excluded",
                "order": "renewal_event_date.asc",
            },
            limit=1000,
        )
    except SupabaseClientError as e:
        return SentinelQueryResult(label="PROJECT 85 RENEWALS", rows=[], error=str(e))
    mapped: list[dict[str, Any]] = []
    for row in rows:
        renewal = _parse_iso_date(str(_pick(row, "renewal_event_date", "expiration_date") or ""))
        if not renewal:
            continue
        days_left = (renewal - now_local.date()).days
        if days_left < 0 or days_left > max_checkpoint:
            continue
        tier = _renewal_tier(days_left, checkpoints)
        # `in_working_queue` on the custom CRM replaces the dead Espo Account.renewalOutreachStage.
        in_pipeline = bool(row.get("in_working_queue"))
        risk = str(_pick(row, "risk_status") or "").strip()
        pipeline_stage = "In working queue" if in_pipeline else (risk or "Not in pipeline")
        mapped.append(
            {
                "entity": "Policy",
                "record_id": str(_pick(row, "policy_number", "id") or ""),
                "name": str(_pick(row, "client_name") or "Unknown"),
                "lob": _pick(row, "line_of_business") or "Unknown",
                # Same date field that days_left is computed from, so "Exp: <date> [Nd]" is consistent.
                "date_label": _format_date(_pick(row, "renewal_event_date", "expiration_date")),
                "date_prefix": "Renews",
                "category": "PROJECT 85 RENEWALS",
                "premium": _as_money(_pick(row, "premium_current", "premium_renewal")),
                "action": _renewal_action_for_checkpoint(tier, in_pipeline=in_pipeline),
                "checkpoint_days": tier,
                "days_left": days_left,
                "pipeline_stage": pipeline_stage,
                "in_pipeline": in_pipeline,
            }
        )
    mapped.sort(key=lambda row: (int(row.get("days_left") or 0), not bool(row.get("in_pipeline"))))
    return SentinelQueryResult(label="PROJECT 85 RENEWALS", rows=mapped)


def _query_x_dates(*, supa: SupabaseClient, now_local: datetime) -> SentinelQueryResult:
    """Re-quote pipeline: opportunities the agency lost — chase them back.

    Lost opportunities in the custom CRM are the re-quote signal.
    """
    try:
        rows = supa.select(
            "opportunities",
            params={"status": "eq.lost", "order": "updated_at.desc"},
            limit=200,
        )
    except SupabaseClientError as e:
        return SentinelQueryResult(label="X-DATE OPPORTUNITIES", rows=[], error=str(e))
    mapped: list[dict[str, Any]] = []
    for row in rows:
        xdate = _pick(row, "expiration_date", "closed_date", "updated_at")
        mapped.append(
            {
                "entity": "Opportunity",
                "record_id": str(row.get("id") or ""),
                "name": str(_pick(row, "insured_name") or "Unknown"),
                "lob": _pick(row, "line_of_business") or "Unknown",
                "date_label": _format_date(xdate),
                "date_prefix": "X-Date",
                "category": "X-DATE OPPORTUNITIES",
                "premium": _as_money(_pick(row, "premium_estimate", "premium_actual")),
                "action": "Re-quote",
                "carrier": _pick(row, "carrier"),
                "stage": _pick(row, "stage", "status"),
            }
        )
    return SentinelQueryResult(label="X-DATE OPPORTUNITIES", rows=mapped)


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
        f"🔄 PROJECT 85: RENEWALS (next 90 days by urgency) — {len(renewals)}",
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
    if len(rows) > 8:
        output.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"... +{len(rows) - 8} more"}]})
    return output


def _format_section_lines(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["• None found."]
    return [f"• {_format_line(row)}" for row in rows[:8]] + ([f"• ... +{len(rows) - 8} more"] if len(rows) > 8 else [])


def _format_renewal_checkpoint_lines(buckets: dict[int, list[dict[str, Any]]]) -> list[str]:
    lines: list[str] = []
    for checkpoint in sorted(_renewal_checkpoints()):
        rows = buckets.get(checkpoint, [])
        lines.append(f"{_checkpoint_label(checkpoint)} — {len(rows)}")
        lines.extend(_format_section_lines(rows))
    return lines


def _renewal_tier(days_left: int, checkpoints: list[int]) -> int:
    """Smallest checkpoint tier this renewal falls within (e.g. 11 days -> 30)."""
    for checkpoint in sorted(checkpoints):
        if days_left <= checkpoint:
            return checkpoint
    return max(checkpoints)


def _checkpoint_label(checkpoint: int, checkpoints: list[int] | None = None) -> str:
    """Human range label for a checkpoint tier: '≤30d', '31-60d', '61-90d'."""
    ordered = sorted(checkpoints or _renewal_checkpoints())
    idx = ordered.index(checkpoint) if checkpoint in ordered else 0
    if idx == 0:
        return f"≤{checkpoint}d"
    return f"{ordered[idx - 1] + 1}-{checkpoint}d"


def _format_line(row: dict[str, Any]) -> str:
    whale = f"{WHALE_MARKER} " if row.get("_is_whale") else ""
    name = str(row.get("name") or "Unknown")
    lob = str(row.get("lob") or "Unknown")
    date_prefix = str(row.get("date_prefix") or "Date")
    date_label = str(row.get("date_label") or "?")
    action = str(row.get("action") or "").strip()
    carrier = str(row.get("carrier") or "").strip()
    # Prefer the true days-to-expiration for display; fall back to the tier value.
    checkpoint = row.get("days_left", row.get("checkpoint_days"))
    checkpoint_note = f" [{checkpoint}d]" if checkpoint is not None else ""
    pipeline_stage = str(row.get("pipeline_stage") or "").strip()
    pipeline_note = f" Pipeline: {pipeline_stage}." if pipeline_stage else ""
    carrier_note = f" - Lost to {carrier} last year." if carrier else ""
    action_note = f" **Action: {action}.**" if action else ""
    return f"{whale}*{name}* ({lob}) - {date_prefix}: {date_label}{checkpoint_note}.{pipeline_note}{carrier_note}{action_note}"


def _renewal_section_blocks(buckets: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output = [{"type": "section", "text": {"type": "mrkdwn", "text": "*🔄 PROJECT 85: RENEWALS (next 90 days by urgency)*"}}]
    for checkpoint in sorted(_renewal_checkpoints()):
        rows = buckets.get(checkpoint, [])
        output.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{_checkpoint_label(checkpoint)}* — {len(rows)}"},
            }
        )
        if not rows:
            output.append({"type": "section", "text": {"type": "mrkdwn", "text": "• None found."}})
            continue
        for row in rows[:8]:
            item_line = _format_line(row)
            output.append({"type": "section", "text": {"type": "mrkdwn", "text": item_line}})
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


def _parse_datetime(value: Any, now_local: datetime) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text[:19] if pattern == "%Y-%m-%d %H:%M:%S" else text[:10], pattern)
            return parsed.replace(tzinfo=now_local.tzinfo)
        except ValueError:
            continue
    return None


def _expected_latest_business_day(today_local: date) -> date:
    weekday = today_local.weekday()
    if weekday <= 4:
        return today_local
    if weekday == 5:
        return today_local - timedelta(days=1)
    return today_local - timedelta(days=2)


def _missing_required_env() -> list[str]:
    """Env the briefing genuinely cannot run without.

    Was ["SLACK_BOT_TOKEN"]. Slack is retired and the sentinel posts through the
    TeamNotifier to Nextcloud Talk, so requiring a Slack credential meant the
    briefing would refuse to run the day that stale token was removed. The Talk
    room is what it actually needs; TeamNotifier falls back to the boss room, so
    HERMES_TALK_ROOM_BOSS is the one that must exist.
    """
    required = ["HERMES_TALK_ROOM_BOSS"]
    return [key for key in required if not os.environ.get(key, "").strip()]


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

