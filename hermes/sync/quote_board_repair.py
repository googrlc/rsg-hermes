"""Repair the opportunities the quote sync already put on the board.

``quote_sync`` no longer produces these defects. This fixes the rows that are
there now, from ``canonical_quotes`` — the AMS quote register, which holds the
right answer for every one of them:

* **type** — 37 rows say New Business on quotes NowCerts calls Renewal.
* **premium** — 51 rows carry a premium from a different six-month term than the
  dates beside them. The register is keyed by quote guid; the guid on the row is
  correct, so the premium that belongs with it is knowable.
* **owner** — nothing on the board has one. Personal lines to Gretchen, the rest
  to Lamar, written to ``assigned_to_email`` because that is the column the CRM
  reads. Writing ``assigned_to`` instead reports success and shows nothing.
* **close date** — NULL nearly everywhere, so the board falls back to the
  expiration date and every date on screen looks arbitrary.

It does **not** delete anything. Removing the term artifacts — quotes whose
policy is already bound — is a separate, larger judgment and belongs with
``opportunity_dedupe``, which archives before it removes. This only corrects
values in place, so the worst case is a wrong value replaced by another wrong
value, not a lost row.

Nothing is written unless ``apply=True``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes.intake import opportunities as opp

log = logging.getLogger(__name__)

QUOTES_TABLE = "canonical_quotes"

# Same routing the sync now applies at creation. Kept in one place would be
# nicer; kept correct in two is what matters, so the personal-lines set is
# imported rather than restated.
from hermes.sync.quote_sync import _owner_for, _stage_for  # noqa: E402


@dataclass
class Fix:
    opportunity_id: str
    insured_name: str
    changes: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    def describe(self) -> str:
        bits = ", ".join(f"{k}: {old!r} → {new!r}" for k, (old, new) in sorted(self.changes.items()))
        return f"{self.insured_name}: {bits}"


@dataclass
class RepairResult:
    fixes: list[Fix] = field(default_factory=list)
    applied: int = 0
    backup_path: str | None = None
    unmatched: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        by_field: dict[str, int] = {}
        for f in self.fixes:
            for k in f.changes:
                by_field[k] = by_field.get(k, 0) + 1
        detail = ", ".join(f"{k}={n}" for k, n in sorted(by_field.items())) or "nothing to change"
        return (f"{len(self.fixes)} rows need work ({detail}); applied={self.applied}; "
                f"unmatched={len(self.unmatched)}; errors={len(self.errors)}")


def _norm_type(business_type: Any) -> str | None:
    raw = str(business_type or "").strip().lower()
    if raw.startswith("renew"):
        return "Renewals"
    if raw.startswith("new"):
        return opp.TYPE_NEW_BUSINESS
    return None


def _money(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def backup(rows: list[dict[str, Any]], directory: str = "/var/lib/hermes") -> str:
    """Write every opportunity to a timestamped JSON file before anything changes.

    PostgREST cannot CREATE TABLE, so the backup is a file rather than the
    `backup_20260729_opportunities_prewipe` table the runbook assumes. Same
    purpose: a way back that does not depend on this code being right.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(directory) / f"opportunities-backup-{stamp}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2, default=str))
    except OSError:
        path = Path.cwd() / path.name
        path.write_text(json.dumps(rows, indent=2, default=str))
    log.info("opportunities backed up: %s (%d rows)", path, len(rows))
    return str(path)


def plan(supa: Any) -> RepairResult:
    """Work out every correction, touching nothing."""
    result = RepairResult()
    opps = supa.select(opp.TABLE, columns="*", limit=5000)
    quotes = supa.select(QUOTES_TABLE, columns="*", limit=5000)
    by_guid = {str(q.get("nowcerts_quote_guid")): q for q in quotes if q.get("nowcerts_quote_guid")}

    for o in opps:
        guid = str(o.get("nowcerts_quote_guid") or "")
        q = by_guid.get(guid)
        changes: dict[str, tuple[Any, Any]] = {}

        if q:
            want_type = _norm_type(q.get("business_type"))
            # Only correct a type the sync guessed. A human who set this deliberately
            # is not overruled by a register row.
            if (want_type and want_type != o.get("opportunity_type")
                    and str(o.get("sync_source") or "") != "crm"):
                changes["opportunity_type"] = (o.get("opportunity_type"), want_type)
                stage = _stage_for(want_type)
                if o.get("stage") not in opp.stages_for_type(want_type):
                    changes["stage"] = (o.get("stage"), stage)

            want_prem = _money(q.get("premium_estimate"))
            if want_prem is not None and want_prem != _money(o.get("premium_estimate")):
                changes["premium_estimate"] = (o.get("premium_estimate"), want_prem)

            for col, src in (("carrier", "carrier"), ("effective_date", "effective_date"),
                             ("expiration_date", "expiration_date")):
                want = q.get(src)
                if want and str(want)[:10] != str(o.get(col) or "")[:10]:
                    changes[col] = (o.get(col), want)
        elif guid:
            result.unmatched.append(f"{o.get('insured_name')} ({guid})")

        if not o.get("assigned_to_email"):
            changes["assigned_to_email"] = (None, _owner_for(o.get("line_of_business")))

        eff = changes.get("effective_date", (o.get("effective_date"),))[-1] or o.get("effective_date")
        if not o.get("expected_close_date") and eff:
            changes["expected_close_date"] = (None, str(eff)[:10])

        if changes:
            result.fixes.append(Fix(str(o.get("id")), str(o.get("insured_name") or "?"), changes))
    return result


def run_repair(supa: Any, *, apply: bool = False, backup_dir: str = "/var/lib/hermes") -> RepairResult:
    result = plan(supa)
    if not apply:
        return result

    result.backup_path = backup(supa.select(opp.TABLE, columns="*", limit=5000), backup_dir)
    for fix in result.fixes:
        payload = {k: new for k, (_, new) in fix.changes.items()}
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            supa.update(opp.TABLE, fix.opportunity_id, payload)
            result.applied += 1
        except Exception as exc:  # noqa: BLE001 — one bad row shouldn't stop the sweep
            result.errors.append(f"{fix.insured_name}: {exc}")
            log.warning("repair failed for %s: %s", fix.insured_name, exc)
    log.info("quote board repair: %s", result.message)
    return result
