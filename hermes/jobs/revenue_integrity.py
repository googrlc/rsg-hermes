"""Revenue Integrity jobs (commission audit)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from hermes.core.client import EspoClient, EspoClientError
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError

AUDIT_STATUSES = {"bound", "active"}


@dataclass
class CommissionAuditResult:
    ok: bool
    posted: bool
    skipped: bool
    message: str
    rows: list[dict[str, Any]]
    warnings: list[str]


@dataclass
class EomScorecardResult:
    ok: bool
    posted: bool
    skipped: bool
    message: str
    summary: dict[str, Any]
    warnings: list[str]


def run_commission_audit(
    client: EspoClient,
    *,
    notifier: SlackNotifier | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> CommissionAuditResult:
    _ = now
    rows, warnings = _query_commission_blind_spots(client)
    text, blocks = _build_slack_payload(rows)
    if dry_run:
        return CommissionAuditResult(
            ok=True,
            posted=False,
            skipped=False,
            message=text,
            rows=rows,
            warnings=warnings,
        )
    today = date.today()
    if not force and _already_sent_today(today):
        return CommissionAuditResult(
            ok=True,
            posted=False,
            skipped=True,
            message=f"Commission audit already posted for {today.isoformat()}; skipping duplicate post.",
            rows=rows,
            warnings=warnings,
        )
    active_notifier = notifier or SlackNotifier(channel=os.environ.get("HERMES_COMMISSION_AUDIT_CHANNEL", "").strip() or None)
    try:
        active_notifier.post_message(text=text, blocks=blocks)
    except SlackNotifierError as e:
        return CommissionAuditResult(
            ok=False,
            posted=False,
            skipped=False,
            message=f"Commission audit Slack post failed: {e}",
            rows=rows,
            warnings=warnings,
        )
    _write_state(today)
    return CommissionAuditResult(
        ok=True,
        posted=True,
        skipped=False,
        message=f"Commission audit posted for {today.isoformat()}",
        rows=rows,
        warnings=warnings,
    )


def run_eom_scorecard(
    client: EspoClient,
    *,
    notifier: SlackNotifier | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> EomScorecardResult:
    ref = now or datetime.now()
    target_month_start, target_month_end = _previous_month_window(ref.date())
    summary, warnings = _build_eom_summary(
        client=client,
        month_start=target_month_start,
        month_end=target_month_end,
    )
    month_key = target_month_start.strftime("%Y-%m")
    text, blocks = _build_eom_slack_payload(summary)
    if dry_run:
        return EomScorecardResult(
            ok=True,
            posted=False,
            skipped=False,
            message=text,
            summary=summary,
            warnings=warnings,
        )
    if not force and _eom_already_sent(month_key):
        return EomScorecardResult(
            ok=True,
            posted=False,
            skipped=True,
            message=f"EOM scorecard already posted for {month_key}; skipping duplicate post.",
            summary=summary,
            warnings=warnings,
        )
    active_notifier = notifier or SlackNotifier(channel=os.environ.get("HERMES_EOM_SCORECARD_CHANNEL", "").strip() or None)
    try:
        active_notifier.post_message(text=text, blocks=blocks)
    except SlackNotifierError as e:
        return EomScorecardResult(
            ok=False,
            posted=False,
            skipped=False,
            message=f"EOM scorecard Slack post failed: {e}",
            summary=summary,
            warnings=warnings,
        )
    _write_eom_state(month_key)
    return EomScorecardResult(
        ok=True,
        posted=True,
        skipped=False,
        message=f"EOM scorecard posted for {month_key}",
        summary=summary,
        warnings=warnings,
    )


def build_commission_action_value(*, policy_id: str, policy_name: str) -> str:
    payload = {"policy_id": policy_id, "policy_name": policy_name}
    return json.dumps(payload, separators=(",", ":"))


def parse_commission_action_value(raw_value: str) -> dict[str, str]:
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError as e:
        raise ValueError("Invalid commission action payload.") from e
    if not isinstance(data, dict):
        raise ValueError("Invalid commission action payload shape.")
    policy_id = str(data.get("policy_id") or "").strip()
    policy_name = str(data.get("policy_name") or "").strip()
    if not policy_id:
        raise ValueError("Commission action payload missing policy_id.")
    return {"policy_id": policy_id, "policy_name": policy_name}


def handle_commission_action(*, client: EspoClient, action: str, action_value: str) -> str:
    if action != "commission_update_pct":
        return "Unknown commission action."
    item = parse_commission_action_value(action_value)
    assigned_user_id = (
        os.environ.get("HERMES_COMMISSION_TASK_ASSIGNEE_ID", "").strip()
        or os.environ.get("HERMES_SENTINEL_GRETCHEN_USER_ID", "").strip()
    )
    payload: dict[str, Any] = {
        "name": f"Update Commission %: {item['policy_name'] or item['policy_id']}",
        "status": "Not Started",
        "description": f"Revenue Integrity audit flagged missing commission for Policy {item['policy_id']}.",
    }
    if assigned_user_id:
        payload["assignedUserId"] = assigned_user_id
    client.create("Task", payload)
    return "Commission update task created."


def _query_commission_blind_spots(client: EspoClient) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    try:
        body = client.get("Policy", params={"maxSize": 200})
    except EspoClientError as e:
        return [], [str(e)]
    rows = _list_rows(body)
    blind_spots: list[dict[str, Any]] = []
    for row in rows:
        status = str(_pick(row, "status") or "").strip().lower()
        if status not in AUDIT_STATUSES:
            continue
        commission = _as_decimal(_pick(row, "commissionRate", "commission_rate", "commissionPercentage", "commission_percentage"))
        if commission is not None and commission > Decimal("0"):
            continue
        blind_spots.append(
            {
                "policy_id": str(row.get("id") or ""),
                "name": str(_pick(row, "accountName", "name") or "Unknown account"),
                "lob": str(_pick(row, "lineOfBusiness", "line_of_business") or "Unknown"),
                "premium": _as_money(_pick(row, "premiumAmount", "premium_amount", "amount")),
            }
        )
    blind_spots.sort(key=lambda r: -r["premium"])
    return blind_spots, warnings


def _build_eom_summary(
    *,
    client: EspoClient,
    month_start: date,
    month_end: date,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        body = client.get("Policy", params={"maxSize": 500})
    except EspoClientError as e:
        return {
            "month_label": month_start.strftime("%B %Y").upper(),
            "month_key": month_start.strftime("%Y-%m"),
            "total_premium": Decimal("0"),
            "agency_revenue": Decimal("0"),
            "new_business_premium": Decimal("0"),
            "renewals_premium": Decimal("0"),
            "retention_pct": Decimal("0"),
            "north_star_pct": Decimal("0"),
            "primary_lob": "Unknown",
            "count": 0,
        }, [str(e)]
    policies = _list_rows(body)
    month_rows: list[dict[str, Any]] = []
    for row in policies:
        bound = _parse_date(_pick(row, "boundDate", "bindDate", "effectiveDate", "createdAt"))
        if not bound or bound < month_start or bound > month_end:
            continue
        month_rows.append(row)
    total_premium = Decimal("0")
    total_revenue = Decimal("0")
    new_business_premium = Decimal("0")
    renewals_premium = Decimal("0")
    lob_totals: dict[str, Decimal] = {}
    for row in month_rows:
        premium = _as_money(_pick(row, "premiumAmount", "premium_amount", "amount"))
        commission_rate = _as_decimal(_pick(row, "commissionRate", "commission_rate"))
        commission_amount = _as_money(_pick(row, "commissionAmount", "commission_amount"))
        revenue = commission_amount
        if revenue <= 0 and commission_rate is not None and commission_rate > 0:
            revenue = (premium * commission_rate) / Decimal("100")
        lob = str(_pick(row, "lineOfBusiness", "line_of_business") or "Unknown")
        is_renewal = _is_renewal_policy(row)
        total_premium += premium
        total_revenue += revenue
        lob_totals[lob] = lob_totals.get(lob, Decimal("0")) + premium
        if is_renewal:
            renewals_premium += premium
        else:
            new_business_premium += premium
    retention_pct = Decimal("0")
    if total_premium > 0:
        retention_pct = (renewals_premium / total_premium) * Decimal("100")
    north_star_target = _as_money(os.environ.get("HERMES_NORTH_STAR_PREMIUM_GOAL", "1000000"))
    north_star_pct = Decimal("0")
    if north_star_target > 0:
        north_star_pct = (total_premium / north_star_target) * Decimal("100")
    primary_lob = "Unknown"
    if lob_totals:
        primary_lob = max(lob_totals.items(), key=lambda kv: kv[1])[0]
    summary = {
        "month_label": month_start.strftime("%B %Y").upper(),
        "month_key": month_start.strftime("%Y-%m"),
        "total_premium": total_premium,
        "agency_revenue": total_revenue,
        "new_business_premium": new_business_premium,
        "renewals_premium": renewals_premium,
        "retention_pct": retention_pct,
        "north_star_pct": north_star_pct,
        "primary_lob": primary_lob,
        "count": len(month_rows),
    }
    return summary, warnings


def _build_slack_payload(rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    if not rows:
        text = "Revenue Integrity check complete: no missing commission percentages found."
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
        return text, blocks
    lines = [
        "⚠️ REVENUE BLIND SPOT",
        "",
        "We bound these policies but have no commission % recorded:",
        "",
    ]
    for row in rows[:12]:
        lines.append(f"• *{row['name']}* ({row['lob']}) - Premium: ${row['premium']:,.0f}.")
    text = "\n".join(lines)
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*⚠️ REVENUE BLIND SPOT*\nWe bound these policies but have no commission % recorded.",
            },
        }
    ]
    for row in rows[:12]:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"• *{row['name']}* ({row['lob']}) - Premium: ${row['premium']:,.0f}."},
            }
        )
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Update Commission %"},
                        "action_id": "commission_update_pct",
                        "value": build_commission_action_value(
                            policy_id=row["policy_id"],
                            policy_name=row["name"],
                        ),
                    }
                ],
            }
        )
    return text, blocks


def _build_eom_slack_payload(summary: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    text = "\n".join(
        [
            f"🏆 {summary['month_label']} REVENUE REPORT",
            "",
            f"Total Premium: ${summary['total_premium']:,.0f}",
            f"Agency Revenue (Est): ${summary['agency_revenue']:,.0f}",
            f"Project 85 Progress: {summary['retention_pct']:.1f}% Retention",
            "",
            f"New Business: ${summary['new_business_premium']:,.0f} (Primary: {summary['primary_lob']})",
            f"Renewals: ${summary['renewals_premium']:,.0f}",
            "",
            f"North Star Tracker: You are {summary['north_star_pct']:.1f}% of the way to the goal! 🚀",
        ]
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*🏆 {summary['month_label']} REVENUE REPORT*"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Total Premium:* ${summary['total_premium']:,.0f}\n"
                    f"*Agency Revenue (Est):* ${summary['agency_revenue']:,.0f}\n"
                    f"*Project 85 Progress:* {summary['retention_pct']:.1f}% Retention"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*New Business:* ${summary['new_business_premium']:,.0f} "
                    f"(Primary: {summary['primary_lob']})\n"
                    f"*Renewals:* ${summary['renewals_premium']:,.0f}"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*North Star Tracker:* {summary['north_star_pct']:.1f}% of the way to the goal! 🚀",
            },
        },
    ]
    return text, blocks


def _state_path() -> Path:
    raw = os.environ.get("HERMES_COMMISSION_AUDIT_STATE_FILE", ".hermes/commission_audit_state.json").strip()
    return Path(raw).expanduser()


def _eom_state_path() -> Path:
    raw = os.environ.get("HERMES_EOM_SCORECARD_STATE_FILE", ".hermes/eom_scorecard_state.json").strip()
    return Path(raw).expanduser()


def _already_sent_today(day: date) -> bool:
    path = _state_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(data, dict) and str(data.get("last_sent_date")) == day.isoformat()


def _write_state(day: date) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_sent_date": day.isoformat()}))
    except OSError:
        return


def _eom_already_sent(month_key: str) -> bool:
    path = _eom_state_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(data, dict) and str(data.get("last_report_month")) == month_key


def _write_eom_state(month_key: str) -> None:
    path = _eom_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_report_month": month_key}))
    except OSError:
        return


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in ("", None):
            return row[key]
    return None


def _list_rows(body: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(body, dict) and isinstance(body.get("list"), list):
        return [x for x in body["list"] if isinstance(x, dict)]
    return []


def _as_decimal(value: Any) -> Decimal | None:
    if value in ("", None):
        return None
    try:
        return Decimal(str(value).replace("%", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _as_money(value: Any) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    try:
        return Decimal(str(value).replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _parse_date(value: Any) -> date | None:
    if value in ("", None):
        return None
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            if pattern == "%Y-%m-%d":
                return datetime.strptime(text[:10], pattern).date()
            return datetime.strptime(text[:19].replace("T", " "), pattern).date()
        except ValueError:
            continue
    return None


def _previous_month_window(today: date) -> tuple[date, date]:
    first_this_month = date(today.year, today.month, 1)
    prev_end_date = first_this_month - date.resolution
    prev_start = date(prev_end_date.year, prev_end_date.month, 1)
    return prev_start, prev_end_date


def _is_renewal_policy(row: dict[str, Any]) -> bool:
    if _pick(row, "renewedFrom"):
        return True
    marker = str(_pick(row, "businessType", "policyType", "type", "name") or "").lower()
    return "renew" in marker

