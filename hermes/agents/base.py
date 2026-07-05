"""AgentRunner — the shared lifecycle every RSG agent inherits.

Enforces the rules from ``00_shared_standards.md`` exactly once:

* blast-radius caps (50 writes / run, 1 write / second)        — §3.2
* dry-run / shadow / live gating                                — §3.3 / §4
* identity + tagging on every write                             — §3.1
* Supabase ``agent_writes`` audit mirror + 7-day rollback link  — §3.6
* escalation triggers (low confidence, monetary, carrier-facing,
  systemic anomaly, human-touched < 24h)                       — §3.5
* PII redaction of payloads before they are persisted           — §3.8
* field-name resilience (camelCase / snake_case) is the agent's
  read layer's job; the runner stays AMS-agnostic                — §3.7

Concrete agents subclass ``AgentRunner`` and implement ``collect()``
(read), ``decide(item)`` (propose actions) and optionally ``do_write``
(execute one AMS write). The base handles caps, throttling, audit,
escalation and reporting so those concerns are never re-implemented.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ULID generator (no external dependency) — 26-char Crockford base32.
# 48-bit ms timestamp + 80-bit CSPRNG randomness, sortable + collision-safe.
# ---------------------------------------------------------------------------

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid(ms: int | None = None) -> str:
    """Generate a 26-character ULID."""
    import secrets

    ts = int(time.time() * 1000) if ms is None else ms
    rand = secrets.token_bytes(10)
    ts_chars = []
    for _ in range(10):
        ts_chars.append(_CROCKFORD[ts & 0x1F])
        ts >>= 5
    ts_chars.reverse()
    rnd_int = int.from_bytes(rand, "big")
    rnd_chars = []
    for _ in range(16):
        rnd_chars.append(_CROCKFORD[rnd_int & 0x1F])
        rnd_int >>= 5
    rnd_chars.reverse()
    return "".join(ts_chars) + "".join(rnd_chars)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------------------
# PII redaction — payloads persisted to Supabase must not carry raw PII.
# §3.8: redact SSN, DOB, driver's license, full policy numbers (keep last 4).
# ---------------------------------------------------------------------------


def _redact_value(key: str, value: Any) -> Any:
    k = key.lower().replace("-", "").replace("_", "").replace(" ", "")
    if isinstance(value, str) and any(token in k for token in ("ssn", "security")):
        return "[REDACTED:SSN]"
    if isinstance(value, str) and k in ("dob", "dateofbirth"):
        return "[REDACTED:DOB]"
    if isinstance(value, str) and "license" in k:
        return "[REDACTED:DL]"
    if isinstance(value, str) and ("policynumber" in k or k in ("policyno", "policynum")):
        return f"[REDACTED:POLICY]…{value[-4:]}" if len(value) > 4 else "[REDACTED:POLICY]"
    return value


def redact_payload(payload: Any) -> Any:
    """Return a deep copy of ``payload`` with PII fields redacted."""
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            out[key] = redact_payload(value) if isinstance(value, (dict, list)) else _redact_value(key, value)
        return out
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class AgentAction:
    """A proposed AMS mutation, decided by an agent and gated by the runner."""

    tool: str
    target_system: str = "momentum"
    target_entity: str = ""  # insured|policy|opportunity|task|note|certificate|tag
    target_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    reversible: bool = True
    monetary: bool = False
    carrier_facing: bool = False
    human_touched_within_24h: bool = False
    anomaly_key: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRunResult:
    run_id: str
    agent_name: str
    state: str
    dry_run: bool
    trigger: str = "on-demand"
    reads: int = 0
    writes_attempted: int = 0
    writes_executed: int = 0
    writes_skipped: int = 0
    escalations: int = 0
    findings: int = 0
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    error: str | None = None
    summary: str = ""
    capped: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def message(self) -> str:
        prefix = "DRY RUN: " if self.dry_run else ""
        capped = " CAPPED" if self.capped else ""
        err = f" ERROR={self.error}" if self.error else ""
        return (
            f"{prefix}{self.agent_name} run {self.run_id} "
            f"(state={self.state}, trigger={self.trigger}) — "
            f"reads={self.reads} writes={self.writes_executed}/{self.writes_attempted} "
            f"skipped={self.writes_skipped} escalations={self.escalations} "
            f"findings={self.findings}{capped}{err}"
        )


class EscalationError(Exception):
    """Raised when an agent should stop entirely (used by concrete agents)."""


# ---------------------------------------------------------------------------
# Notifier abstraction — duck-typed: ``notify(channel, text)``.
# ---------------------------------------------------------------------------


class NullNotifier:
    def notify(self, channel: str, text: str) -> None:
        log.info("[notify/%s] %s", channel, text)


# ---------------------------------------------------------------------------
# AgentRunner
# ---------------------------------------------------------------------------


class AgentRunner:
    """Shared trigger → read → decide → write → report lifecycle.

    Subclasses set ``name`` and implement ``collect()`` and ``decide()``.
    Agents that perform AMS writes override ``do_write()``.
    """

    name: str = "base"
    default_state: str = "dry_run"
    max_writes_per_run: int = 50
    write_interval_seconds: float = 1.0
    rollback_window_days: int = 7
    confidence_threshold: float = 0.75
    systemic_anomaly_cap: int = 3
    notify_channel: str = "the-boss"

    STATES: tuple[str, ...] = (
        "draft",
        "dry_run",
        "shadow",
        "live_supervised",
        "live_autonomous",
        "paused",
        "retired",
    )
    LIVE_STATES: frozenset[str] = frozenset({"live_supervised", "live_autonomous"})

    def __init__(
        self,
        supa: Any | None = None,
        *,
        state: str | None = None,
        dry_run: bool | None = None,
        trigger: str = "on-demand",
        llm: Any | None = None,
        notifier: Any | None = None,
        run_id: str | None = None,
    ) -> None:
        self.supa = supa
        self.llm = llm
        self.notifier = notifier or NullNotifier()
        self.trigger = trigger
        self.state = self._resolve_state(state)
        self.dry_run = self._resolve_dry_run(dry_run)
        self.run_id = run_id or _ulid()
        self.started_at = _now()

        self._writes_this_run = 0
        self._last_write_ts = 0.0
        self._anomaly_counts: dict[str, int] = {}
        self._capped = False

        self.result = AgentRunResult(
            run_id=self.run_id,
            agent_name=self.name,
            state=self.state,
            dry_run=self.dry_run,
            trigger=self.trigger,
            started_at=self.started_at,
        )

    # -- state resolution ---------------------------------------------------

    def _resolve_state(self, state: str | None) -> str:
        env_key = f"AGENT_STATE_{self.name.upper().replace('-', '_')}"
        explicit = state or os.environ.get(env_key)
        if explicit and explicit in self.STATES:
            return explicit
        return self.default_state

    def _resolve_dry_run(self, dry_run: bool | None) -> bool:
        if dry_run is not None:
            return dry_run
        return self.state not in self.LIVE_STATES

    @property
    def can_write_live(self) -> bool:
        return self.state in self.LIVE_STATES and not self.dry_run

    # -- lifecycle hooks (override in subclasses) ---------------------------

    def collect(self) -> Iterable[dict[str, Any]]:
        """Read phase — return an iterable of records to evaluate."""
        raise NotImplementedError

    def decide(self, item: dict[str, Any]) -> list[AgentAction]:
        """Decision phase — propose zero or more actions for one record."""
        raise NotImplementedError

    def do_write(self, action: AgentAction) -> dict[str, Any]:
        """Execute one AMS write. Override for agents that promote to live."""
        return {}

    # -- optional finding logging (Agent 01 uses this) ---------------------

    def log_finding(
        self,
        *,
        insured_database_id: str | None,
        finding_type: str,
        severity: str = "medium",
        confidence: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record a Book Hygiene finding (dry-run safe; no AMS write)."""
        self.result.findings += 1
        if not self.supa:
            return
        try:
            self.supa.insert(
                "book_hygiene_findings",
                {
                    "run_id": self.run_id,
                    "insured_database_id": insured_database_id,
                    "finding_type": finding_type,
                    "severity": severity,
                    "confidence": round(float(confidence), 3),
                    "details": redact_payload(details or {}),
                    "status": "open",
                },
            )
        except Exception as exc:  # audit storage must never kill a run
            log.warning("%s: book_hygiene_findings insert failed: %s", self.name, exc)

    # -- escalation (§3.5) --------------------------------------------------

    def _should_escalate(self, action: AgentAction) -> str | None:
        if action.confidence < self.confidence_threshold:
            return "low-confidence"
        if action.monetary:
            return "monetary-field-change"
        if action.carrier_facing:
            return "carrier-facing-document"
        if action.human_touched_within_24h:
            return "human-touched-24h"
        if action.anomaly_key:
            count = self._anomaly_counts.get(action.anomaly_key, 0) + 1
            self._anomaly_counts[action.anomaly_key] = count
            if count > self.systemic_anomaly_cap:
                return "systemic-anomaly"
        return None

    def _escalate(self, action: AgentAction, reason: str) -> None:
        self.result.escalations += 1
        msg = (
            f"⚠️ {self.name} escalation ({reason}) run={self.run_id} "
            f"tool={action.tool} target={action.target_entity}:{action.target_id or '?'}"
        )
        log.warning(msg)
        try:
            self.notifier.notify(self.notify_channel, msg)
        except Exception as exc:
            log.warning("%s: notifier failed: %s", self.name, exc)
        self._audit_write(action, status="escalated", response={"reason": reason}, reversible=False)

    # -- blast radius (§3.2) ------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_write_ts
        wait = self.write_interval_seconds - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_write_ts = time.monotonic()

    def _at_cap(self) -> bool:
        return self._writes_this_run >= self.max_writes_per_run

    def _halt_for_cap(self) -> None:
        if self._capped:
            return
        self._capped = True
        self.result.capped = True
        msg = (
            f"{self.name} hit blast-radius cap ({self.max_writes_per_run} writes) "
            f"run={self.run_id} — pausing, remaining work deferred."
        )
        log.warning(msg)
        try:
            self.notifier.notify(self.notify_channel, msg)
        except Exception as exc:
            log.warning("%s: notifier failed: %s", self.name, exc)

    # -- audit mirror (§3.1 / §3.6) -----------------------------------------

    def _audit_write(
        self,
        action: AgentAction,
        *,
        status: str,
        response: dict[str, Any],
        reversible: bool,
    ) -> None:
        if not self.supa:
            return
        reversible_until = None
        if reversible and status == "executed":
            reversible_until = (_now() + timedelta(days=self.rollback_window_days)).isoformat()
        note = {
            "agent": self.name,
            "run_id": self.run_id,
            "ts": _iso(_now()),
            "trigger": self.trigger,
        }
        row = {
            "run_id": self.run_id,
            "agent_name": self.name,
            "target_system": action.target_system,
            "tool_name": action.tool,
            "target_entity": action.target_entity,
            "target_id": action.target_id,
            "payload": redact_payload({**action.payload, "_note": note}),
            "response": response,
            "status": status,
            "dry_run": self.dry_run,
            "reversible_until": reversible_until,
        }
        try:
            self.supa.insert("agent_writes", row)
        except Exception as exc:
            log.warning("%s: agent_writes insert failed: %s", self.name, exc)

    # -- write handling -----------------------------------------------------

    def _handle_action(self, item: dict[str, Any], action: AgentAction) -> None:
        reason = self._should_escalate(action)
        if reason:
            self._escalate(action, reason)
            return

        if self._at_cap():
            self._halt_for_cap()
            return

        self._throttle()
        self._writes_this_run += 1
        self.result.writes_attempted += 1

        if not self.can_write_live:
            self.result.writes_skipped += 1
            self._audit_write(
                action,
                status="skipped",
                response={"dry_run": True},
                reversible=action.reversible,
            )
            return

        try:
            response = self.do_write(action) or {}
            status = "executed"
            self.result.writes_executed += 1
        except Exception as exc:
            response = {"error": str(exc)[:1000]}
            status = "failed"
            log.exception("%s: do_write failed for %s", self.name, action.tool)

        self._audit_write(
            action,
            status=status,
            response=response,
            reversible=action.reversible,
        )

    # -- rollback linkage (§3.6) --------------------------------------------

    def rollback_write(self, write_id: int) -> dict[str, Any]:
        """Mark a single audited write as rolled_back (best-effort)."""
        if not self.supa:
            return {"ok": False, "error": "no Supabase client"}
        try:
            self.supa.update("agent_writes", str(write_id), {"status": "rolled_back"})
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "write_id": write_id}

    # -- template method ----------------------------------------------------

    def run(self) -> AgentRunResult:
        """Orchestrate collect -> decide -> write -> report."""
        log.info(
            "%s: starting run %s (state=%s dry_run=%s)",
            self.name,
            self.run_id,
            self.state,
            self.dry_run,
        )
        try:
            items = list(self.collect())
            self.result.reads = len(items)
            for item in items:
                if self._capped:
                    break
                for action in self.decide(item) or []:
                    if self._capped:
                        break
                    self._handle_action(item, action)
        except EscalationError as exc:
            self.result.error = f"escalation halt: {exc}"
            log.error(self.result.error)
        except Exception as exc:
            self.result.error = f"{type(exc).__name__}: {exc}"
            log.exception("%s: run failed", self.name)
        self.report()
        return self.result

    def report(self) -> None:
        """Persist the run summary to Supabase + notify the human owner."""
        self.result.finished_at = _now()
        self.result.summary = self.result.message
        if self.supa:
            try:
                self.supa.insert(
                    "agent_runs",
                    {
                        "run_id": self.run_id,
                        "agent_name": self.name,
                        "trigger_source": self.trigger,
                        "state": self.state,
                        "started_at": _iso(self.result.started_at),
                        "finished_at": _iso(self.result.finished_at),
                        "writes_attempted": self.result.writes_attempted,
                        "writes_executed": self.result.writes_executed,
                        "writes_skipped": self.result.writes_skipped,
                        "escalations": self.result.escalations,
                        "dry_run": self.dry_run,
                        "summary": self.result.summary,
                        "error": self.result.error,
                        "meta": {"findings": self.result.findings, "capped": self.result.capped},
                    },
                )
            except Exception as exc:
                log.warning("%s: agent_runs insert failed: %s", self.name, exc)
        try:
            self.notifier.notify(self.notify_channel, self.result.summary)
        except Exception as exc:
            log.warning("%s: notifier failed: %s", self.name, exc)
        log.info(self.result.summary)
