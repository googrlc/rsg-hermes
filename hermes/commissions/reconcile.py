"""Statement reconciliation against Supabase commission_ledger (Phase 3a).

Reuses the CSV/XLSX/PDF statement parsers from the existing EspoCRM-facing job
(``hermes.jobs.commission_reconciliation``) but matches lines to
``commission_ledger`` rows on the Commission Command spine: writes back
``actual_commission`` + ``delta`` and opens ``commission_reconciliation`` rows
for shortages/overages and unmatched statement lines.

Idempotent: ledger updates are keyed by row id (re-applying is a no-op), and a
reconciliation row is only opened once per (ledger_id | policy_number, statement
date) — re-running the same statement won't duplicate the queue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.jobs.commission_reconciliation import _matching_keys, _parse_statement

from . import config

log = logging.getLogger(__name__)

_LEDGER_COLUMNS = (
    "id,policy_number,carrier_name,client_name,expected_commission,statement_date"
)


@dataclass
class ReconcileResult:
    ok: bool
    message: str
    statement: str = ""
    parsed: int = 0
    matched: int = 0
    unmatched: int = 0
    discrepancies: int = 0
    ledger_updated: int = 0
    duplicates_skipped: int = 0
    total_short: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def _utcnow_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _priority(abs_delta: float) -> str:
    if abs_delta >= config.PRIORITY_HIGH_ABS:
        return "high"
    if abs_delta >= config.PRIORITY_MED_ABS:
        return "medium"
    return "low"


def _load_ledger_index(supa: Any) -> dict[str, list[dict[str, Any]]]:
    rows = supa.select(config.LEDGER_TABLE, columns=_LEDGER_COLUMNS, limit=1_000_000)
    index: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        for key in _matching_keys(r.get("policy_number") or ""):
            index.setdefault(key, []).append(r)
    return index


def _load_open_recon_keys(supa: Any, statement_date: str) -> set[tuple[str, str]]:
    """Existing OPEN reconciliation rows for this statement date (for dedup)."""
    rows = supa.select(
        config.RECON_TABLE,
        columns="ledger_id,policy_number,statement_date,status",
        params={"status": "eq.open", "statement_date": f"eq.{statement_date}"},
        limit=1_000_000,
    )
    keys: set[tuple[str, str]] = set()
    for r in rows:
        if r.get("ledger_id"):
            keys.add(("ledger", str(r["ledger_id"])))
        else:
            keys.add(("policy", str(r.get("policy_number") or "")))
    return keys


def _match(
    index: dict[str, list[dict[str, Any]]], policy_number: str, carrier: str
) -> Optional[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in _matching_keys(policy_number):
        candidates = index.get(key, [])
        if candidates:
            break
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Multiple ledger rows share this policy number — disambiguate by carrier.
    c = (carrier or "").strip().lower()
    if c:
        for row in candidates:
            rc = (row.get("carrier_name") or "").lower()
            if rc and (c in rc or rc in c):
                return row
    return candidates[0]


def run_reconciliation(
    supa: Any,
    statement_path: str,
    *,
    notifier: Optional[SlackNotifier] = None,
    dry_run: bool = False,
    statement_date: Optional[str] = None,
    now_date: Optional[str] = None,
) -> ReconcileResult:
    """Parse a carrier statement and reconcile it against commission_ledger."""
    path = Path(statement_path).expanduser()
    stmt_date = statement_date or now_date or _utcnow_date()

    rows, warnings = _parse_statement(path)
    result = ReconcileResult(
        ok=True, message="", statement=path.name, parsed=len(rows),
        warnings=list(warnings), dry_run=dry_run,
    )
    if not rows:
        result.message = (
            f"Commission reconciliation: 0 rows parsed from {path.name}"
            + (f" ({'; '.join(warnings)})" if warnings else "")
        )
        return result

    index = _load_ledger_index(supa)
    seen = _load_open_recon_keys(supa, stmt_date)

    for row in rows:
        pol = row.get("policy_number") or ""
        carrier = row.get("carrier") or ""
        paid = float(row.get("paid_commission") or 0)
        try:
            match = _match(index, pol, carrier)
            if match:
                result.matched += 1
                expected = float(match.get("expected_commission") or 0)
                delta = round(paid - expected, 2)  # negative == shorted
                status = "reconciled" if abs(delta) <= config.DELTA_TOLERANCE else "discrepancy"
                if not dry_run:
                    supa.update(
                        config.LEDGER_TABLE, match["id"],
                        {
                            "actual_commission": paid,
                            "delta": delta,
                            "payment_received": True,
                            "reconciliation_status": status,
                        },
                    )
                result.ledger_updated += 1

                if abs(delta) > config.DELTA_TOLERANCE:
                    if delta < 0:
                        result.total_short += abs(delta)
                    key = ("ledger", str(match["id"]))
                    if key in seen:
                        result.duplicates_skipped += 1
                        continue
                    result.discrepancies += 1
                    seen.add(key)
                    if not dry_run:
                        supa.insert(config.RECON_TABLE, {
                            "ledger_id": match["id"],
                            "policy_number": match.get("policy_number") or pol,
                            "carrier_name": match.get("carrier_name") or carrier or "(unknown carrier)",
                            "client_name": match.get("client_name") or "(unknown insured)",
                            "statement_date": stmt_date,
                            "expected_commission": expected,
                            "actual_commission": paid,
                            "delta": delta,
                            "delta_percent": round(delta / expected * 100, 2) if expected else None,
                            "discrepancy_type": "short" if delta < 0 else "overpaid",
                            "priority": _priority(abs(delta)),
                            "status": "open",
                            "assigned_to": config.RECON_ASSIGNEE,
                        })
            else:
                result.unmatched += 1
                key = ("policy", pol)
                if key in seen:
                    result.duplicates_skipped += 1
                    continue
                result.discrepancies += 1
                seen.add(key)
                if not dry_run:
                    supa.insert(config.RECON_TABLE, {
                        "policy_number": pol or "(unknown)",
                        "carrier_name": carrier or "(unknown carrier)",
                        "client_name": "(unmatched statement line)",
                        "statement_date": stmt_date,
                        "actual_commission": paid,
                        "delta": paid,
                        "discrepancy_type": "unmatched_statement_line",
                        "priority": _priority(abs(paid)),
                        "status": "open",
                        "assigned_to": config.RECON_ASSIGNEE,
                    })
        except Exception as e:  # noqa: BLE001 — one bad line must not abort
            result.errors.append(f"{pol}: {e}")
            log.error("reconcile: failed for %s: %s", pol, e)

    result.ok = not result.errors
    result.message = _summary(result)
    if not dry_run:
        _post_slack(notifier, result.message)
    return result


def _summary(r: ReconcileResult) -> str:
    prefix = "Commission reconciliation (dry-run)" if r.dry_run else "Commission reconciliation"
    dup = f" · {r.duplicates_skipped} dup-skipped" if r.duplicates_skipped else ""
    err = f" · {len(r.errors)} ERRORS" if r.errors else ""
    return (
        f"{prefix} [{r.statement}]: {r.parsed} lines · {r.matched} matched · "
        f"{r.unmatched} unmatched · {r.discrepancies} flagged"
        f" · ${r.total_short:,.2f} short{dup}{err}"
    )


def _post_slack(notifier: Optional[SlackNotifier], text: str) -> None:
    active = notifier
    if active is None:
        try:
            active = SlackNotifier(channel=config.SLACK_SYSTEMS_CHECK)
        except Exception as e:  # noqa: BLE001
            log.warning("reconcile: Slack notifier unavailable: %s", e)
            return
    try:
        active.post_message(text=text)
    except SlackNotifierError as e:
        log.warning("reconcile: Slack post failed: %s", e)
