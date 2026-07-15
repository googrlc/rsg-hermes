"""Tests for the renewal executor (Hermes Job Contract v2)."""

from __future__ import annotations

from typing import Any

import pytest

from hermes.renewals import executor


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeSupa:
    def __init__(self, *, eligible=None, renewals=None, claim_ok=True):
        self.eligible = list(eligible or [])
        self.renewals = renewals or {}
        self._by_id = {r["id"]: r for r in self.eligible}
        self.claim_ok = claim_ok
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, str, dict]] = []
        self.update_wheres: list[tuple[str, dict, dict]] = []
        self.selects: list[tuple[str, dict]] = []

    def select(self, table, *, columns="*", params=None, limit=100):
        params = params or {}
        self.selects.append((table, params))
        if table == "outbound_sync_queue":
            return list(self.eligible)
        if table == "project_85_renewals":
            rid = str(params.get("id", "")).replace("eq.", "")
            row = self.renewals.get(rid)
            return [row] if row else []
        return []

    def insert(self, table, payload):
        self.inserts.append((table, payload))
        return {"id": f"{table}-id", **payload}

    def update(self, table, record_id, payload):
        self.updates.append((table, record_id, payload))
        return {"id": record_id, **payload}

    def update_where(self, table, payload, *, filters):
        self.update_wheres.append((table, payload, filters))
        if not self.claim_ok:
            return []
        rid = str(filters.get("id", "")).replace("eq.", "")
        base = self._by_id.get(rid, {"id": rid})
        return [{**base, **payload}]

    # convenience accessors
    def inserted(self, table):
        return [p for t, p in self.inserts if t == table]


class FakeNowCerts:
    def __init__(self, *, policy=None, policy_after=None, task_result=None):
        self._policy = policy
        self._policy_after = policy_after if policy_after is not None else policy
        self.task_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.reads = 0
        self.task_result = task_result or {"database_id": "task-1"}

    def find_policy_by_number(self, number):
        self.reads += 1
        return self._policy if self.reads == 1 else self._policy_after

    def insert_task(self, payload):
        self.task_calls.append(payload)
        return self.task_result

    def update_policy(self, payload):
        self.update_calls.append(payload)
        return {"ok": True}


class FakeMomentum:
    def __init__(self, result=None):
        self.calls: list[dict] = []
        self.result = result or {"noteId": "note-1"}

    def manage_notes(self, payload):
        self.calls.append(payload)
        return self.result


class FakeNotifier:
    posts: list[str] = []

    def __init__(self, *, channel=None, **kw):
        self.channel = channel

    def post_message(self, *, text, blocks=None):
        FakeNotifier.posts.append(text)
        return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_row(
    action,
    *,
    fields=None,
    channel="task",
    note=None,
    policy="POL1",
    renewal_id="ren-1",
    approved=True,
    expected="apply the approved renewal change",
    queue_id="q-1",
):
    payload: dict[str, Any] = {
        "action": action,
        "renewal_id": renewal_id,
        "policy_number": policy,
        "expected_result": expected,
        "channel": channel,
    }
    if fields:
        payload["fields"] = fields
    if note:
        payload["note"] = note
    return {
        "id": queue_id,
        "approved_by": "lamar" if approved else None,
        "approved_at": "2026-07-15T00:00:00Z" if approved else None,
        "payload": payload,
    }


def renewal_map(renewal_id="ren-1"):
    return {renewal_id: {"id": renewal_id, "policy_number": "POL1", "client_name": "Acme"}}


POLICY = {"databaseId": "pol-guid", "number": "POL1", "premium": 4000, "insuredDatabaseId": "ins-guid"}


@pytest.fixture(autouse=True)
def _reset_notifier():
    FakeNotifier.posts = []
    yield
    FakeNotifier.posts = []


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_is_noop_case_insensitive_numeric():
    before = {"premium": 4200, "PolicyStatus": "Active"}
    assert executor._is_noop(before, {"Premium": 4200}) is True
    assert executor._is_noop(before, {"Premium": "4200"}) is True
    assert executor._is_noop(before, {"Premium": 4300}) is False
    assert executor._is_noop(before, {}) is False


def test_current_value_case_insensitive():
    assert executor._current_value({"premium": 10}, "Premium") == 10
    assert executor._current_value({"Premium": 10}, "premium") == 10
    assert executor._current_value({"a": 1}, "missing") is None


# ---------------------------------------------------------------------------
# stage_renewal_job
# ---------------------------------------------------------------------------
def test_stage_renewal_job_shapes_contract_row():
    supa = FakeSupa()
    executor.stage_renewal_job(
        supa, action="update_ams", renewal_id="ren-1", policy_number="POL1",
        expected_result="premium 4200", approved_by="lamar", fields={"Premium": 4200},
    )
    table, payload = supa.inserts[0]
    assert table == "outbound_sync_queue"
    assert payload["object_type"] == "renewal"
    assert payload["destination_system"] == "nowcerts"
    assert payload["object_id"] == "POL1"
    assert payload["status"] == "queued"
    assert payload["action"] == "update"  # queue enum, not the renewal action
    assert payload["approved_by"] == "lamar"
    assert payload["approved_at"]
    assert payload["payload"]["action"] == "update_ams"
    assert payload["payload"]["fields"] == {"Premium": 4200}


def test_stage_renewal_job_rejects_unknown_action():
    with pytest.raises(ValueError):
        executor.stage_renewal_job(
            FakeSupa(), action="delete_everything", renewal_id="r", policy_number="P",
            expected_result="x", approved_by="lamar",
        )


# ---------------------------------------------------------------------------
# process_job — success paths
# ---------------------------------------------------------------------------
def test_request_terms_creates_task_no_policy_write():
    supa = FakeSupa(renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))
    out = executor.process_job(supa, make_row("request_terms"), nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "completed"
    assert out.verified is True
    assert len(nc.task_calls) == 1
    assert nc.task_calls[0]["insured_database_id"] == "ins-guid"
    assert nc.task_calls[0]["policy_number"] == "POL1"
    assert nc.update_calls == []  # never touches policy fields
    receipt = supa.inserted("renewal_execution_receipts")[0]
    assert receipt["status"] == "completed"
    trail = supa.inserted("renewal_actions")[0]
    assert trail["action_type"] == "REQUEST_TERMS"
    # queue marked completed
    assert supa.updates[-1][0] == "outbound_sync_queue"
    assert supa.updates[-1][2]["status"] == "completed"


def test_prepare_options_stages_only_no_ams_write():
    supa = FakeSupa(renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))
    out = executor.process_job(supa, make_row("prepare_options"), nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "completed"
    assert nc.task_calls == []
    assert nc.update_calls == []
    assert supa.inserted("renewal_actions")[0]["action_type"] == "PREPARE_OPTIONS"


def test_client_follow_up_creates_task():
    supa = FakeSupa(renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))
    out = executor.process_job(supa, make_row("client_follow_up"), nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "completed"
    assert len(nc.task_calls) == 1
    assert supa.inserted("renewal_actions")[0]["action_type"] == "CLIENT_FOLLOW_UP"


def test_request_terms_note_channel_uses_momentum():
    supa = FakeSupa(renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))
    mom = FakeMomentum()
    row = make_row("request_terms", channel="note", note="please quote renewal terms")
    out = executor.process_job(supa, row, nowcerts=nc, momentum=mom, notifier_cls=FakeNotifier)

    assert out.outcome == "completed"
    assert len(mom.calls) == 1
    assert mom.calls[0]["databaseId"] == "ins-guid"
    assert nc.task_calls == []


def test_update_ams_writes_only_approved_fields_and_verifies():
    supa = FakeSupa(renewals=renewal_map())
    before = dict(POLICY)  # premium 4000
    after = {**POLICY, "premium": 4200}
    nc = FakeNowCerts(policy=before, policy_after=after)
    row = make_row("update_ams", fields={"Premium": 4200})
    out = executor.process_job(supa, row, nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "completed"
    assert out.verified is True
    assert len(nc.update_calls) == 1
    assert nc.update_calls[0]["DatabaseId"] == "pol-guid"
    assert nc.update_calls[0]["Premium"] == 4200
    assert "premium" not in nc.update_calls[0]  # only the approved keys are sent
    receipt = supa.inserted("renewal_execution_receipts")[0]
    assert receipt["after_state"]["premium"] == 4200
    assert supa.inserted("renewal_actions")[0]["action_type"] == "AMS_UPDATE"


def test_update_ams_noop_short_circuits_without_write():
    supa = FakeSupa(renewals=renewal_map())
    before = {**POLICY, "premium": 4200}
    nc = FakeNowCerts(policy=before)
    row = make_row("update_ams", fields={"Premium": 4200})
    out = executor.process_job(supa, row, nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "completed"
    assert nc.update_calls == []  # already in the approved state — no mutation
    assert nc.reads == 1  # only the before-read; no post-write verify read


# ---------------------------------------------------------------------------
# process_job — failure / block paths
# ---------------------------------------------------------------------------
def test_update_ams_verify_mismatch_fails_and_escalates(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    supa = FakeSupa(renewals=renewal_map())
    before = dict(POLICY)  # 4000
    after = dict(POLICY)   # still 4000 — the write did not persist
    nc = FakeNowCerts(policy=before, policy_after=after)
    row = make_row("update_ams", fields={"Premium": 4200}, note="SENTINEL_SECRET_TOKEN")
    out = executor.process_job(supa, row, nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "failed"
    receipt = supa.inserted("renewal_execution_receipts")[0]
    assert receipt["status"] == "failed"
    assert supa.updates[-1][2]["status"] == "failed"  # queue terminal
    assert supa.inserted("guardrail_logs")  # exception evidence
    assert supa.inserted("renewal_actions")[0]["action_type"] == "EXECUTION_FAILED"
    # high-impact escalation fired, and leaks no raw payload / secret
    assert FakeNotifier.posts, "expected a Slack escalation"
    assert "SENTINEL_SECRET_TOKEN" not in FakeNotifier.posts[0]
    assert "POL1" in FakeNotifier.posts[0]


def test_missing_mapping_blocks():
    supa = FakeSupa(renewals=renewal_map())
    nc = FakeNowCerts(policy=None)  # no policy matches
    out = executor.process_job(supa, make_row("update_ams", fields={"Premium": 1}),
                               nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "blocked"
    assert nc.update_calls == []
    assert supa.inserted("renewal_execution_receipts")[0]["status"] == "blocked"
    assert supa.updates[-1][2]["status"] == "failed"


def test_duplicate_policy_blocks():
    supa = FakeSupa(renewals=renewal_map())
    nc = FakeNowCerts(policy={"_ambiguous": True, "matches": [POLICY, POLICY]})
    out = executor.process_job(supa, make_row("update_ams", fields={"Premium": 1}),
                               nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "blocked"
    assert nc.update_calls == []


def test_missing_renewal_blocks():
    supa = FakeSupa(renewals={})  # renewal not in project_85_renewals
    nc = FakeNowCerts(policy=dict(POLICY))
    out = executor.process_job(supa, make_row("request_terms"), nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "blocked"
    assert "not found" in (out.reason or "")
    assert nc.task_calls == []


def test_update_ams_without_fields_blocks():
    supa = FakeSupa(renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))
    out = executor.process_job(supa, make_row("update_ams"), nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "blocked"
    assert nc.update_calls == []


def test_missing_expected_result_blocks():
    supa = FakeSupa(renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))
    row = make_row("request_terms", expected="")
    out = executor.process_job(supa, row, nowcerts=nc, notifier_cls=FakeNotifier)

    assert out.outcome == "blocked"


# ---------------------------------------------------------------------------
# run_executor — claim + eligibility + dry-run
# ---------------------------------------------------------------------------
def test_run_executor_claims_and_processes():
    supa = FakeSupa(eligible=[make_row("request_terms")], renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))
    summary = executor.run_executor(supa=supa, nowcerts=nc, notifier_cls=FakeNotifier, limit=1)

    assert summary["claimed"] == 1
    assert summary["completed"] == 1
    # guarded claim happened before any write
    assert supa.update_wheres[0][0] == "outbound_sync_queue"
    assert supa.update_wheres[0][1]["status"] == "processing"


def test_run_executor_skips_when_row_already_taken():
    supa = FakeSupa(eligible=[make_row("request_terms")], renewals=renewal_map(), claim_ok=False)
    nc = FakeNowCerts(policy=dict(POLICY))
    summary = executor.run_executor(supa=supa, nowcerts=nc, notifier_cls=FakeNotifier, limit=1)

    assert summary["claimed"] == 0
    assert nc.task_calls == []
    assert supa.inserted("renewal_execution_receipts") == []


def test_run_executor_eligibility_filter_requires_approval():
    supa = FakeSupa(eligible=[], renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))
    executor.run_executor(supa=supa, nowcerts=nc, notifier_cls=FakeNotifier, limit=1)

    _, params = supa.selects[0]
    assert params["object_type"] == "eq.renewal"
    assert params["destination_system"] == "eq.nowcerts"
    assert params["status"] == "eq.queued"
    assert params["approved_by"] == "not.is.null"
    assert params["approved_at"] == "not.is.null"


def test_run_executor_dry_run_is_side_effect_free():
    supa = FakeSupa(eligible=[make_row("update_ams", fields={"Premium": 4200})], renewals=renewal_map())
    nc = FakeNowCerts(policy=dict(POLICY))  # premium 4000 -> would execute
    summary = executor.run_executor(supa=supa, nowcerts=nc, notifier_cls=FakeNotifier, limit=1, dry_run=True)

    assert summary["claimed"] == 0
    assert summary["previews"][0]["verdict"] == "would_execute"
    assert supa.update_wheres == []       # no claim
    assert supa.inserts == []             # no receipt
    assert nc.update_calls == []          # no NowCerts mutation
