"""Renewal Executor — Hermes Job Contract v2.

Hermes is the controlled execution worker for the RSG Renewal Desk. It processes
human-approved renewal instructions staged in ``outbound_sync_queue`` and performs
only validated, authorized writes in NowCerts. It is NOT the renewal workspace, the
source of approval, or the system of record.

Authorized input — every field must hold:
    object_type='renewal' · destination_system='nowcerts' · status='queued'
    · approved_by set · approved_at set · payload.renewal_id resolves in
    project_85_renewals · payload.action ∈ ACTIONS · payload.expected_result set.

Procedure per job (contract §Execution procedure):
    claim → validate → read NowCerts → compare → stop on ambiguity → execute the
    approved action → re-read to verify → write receipt → mark queue completed/failed
    → record the outcome in renewal_actions. Escalate high-impact failures to Slack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError
from hermes.operations import renewal_tracker
from hermes.operations.guardrails import log_guardrail_event
from hermes.sync.nowcerts_client import NowCertsClient, NowCertsClientError

from . import config, eligibility
from .momentum_mcp_client import MomentumMCPClient, MomentumMCPClientError

log = logging.getLogger(__name__)

# --- Authorized-input constants ------------------------------------------------
QUEUE_TABLE = "outbound_sync_queue"
RECEIPTS_TABLE = "renewal_execution_receipts"
OBJECT_TYPE_RENEWAL = "renewal"
DESTINATION_NOWCERTS = "nowcerts"

QUEUE_QUEUED = "queued"
QUEUE_PROCESSING = "processing"
QUEUE_COMPLETED = "completed"
QUEUE_FAILED = "failed"

ACTION_REQUEST_TERMS = "request_terms"
ACTION_PREPARE_OPTIONS = "prepare_options"
ACTION_CLIENT_FOLLOW_UP = "client_follow_up"
ACTION_UPDATE_AMS = "update_ams"
ACTIONS = frozenset(
    {ACTION_REQUEST_TERMS, ACTION_PREPARE_OPTIONS, ACTION_CLIENT_FOLLOW_UP, ACTION_UPDATE_AMS}
)
# Actions that never mutate the AMS policy/coverage/premium surface.
NON_MUTATING_ACTIONS = frozenset({ACTION_PREPARE_OPTIONS})

# Executor action → renewal_actions.action_type (see renewal_tracker.VALID_ACTION_TYPES).
ACTION_TO_TRAIL = {
    ACTION_REQUEST_TERMS: "REQUEST_TERMS",
    ACTION_PREPARE_OPTIONS: "PREPARE_OPTIONS",
    ACTION_CLIENT_FOLLOW_UP: "CLIENT_FOLLOW_UP",
    ACTION_UPDATE_AMS: "AMS_UPDATE",
}

ACTOR_ROLE = "HermesRenewalExecutor"

# High-impact = anything that mutates the AMS or is a lost-premium risk. Failures
# and blocks on these escalate to Slack.
HIGH_IMPACT_ACTIONS = frozenset({ACTION_UPDATE_AMS})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------
# Job context + result types
# ------------------------------------------------------------------------------
@dataclass
class JobContext:
    """Parsed, contract-shaped view of one queue row."""

    queue_id: str
    action: str
    renewal_id: str
    policy_number: str
    expected_result: str
    approved_by: str
    approved_at: str | None
    fields: dict[str, Any]
    payload: dict[str, Any]
    renewal_row: dict[str, Any] | None = None


@dataclass
class JobOutcome:
    outcome: str  # completed | failed | blocked
    reason: str | None = None
    receipt_id: str | None = None
    verified: bool = False
    nowcerts_ids: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------------------
# Enqueue helper (for the upstream Renewal Desk Site / internal callers / tests)
# ------------------------------------------------------------------------------
def stage_renewal_job(
    supa: SupabaseClient,
    *,
    action: str,
    renewal_id: str,
    policy_number: str,
    expected_result: str,
    approved_by: str,
    approved_at: str | None = None,
    fields: dict[str, Any] | None = None,
    channel: str = "task",
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Stage a contract-shaped, pre-approved renewal instruction on the queue.

    The queue's native ``action`` enum (create/update/skip) is set from the
    renewal action; the real renewal action lives in ``payload.action``.
    ``object_id`` = policy_number reuses ``uq_outbound_queue_open_work`` so a
    given policy+action can have at most one open queued job.
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown renewal action '{action}'; must be one of {sorted(ACTIONS)}")
    now = now or _utcnow()
    approved_at = approved_at or now.isoformat()
    queue_action = "update" if action == ACTION_UPDATE_AMS else "create"
    payload = {
        "action": action,
        "renewal_id": renewal_id,
        "policy_number": policy_number,
        "expected_result": expected_result,
        "channel": channel,
    }
    if fields:
        payload["fields"] = fields
    if note:
        payload["note"] = note
    return supa.insert(
        QUEUE_TABLE,
        {
            "object_type": OBJECT_TYPE_RENEWAL,
            "object_id": policy_number,
            "destination_system": DESTINATION_NOWCERTS,
            "action": queue_action,
            "payload": payload,
            "status": QUEUE_QUEUED,
            "attempt_count": 0,
            "approved_by": approved_by,
            "approved_at": approved_at,
        },
    )


# ------------------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------------------
def run_executor(
    *,
    supa: SupabaseClient | None = None,
    nowcerts: NowCertsClient | None = None,
    momentum: MomentumMCPClient | None = None,
    notifier_cls: type[SlackNotifier] = SlackNotifier,
    limit: int = 1,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Process up to ``limit`` approved renewal jobs (contract: "claim one").

    ``dry_run`` is side-effect-free: it validates, reads NowCerts, and compares,
    then reports the intended write WITHOUT claiming the row or mutating anything.
    """
    supa = supa or SupabaseClient()
    now = now or _utcnow()
    summary = {"claimed": 0, "completed": 0, "failed": 0, "blocked": 0, "previews": []}

    eligible = _eligible_jobs(supa, limit=limit)
    if not eligible:
        return summary

    nowcerts = nowcerts or NowCertsClient()

    for row in eligible[:limit]:
        if dry_run:
            summary["previews"].append(_preview_job(supa, nowcerts, row))
            continue

        claimed = _claim(supa, row, now=now)
        if claimed is None:
            continue  # another cycle grabbed it — single-writer scheduler backstop
        summary["claimed"] += 1
        try:
            outcome = process_job(
                supa,
                claimed,
                nowcerts=nowcerts,
                momentum=momentum,
                notifier_cls=notifier_cls,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 — one bad job must not kill the run
            log.exception("Renewal executor crashed on queue_id=%s", claimed.get("id"))
            outcome = _terminal_error(
                supa, claimed, nowcerts=None, notifier_cls=notifier_cls,
                reason=f"unexpected executor error: {exc}", now=now,
            )
        summary[outcome.outcome] = summary.get(outcome.outcome, 0) + 1

    return summary


def run_worker_loop(*, poll_seconds: float = 300.0, limit: int = 1) -> None:
    """Continuously process approved renewal jobs (opt-in systemd worker).

    Not always-on by contract intent — the primary path is a scheduled/triggered
    one-shot. This loop exists for parity with the other Hermes queue workers.
    """
    import time

    interval = poll_seconds if poll_seconds > 0 else 300.0
    log.info("Starting renewal executor loop: interval=%ss limit=%s", interval, limit)
    while True:
        try:
            summary = run_executor(limit=limit)
            if summary["claimed"]:
                log.info(
                    "Renewal executor: claimed=%s completed=%s failed=%s blocked=%s",
                    summary["claimed"], summary["completed"], summary["failed"], summary["blocked"],
                )
        except Exception:  # noqa: BLE001 — never let one bad cycle kill the loop
            log.exception("Renewal executor cycle failed")
        time.sleep(interval)


def _eligible_jobs(supa: SupabaseClient, *, limit: int) -> list[dict[str, Any]]:
    """Rows matching the full authorized-input filter, oldest first."""
    return supa.select(
        QUEUE_TABLE,
        params={
            "object_type": f"eq.{OBJECT_TYPE_RENEWAL}",
            "destination_system": f"eq.{DESTINATION_NOWCERTS}",
            "status": f"eq.{QUEUE_QUEUED}",
            "approved_by": "not.is.null",
            "approved_at": "not.is.null",
            "order": "created_at.asc",
        },
        limit=max(limit, 1),
    )


def _claim(supa: SupabaseClient, row: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    """Guarded claim: flip queued→processing only if still queued.

    On Hermes's single-writer scheduler this is race-safe — a concurrent claim of
    the same row updates zero rows and returns an empty list.
    """
    queue_id = row.get("id")
    try:
        updated = supa.update_where(
            QUEUE_TABLE,
            {"status": QUEUE_PROCESSING, "updated_at": now.isoformat()},
            filters={"id": f"eq.{queue_id}", "status": f"eq.{QUEUE_QUEUED}"},
        )
    except SupabaseClientError:
        log.exception("Failed to claim queue_id=%s", queue_id)
        return None
    return updated[0] if updated else None


# ------------------------------------------------------------------------------
# Single-job pipeline
# ------------------------------------------------------------------------------
def process_job(
    supa: SupabaseClient,
    row: dict[str, Any],
    *,
    nowcerts: NowCertsClient,
    momentum: MomentumMCPClient | None = None,
    notifier_cls: type[SlackNotifier] = SlackNotifier,
    now: datetime | None = None,
) -> JobOutcome:
    """Validate → read → compare → execute → verify → receipt → finalize."""
    now = now or _utcnow()
    started_at = now
    ctx = _load(row)

    # 2. Validate the renewal, policy, mapping, approval, and requested action.
    reason = _validate(supa, ctx)
    if reason:
        return _block(supa, ctx, reason=reason, before_state=None,
                      notifier_cls=notifier_cls, started_at=started_at)

    # 3. Read the current NowCerts record before writing.
    try:
        before_state = nowcerts.find_policy_by_number(ctx.policy_number)
    except NowCertsClientError as exc:
        return _fail(supa, ctx, reason=f"NowCerts read failed: {exc}", before_state=None,
                     after_state=None, nowcerts_ids={}, notifier_cls=notifier_cls,
                     started_at=started_at)

    # 5. Stop on ambiguity, missing mappings, duplicate policies, conflicting state.
    if before_state is None:
        return _block(supa, ctx, reason=f"no NowCerts policy matches number {ctx.policy_number!r} (missing mapping)",
                      before_state=None, notifier_cls=notifier_cls, started_at=started_at)
    if before_state.get("_ambiguous"):
        return _block(supa, ctx, reason=f"duplicate NowCerts policies for number {ctx.policy_number!r}",
                      before_state={"matches": len(before_state.get("matches") or [])},
                      notifier_cls=notifier_cls, started_at=started_at)

    # 5b. Execution-time eligibility revalidation — the SAME centralized rule,
    # re-run on the freshly-read NowCerts policy + a live insured-active check.
    # Blocks only on a definitive `excluded` verdict (policy went dead/superseded
    # or the insured was deactivated since approval) — a human already approved,
    # so ambiguous/needs_verification does not hard-block here.
    reval = _revalidate_eligibility(nowcerts, before_state, now=now)
    if reval:
        return _block(supa, ctx, reason=reval, before_state=before_state,
                      notifier_cls=notifier_cls, started_at=started_at)

    # 4/5. Compare current values with the approved instruction.
    if ctx.action == ACTION_UPDATE_AMS and _is_noop(before_state, ctx.fields):
        # Already in the approved state — complete idempotently, no write.
        return _complete(supa, ctx, before_state=before_state, after_state=before_state,
                         verified=True, nowcerts_ids={"policy_database_id": before_state.get("databaseId")},
                         note="no-op: NowCerts already matches approved values",
                         started_at=started_at)

    # 6. Execute only the approved action.
    try:
        result, nowcerts_ids = _execute(nowcerts, momentum, ctx, before_state)
    except (NowCertsClientError, MomentumMCPClientError) as exc:
        return _fail(supa, ctx, reason=f"execute failed: {exc}", before_state=before_state,
                     after_state=None, nowcerts_ids={}, notifier_cls=notifier_cls,
                     started_at=started_at)

    # 7. Read the NowCerts record again to verify the result (a 200 is not proof).
    verified, after_state, verify_reason = _verify(nowcerts, ctx, result, before_state)
    if not verified:
        return _fail(supa, ctx, reason=f"verification failed: {verify_reason}",
                     before_state=before_state, after_state=after_state,
                     nowcerts_ids=nowcerts_ids, notifier_cls=notifier_cls,
                     started_at=started_at)

    return _complete(supa, ctx, before_state=before_state, after_state=after_state,
                     verified=True, nowcerts_ids=nowcerts_ids, started_at=started_at)


def _load(row: dict[str, Any]) -> JobContext:
    payload = dict(row.get("payload") or {})
    return JobContext(
        queue_id=str(row.get("id")),
        action=str(payload.get("action") or "").strip(),
        renewal_id=str(payload.get("renewal_id") or "").strip(),
        policy_number=str(payload.get("policy_number") or "").strip(),
        expected_result=str(payload.get("expected_result") or "").strip(),
        approved_by=str(row.get("approved_by") or "").strip(),
        approved_at=row.get("approved_at"),
        fields=dict(payload.get("fields") or {}),
        payload=payload,
    )


def _validate(supa: SupabaseClient, ctx: JobContext) -> str | None:
    """Return a block reason, or None when the job is authorized to execute."""
    if ctx.action not in ACTIONS:
        return f"unknown or missing action {ctx.action!r}"
    if not ctx.approved_by or not ctx.approved_at:
        return "missing approval (approved_by / approved_at)"
    if not ctx.policy_number:
        return "missing policy_number"
    if not ctx.expected_result:
        return "missing expected_result"
    if not ctx.renewal_id:
        return "missing renewal_id"
    if ctx.action == ACTION_UPDATE_AMS and not ctx.fields:
        return "update_ams requires an explicit non-empty fields map"
    # Linked renewal must exist in project_85_renewals.
    try:
        rows = supa.select(
            "project_85_renewals", params={"id": f"eq.{ctx.renewal_id}"}, limit=1
        )
    except SupabaseClientError as exc:
        return f"renewal lookup failed: {exc}"
    if not rows:
        return f"renewal {ctx.renewal_id} not found in project_85_renewals"
    ctx.renewal_row = rows[0]
    return None


# ------------------------------------------------------------------------------
# Compare / execute / verify
# ------------------------------------------------------------------------------
def _revalidate_eligibility(
    nowcerts: NowCertsClient, before_state: dict[str, Any], *, now: datetime
) -> str | None:
    """Execution-time safety gate using the centralized eligibility vocabulary.

    Blocks only on *definitive drift since approval* — the policy went dead
    (Cancelled/Expired/Flat Cancel/Non-Renewed/Lapsed), was superseded
    (Renewed/Rewritten), or the insured was deactivated. It deliberately does NOT
    apply the 120-day discovery window or lineage checks (those are discovery
    filters, not execution concerns) — a human already approved this specific job.
    Transient AMS read failures never block.
    """
    guid = before_state.get("insuredDatabaseId") or before_state.get("InsuredDatabaseId")
    if guid:
        try:
            if not nowcerts.is_insured_active(str(guid)):
                return "insured is no longer active in NowCerts"
        except NowCertsClientError:
            pass  # don't block on a transient AMS read failure

    status = eligibility.normalize_status(
        before_state.get("policyStatus") or before_state.get("PolicyStatus")
        or before_state.get("status") or before_state.get("Status")
    )
    if status in eligibility.EXCLUDE_STATUSES:
        return f"policy lifecycle status is now {status}"
    if status in eligibility.SUPERSEDED_STATUSES:
        return f"policy has been superseded ({status})"
    return None


def _current_value(before: dict[str, Any], key: str) -> Any:
    """Case-insensitive field lookup (write keys are PascalCase, reads camelCase)."""
    if key in before:
        return before[key]
    lowered = key.lower()
    for k, v in before.items():
        if k.lower() == lowered:
            return v
    return None


def _values_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _is_noop(before: dict[str, Any], fields: dict[str, Any]) -> bool:
    """True when every approved field already equals the current NowCerts value."""
    if not fields:
        return False
    return all(_values_equal(_current_value(before, k), v) for k, v in fields.items())


def _extract_created_id(result: Any) -> str | None:
    """Pull the created task/note id from a NowCerts insert response.

    NowCerts' Zapier InsertTask nests the record under ``data`` (e.g.
    ``result["data"]["database_id"]``), so check BOTH the top level and the
    nested ``data`` object — otherwise a successfully-created task reads as
    "no id" and fails read-after-write verification.
    """
    if not isinstance(result, dict):
        return None
    for obj in (result, result.get("data") if isinstance(result.get("data"), dict) else {}):
        for key in ("database_id", "databaseId", "noteId", "note_id", "id"):
            val = obj.get(key)
            if val:
                return str(val)
    return None


def _execute(
    nowcerts: NowCertsClient,
    momentum: MomentumMCPClient | None,
    ctx: JobContext,
    before: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Perform exactly the approved action. Returns (result, nowcerts_ids)."""
    if ctx.action == ACTION_PREPARE_OPTIONS:
        # Staged analysis only — no AMS mutation.
        return {"staged": True}, {}

    if ctx.action == ACTION_UPDATE_AMS:
        policy_db_id = before.get("databaseId") or before.get("DatabaseId")
        result = nowcerts.update_policy({"DatabaseId": policy_db_id, **ctx.fields})
        return result, {"policy_database_id": policy_db_id}

    # request_terms / client_follow_up → task (default) or note.
    channel = str(ctx.payload.get("channel") or "task").strip().lower()
    if channel == "note":
        client = momentum or MomentumMCPClient()
        note_payload = {
            "operation": "create",
            "databaseId": ctx.payload.get("momentum_client_id") or before.get("insuredDatabaseId"),
            "title": _task_title(ctx),
            "note": _task_body(ctx),
            "renewalId": ctx.renewal_id,
        }
        result = client.manage_notes(note_payload)
        return result, {"note_id": _extract_created_id(result)}

    task_payload = _build_task_payload(ctx, before)
    result = nowcerts.insert_task(task_payload)
    return result, {
        "task_database_id": _extract_created_id(result),
        "insured_database_id": task_payload.get("insured_database_id"),
    }


def _verify(
    nowcerts: NowCertsClient,
    ctx: JobContext,
    result: dict[str, Any],
    before: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Re-read NowCerts and confirm the change persisted."""
    if ctx.action == ACTION_PREPARE_OPTIONS:
        return True, before, None

    if ctx.action == ACTION_UPDATE_AMS:
        try:
            after = nowcerts.find_policy_by_number(ctx.policy_number)
        except NowCertsClientError as exc:
            return False, None, f"post-write read failed: {exc}"
        if not after or after.get("_ambiguous"):
            return False, after, "post-write policy read empty or ambiguous"
        mismatched = [
            k for k, v in ctx.fields.items() if not _values_equal(_current_value(after, k), v)
        ]
        if mismatched:
            return False, after, f"fields did not persist: {', '.join(mismatched)}"
        return True, after, None

    # request_terms / client_follow_up: the returned record id is the persistence
    # proof for a create. No list re-read endpoint for tasks/notes. NowCerts nests
    # the id under `data`, so _extract_created_id checks top-level AND nested.
    created_id = _extract_created_id(result)
    if not created_id:
        return False, {"result": result}, "NowCerts returned no id for the created task/note"
    return True, {"created_id": created_id}, None


def _build_task_payload(ctx: JobContext, before: dict[str, Any]) -> dict[str, Any]:
    """NowCerts Zapier InsertTask body (snake_case). Values overridable via payload."""
    p = ctx.payload
    due = p.get("due_date") or (_utcnow() + timedelta(days=int(p.get("due_in_days", 7)))).date().isoformat()
    return {
        "title": _task_title(ctx),
        "status": p.get("task_status") or "Open",
        "priority": p.get("task_priority") or "Normal",
        "due_date": due,
        "description": _task_body(ctx),
        "insured_database_id": p.get("insured_database_id") or before.get("insuredDatabaseId"),
        "policy_number": ctx.policy_number,
        "category_name": p.get("category_name") or "Renewal",
    }


def _task_title(ctx: JobContext) -> str:
    default = "Renewal — request terms" if ctx.action == ACTION_REQUEST_TERMS else "Renewal — client follow-up"
    return str(ctx.payload.get("title") or default)


def _task_body(ctx: JobContext) -> str:
    note = str(ctx.payload.get("note") or "").strip()
    body = note or ctx.expected_result
    return f"{body}\n\nApproved by: {ctx.approved_by}".strip()


# ------------------------------------------------------------------------------
# Finalizers — receipt, queue status, renewal trail, escalation
# ------------------------------------------------------------------------------
def _complete(
    supa: SupabaseClient,
    ctx: JobContext,
    *,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
    verified: bool,
    nowcerts_ids: dict[str, Any],
    started_at: datetime,
    note: str | None = None,
) -> JobOutcome:
    receipt_id = _write_receipt(
        supa, ctx, status="completed", verified=verified, before_state=before_state,
        after_state=after_state, nowcerts_ids=nowcerts_ids, error=note, started_at=started_at,
    )
    _set_queue_status(supa, ctx.queue_id, QUEUE_COMPLETED)
    _log_trail(supa, ctx, ACTION_TO_TRAIL[ctx.action], receipt_id=receipt_id,
               verified=verified, nowcerts_ids=nowcerts_ids, extra={"note": note} if note else None)
    log.info("Renewal executor completed queue_id=%s action=%s policy=%s",
             ctx.queue_id, ctx.action, ctx.policy_number)
    return JobOutcome(outcome="completed", receipt_id=receipt_id, verified=verified, nowcerts_ids=nowcerts_ids)


def _block(
    supa: SupabaseClient,
    ctx: JobContext,
    *,
    reason: str,
    before_state: dict[str, Any] | None,
    notifier_cls: type[SlackNotifier],
    started_at: datetime,
) -> JobOutcome:
    return _terminal(supa, ctx, receipt_status="blocked", trail="EXECUTION_BLOCKED",
                     reason=reason, before_state=before_state, after_state=None,
                     nowcerts_ids={}, notifier_cls=notifier_cls, started_at=started_at)


def _fail(
    supa: SupabaseClient,
    ctx: JobContext,
    *,
    reason: str,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
    nowcerts_ids: dict[str, Any],
    notifier_cls: type[SlackNotifier],
    started_at: datetime,
) -> JobOutcome:
    return _terminal(supa, ctx, receipt_status="failed", trail="EXECUTION_FAILED",
                     reason=reason, before_state=before_state, after_state=after_state,
                     nowcerts_ids=nowcerts_ids, notifier_cls=notifier_cls, started_at=started_at)


def _terminal(
    supa: SupabaseClient,
    ctx: JobContext,
    *,
    receipt_status: str,
    trail: str,
    reason: str,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
    nowcerts_ids: dict[str, Any],
    notifier_cls: type[SlackNotifier],
    started_at: datetime,
) -> JobOutcome:
    """Preserve evidence, mark the queue failed, and escalate high-impact stops."""
    receipt_id = _write_receipt(
        supa, ctx, status=receipt_status, verified=False, before_state=before_state,
        after_state=after_state, nowcerts_ids=nowcerts_ids, error=reason, started_at=started_at,
    )
    _set_queue_status(supa, ctx.queue_id, QUEUE_FAILED, error=reason)
    _log_trail(supa, ctx, trail, receipt_id=receipt_id, verified=False,
               nowcerts_ids=nowcerts_ids, extra={"reason": reason})
    _log_guardrail(supa, ctx, receipt_status=receipt_status, reason=reason)
    if ctx.action in HIGH_IMPACT_ACTIONS:
        _escalate(notifier_cls, ctx, receipt_status=receipt_status, reason=reason)
    log.warning("Renewal executor %s queue_id=%s action=%s policy=%s: %s",
                receipt_status, ctx.queue_id, ctx.action, ctx.policy_number, reason)
    return JobOutcome(outcome=receipt_status, reason=reason, receipt_id=receipt_id)


def _terminal_error(
    supa: SupabaseClient,
    row: dict[str, Any],
    *,
    nowcerts: NowCertsClient | None,
    notifier_cls: type[SlackNotifier],
    reason: str,
    now: datetime,
) -> JobOutcome:
    """Last-resort finalizer for an unexpected crash after claim."""
    ctx = _load(row)
    return _fail(supa, ctx, reason=reason, before_state=None, after_state=None,
                 nowcerts_ids={}, notifier_cls=notifier_cls, started_at=now)


def _write_receipt(
    supa: SupabaseClient,
    ctx: JobContext,
    *,
    status: str,
    verified: bool,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
    nowcerts_ids: dict[str, Any],
    error: str | None,
    started_at: datetime,
) -> str | None:
    row = {
        "queue_id": ctx.queue_id,
        "renewal_id": ctx.renewal_id or None,
        "policy_number": ctx.policy_number or None,
        "action": ctx.action or "unknown",
        "actor": ctx.approved_by or None,
        "approved_at": ctx.approved_at,
        "before_state": before_state,
        "requested_change": {
            "action": ctx.action,
            "expected_result": ctx.expected_result,
            "fields": ctx.fields or None,
            "channel": ctx.payload.get("channel"),
        },
        "after_state": after_state,
        "verified": verified,
        "nowcerts_ids": nowcerts_ids or {},
        "status": status,
        "error": error,
        "started_at": started_at.isoformat(),
        "finished_at": _utcnow().isoformat(),
    }
    try:
        created = supa.insert(RECEIPTS_TABLE, row)
        return created.get("id") if isinstance(created, dict) else None
    except SupabaseClientError:
        log.exception("Failed to write renewal execution receipt for queue_id=%s", ctx.queue_id)
        return None


def _set_queue_status(
    supa: SupabaseClient, queue_id: str, status: str, *, error: str | None = None
) -> None:
    payload: dict[str, Any] = {"status": status, "updated_at": _utcnow().isoformat()}
    if error:
        payload["last_error"] = error[:2000]
    try:
        supa.update(QUEUE_TABLE, queue_id, payload)
    except SupabaseClientError:
        log.exception("Failed to set queue status %s for queue_id=%s", status, queue_id)


def _log_trail(
    supa: SupabaseClient,
    ctx: JobContext,
    action_type: str,
    *,
    receipt_id: str | None,
    verified: bool,
    nowcerts_ids: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> None:
    if not ctx.renewal_row:
        return  # no linked project_85 renewal → nothing to append to (FK safety)
    details = {
        "queue_id": ctx.queue_id,
        "receipt_id": receipt_id,
        "policy_number": ctx.policy_number,
        "verified": verified,
        "nowcerts_ids": nowcerts_ids or {},
        "actor": ctx.approved_by,
    }
    if extra:
        details.update(extra)
    try:
        renewal_tracker.log_renewal_action(
            supa,
            renewal_id=ctx.renewal_row.get("id"),
            action_type=action_type,
            details=details,
            performed_by_role=ACTOR_ROLE,
        )
    except (SupabaseClientError, ValueError):
        log.exception("Failed to append renewal_actions trail for queue_id=%s", ctx.queue_id)


def _log_guardrail(
    supa: SupabaseClient, ctx: JobContext, *, receipt_status: str, reason: str
) -> None:
    try:
        log_guardrail_event(
            supa,
            agent_role=ACTOR_ROLE,
            attempted_action=f"renewal_executor:{ctx.action}",
            rule_violated=f"renewal_execution_{receipt_status}",
            context_payload={
                "queue_id": ctx.queue_id,
                "renewal_id": ctx.renewal_id,
                "policy_number": ctx.policy_number,
                "reason": reason,
            },
            severity="CRITICAL" if ctx.action in HIGH_IMPACT_ACTIONS else "HIGH",
        )
    except SupabaseClientError:
        log.exception("Failed to persist guardrail log for queue_id=%s", ctx.queue_id)


def _escalate(
    notifier_cls: type[SlackNotifier], ctx: JobContext, *, receipt_status: str, reason: str
) -> None:
    """Escalate high-impact renewal stops to #systems-check.

    Posts identifiers + reason only — never the raw payload or credentials.
    """
    import os

    if not os.environ.get("SLACK_BOT_TOKEN", "").strip():
        return
    text = (
        f":rotating_light: Renewal executor {receipt_status.upper()}\n"
        f"- action: {ctx.action}\n"
        f"- policy: {ctx.policy_number or 'n/a'}\n"
        f"- renewal_id: {ctx.renewal_id or 'n/a'}\n"
        f"- queue_id: {ctx.queue_id}\n"
        f"- reason: {reason}"
    )
    try:
        notifier_cls(channel=config.SLACK_SYSTEMS_CHECK).post_message(text=text)
    except (SlackNotifierError, Exception):  # noqa: BLE001 — alerting must never crash the run
        log.exception("Failed to post renewal escalation for queue_id=%s", ctx.queue_id)


# ------------------------------------------------------------------------------
# Dry-run preview (no claim, no write)
# ------------------------------------------------------------------------------
def _preview_job(
    supa: SupabaseClient, nowcerts: NowCertsClient, row: dict[str, Any]
) -> dict[str, Any]:
    ctx = _load(row)
    reason = _validate(supa, ctx)
    if reason:
        return {"queue_id": ctx.queue_id, "action": ctx.action, "policy_number": ctx.policy_number,
                "verdict": "would_block", "reason": reason}
    try:
        before = nowcerts.find_policy_by_number(ctx.policy_number)
    except NowCertsClientError as exc:
        return {"queue_id": ctx.queue_id, "action": ctx.action, "policy_number": ctx.policy_number,
                "verdict": "would_fail", "reason": f"NowCerts read failed: {exc}"}
    if before is None:
        return {"queue_id": ctx.queue_id, "action": ctx.action, "policy_number": ctx.policy_number,
                "verdict": "would_block", "reason": "missing NowCerts mapping"}
    if before.get("_ambiguous"):
        return {"queue_id": ctx.queue_id, "action": ctx.action, "policy_number": ctx.policy_number,
                "verdict": "would_block", "reason": "duplicate NowCerts policies"}
    if ctx.action == ACTION_UPDATE_AMS and _is_noop(before, ctx.fields):
        verdict = "would_noop"
    else:
        verdict = "would_execute"
    return {
        "queue_id": ctx.queue_id,
        "action": ctx.action,
        "policy_number": ctx.policy_number,
        "verdict": verdict,
        "intended_change": {"fields": ctx.fields or None, "expected_result": ctx.expected_result},
    }
