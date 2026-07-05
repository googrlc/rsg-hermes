"""Tests for the shared AgentRunner lifecycle (hermes/agents/base.py).

Uses fakes for Supabase + notifier so no external services are required.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes.agents.base import AgentAction, AgentRunner, NullNotifier, _ulid, redact_payload


class FakeSupa:
    def __init__(self):
        self.writes = []
        self.runs = []
        self.findings = []

    def insert(self, table, payload):
        getattr(self, {"agent_writes": "writes", "agent_runs": "runs", "book_hygiene_findings": "findings"}.get(table, "writes")).append(payload)
        return payload

    def update(self, table, _id, payload):
        return payload


class CountingNotifier(NullNotifier):
    def __init__(self):
        self.messages = []

    def notify(self, channel, text):
        self.messages.append((channel, text))


class NoopAgent(AgentRunner):
    name = "noop"
    default_state = "dry_run"

    def __init__(self, *a, decisions=None, **kw):
        super().__init__(*a, **kw)
        self._decisions = decisions or {}

    def collect(self):
        return [{"id": i} for i in range(self._decisions.get("items", 3))]

    def decide(self, item):
        return [AgentAction(tool="notes.create", target_entity="note", target_id=str(item["id"]), confidence=0.9)]


class LiveNoopAgent(NoopAgent):
    default_state = "live_autonomous"

    def do_write(self, action):
        return {"id": "written-" + action.target_id}


def test_ulid_is_26_chars_and_sortable():
    a = _ulid(ms=1_000_000)
    b = _ulid(ms=2_000_000)
    assert len(a) == 26 and len(b) == 26
    assert a < b  # later timestamp sorts after


def test_redact_payload_redacts_pii_keeps_last4():
    out = redact_payload({
        "ssn": "123-45-6789",
        "dateOfBirth": "1980-01-01",
        "driversLicense": "GA-999",
        "policyNumber": "POL-AB-12345678",
        "commercialName": "Acme LLC",
        "nested": {"ssn": "000-00-0000", "city": "Atlanta"},
    })
    assert out["ssn"] == "[REDACTED:SSN]"
    assert out["dateOfBirth"] == "[REDACTED:DOB]"
    assert out["driversLicense"] == "[REDACTED:DL]"
    assert out["policyNumber"].endswith("5678")
    assert out["commercialName"] == "Acme LLC"
    assert out["nested"]["ssn"] == "[REDACTED:SSN]"
    assert out["nested"]["city"] == "Atlanta"


def test_dry_run_skips_writes_and_audits():
    supa = FakeSupa()
    agent = NoopAgent(supa, dry_run=True, notifier=CountingNotifier())
    result = agent.run()
    assert result.writes_attempted == 3
    assert result.writes_executed == 0
    assert result.writes_skipped == 3
    assert len(supa.writes) == 3
    assert all(w["status"] == "skipped" and w["dry_run"] for w in supa.writes)
    assert len(supa.runs) == 1
    assert supa.runs[0]["agent_name"] == "noop"


def test_live_state_executes_and_sets_reversible_until():
    supa = FakeSupa()
    agent = LiveNoopAgent(supa, notifier=CountingNotifier())
    assert agent.can_write_live
    result = agent.run()
    assert result.writes_executed == 3
    assert result.writes_skipped == 0
    assert all(w["status"] == "executed" for w in supa.writes)
    assert all(w["reversible_until"] is not None for w in supa.writes)


def test_blast_cap_halts_and_notifies():
    supa = FakeSupa()
    notifier = CountingNotifier()
    agent = LiveNoopAgent(supa, notifier=notifier, decisions={"items": 60})
    agent.max_writes_per_run = 2
    result = agent.run()
    assert result.writes_executed == 2
    assert result.capped is True
    assert any("blast-radius cap" in m[1] for m in notifier.messages)


def test_low_confidence_escalates():
    supa = FakeSupa()
    notifier = CountingNotifier()

    class LowConf(LiveNoopAgent):
        def decide(self, item):
            return [AgentAction(tool="x", confidence=0.5)]

    agent = LowConf(supa, notifier=notifier)
    result = agent.run()
    assert result.escalations == 3
    assert result.writes_executed == 0
    assert any(w["status"] == "escalated" for w in supa.writes)


def test_monetary_and_carrier_facing_escalate():
    supa = FakeSupa()

    class Money(LiveNoopAgent):
        def decide(self, item):
            return [AgentAction(tool="x", confidence=0.99, monetary=True)]

    class Carrier(LiveNoopAgent):
        def decide(self, item):
            return [AgentAction(tool="x", confidence=0.99, carrier_facing=True)]

    assert Money(supa).run().escalations == 3
    assert Carrier(supa).run().escalations == 3


def test_systemic_anomaly_escalates_after_cap():
    supa = FakeSupa()

    class Anomaly(LiveNoopAgent):
        def __init__(self, *a, **kw):
            kw.setdefault("decisions", {"items": 5})
            super().__init__(*a, **kw)

        def decide(self, item):
            return [AgentAction(tool="x", confidence=0.99, anomaly_key="dup-email")]

    agent = Anomaly(supa)
    agent.systemic_anomaly_cap = 3  # "more than 3 records" -> 4th trips
    result = agent.run()
    # first 3 records write fine; the 4th + 5th trip the systemic-anomaly gate.
    assert result.escalations == 2
    assert result.writes_executed == 3


def test_throttle_enforces_min_interval():
    supa = FakeSupa()

    class Fast(LiveNoopAgent):
        pass

    agent = Fast(supa, decisions={"items": 3})
    agent.write_interval_seconds = 0.05
    start = time.monotonic()
    agent.run()
    assert time.monotonic() - start >= 0.1  # at least 2 gaps of 0.05s


def test_log_finding_writes_to_findings_table():
    supa = FakeSupa()
    agent = NoopAgent(supa, dry_run=True)

    class Finder(NoopAgent):
        def decide(self, item):
            self.log_finding(insured_database_id="42", finding_type="missing_field", confidence=1.0)
            return []

    agent2 = Finder(supa, dry_run=True)
    agent2.run()
    assert len(supa.findings) == 3
    assert supa.findings[0]["finding_type"] == "missing_field"


def test_no_supabase_still_runs():
    agent = NoopAgent(None, dry_run=True, notifier=CountingNotifier())
    result = agent.run()
    assert result.ok
    assert result.writes_skipped == 3
