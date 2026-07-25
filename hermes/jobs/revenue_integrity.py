"""Revenue Integrity jobs (commission audit + EOM scorecard).

Data source: the custom CRM (Command Center). Both the commission audit and the
EOM scorecard read directly from the custom CRM's Supabase tables via the
in-process SupabaseClient — the same path the revenue sentinel (see
hermes/jobs/revenue_sentinel.py) and canonical-book jobs use:

  - COMMISSION AUDIT (revenue blind spot) -> commission_ledger. A blind spot is a
    ledgered, commissionable policy with no expected commission recorded
    (expected_commission NULL or <= 0), i.e. we track the policy but never
    captured a commission %. Chargebacks (negative/clawback rows) are excluded.
  - EOM SCORECARD -> canonical_policies (the read-only NowCerts book mirror),
    aggregated over the prior month by effective_date. Agency revenue comes from
    canonical_policies.agency_commission_amount.

The emitted row/summary shapes are preserved so the Slack/Talk rendering is
unchanged; delivery is already Slack-free (SlackNotifier is a shim onto
Nextcloud Talk team_notify).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

# Chargeback / clawback ledger rows are not commission blind spots — exclude them.
_CHARGEBACK_STATUSES = {"chargeback"}


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
    *,
    supa: SupabaseClient | None = None,
    notifier: SlackNotifier | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> CommissionAuditResult:
    _ = now
    supa = supa or SupabaseClient()
    rows, warnings = _query_commission_blind_spots(supa)
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
    *,
    supa: SupabaseClient | None = None,
    notifier: SlackNotifier | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> EomScorecardResult:
    supa = supa or SupabaseClient()
    ref = now or datetime.now()
    target_month_start, target_month_end = _previous_month_window(ref.date())
    summary, warnings = _build_eom_summary(
        supa=supa,
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


def _query_commission_blind_spots(supa: SupabaseClient) -> tuple[list[dict[str, Any]], list[str]]:
    """Ledgered, commissionable policies with no commission % recorded.

    Reads the custom CRM's ``commission_ledger`` and flags rows whose
    ``expected_commission`` is missing or non-positive — the money table knows
    about the policy but never captured a commission, our post-Espo analog of
    "we bound this policy but have no commission % recorded." Chargeback rows
    (clawbacks, ingested with a negative/`chargeback` marker) are not blind
    spots and are excluded.
    """
    warnings: list[str] = []
    try:
        rows = supa.select(
            "commission_ledger",
            columns="policy_number,client_name,carrier_name,lob,gross_premium,"
            "expected_commission,reconciliation_status",
            params={"order": "gross_premium.desc"},
            limit=2000,
        )
    except SupabaseClientError as e:
        return [], [str(e)]
    blind_spots: list[dict[str, Any]] = []
    for row in rows:
        recon = str(_pick(row, "reconciliation_status") or "").strip().lower()
        if recon in _CHARGEBACK_STATUSES:
            continue
        expected = _as_decimal(_pick(row, "expected_commission"))
        if expected is not None and expected > Decimal("0"):
            continue
        blind_spots.append(
            {
                "policy_id": str(_pick(row, "policy_number") or ""),
                "name": str(_pick(row, "client_name") or "Unknown account"),
                "lob": str(_pick(row, "lob", "line_of_business") or "Unknown"),
                "premium": _as_money(_pick(row, "gross_premium", "premium")),
            }
        )
    blind_spots.sort(key=lambda r: -r["premium"])
    return blind_spots, warnings


def _build_eom_summary(
    *,
    supa: SupabaseClient,
    month_start: date,
    month_end: date,
) -> tuple[dict[str, Any], list[str]]:
    """Aggregate the prior month's book from the custom CRM's canonical policies.

    Reads ``canonical_policies`` (the read-only NowCerts book mirror) and buckets
    every policy whose effective_date lands in the target month, summing premium,
    agency revenue (agency_commission_amount), and new-vs-renewal split.
    """
    warnings: list[str] = []
    try:
        policies = supa.select(
            "canonical_policies",
            columns="policy_number,carrier,lines_of_business,status,business_type,"
            "business_sub_type,renewed_policy,effective_date,expiration_date,"
            "premium_amount,annualized_premium,agency_commission_amount",
            params={"order": "effective_date.desc"},
            limit=5000,
        )
    except SupabaseClientError as e:
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
    month_rows: list[dict[str, Any]] = []
    for row in policies:
        # Bucket strictly by effective (bind) date — expiration is a year out, so
        # it would misattribute the month. Rows with no effective_date can't be
        # tied to a bind month and are skipped (matches the pre-Espo behavior).
        bound = _parse_date(_pick(row, "effective_date"))
        if not bound or bound < month_start or bound > month_end:
            continue
        month_rows.append(row)
    total_premium = Decimal("0")
    total_revenue = Decimal("0")
    new_business_premium = Decimal("0")
    renewals_premium = Decimal("0")
    lob_totals: dict[str, Decimal] = {}
    for row in month_rows:
        premium = _as_money(_pick(row, "premium_amount", "annualized_premium"))
        revenue = _as_money(_pick(row, "agency_commission_amount"))
        lob = str(_pick(row, "lines_of_business", "line_of_business") or "Unknown")
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
    # canonical_policies.renewed_policy holds the prior policy number on a
    # renewal (the post-Espo analog of Espo's renewedFrom); business_type is
    # "New Business" vs "Renewal".
    if _pick(row, "renewed_policy", "renewedFrom"):
        return True
    marker = str(
        _pick(
            row,
            "business_type",
            "business_sub_type",
            "businessType",
            "policyType",
            "type",
            "name",
        )
        or ""
    ).lower()
    return "renew" in marker

