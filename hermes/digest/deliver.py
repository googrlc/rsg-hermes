"""Format and deliver the morning digest to #the-boss (read-only)."""
from __future__ import annotations

from datetime import date, datetime

from . import config


def _money(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "$?"


def _days_until(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) - date.today()).days
    except ValueError:
        return None


def _bucket_policies(policies: list[dict]) -> dict:
    buckets = {b: [] for b in config.RENEWAL_BUCKETS}
    for p in policies:
        d = _days_until(p.get("expiration_date"))
        if d is None:
            continue
        for lo, hi in config.RENEWAL_BUCKETS:
            if lo <= d <= hi:
                p["_days"] = d
                buckets[(lo, hi)].append(p)
                break
    return buckets


def build_text(data: dict) -> str:
    """Plain-text digest (also used as Slack fallback text)."""
    lines = [f"☀️ *RSG Morning Digest* — {data['generated']}", ""]

    buckets = _bucket_policies(data["policies"])
    total_at_risk = sum(float(p.get("premium_amount") or 0)
                        for p in data["policies"])
    lines.append(f"*🔄 Renewal radar — {len(data['policies'])} policies, "
                 f"{_money(total_at_risk)} premium in the next 90 days*")
    for (lo, hi), pols in buckets.items():
        if not pols:
            continue
        prem = sum(float(p.get('premium_amount') or 0) for p in pols)
        lines.append(f"  *{lo}-{hi} days:* {len(pols)} policies / {_money(prem)}")
        for p in pols[:config.SECTION_CAP]:
            lines.append(f"    • {p.get('accountName') or '?'} — "
                         f"{p.get('line_of_business') or '?'} "
                         f"({_money(p.get('premium_amount'))}) — "
                         f"{p['_days']}d ({p.get('carrier') or '?'})")
        if len(pols) > config.SECTION_CAP:
            lines.append(f"    …+{len(pols) - config.SECTION_CAP} more")
    lines.append("")

    quiet = data["quiet"]
    lines.append(f"*🤫 Quiet pipeline — {len(quiet)} open deals untouched "
                 f"{config.QUIET_DAYS}+ days*")
    for o in quiet[:config.SECTION_CAP]:
        prem = o.get("estimatedPremium") or o.get("amount")
        lines.append(f"    • {o.get('accountName') or o.get('name')} — "
                     f"{o.get('stage')} — {_money(prem)} — "
                     f"quiet {o.get('_quiet_days')}d "
                     f"({o.get('assignedUserName') or '?'})")
    if len(quiet) > config.SECTION_CAP:
        lines.append(f"    …+{len(quiet) - config.SECTION_CAP} more")
    lines.append("")

    tasks = data["tasks"]
    lines.append(f"*⏰ Overdue tasks — {len(tasks)}*")
    for t in tasks[:config.SECTION_CAP]:
        lines.append(f"    • {t.get('name')} — due {t.get('dateEnd')} "
                     f"({t.get('assignedUserName') or 'unassigned'})")
    if len(tasks) > config.SECTION_CAP:
        lines.append(f"    …+{len(tasks) - config.SECTION_CAP} more")

    lines.append("")
    lines.append("_Read-only digest (Slice B). No messages were sent to clients._")
    return "\n".join(lines)


def post(data: dict) -> dict:
    from hermes.integrations.slack_notifier import SlackNotifier
    notifier = SlackNotifier(channel=config.SLACK_THE_BOSS)
    return notifier.post_message(text=build_text(data))
