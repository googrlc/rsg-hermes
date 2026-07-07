"""Commission ingest entry point: NowCerts -> commission_ledger.

``run()`` is the single code path used by both the nightly incremental job and the
one-time backfill (``full=True``). Idempotent: keyed on ``nowcerts_policy_id``, so
re-running yields identical ledger state.

HARD GATE: do not invoke against live NowCerts until the duplicate purge is
confirmed. Policies tagged PURGE-POLICY-2026-07 are always excluded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError

from . import config, mapping, state
from .rules import compute_expected, find_rule

log = logging.getLogger(__name__)

_RULE_COLUMNS = (
    "id,carrier_name,lob,state,nb_percent,renewal_percent,flat_fee,"
    "commission_method,commission_basis,lookup_priority,revenue_split_percent"
)


@dataclass
class CommissionSyncResult:
    ok: bool
    message: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    needs_rule: int = 0
    purged_skipped: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    watermark: Optional[str] = None
    dry_run: bool = False

    @property
    def upserted(self) -> int:
        return self.inserted + self.updated


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_rules(supa: Any) -> list[dict[str, Any]]:
    return supa.select(
        config.RULES_TABLE,
        columns=_RULE_COLUMNS,
        params={"active": "is.true"},
        limit=5000,
    )


def _load_existing_ids(supa: Any) -> set[str]:
    rows = supa.select(
        config.LEDGER_TABLE,
        columns="nowcerts_policy_id",
        params={"nowcerts_policy_id": "not.is.null"},
        limit=1_000_000,
    )
    return {str(r["nowcerts_policy_id"]) for r in rows if r.get("nowcerts_policy_id")}


def run(
    nc: Any,
    supa: Any,
    *,
    notifier: Optional[SlackNotifier] = None,
    since: Optional[str] = None,
    full: bool = False,
    dry_run: bool = False,
    limit: Optional[int] = None,
    now_iso: Optional[str] = None,
) -> CommissionSyncResult:
    """Fetch NowCerts policies and upsert computed commission rows.

    full=True ignores the watermark and pulls the whole book (backfill).
    dry_run=True computes everything but writes nothing and posts no Slack message.
    """
    run_started = now_iso or _utcnow_iso()
    effective_since = None if full else (since or state.read_watermark() or config.DEFAULT_SINCE)

    rules = _load_rules(supa)
    existing_ids = _load_existing_ids(supa)

    policies = nc.fetch_policies(since=effective_since, page_size=config.PAGE_SIZE)
    if limit is not None:
        policies = policies[:limit]

    result = CommissionSyncResult(ok=True, message="", fetched=len(policies), dry_run=dry_run)

    for policy in policies:
        if mapping.is_purged(policy):
            result.purged_skipped += 1
            continue

        fields = mapping.extract_fields(policy)
        rule = find_rule(
            rules,
            carrier=fields.get("carrier") or "",
            lob=fields.get("lob") or "",
            state=fields.get("state") or "",
        )
        expected = (
            compute_expected(
                rule, gross_premium=fields.get("gross_premium"), is_renewal=fields["is_renewal"]
            )
            if rule
            else None
        )
        row = mapping.build_ledger_row(fields, rule, expected)
        if row is None:
            result.skipped += 1
            continue

        if row["reconciliation_status"] == config.STATUS_NEEDS_RULE:
            result.needs_rule += 1

        is_update = row["nowcerts_policy_id"] in existing_ids

        if dry_run:
            if is_update:
                result.updated += 1
            else:
                result.inserted += 1
            continue

        try:
            supa.upsert(config.LEDGER_TABLE, row, on_conflict=config.LEDGER_CONFLICT_KEY)
            existing_ids.add(row["nowcerts_policy_id"])
            if is_update:
                result.updated += 1
            else:
                result.inserted += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not abort the run
            msg = f"{row.get('policy_number', '?')}: {e}"
            result.errors.append(msg)
            log.error("commissions: upsert failed for %s", msg)

    result.ok = not result.errors

    # Advance the watermark on any completed real run (idempotency makes any
    # incidental reprocessing harmless; errors are surfaced to Slack).
    if not dry_run:
        state.write_watermark(run_started)
        result.watermark = run_started

    result.message = _summary(result, effective_since)

    if not dry_run:
        _post_slack(notifier, result.message)

    return result


def _summary(r: CommissionSyncResult, since: Optional[str]) -> str:
    prefix = "Commission ingest (dry-run)" if r.dry_run else "Commission ingest"
    since_txt = "full book" if since is None else f"since {since}"
    err = f" · {len(r.errors)} ERRORS" if r.errors else ""
    wm = f" · watermark→{r.watermark}" if r.watermark else ""
    return (
        f"{prefix} [{since_txt}]: {r.fetched} fetched · {r.inserted} new · "
        f"{r.updated} updated · {r.needs_rule} needs_rule · "
        f"{r.purged_skipped} purge-skipped · {r.skipped} skipped{err}{wm}"
    )


def _post_slack(notifier: Optional[SlackNotifier], text: str) -> None:
    active = notifier
    if active is None:
        try:
            active = SlackNotifier(channel=config.SLACK_SYSTEMS_CHECK)
        except Exception as e:  # noqa: BLE001 — Slack is best-effort
            log.warning("commissions: Slack notifier unavailable: %s", e)
            return
    try:
        active.post_message(text=text)
    except SlackNotifierError as e:
        log.warning("commissions: Slack post failed: %s", e)
