"""intake_submissions worker — drives the rsg-intake state machine.

Pipeline per row (PROJECT-CONTEXT.md):

  received → synthesizing → synthesized → drafting → awaiting_approval
                                                              ↓ (Slack APPROVE)
                                                            approved
                                                              ↓
                                                            writing → written → complete

  Any state → failed (with error_log entry + Slack alert)

This file lands incrementally across Phase 3:

  Step 2 (here): worker scaffold + ``received -> awaiting_approval`` arc.
                 Synthesizer + Slack draft post are stubbed; the arc
                 transitions states so the claim pattern is exercised
                 end-to-end. Stubs raise NotImplementedError which the
                 worker catches and routes to ``failed`` + Slack alert.
  Step 3:        actual synthesizer fill-in (commands/agency_intake.py).
  Step 4:        Slack draft post replaces the stubbed _post_draft.
  Step 5:        ``approved -> writing -> written -> complete`` arc +
                 retrieval inserts + completion post.

Single-worker assumption is fine today; the claim pattern in
``claim_next_received`` makes scale-out safe later.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from hermes.integrations.intake_submissions import (
    IntakeError,
    TABLE,
    claim_next_received,
    transition,
)
from hermes.integrations.slack_notifier import SlackNotifier, SlackNotifierError
from hermes.integrations.supabase_client import SupabaseClient, SupabaseClientError

log = logging.getLogger(__name__)


DEFAULT_POLL_SECONDS = 5.0


def _intake_alert_channel() -> str:
    """Slack channel for failure alerts (#systems-check by default)."""
    return os.environ.get(
        "HERMES_INTAKE_ALERT_CHANNEL", os.environ.get("HERMES_SYSTEMS_CHECK_CHANNEL", "C0ANSEP6SSD")
    ).strip()


def _intake_draft_channel() -> str:
    """Slack channel for draft + completion posts."""
    return os.environ.get(
        "HERMES_INTAKE_DRAFT_CHANNEL",
        os.environ.get("HERMES_SENTINEL_SLACK_CHANNEL", "D0B2PJYLGQG"),
    ).strip()


def _post_alert(text: str, *, channel: str | None = None) -> None:
    """Post to Slack, swallowing errors so the worker doesn't crash on Slack failures."""
    try:
        notifier = SlackNotifier(channel=channel or _intake_alert_channel())
        notifier.post_message(text=text)
    except SlackNotifierError:
        log.exception("Slack post failed (channel=%s)", channel)
    except Exception:
        log.exception("Unexpected Slack post failure (channel=%s)", channel)


def _safe_transition_to_failed(
    supa: SupabaseClient, submission_id: str, *, exc: Exception, stage: str
) -> None:
    """Move the submission to ``failed`` and alert Slack. Swallows transition errors."""
    err = {"stage": stage, "message": str(exc), "exception_type": type(exc).__name__}
    try:
        transition(supa, submission_id, "failed", note=f"{stage} failed", error=err)
    except Exception:
        log.exception("Failed to mark submission %s as failed", submission_id)
    _post_alert(
        f":rotating_light: Hermes intake submission failed\n"
        f"- submission_id: {submission_id}\n"
        f"- stage: {stage}\n"
        f"- error: {exc}"
    )


# ---------------------------------------------------------------------------
# Pluggable hooks (filled across Steps 3-4).
# Steps 3+4 swap real implementations in via module-level assignment so the
# worker's claim/transition flow stays stable.
# ---------------------------------------------------------------------------


from hermes.commands.agency_intake import (  # noqa: E402
    build_approval_blocks,
    render_hermes_blocks,
    synthesize_from_payload,
)


def _format_submission_approval_prompt(
    submission_id: str, draft_summary: dict[str, Any]
) -> str:
    """One-liner Slack-friendly summary keyed on submission_id (not draft_id).

    Lamar reviews this in #crm-entry / the draft channel before clicking
    APPROVE ALL. The submission_id is embedded so the audit trail
    matches the intake_submissions row even if the buttons are clipped.
    """
    account = draft_summary.get("account") or {}
    contacts = draft_summary.get("contacts") or []
    opps = draft_summary.get("opportunities") or []
    facts = draft_summary.get("facts") or []
    note = draft_summary.get("note") or {}

    contact_names = ", ".join(
        c.get("full_name", "") or f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        for c in contacts
    ) or "(none)"
    lob_lines = [
        f"  - {o.get('line_of_business', '?')}  ({o.get('stage', '?')})"
        + (f"  quote {o.get('quote_number')}" if o.get("quote_number") else "")
        for o in opps
    ]
    restricted = sum(1 for f in facts if isinstance(f, dict) and f.get("sensitivity") == "restricted")

    return (
        f"*Intake draft ready — NOTHING WRITTEN YET.*  "
        f"(submission_id: `{submission_id}`)\n\n"
        f"*Account:*       {account.get('account_name', '?')}  "
        f"({account.get('entity_type', '?')}, {account.get('industry', '?')})\n"
        f"*Contacts:*      {contact_names}\n"
        f"*Opportunities:* {len(opps)}\n"
        + ("\n".join(lob_lines) + "\n" if lob_lines else "")
        + f"*Note:*          {note.get('title', '(none)')}  ({note.get('note_type', '?')})\n"
        f"*Facts staged:*  {len(facts)} ({restricted} restricted)\n\n"
        "Click a button below — or reply with the token verbatim."
    )


def post_draft_to_slack(submission_id: str, draft_summary: dict[str, Any]) -> None:
    """Real Step-4 implementation of ``post_draft``.

    Posts the synthesized intake draft to the draft channel with the same
    interactive Block Kit buttons used by the agency-intake Slack flow —
    the existing ``^agency_intake_`` action handler in slack_socket.py
    will route button clicks to the (Step-5-rewritten) approve_draft.

    The button value is ``submission_id`` (NOT draft_id) and the
    ``block_id`` carries it too so the handler can recover it from either
    field.

    Slack failures are caught + logged so the worker doesn't fail the
    transition. The row will still settle at awaiting_approval; an
    operator can manually post or click the approval token reply.
    """
    prompt_text = _format_submission_approval_prompt(submission_id, draft_summary)
    blocks = build_approval_blocks(submission_id, prompt_text)
    try:
        notifier = SlackNotifier(channel=_intake_draft_channel())
        notifier.post_message(text=prompt_text, blocks=blocks)
        log.info("Posted intake draft to Slack for submission %s", submission_id)
    except SlackNotifierError as exc:
        log.exception("Slack draft post failed for submission %s", submission_id)
        # Surface to the alert channel so it doesn't get lost.
        _post_alert(
            f":warning: Intake draft synthesized but Slack post failed\n"
            f"- submission_id: {submission_id}\n"
            f"- error: {exc}\n"
            f"Row will settle at awaiting_approval; approve via reply token "
            f"or POST /agency-intake/approve."
        )


synthesize_payload = synthesize_from_payload
render_blocks = render_hermes_blocks
post_draft = post_draft_to_slack


# ---------------------------------------------------------------------------
# Arc 1: received -> synthesizing -> synthesized -> drafting -> awaiting_approval
# ---------------------------------------------------------------------------


def process_one_received(supa: SupabaseClient) -> bool:
    """Pull one received row through to awaiting_approval.

    Returns True if a row was processed (success OR failed), False if the
    queue was empty / lost the claim race.
    """
    claimed = claim_next_received(supa)
    if claimed is None:
        return False

    submission_id = str(claimed["id"])
    log.info("Claimed intake submission %s (synthesizing)", submission_id)

    payload = claimed.get("payload") or {}

    # Stage A: synthesize -> 'synthesized' (writes hermes_blocks + draft_summary)
    try:
        draft_summary, warnings = synthesize_payload(payload)
        hermes_blocks = render_blocks(draft_summary)
    except Exception as exc:
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="synthesize")
        return True

    if warnings:
        log.warning("Submission %s synthesis warnings: %s", submission_id, warnings)

    try:
        transition(
            supa, submission_id, "synthesized",
            note="synthesis complete",
            extra_fields={"hermes_blocks": hermes_blocks, "draft_summary": draft_summary},
        )
    except (IntakeError, SupabaseClientError) as exc:
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="synthesized-transition")
        return True

    # Stage B: drafting — pass-through. Dedup probes deferred per Phase 3
    # decision (post-Phase-5 work).
    try:
        transition(
            supa, submission_id, "drafting",
            note="dedup probes deferred — pass-through",
        )
    except (IntakeError, SupabaseClientError) as exc:
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="drafting-transition")
        return True

    # Stage C: post Slack draft and settle at awaiting_approval.
    try:
        post_draft(submission_id, draft_summary)
        transition(
            supa, submission_id, "awaiting_approval",
            note="draft posted to Slack",
        )
    except (IntakeError, SupabaseClientError) as exc:
        _safe_transition_to_failed(
            supa, submission_id, exc=exc, stage="awaiting_approval-transition"
        )
        return True
    except Exception as exc:
        # Slack post failures are non-fatal; record and continue to
        # awaiting_approval. But if the post + transition both crash here,
        # surface as failed.
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="slack-draft-post")
        return True

    log.info("Submission %s now awaiting_approval", submission_id)
    return True


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Arc 2: approved -> writing (enqueue CRM writes; crm_queue_worker POSTs)
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _claim_next_approved(supa: SupabaseClient) -> dict[str, Any] | None:
    """Atomically claim one ``status='approved'`` row -> 'writing'.

    Same race-safe pattern as ``claim_next_received``: SELECT then
    conditional UPDATE WHERE status='approved'. Losing workers get [].
    """
    candidates = supa.select(
        TABLE,
        columns="id,status_history,draft_summary,approved_by",
        params={"status": "eq.approved", "order": "created_at.asc"},
        limit=1,
    )
    if not candidates:
        return None
    candidate = candidates[0]
    candidate_id = candidate["id"]

    history = list(candidate.get("status_history") or [])
    history.append({
        "from": "approved",
        "to": "writing",
        "at": _utcnow_iso(),
        "note": "claimed by intake worker",
    })
    claimed = supa.update_where(
        TABLE,
        {"status": "writing", "status_history": history},
        filters={"id": f"eq.{candidate_id}", "status": "eq.approved"},
    )
    if not claimed:
        return None
    row = claimed[0]
    # update_where doesn't return joined columns; keep the draft_summary
    # we already SELECTed on candidate.
    row.setdefault("draft_summary", candidate.get("draft_summary"))
    row.setdefault("approved_by", candidate.get("approved_by"))
    return row


def process_one_approved(supa: SupabaseClient) -> bool:
    """Enqueue CRM writes for one approved submission; transition to 'writing'.

    The crm_queue_worker (a separate process) actually POSTs to EspoCRM.
    ``process_writing_check`` (below) advances the row to written → complete
    once those queue rows reach terminal status.
    """
    # Imported here to avoid circular-import: agency_intake_approval imports
    # this module too (no, it doesn't — but kept here for symmetry with the
    # retrieval-inserts call further down).
    from hermes.operations.agency_intake_approval import _enqueue_crm_writes

    claimed = _claim_next_approved(supa)
    if claimed is None:
        return False

    submission_id = str(claimed["id"])
    log.info("Claimed approved submission %s -> writing", submission_id)

    draft_summary = claimed.get("draft_summary") or {}
    approver = claimed.get("approved_by") or "system"

    # Intake target: NowCerts (record of truth) + Supabase pipeline is the active
    # architecture; the legacy EspoCRM enqueue is kept behind the flag for rollback.
    if os.environ.get("HERMES_INTAKE_TARGET", "nowcerts").strip().lower() == "nowcerts":
        from hermes.integrations.intake_submissions import transition
        from hermes.intake.commit import commit_draft

        try:
            result = commit_draft(supa, draft_summary, approved_by=approver)
        except Exception as exc:
            _safe_transition_to_failed(supa, submission_id, exc=exc, stage="commit-nowcerts-intake")
            return True
        try:
            supa.update(
                TABLE, submission_id,
                {"records_created": {
                    "target": "nowcerts",
                    "opportunities": [r.get("id") for r in result.get("opportunities", [])],
                    "intake_job_id": result.get("intake_job_id"),
                    "nextcloud_folder": result.get("nextcloud_folder"),
                }},
            )
            # writing -> written -> complete (opportunities created now; the
            # NowCerts insured create is a separate approval-gated executor job).
            transition(supa, submission_id, "written",
                       note="intake opportunities created; NowCerts insured staged")
            transition(supa, submission_id, "complete",
                       note=f"intake committed to NowCerts+Supabase by {approver}")
        except Exception as exc:
            _safe_transition_to_failed(supa, submission_id, exc=exc, stage="complete-nowcerts-intake")
        log.info(
            "Submission %s committed to NowCerts+Supabase (%d opportunities, intake_job=%s)",
            submission_id, result.get("opportunity_count", 0), result.get("intake_job_id"),
        )
        return True

    try:
        queue_ids, write_plan = _enqueue_crm_writes(
            supa, draft_summary,
            created_by_role=f"agency-intake:{approver}:submission-{submission_id}",
        )
    except Exception as exc:
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="enqueue-crm-writes")
        return True

    # Stash queue_ids in records_created so the writing arc can resolve
    # them. The dict shape will be augmented with espo_records once the
    # queue rows terminate, and finally with retrieval_row_ids on complete.
    try:
        supa.update(
            TABLE, submission_id,
            {"records_created": {"queue_ids": queue_ids, "write_plan": write_plan}},
        )
    except SupabaseClientError as exc:
        _safe_transition_to_failed(supa, submission_id, exc=exc, stage="stash-queue-ids")
        return True

    log.info(
        "Submission %s enqueued %d CRM writes; awaiting crm_queue_worker",
        submission_id, len(queue_ids),
    )
    return True


# ---------------------------------------------------------------------------
# Arc 3: writing -> written -> complete
# ---------------------------------------------------------------------------


def _resolve_queue_outcomes(
    supa: SupabaseClient, queue_ids: list[str]
) -> tuple[bool, list[dict[str, Any]], list[str]]:
    """Look up crm_write_queue rows. Returns (all_terminal, success_records, failures).

    ``success_records`` is a list of dicts with keys queue_id, entity_type,
    entity_id (resolved from crm_receipts.transaction_id when needed).
    """
    if not queue_ids:
        return True, [], []

    in_filter = ",".join(queue_ids)
    rows = supa.select(
        "crm_write_queue",
        columns="id,entity_type,entity_id,status",
        params={"id": f"in.({in_filter})"},
        limit=len(queue_ids) + 5,
    )
    by_id = {str(r["id"]): r for r in rows}

    success: list[dict[str, Any]] = []
    failures: list[str] = []
    all_terminal = True

    for qid in queue_ids:
        row = by_id.get(qid)
        if not row:
            # Queue row not yet visible (rare timing); treat as not-terminal.
            all_terminal = False
            continue
        status = (row.get("status") or "").upper()
        if status == "SUCCESS":
            entity_id = row.get("entity_id")
            if not entity_id:
                # New record — recover Espo ID from crm_receipts.transaction_id.
                receipts = supa.select(
                    "crm_receipts",
                    columns="queue_id,transaction_id,entity_id",
                    params={"queue_id": f"eq.{qid}"},
                    limit=1,
                )
                if receipts:
                    tx = str(receipts[0].get("transaction_id") or "")
                    entity_id = (
                        tx[5:] if tx.startswith("espo_")
                        else receipts[0].get("entity_id") or tx or None
                    )
            success.append({
                "queue_id": qid,
                "entity_type": row.get("entity_type"),
                "entity_id": entity_id,
            })
        elif status in {"FAILED", "BLOCKED"}:
            failures.append(qid)
        else:
            all_terminal = False
    return all_terminal, success, failures


def process_writing_check(supa: SupabaseClient) -> int:
    """Advance any submission in ``writing`` whose CRM writes have settled.

    Returns the number of submissions advanced (to written or failed).
    """
    from hermes.operations.agency_intake_approval import _insert_retrieval_rows

    candidates = supa.select(
        TABLE,
        columns="id,records_created,draft_summary,status_history",
        params={"status": "eq.writing", "order": "created_at.asc"},
        limit=10,
    )
    advanced = 0
    for row in candidates:
        submission_id = str(row["id"])
        stash = row.get("records_created") or {}
        queue_ids = stash.get("queue_ids") if isinstance(stash, dict) else None
        if not queue_ids:
            log.warning(
                "Submission %s in writing but has no queue_ids stashed — skipping",
                submission_id,
            )
            continue

        all_terminal, success, failures = _resolve_queue_outcomes(supa, queue_ids)
        if not all_terminal:
            continue  # still in flight

        if failures:
            _safe_transition_to_failed(
                supa, submission_id,
                exc=Exception(
                    f"{len(failures)} of {len(queue_ids)} CRM writes failed permanently: "
                    f"{failures}"
                ),
                stage="crm-writes",
            )
            advanced += 1
            continue

        # Walk writing -> written.
        try:
            transition(
                supa, submission_id, "written",
                note=f"{len(success)} CRM records written",
                extra_fields={
                    "records_created": {
                        "queue_ids": queue_ids,
                        "espo_records": success,
                    },
                },
            )
        except (IntakeError, SupabaseClientError) as exc:
            _safe_transition_to_failed(supa, submission_id, exc=exc, stage="written-transition")
            advanced += 1
            continue

        # Retrieval inserts → client_entities / client_facts / client_notes.
        retrieval_ids: dict[str, list[str]] = {}
        try:
            retrieval_ids = _insert_retrieval_rows(supa, row.get("draft_summary") or {})
        except Exception as exc:
            log.exception("Retrieval insert failed for submission %s", submission_id)
            _safe_transition_to_failed(supa, submission_id, exc=exc, stage="retrieval-inserts")
            advanced += 1
            continue

        # written -> complete.
        try:
            transition(
                supa, submission_id, "complete",
                note="retrieval inserts complete",
                extra_fields={
                    "records_created": {
                        "queue_ids": queue_ids,
                        "espo_records": success,
                        "retrieval_row_ids": retrieval_ids,
                    },
                },
            )
        except (IntakeError, SupabaseClientError) as exc:
            _safe_transition_to_failed(supa, submission_id, exc=exc, stage="complete-transition")
            advanced += 1
            continue

        # Completion post to the draft channel for Gretchen's visibility.
        retrieval_summary = ", ".join(
            f"{k}={len(v)}" for k, v in retrieval_ids.items() if v
        ) or "(none)"
        _post_alert(
            f":white_check_mark: Hermes intake complete\n"
            f"- submission_id: {submission_id}\n"
            f"- CRM records: {len(success)}\n"
            f"- Retrieval rows: {retrieval_summary}",
            channel=_intake_draft_channel(),
        )
        advanced += 1
    return advanced


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


def tick(supa: SupabaseClient) -> dict[str, int]:
    """One worker iteration. Drives all three arcs once each."""
    received = 1 if process_one_received(supa) else 0
    approved = 1 if process_one_approved(supa) else 0
    writing_advanced = process_writing_check(supa)
    return {
        "received_processed": received,
        "approved_processed": approved,
        "writing_advanced": writing_advanced,
    }


def run_intake_worker_loop(
    supa: SupabaseClient,
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> None:
    """Continuously drive the intake state machine. Designed for
    ``hermes --run-intake-worker`` and a docker-compose service."""
    interval = poll_seconds if poll_seconds > 0 else DEFAULT_POLL_SECONDS
    log.info("Starting intake worker loop: interval=%ss", interval)
    while True:
        try:
            result = tick(supa)
            if any(result.values()):
                log.info("intake tick: %s", result)
        except Exception:
            log.exception("intake worker tick crashed; continuing")
        time.sleep(interval)
