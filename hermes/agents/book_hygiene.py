"""Agent 01 — Book Hygiene Auditor (Phase 1: read-only missing-field pass).

Phase 1 is strictly read-only: it logs findings to Supabase
``book_hygiene_findings`` and performs zero AMS writes. The full duplicate
detection (rapidfuzz) and Phase 2 tag/note writes are built in a later step
under the §4 lifecycle (dry_run -> shadow -> live_supervised).

This first cut implements the missing-critical-field and stale-record checks
from the agent spec, which are deterministic (no LLM) and safe to run during
the 48h AMS-cleanup wait because they never mutate Momentum.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from hermes.agents import register_agent
from hermes.agents.base import AgentAction, AgentRunner

log = logging.getLogger(__name__)

# Critical fields per the agent spec §5. Empty => flagged missing.
# Accepts both camelCase and snake_case (field-name resilience, §3.7).
CRITICAL_FIELDS = (
    ("effective_date", "effectiveDate"),
    ("expiration_date", "expirationDate"),
    ("carrier", "carrier"),
    ("line_of_business", "lineOfBusiness", "lob"),
    ("primary_contact", "primaryContact"),
)

# Records with no activity in this many months AND no active policy => stale.
STALE_MONTHS = 18


def _field(record: dict[str, Any], *keys: str) -> Any:
    """Read a field case-insensitively across camelCase / snake_case variants."""
    lower = {k.lower().replace("-", "").replace("_", ""): v for k, v in record.items() if isinstance(k, str)}
    for key in keys:
        if key in record:
            return record[key]
        if key in lower:
            return lower[key]
    return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


@register_agent("book-hygiene-auditor")
class BookHygieneAuditor(AgentRunner):
    """Read-only daily audit for missing fields + stale records."""

    default_state = "dry_run"
    notify_channel = "the-boss"
    # Phase 1 is read-only forever until Lamar promotes it; never live-writes.
    max_writes_per_run = 0

    def __init__(self, *args: Any, momentum: Any | None = None, max_records: int = 500, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._momentum = momentum
        self.max_records = max_records

    @property
    def momentum(self) -> Any:
        if self._momentum is None:
            from hermes.integrations.momentum_rest import MomentumRESTClient

            self._momentum = MomentumRESTClient()
        return self._momentum

    def collect(self) -> Iterable[dict[str, Any]]:
        """Page through Insured records (capped) — read-only."""
        try:
            return self.momentum.enumerate_all(
                "Insured",
                select="DatabaseId,CommercialName,FirstName,LastName,InsuredType,EMail,changeDate",
                max_records=self.max_records,
            )
        except Exception as exc:
            log.warning("%s: collect failed: %s", self.name, exc)
            return []

    def _insured_name(self, record: dict[str, Any]) -> str:
        commercial = _field(record, "commercialName", "CommercialName")
        if _has_value(commercial):
            return str(commercial)
        first = _field(record, "firstName", "FirstName") or ""
        last = _field(record, "lastName", "LastName") or ""
        return f"{first} {last}".strip() or "(unnamed)"

    def decide(self, item: dict[str, Any]) -> list[AgentAction]:
        """Inspect one insured; log findings. Returns no AMS write actions."""
        database_id = _field(item, "databaseId", "DatabaseId")
        name = self._insured_name(item)
        insured_type = _field(item, "insuredType", "InsuredType")

        # Missing critical fields.
        for camel, *aliases in CRITICAL_FIELDS:
            keys = (camel,) + tuple(aliases)
            if not _has_value(_field(item, *keys)):
                self.log_finding(
                    insured_database_id=str(database_id) if database_id else None,
                    finding_type="missing_field",
                    severity="medium",
                    confidence=1.0,
                    details={"field": camel, "name": name, "insured_type": insured_type},
                )

        # Stale record: no activity in 18+ months (changeDate is the AMS touch).
        change = _field(item, "changeDate", "change_date")
        if _has_value(change):
            try:
                ts = str(change).replace("Z", "+00:00")
                changed = datetime.fromisoformat(ts)
                if changed.tzinfo is None:
                    changed = changed.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - changed).days
                if age_days >= STALE_MONTHS * 30:
                    self.log_finding(
                        insured_database_id=str(database_id) if database_id else None,
                        finding_type="stale",
                        severity="low",
                        confidence=1.0,
                        details={"name": name, "age_days": age_days},
                    )
            except (ValueError, TypeError):
                pass

        return []  # Phase 1 performs no AMS writes.
